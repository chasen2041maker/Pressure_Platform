// The event contract is transport-independent; response data never enters emitted events.
function path(value, expression) {
  for (const key of expression.slice(1).match(/[A-Za-z_][A-Za-z0-9_]*|\d+/g) || []) {
    if (value === null || value === undefined || !Object.prototype.hasOwnProperty.call(Object(value), key)) return undefined;
    value = value[key];
  }
  return value;
}

function validHttpURL(value) {
  if (typeof value !== 'string' || /[\s\x00-\x1f\x7f\\#]/.test(value)) return false;
  const match = value.match(/^https?:\/\/(\[[^\]]+\]|[^:/@?]+)(?::(\d+))?\/[^]*$/);
  if (!match || match[2] && (+match[2] < 1 || +match[2] > 65535)) return false;
  const host = match[1];
  const ipv4 = text => text.split('.').length === 4 && text.split('.').every(part => /^\d{1,3}$/.test(part) && +part <= 255);
  if (host.startsWith('[')) {
    const address = host.slice(1,-1), halves = address.split('::');
    if (halves.length > 2) return false;
    const groups = halves.flatMap(part => part ? part.split(':') : []);
    let size = 0;
    for (let index=0; index<groups.length; index++) {
      const group = groups[index];
      if (/^[a-fA-F0-9]{1,4}$/.test(group)) size++;
      else if (index === groups.length-1 && address.endsWith(group) && ipv4(group)) size += 2;
      else return false;
    }
    return halves.length === 2 ? size < 8 : size === 8;
  }
  if (/^[0-9.]+$/.test(host)) return ipv4(host);
  return host.length <= 253 && host.replace(/\.$/,'').split('.').every(label =>
    label.length <= 63 && /^[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?$/.test(label));
}

export function createEventContract(config, context) {
  const counts = Object.create(null), extracted = Object.create(null);
  let failure = '', completed = false, eventIndex = 0, diagnostic = null;
  for (const rule of config.rules) counts[rule.name] = 0;
  const fail = (reason, scope='event', ruleIndex, conditionIndex) => {
    if (!failure) {
      failure = reason;
      diagnostic = {event_index:eventIndex,scope,
        ...(ruleIndex === undefined ? {} : {rule_index:ruleIndex + 1}),
        ...(conditionIndex === undefined ? {} : {condition_index:conditionIndex + 1})};
    }
    return 'error';
  };
  function expected(value) {
    if (typeof value !== 'string') return value;
    const scope = Object.assign(Object.create(null), context, extracted);
    const whole = value.match(/^(?:\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+)\})$/);
    if (whole) return scope[(whole[1] || whole[2]).trim()];
    return value.replace(/\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+)\}/g, (_, a, b) => {
      const item = scope[(a || b).trim()];
      if (item === undefined) throw new Error('unmapped');
      return String(item);
    });
  }
  function check(rule, data) {
    const actual = path(data, rule.expr);
    if (actual === undefined) return false;
    const wanted = rule.expected_count ? counts[rule.expected_count] : expected(rule.expected);
    switch (rule.operator || 'eq') {
      case 'eq': return wanted !== undefined && actual === wanted;
      case 'ne': return wanted !== undefined && actual !== wanted;
      case 'exists': return actual !== null;
      case 'nonempty': return typeof actual === 'string' && actual.trim().length > 0;
      case 'blank': return typeof actual === 'string' && actual.trim() === '';
      case 'positive_int': return Number.isSafeInteger(actual) && actual > 0;
      case 'nonnegative_int': return Number.isSafeInteger(actual) && actual >= 0;
      case 'base64': return typeof actual === 'string' && actual.length > 0 && actual.length % 4 === 0
        && /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(actual);
      case 'url': return validHttpURL(actual);
      default: return false;
    }
  }
  function accept(event) {
    eventIndex++;
    if (failure || completed) return fail('event_order');
    if (event.name === 'error') return fail('event_error');
    const literal = config.rules.filter(rule => rule.event === event.name && Object.hasOwn(rule, 'data') && rule.data === event.data);
    let data;
    if (!literal.length) {
      try { data = JSON.parse(event.data); } catch (_) { return fail('invalid_json'); }
      if (data === null || typeof data !== 'object' || Array.isArray(data)) return fail('invalid_json');
      const errorIndex = (config.error_conditions || []).findIndex(rule => check(rule, data));
      if (errorIndex >= 0) return fail('business_error', 'error_condition', undefined, errorIndex);
      const assertionIndex = (config.assertions || []).findIndex(rule => !check(rule, data));
      if (assertionIndex >= 0) return fail('assertion_failed', 'global_assertion', undefined, assertionIndex);
    }
    const matches = literal.length ? literal : config.rules.filter(rule => rule.event === event.name
      && !Object.hasOwn(rule, 'data') && (rule.match || []).every(item => check(item, data)));
    if (matches.length !== 1) return fail(matches.length ? 'event_ambiguous' : 'event_unmatched');
    const rule = matches[0];
    const ruleIndex = config.rules.indexOf(rule);
    if ((rule.after || []).some(name => !counts[name])) return fail('event_order', 'rule', ruleIndex);
    if ((rule.without || []).some(name => counts[name])) return fail('event_order', 'rule', ruleIndex);
    if (counts[rule.name] >= rule.max_events) return fail('event_count', 'rule', ruleIndex);
    const assertionIndex = (rule.assertions || []).findIndex(item => !check(item, data));
    if (assertionIndex >= 0) return fail('assertion_failed', 'rule_assertion', ruleIndex, assertionIndex);
    if (rule.sequence && path(data, rule.sequence.expr) !== rule.sequence.start + counts[rule.name]) return fail('sequence', 'sequence', ruleIndex);
    const pending = Object.create(null);
    for (const [extractorIndex, item] of (rule.extractors || []).entries()) {
      const value = path(data, item.expr);
      if (value === null || value === undefined || !['string','number','boolean'].includes(typeof value)
          || typeof value === 'string' && (!value.trim() || value.length > 4096)
          || typeof value === 'number' && !Number.isFinite(value)) return fail('extraction_failed', 'extractor', ruleIndex, extractorIndex);
      pending[item.name] = value;
    }
    Object.assign(extracted, pending);
    counts[rule.name]++;
    if (rule.terminal) {
      const missingIndex = config.rules.findIndex(item => counts[item.name] < item.min_events);
      if (missingIndex >= 0) return fail('event_count', 'terminal', missingIndex);
      completed = true;
      return 'complete';
    }
    return 'continue';
  }
  return {accept, reason: () => failure, diagnostic: () => diagnostic, completed: () => completed, outputs: () => completed && !failure ? extracted : {}};
}

export const SSE_REASONS = new Set(['completed','invalid_options','cancelled','total_timeout','idle_timeout',
  'transport_error','http_status','redirect','content_type','unexpected_eof','read_error','event_bytes_limit',
  'total_bytes_limit','events_limit','invalid_utf8','event_error','business_error','callback_error','protocol_error',
  'assertion_failed','extraction_failed','invalid_json','event_unmatched','event_ambiguous','event_order',
  'event_count','sequence','terminal_missing','preparation_failed','runtime_unavailable']);

export function runSSESession(config, deps) {
  const names = config.rules.flatMap(rule => (rule.extractors || []).map(item => item.name));
  names.forEach(name => { delete deps.context[name]; });
  const contract = createEventContract(config, deps.context);
  const started = Date.now();
  let first = false, result;
  try {
    result = deps.open(deps.url, {method:deps.method,headers:deps.headers,body:deps.body || '',
      total_ms:Math.max(1,Math.min(config.total_ms,Math.floor(deps.remaining()))),
      idle_ms:Math.max(1,Math.min(config.idle_ms,config.total_ms,Math.floor(deps.remaining()))),
      max_event_bytes:config.max_event_bytes,max_total_bytes:config.max_total_bytes,max_events:config.max_events}, event => {
      if (!first) {first=true;deps.emit({kind:'sse_first_event',elapsed_ms:Date.now()-started});}
      return contract.accept(event);
    });
  } catch (_) {
    result = {ok:false,started:true,closed:false,reason:'callback_error',status:0,events:0,bytes:0,elapsed_ms:Date.now()-started};
  }
  // Named error events are rejected by the native reader before invoking the callback.
  if (!first && result.started === true && result.events > 0
      && Number.isFinite(result.first_event_ms) && result.first_event_ms >= 0) {
    deps.emit({kind:'sse_first_event',elapsed_ms:result.first_event_ms});
  }
  let reason = ['cancelled','total_timeout','idle_timeout'].includes(result.reason) ? result.reason
    : contract.reason() || (SSE_REASONS.has(result.reason) ? result.reason : 'protocol_error');
  const ok = result.ok === true && result.closed === true && contract.completed() && !contract.reason();
  if (result.ok && !ok) reason = 'terminal_missing';
  if (ok) Object.assign(deps.context, contract.outputs());
  const phase = ['assertion_failed','extraction_failed','invalid_json','event_unmatched','event_ambiguous',
    'event_order','event_count','sequence','terminal_missing','business_error','event_error'].includes(reason) ? 'business' : 'transport';
  const diagnostic = ok ? null : contract.reason() === reason ? contract.diagnostic() : {
    scope:'transport', ...(Number.isInteger(result.events) && result.events > 0 && result.events <= config.max_events
      ? {event_index:result.events} : {})};
  return {ok, reason, phase, started:result.started===true, closed:result.closed===true, status:result.status || 0,
    events:result.events || 0,bytes:result.bytes || 0,elapsed_ms:result.elapsed_ms || Date.now()-started,diagnostic};
}
