import http from 'k6/http';
import encoding from 'k6/encoding';
import { sleep } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { randomBytes, sha256 } from 'k6/crypto';
import { WebSocket } from 'k6/websockets';
import { runWebSocketSession } from './k6_websocket.js';
import { createPolicyRuntime } from './k6_execution_policy.js';
import sseRuntime from './k6_sse_runtime.js';
import { runSSESession } from './k6_sse.js';
import { createReminderGuard } from './k6_reminder_recovery.js';

// Runtime data lives outside report artifacts and is never sent to console.
const config = JSON.parse(open(__ENV.K6_TESTHUB_CONFIG));
const csvPools = Object.create(null);
for (const fileId of Object.keys(config.csv_files || {})) {
  csvPools[fileId] = new SharedArray('testhub-csv-' + fileId,
    () => JSON.parse(open(config.csv_files[fileId])));
}
const uploadFiles = Object.create(null);
for (const digest of Object.keys(config.upload_files || {})) {
  uploadFiles[digest] = new SharedArray('testhub-upload-' + digest,
    () => [encoding.b64encode(open(config.upload_files[digest], 'b'))]);
}
function uploadBytes(file) { return encoding.b64decode(uploadFiles[file.sha256][0]); }
const load = config.load_config || {};
const runtime = config.runtime_config || {};
const auth = runtime.auth_profile || {};
const authEnabled = Boolean(auth.mode);
const debugEnabled = load._purpose === 'debug' && Number(load.concurrency) === 1 && Number(load.iterations_per_vu) === 1;
let debugCount = 0;
const DEBUG_LIMIT = 100;
const rounds = Number(load.iterations_per_vu || 0);
const concurrency = Number(load.concurrency || 1);
const durationSeconds = Number(load.duration || 60);
const duration = `${durationSeconds}s`;
const requestTimeout = Number(runtime.timeout || 30);
const drainSeconds = requestTimeout + 5;
const loadExpired = new Error('LoadDurationElapsed');
let durationElapsed = false;
const steps = config.steps || [];
const executionGroups = config.execution_groups || steps.map((step, index) => ({
  policy: {}, steps: [{ index, requires: [] }], outputs: [],
})).filter(group => !steps[group.steps[0].index].is_setup);
const hasPolicy = executionGroups.some(group => Boolean(group.policy.group_id));
let policyRuntime = null;
let businessActive = false;
const hasWebSocket = steps.some(step => step.protocol === 'WEBSOCKET');
const hasSSE = steps.some(step => step.protocol === 'SSE');
export const options = {
  scenarios: { business: rounds > 0
    ? { executor: 'per-vu-iterations', vus: concurrency, iterations: rounds, maxDuration: duration, gracefulStop: '0s' }
    : { executor: 'constant-vus', vus: concurrency, duration, gracefulStop: `${drainSeconds}s` } },
  maxRedirects: 0,
  insecureSkipTLSVerify: (config.env_config || {}).verify_ssl === false,
  noConnectionReuse: runtime.keep_alive === false,
  noCookiesReset: authEnabled,
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'],
};
let context = null;
let reminderGuard = null;
let initialized = false;
let setupPassed = false;
let failedPermanently = false;
let cookieJar = null;
let expiresAt = 0;
let authenticated = false;
let requestScope = null;

function event(data) {
  if (data.kind === 'vu_done' && policyRuntime) policyRuntime.flush(true);
  console.log('TESTHUB_K6_EVENT ' + JSON.stringify(Object.assign({ vu: exec.vu.idInTest }, data)));
}

function setBusinessActive(active) {
  if (!hasPolicy || businessActive === active) return;
  businessActive = active;
  event({ kind: active ? 'business_active' : 'business_idle' });
}

// Both HTTP and async HTTP/WS paths drive this same group decision and outcome loop.
function* businessIteration() {
  let work = false, blockedIteration = false;
  for (const group of executionGroups) {
    requireLoadTime();
    if (!policyRuntime.begin(group, context)) continue;
    let failed = false, blocked = 0, executed = 0, finalThink = 0;
    for (const item of group.steps) {
      requireLoadTime();
      if ((failed && reminderGuard && reminderGuard.handles(steps[item.index]))
          || (group.policy.group_id && item.requires.some(name => name !== 'request_id' && !(name in context)))) {
        blocked++; blockedIteration = true;
        continue;
      }
      work = true; setBusinessActive(true);
      const ok = yield item.index;
      executed++;
      if (!ok) {
        failed = true;
        if (group.policy.group_id) for (const name of group.outputs) delete context[name];
      }
      if (failedPermanently) return;
      const think = steps[item.index].think_time || {};
      if ((think.type || '').toUpperCase() === 'FIXED' && Number(think.min || 0) > 0) {
        if (executed + blocked === group.steps.length) finalThink = Number(think.min) / 1000;
        else loadSleep(Number(think.min) / 1000);
      }
    }
    policyRuntime.complete(group, failed, blocked, executed);
    if (finalThink > 0) loadSleep(finalThink);
  }
  event({ kind: 'executor_iteration', idle: !work });
  if (work && !blockedIteration) event({ kind: 'iteration' });
  if (!work) {
    setBusinessActive(false);
    // Empty rounds remain bounded and never contribute to business activity or TPS.
    policyRuntime.flush();
    if (!rounds) loadSleep(Math.max(0.05, Math.min(policyRuntime.waitMs(), remainingLoadMs()) / 1000));
    else if (exec.vu.iterationInScenario + 1 < rounds) sleep(0.01);
  }
  policyRuntime.flush();
  if (rounds && exec.vu.iterationInScenario + 1 >= rounds) event({ kind: 'vu_done' });
}

function remainingLoadMs(now = Date.now()) {
  return rounds > 0 && !hasWebSocket ? Infinity : exec.scenario.startTime + durationSeconds * 1000 - now;
}

// 到期后不再发起业务、前置、登录或重试；已发出的请求仍完成断言和采样。
function requireLoadTime() {
  if (remainingLoadMs() <= 0) throw loadExpired;
}

function loadSleep(seconds) {
  requireLoadTime();
  const remaining = remainingLoadMs() / 1000;
  sleep(Math.min(seconds, remaining));
  if (seconds >= remaining) throw loadExpired;
}

function variableValue(key) {
  if (key === 'request_id') {
    if (!requestScope) throw new Error('MissingRequestScope');
    if (!requestScope.id) {
      const bytes = new Uint8Array(randomBytes(16));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
      requestScope.id = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    return requestScope.id;
  }
  if (!(key in context)) throw new Error('MissingVariable');
  return context[key];
}

function renderString(value, native = false) {
  if (typeof value !== 'string') return value;
  const regex = /\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+)\}/g;
  const matches = Array.from(value.matchAll(regex));
  if (native && matches.length === 1 && matches[0][0] === value) {
    const key = (matches[0][1] || matches[0][2]).trim();
    return variableValue(key);
  }
  return value.replace(regex, (_, brace, dollar) => {
    const key = (brace || dollar).trim();
    const value = variableValue(key);
    return value == null ? '' : String(value);
  });
}

function renderTree(value) {
  if (Array.isArray(value)) return value.map(renderTree);
  if (value !== null && typeof value === 'object') {
    const result = {};
    for (const key of Object.keys(value)) result[renderString(key)] = renderTree(value[key]);
    return result;
  }
  return renderString(value, true);
}

function jsonPath(data, expression) {
  const tokens = expression.slice(1).match(/\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\]/g) || [];
  let value = data;
  for (const token of tokens) {
    const key = token[0] === '.' ? token.slice(1) : Number(token.slice(1, -1));
    if (value === null || value === undefined || !Object.prototype.hasOwnProperty.call(value, key)) return undefined;
    value = value[key];
  }
  return value;
}

function authOrigin(value) {
  const parsed = String(value).match(/^(https?):\/\/(\[[0-9a-f:]+\]|[^/?#:@\s\\%]+)(?::(\d+))?(?:[/?#]|$)/i);
  if (!parsed) return '';
  const scheme = parsed[1].toLowerCase();
  const port = Number(parsed[3] || (scheme === 'https' ? 443 : 80));
  return port > 0 && port <= 65535 ? scheme + '://' + parsed[2].toLowerCase() + ':' + port : '';
}

function initialize(skipSetup = false) {
  policyRuntime = hasPolicy ? createPolicyRuntime(executionGroups, exec.vu.idInTest, event)
    : { begin: () => true, complete: () => {}, flush: () => {} };
  context = Object.create(null);
  context.base_url = (config.env_config || {}).base_url || '';
  context.baseUrl = context.base_url;
  context.vu_id = exec.vu.idInTest;
  context.iteration = 0;
  const assignedRows = Object.create(null);
  for (const variable of config.variables || []) {
    if ((variable.type || 'CONSTANT').toUpperCase() === 'CSV') {
      const fileId = String(variable.data_file_id || variable.file_id);
      // No modulo: insufficient accounts must be rejected before execution.
      if (!(fileId in assignedRows)) assignedRows[fileId] = csvPools[fileId][exec.vu.idInTest - 1];
      context[variable.name] = assignedRows[fileId][variable.column];
    } else context[variable.name] = variable.value === undefined ? '' : variable.value;
  }
  reminderGuard = runtime._reminder_recovery
    ? createReminderGuard(runtime._reminder_recovery, context, value => sha256(value, 'hex')) : null;
  initialized = true;
  if (authEnabled) cookieJar = new http.CookieJar();
  event({ kind: 'vu_start', ...(!rounds || hasWebSocket ? { scenario_start_ms: exec.scenario.startTime } : {}) });
  if (authEnabled) {
    if (!authOrigin(context.base_url) || authOrigin(context.base_url) !== authOrigin((config.env_config || {}).base_url)) {
      throw new Error('InvalidAuthOrigin');
    }
    if (auth.mode === 'LOGIN') {
      if (!authenticate('login')) return;
    } else {
      const value = context[auth.transport === 'COOKIE' ? auth.cookie_variable : auth.access_token_variable];
      if (typeof value !== 'string' || !value.trim()) { closeIdentity(); return; }
      if (auth.transport === 'COOKIE') cookieJar.set(context.base_url, auth.cookie_name, value);
      authenticated = true;
    }
  }
  if (skipSetup) return;
  for (let i = 0; i < steps.length; i++) {
    if (steps[i].is_setup && !steps[i].auth_phase && !protectedRequest(steps[i], i)) {
      event({ kind: 'setup_failed' });
      return;
    }
  }
  requireLoadTime();
  setupPassed = true;
}

// Shape-only previews never emit response values or arbitrary response keys.
function responseShape(value, state, depth = 0) {
  if (++state.nodes > 100 || depth > 5) { state.truncated = true; return '[omitted]'; }
  if (value === null) return null;
  if (Array.isArray(value)) {
    if (value.length > 10) state.truncated = true;
    return value.slice(0, 10).map(item => responseShape(item, state, depth + 1));
  }
  if (typeof value === 'object') {
    const result = {};
    const keys = Object.keys(value);
    if (keys.length > 20) state.truncated = true;
    keys.slice(0, 20).forEach((key, i) => {
      const safe = ['code', 'status', 'message', 'data', 'items', 'id', 'success', 'error', 'errors', 'result', 'total'].includes(key) ? key : 'field_' + (i + 1);
      result[safe] = /token|secret|password|authorization|cookie|session|credential/i.test(key) ? '******' : responseShape(value[key], state, depth + 1);
    });
    return result;
  }
  return '******';
}

function responseDiagnostic(response, step) {
  if (step.auth_phase) return { state: 'auth_omitted' };
  if (!response) return { state: 'unavailable' };
  const header = Object.keys(response.headers || {}).find(key => key.toLowerCase() === 'content-type');
  const type = header ? String(response.headers[header]).toLowerCase() : '';
  if (!/(?:application\/json|[+]json)(?:;|$)/.test(type)) return { state: 'non_json_omitted' };
  if (typeof response.body !== 'string') return { state: 'binary_omitted' };
  let bytes = 0;
  for (const character of response.body) {
    const code = character.codePointAt(0);
    bytes += code < 128 ? 1 : code < 2048 ? 2 : code < 65536 ? 3 : 4;
    if (bytes > 65536) return { state: 'oversize_omitted', truncated: true };
  }
  try {
    const state = { nodes: 0, truncated: false };
    const body = responseShape(JSON.parse(response.body), state);
    return { state: 'json', body, truncated: state.truncated };
  } catch (_) { return { state: 'invalid_json' }; }
}

function diagnostic(step, index, data) {
  if (!debugEnabled) return;
  if (debugCount++ >= DEBUG_LIMIT) { if (debugCount === DEBUG_LIMIT + 1) event({ kind: 'debug_truncated' }); return; }
  event({ kind: 'diagnostic', step: index, ...data });
}

function request(step, index, scope = {}) {
  requireLoadTime();
  const previousScope = requestScope;
  requestScope = scope;
  try { return performRequest(step, index); }
  catch (caught) {
    if (caught === loadExpired) throw caught;
    diagnostic(step, index, { outcome: 'not_sent', status: 0, assertions: [], extractors: [], response: { state: 'unavailable' } });
    throw new Error('RequestPreparationFailed');
  }
  finally { requestScope = previousScope; }
}

function performRequest(step, index) {
  let url = renderString(step.url).replace(/^\/(?=https?:\/\/)/, '');
  if (!/^https?:\/\//.test(url)) url = context.base_url.replace(/\/$/, '') + '/' + url.replace(/^\//, '');
  if (authEnabled) {
    if (!authOrigin(url) || authOrigin(url) !== authOrigin((config.env_config || {}).base_url)) throw new Error('InvalidAuthOrigin');
  }
  const params = step.params || {};
  const query = Object.keys(params).map(key => encodeURIComponent(renderString(key)) + '=' + encodeURIComponent(renderString(String(params[key])))).join('&');
  if (query) url += (url.includes('?') ? '&' : '?') + query;
  const headers = Object.create(null);
  const headerConfig = Object.create(null);
  const sources = [(config.env_config || {}).headers || {}, step.headers || {}];
  for (let sourceIndex = 0; sourceIndex < sources.length; sourceIndex++) {
    for (const key of Object.keys(sources[sourceIndex])) {
      const name = String(renderString(key)).toLowerCase();
      if (!/^[!#$%&'*+.^_`|~0-9a-z-]+$/.test(name)) throw new Error('InvalidHeader');
      headerConfig[name] = { value: sources[sourceIndex][key], fromStep: sourceIndex === 1 };
    }
  }
  for (const name of Object.keys(headerConfig)) {
    const entry = headerConfig[name];
    // Only explicit authentication templates may supply protected headers.
    if (authEnabled && (name === 'authorization' || name === 'cookie')
        && (!entry.fromStep || !step.auth_phase)) continue;
    const value = String(renderString(entry.value));
    if (/[\x00-\x08\x0a-\x1f\x7f]/.test(value)) throw new Error('InvalidHeader');
    headers[name] = value.trim();
  }
  if (authEnabled && !step.auth_phase && auth.transport === 'BEARER') {
    const value = context[auth.access_token_variable];
    if (typeof value !== 'string' || !value.trim() || /[\x00-\x08\x0a-\x1f\x7f]/.test(value)) throw new Error('InvalidHeader');
    headers.authorization = 'Bearer ' + value.trim();
  }
  let body = null;
  const bodyType = (step.body_type || 'NONE').toUpperCase();
  if (bodyType === 'JSON') {
    body = JSON.stringify(renderTree(JSON.parse(step.body)));
    if (!headers['content-type']) headers['content-type'] = 'application/json';
  } else if (bodyType === 'FORM') {
    const fields = renderTree(JSON.parse(step.body || '{}'));
    body = Object.create(null);
    for (const key of Object.keys(fields)) {
      if (!['string', 'number', 'boolean'].includes(typeof fields[key])
          || (typeof fields[key] === 'number' && !Number.isFinite(fields[key]))) throw new Error('InvalidFormField');
      body[key] = String(fields[key]);
    }
    for (const file of step.files || []) {
      body[file.field] = http.file(uploadBytes(file), file.filename, file.content_type);
    }
    // k6 generates the matching multipart boundary; never reuse a saved boundary.
    if ((step.files || []).length) delete headers['content-type'];
    else if (!headers['content-type']) headers['content-type'] = 'application/x-www-form-urlencoded';
  } else if (bodyType === 'BINARY') {
    const file = step.files[0];
    body = uploadBytes(file);
    if (!headers['content-type']) headers['content-type'] = file.content_type;
  }
  const reminderRequest = reminderGuard && reminderGuard.request(step, url);
  if (reminderRequest) {
    body = reminderRequest.body;
    if (reminderRequest.key) {
      headers['idempotency-key'] = reminderRequest.key;
      headers['content-type'] = 'application/json';
    }
  }
  requireLoadTime();
  const started = Date.now();
  let response = null;
  let errorCode = 0;
  let error = '';
  let ok = false;
  const assertionDetails = [], extractorDetails = [], authOutputDetails = [];
  function authOutputFailure(role, variable, result) {
    ok = false;
    error = 'ExtractionFailed';
    if (!debugEnabled) return;
    const index = variable ? (step.extractors || []).findIndex(rule => rule.name === variable) : -1;
    const detail = extractorDetails.find(rule => rule.index === index);
    if (detail) detail.result = result;
    authOutputDetails.push({ role, index: index < 0 ? null : index, result });
  }
  let parseFailed = false;
  try {
    event({ kind: 'request_started', step: index, timestamp_ms: Date.now() });
    response = http.request((step.method || 'GET').toUpperCase(), url, body, {
      headers, timeout: `${requestTimeout}s`, redirects: 0,
      tags: { name: `step:${index}` }, ...(authEnabled ? { jar: cookieJar } : {}),
    });
    errorCode = Number(response.error_code || 0);
    ok = response.status >= 200 && response.status < 300;
    if (!ok) error = response.status ? 'HTTPFailed' : 'TransportError';
    let json;
    let parsed = false;
    function getJson() {
      if (!parsed) { try { json = response.json(); parsed = true; } catch (_) { parseFailed = true; throw new Error('InvalidJSON'); } }
      return json;
    }
    if (ok && reminderGuard && reminderGuard.handles(step)
        && (response.status !== 200 || !reminderGuard.response(step, getJson()))) {
      ok = false; error = 'AssertionFailed';
    }
    for (const [ruleIndex, rule] of (step.assertions || []).entries()) {
      let pass = false;
      let invalidJson = false;
      try {
        const kind = rule.type.toUpperCase().replace('JSONPATH', 'JSON_PATH');
        if (kind === 'CONTAINS') {
          pass = typeof rule.expected === 'string' && rule.expected.trim().length > 0
            && typeof response.body === 'string' && response.body.includes(rule.expected);
        } else {
          const actual = kind === 'STATUS_CODE' ? response.status : jsonPath(getJson(), rule.expr || rule.json_path);
          pass = actual !== undefined && String(actual) === String(rule.expected);
        }
      } catch (_) { pass = false; invalidJson = parseFailed; }
      if (debugEnabled && ruleIndex < 32) assertionDetails.push({ index: ruleIndex, result: pass ? 'passed' : invalidJson ? 'invalid_json' : 'mismatch' });
      if (!pass) { ok = false; if (!error) error = 'AssertionFailed'; }
    }
    if (ok) {
      const extracted = {};
      for (const [ruleIndex, rule] of (step.extractors || []).entries()) {
        let value;
        try { value = jsonPath(getJson(), rule.expr || rule.json_path); } catch (_) { value = undefined; }
        if (debugEnabled && ruleIndex < 32) extractorDetails.push({ index: ruleIndex, result: value === undefined || value === null || value === '' ? (parseFailed ? 'invalid_json' : 'missing') : 'passed' });
        if (value === undefined || value === null || value === '') {
          ok = false; error = 'ExtractionFailed'; break;
        }
        extracted[rule.name] = value;
      }
      if (ok && step.auth_phase) {
        if (auth.transport === 'BEARER') {
          const token = extracted[auth.access_token_variable];
          if (typeof token !== 'string') authOutputFailure('access_token', auth.access_token_variable, 'invalid_type');
          else if (!token.trim()) authOutputFailure('access_token', auth.access_token_variable, 'empty');
        }
        if (auth.transport === 'COOKIE' && !(cookieJar.cookiesForURL(context.base_url)[auth.cookie_name] || []).length) {
          authOutputFailure('cookie', null, 'cookie_missing');
        }
        if (auth.expires_in_variable && (!Number.isFinite(Number(extracted[auth.expires_in_variable]))
            || Number(extracted[auth.expires_in_variable]) <= Number(auth.expiry_skew_seconds || 0))) {
          authOutputFailure('expires_in', auth.expires_in_variable, 'invalid_expiry');
        }
      }
      if (ok) for (const key of Object.keys(extracted)) context[key] = extracted[key];
      else for (const rule of step.extractors || []) delete context[rule.name];
    }
  } catch (caught) {
    ok = false; error = 'TransportError';
    errorCode = Number(caught && (caught.error_code || caught.code) || 0);
  }
  // A failed producer cannot leave the previous iteration's resource/token live.
  if (!ok) for (const rule of step.extractors || []) delete context[rule.name];
  event({ kind: 'request', step: index, elapsed_ms: Date.now() - started,
    timestamp_ms: Date.now(), status: response ? response.status : 0,
    error_code: Number.isFinite(errorCode) ? errorCode : 0, ok, error });
  if (debugEnabled) {
    for (let i = extractorDetails.length; i < Math.min((step.extractors || []).length, 32); i++) extractorDetails.push({ index: i, result: 'skipped' });
    const outcomes = { HTTPFailed: 'http_failed', TransportError: 'transport_failed', AssertionFailed: 'assertion_failed', ExtractionFailed: 'extraction_failed' };
    diagnostic(step, index, { outcome: ok ? 'passed' : outcomes[error] || 'transport_failed',
      status: response ? response.status : 0, assertions: assertionDetails, extractors: extractorDetails,
      auth_outputs: authOutputDetails,
      rules_truncated: (step.assertions || []).length > 32 || (step.extractors || []).length > 32,
      response: responseDiagnostic(response, step) });
  }
  return { ok, status: response ? response.status : 0 };
}

function closeIdentity() {
  authenticated = false;
  failedPermanently = true;
  for (const step of steps) for (const rule of step.extractors || []) delete context[rule.name];
  for (const step of steps.filter(value => value.protocol === 'WEBSOCKET')) {
    for (const frame of [step.websocket_config.auth, ...step.websocket_config.commands]) {
      for (const rule of frame.extractors || []) delete context[rule.name];
    }
  }
  for (const step of steps.filter(value => value.protocol === 'SSE')) {
    for (const rule of step.sse_config.rules) for (const item of rule.extractors || []) delete context[item.name];
  }
  for (const key of [auth.access_token_variable, auth.refresh_token_variable, auth.cookie_variable]) {
    if (key) delete context[key];
  }
  cookieJar = new http.CookieJar();
  event({ kind: 'auth_failed' });
  event({ kind: 'vu_done' });
}

function authenticate(phase) {
  requireLoadTime();
  const index = steps.findIndex(step => step.auth_phase === phase);
  if (index < 0) { closeIdentity(); return false; }
  const inputs = steps[index].auth_input_variables || [];
  if (!inputs.length || inputs.some(name => context[name] === undefined || context[name] === null
      || !String(context[name]).trim())) {
    diagnostic(steps[index], index, { outcome: 'not_sent', status: 0 }); closeIdentity(); return false;
  }
  const refreshCredential = auth.refresh_token_variable ? context[auth.refresh_token_variable] : undefined;
  const previousOutputs = {};
  for (const rule of steps[index].extractors || []) {
    if (Object.prototype.hasOwnProperty.call(context, rule.name)) previousOutputs[rule.name] = context[rule.name];
  }
  if (phase === 'refresh' && (typeof refreshCredential !== 'string' || !refreshCredential.trim())) {
    closeIdentity(); return false;
  }
  authenticated = false;
  const scope = {};
  for (let attempt = 0; attempt < Number(auth.max_attempts || 1); attempt++) {
    if (phase === 'login') cookieJar = new http.CookieJar();
    if (phase === 'refresh') {
      for (const name of Object.keys(previousOutputs)) context[name] = previousOutputs[name];
      context[auth.refresh_token_variable] = refreshCredential;
    }
    const result = request(steps[index], index, scope);
    if (result.ok) {
      expiresAt = auth.expires_in_variable ? Date.now() + Number(context[auth.expires_in_variable]) * 1000 : 0;
      authenticated = true;
      return true;
    }
    if (attempt + 1 < Number(auth.max_attempts || 1) && auth.retry_delay_ms) loadSleep(auth.retry_delay_ms / 1000);
  }
  closeIdentity();
  return false;
}

function protectedRequest(step, index) {
  requireLoadTime();
  const scope = {};
  if (!authEnabled) return request(step, index, scope).ok;
  if (!authenticated || failedPermanently) return false;
  if (expiresAt && Date.now() >= expiresAt - Number(auth.expiry_skew_seconds || 0) * 1000) {
    if (!authenticate('refresh')) return false;
  }
  let result = request(step, index, scope);
  if ((auth.refresh_on_status || [401]).includes(result.status)) {
    if (!authenticate('refresh')) return false;
    // Count both HTTP attempts, but preserve the logical request's idempotency key.
    result = request(step, index, scope);
    if ((auth.refresh_on_status || [401]).includes(result.status)) closeIdentity();
  }
  return result.ok;
}

export default function () {
  if (hasWebSocket || hasSSE) return mixedIteration();
  if (durationElapsed) return;
  if (failedPermanently) { if (!rounds) sleep(0.1); return; }
  try {
    requireLoadTime();
    if (!initialized) initialize();
    if (!setupPassed) { if (!rounds) sleep(0.1); return; }
    context.iteration = exec.vu.iterationInScenario;
    const iteration = businessIteration();
    let next = iteration.next();
    while (!next.done) {
      const index = next.value;
      next = iteration.next(protectedRequest(steps[index], index));
    }
  } catch (caught) {
    if (caught === loadExpired) {
      durationElapsed = true;
      event({ kind: 'load_expired', setup_complete: setupPassed });
      event({ kind: 'vu_done' });
      return;
    }
    failedPermanently = true;
    event({ kind: 'runtime_error' });
    if (authEnabled) closeIdentity();
    event({ kind: 'vu_done' });
  }
}

async function websocketRequest(step, index) {
  requireLoadTime();
  if (!authenticated || failedPermanently) return false;
  if (expiresAt && Date.now() >= expiresAt - Number(auth.expiry_skew_seconds || 0) * 1000) {
    if (!authenticate('refresh')) return false;
  }
  const previous = requestScope;
  requestScope = {};
  for (const frame of [step.websocket_config.auth, ...step.websocket_config.commands]) {
    for (const rule of frame.extractors || []) delete context[rule.name];
  }
  try {
    let url = renderString(step.url).replace(/^\/(?=(?:https?|wss?):\/\/)/, '');
    if (!/^(?:https?|wss?):\/\//.test(url)) url = context.base_url.replace(/\/$/, '') + '/' + url.replace(/^\//, '');
    url = url.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
    const httpURL = url.replace(/^ws:/, 'http:').replace(/^wss:/, 'https:');
    if (!/^wss?:\/\//.test(url) || !authOrigin(httpURL)
        || authOrigin(httpURL) !== authOrigin((config.env_config || {}).base_url)
        || /[\\#\s]/.test(url)) throw new Error('InvalidAuthOrigin');
    const secrets = Object.keys(context).filter(key => /token|password|secret|credential|cookie/i.test(key)
      || [auth.access_token_variable, auth.refresh_token_variable].includes(key))
      .map(key => context[key]).filter(value => typeof value === 'string' && value.length > 0);
    const containsSecret = value => secrets.some(secret => value.includes(secret) || value.includes(encodeURIComponent(secret)));
    if (containsSecret(url)) throw new Error('InvalidAuthOrigin');
    const headers = Object.create(null);
    const headerConfig = Object.create(null);
    for (const source of [(config.env_config || {}).headers || {}, step.headers || {}]) {
      for (const key of Object.keys(source)) {
        const name = String(renderString(key)).toLowerCase();
        if (!/^[!#$%&'*+.^_`|~0-9a-z-]+$/.test(name)) throw new Error('InvalidHeader');
        if (['authorization', 'cookie'].includes(name)) continue;
        headerConfig[name] = source[key];
      }
    }
    for (const name of Object.keys(headerConfig)) {
      const value = String(renderString(headerConfig[name])).trim();
      if (/[\x00-\x08\x0a-\x1f\x7f]/.test(value) || containsSecret(name + ':' + value)) throw new Error('InvalidHeader');
      headers[name] = value;
    }
    if (step.websocket_config.mode === 'PUSH') {
      const token = context[auth.access_token_variable];
      if (typeof token !== 'string' || !token || /[\x00-\x20\x7f]/.test(token)) throw new Error('InvalidCredential');
      headers.authorization = 'Bearer ' + token;
    }
    requireLoadTime();
    return await runWebSocketSession(step.websocket_config, {
      WebSocket, context, url, headers, jar: new http.CookieJar(), label: `step:${index}`, debug: debugEnabled,
      fatal: () => { failedPermanently = true; },
      remaining: remainingLoadMs, loadExpired, jsonPath,
      ...(rounds ? {} : { drainRemaining: () => remainingLoadMs() + drainSeconds * 1000 }),
      emit: value => {
        if (value.kind === 'ws_diagnostic') {
          if (debugCount++ >= DEBUG_LIMIT) { if (debugCount === DEBUG_LIMIT + 1) event({ kind: 'debug_truncated' }); return; }
        }
        event({ ...value, step: index });
      },
      nextId: () => { const saved = requestScope; requestScope = {}; try { return variableValue('request_id'); } finally { requestScope = saved; } },
      render: (value, scope) => {
        const saved = requestScope; requestScope = scope;
        try {
          const rendered = renderTree(value);
          if (rendered && typeof rendered === 'object' && (step.websocket_config.mode === 'PUSH' || (rendered.action && rendered.action !== 'auth'))
              && containsSecret(JSON.stringify(rendered))) throw new Error('InvalidCredentialPlacement');
          return rendered;
        } finally { requestScope = saved; }
      },
    });
  } finally { requestScope = previous; }
}

function sseRequest(step, index) {
  requireLoadTime();
  if (!authenticated || failedPermanently) return false;
  if (expiresAt && Date.now() >= expiresAt - Number(auth.expiry_skew_seconds || 0) * 1000 && !authenticate('refresh')) return false;
  const previous = requestScope; requestScope = {};
  for (const rule of step.sse_config.rules) for (const item of rule.extractors || []) delete context[item.name];
  let result;
  try {
    if (sseRuntime.version !== 'testhub-sse/1') throw new Error('RuntimeUnavailable');
    let url = renderString(step.url).replace(/^\/(?=https?:\/\/)/, '');
    if (!/^https?:\/\//.test(url)) url = context.base_url.replace(/\/$/, '') + '/' + url.replace(/^\//, '');
    const query = Object.keys(step.params || {}).map(key => encodeURIComponent(renderString(key)) + '=' + encodeURIComponent(renderString(String(step.params[key])))).join('&');
    if (query) url += (url.includes('?') ? '&' : '?') + query;
    if (!authOrigin(url) || authOrigin(url) !== authOrigin((config.env_config || {}).base_url) || /[\\#\s]/.test(url)) throw new Error('InvalidAuthOrigin');
    const secrets = Object.keys(context).filter(key => /token|password|secret|credential|cookie/i.test(key)
      || [auth.access_token_variable,auth.refresh_token_variable].includes(key)).map(key => context[key])
      .filter(value => typeof value === 'string' && value.length > 0);
    const containsSecret = value => secrets.some(secret => value.includes(secret) || value.includes(encodeURIComponent(secret)));
    const headers = Object.create(null);
    for (const source of [(config.env_config || {}).headers || {}, step.headers || {}]) {
      for (const key of Object.keys(source)) {
        const name = String(renderString(key)).toLowerCase();
        if (['authorization','cookie'].includes(name)) continue;
        if (!/^[!#$%&'*+.^_`|~0-9a-z-]+$/.test(name)) throw new Error('InvalidHeader');
        const value = String(renderString(source[key])).trim();
        if (/[\x00-\x08\x0a-\x1f\x7f]/.test(value) || containsSecret(name + ':' + value)) throw new Error('InvalidHeader');
        headers[name] = value;
      }
    }
    const body = step.body_type === 'JSON' ? JSON.stringify(renderTree(JSON.parse(step.body))) : '';
    if (step.body_type === 'JSON' && !headers['content-type']) headers['content-type'] = 'application/json';
    if (containsSecret(url) || containsSecret(body)) throw new Error('InvalidCredentialPlacement');
    const token = context[auth.access_token_variable];
    if (typeof token !== 'string' || !token || /[\x00-\x20\x7f]/.test(token)) throw new Error('InvalidCredential');
    headers.authorization = 'Bearer ' + token;
    requireLoadTime();
    event({kind:'request_started',step:index,timestamp_ms:Date.now()});
    result = runSSESession(step.sse_config,{open:sseRuntime.open,url,method:step.method || 'GET',body,headers,context,
      remaining:remainingLoadMs,emit:value=>event({...value,step:index})});
  } catch (caught) {
    if (caught === loadExpired) throw caught;
    result = {ok:false,started:false,closed:true,reason:'preparation_failed',phase:'preparation',status:0,events:0,bytes:0,elapsed_ms:0};
  } finally {requestScope = previous;}
  event({kind:'sse_result',step:index,...result});
  // A stream is one HTTP request regardless of the number of delivered events.
  if (result.started) event({kind:'request',step:index,elapsed_ms:result.elapsed_ms,timestamp_ms:Date.now(),
    status:result.status,ok:result.ok,error:result.ok?'':'SSEFailed',error_code:0});
  return result.ok;
}

async function mixedIteration() {
  if (durationElapsed) return;
  if (failedPermanently) { if (!rounds) sleep(0.1); return; }
  try {
    requireLoadTime();
    if (!initialized) {
      initialize(true);
      if (failedPermanently) return;
      for (let i = 0; i < steps.length; i++) {
        if (steps[i].is_setup && !steps[i].auth_phase) {
          const ok = steps[i].protocol === 'WEBSOCKET'
            ? await websocketRequest(steps[i], i) : steps[i].protocol === 'SSE' ? sseRequest(steps[i], i) : protectedRequest(steps[i], i);
          if (!ok) { event({ kind: 'setup_failed' }); return; }
        }
      }
      requireLoadTime(); setupPassed = true;
    }
    if (!setupPassed) { if (!rounds) sleep(0.1); return; }
    context.iteration = exec.vu.iterationInScenario;
    const iteration = businessIteration();
    let next = iteration.next();
    while (!next.done) {
      const index = next.value;
      const ok = steps[index].protocol === 'WEBSOCKET'
        ? await websocketRequest(steps[index], index) : steps[index].protocol === 'SSE' ? sseRequest(steps[index], index) : protectedRequest(steps[index], index);
      next = iteration.next(ok);
    }
  } catch (caught) {
    if (caught === loadExpired) {
      durationElapsed = true;
      event({ kind: 'load_expired', setup_complete: setupPassed });
      event({ kind: 'vu_done' }); return;
    }
    failedPermanently = true;
    event({ kind: 'runtime_error' });
    if (authEnabled) closeIdentity();
    event({ kind: 'vu_done' });
  }
}

export function handleSummary() { return {}; }
