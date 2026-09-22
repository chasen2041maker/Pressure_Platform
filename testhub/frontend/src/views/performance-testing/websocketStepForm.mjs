import { sseTemplate, parseSseDraft, canSelectProtocol } from './sseStepForm.mjs'
const copy = value => JSON.parse(JSON.stringify(value))
const object = value => value != null && typeof value === 'object' && !Array.isArray(value)
const only = (value, keys) => object(value) && Object.keys(value).every(key => keys.includes(key))
const pathPattern = /^\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$/
const namePattern = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/
const reserved = new Set(['base_url', 'baseUrl', 'vu_id', 'iteration', 'request_id', '__proto__', 'prototype', 'constructor'])
export function sourceDeclaresWebsocket(operation) {
  const raw = operation?.raw
  return operation?.method === 'GET' && raw?.['x-websocket'] === true && object(raw.responses?.['101'])
}

export const websocketTimers = {
  connect_timeout_ms: [100, 30000], command_timeout_ms: [100, 60000],
  hold_open_ms: [0, 300000], max_session_ms: [1000, 600000], heartbeat_interval_ms: [0, 60000]
}

export function websocketTemplate(accessVariable = 'access_token') {
  const rules = () => [{ type: 'JSON_PATH', expr: '$.ok', expected: true }]
  return {
    version: 1, connect_timeout_ms: 5000, command_timeout_ms: 5000, hold_open_ms: 0,
    max_session_ms: 15000, heartbeat_interval_ms: 25000,
    auth: { request: { version: 1, action: 'auth', payload: { token: `{{${accessVariable || 'access_token'}}}` } }, assertions: rules(), extractors: [] },
    commands: [
      { name: '读取本人客服历史', request: { version: 1, action: 'timeline.list', payload: { channel: 'demo', limit: 1 } }, assertions: rules(), extractors: [] },
      { name: '读取本人客服未读', request: { version: 1, action: 'timeline.unread', payload: { channel: 'demo' } }, assertions: rules(), extractors: [] }
    ]
  }
}

export function websocketPushTemplate(kind = 'home', accessVariable = 'access_token') {
  const rule = (expr, expected) => ({ type: 'JSON_PATH', expr, expected })
  const command = kind === 'quotes'
    ? { name: '行情订阅快照', request: { type: 'quote.subscribe', codes: ['sz000001'] }, event_type: 'quote.snapshot',
      error_types: ['quote.error'], assertions: [rule('$.quotes[0].code', 'sz000001')],
      extractors: [{ type: 'JSON_PATH', expr: '$.cycle_id', name: 'quote_cycle_id' }] }
    : kind === 'reminder'
      ? { name: '等待本人提醒触发', event_type: 'portfolio.reminder.triggered', event_path: '$.event_type',
        assertions: [rule('$.data.code', '{{reminder_code}}')],
        extractors: [{ type: 'JSON_PATH', expr: '$.event_id', name: 'reminder_event_id' }] }
      : { name: '首页直播教师快照', event_type: 'home.live_teachers.snapshot', assertions: [rule('$.version', 1)],
        extractors: [{ type: 'JSON_PATH', expr: '$.teachers', name: 'home_live_teachers' }] }
  return { version: 1, mode: 'PUSH', connect_timeout_ms: 5000, command_timeout_ms: 5000,
    hold_open_ms: 0, max_session_ms: 15000, heartbeat_interval_ms: 0,
    auth: { type: 'BEARER', token: `{{${accessVariable || 'access_token'}}}` },
    commands: [{ event_path: '$.type', error_types: [], ...command }] }
}

export function parseWebsocketDraft(draft, accessVariable) {
  const errors = []
  const fail = (path, code = 'shape') => errors.push({ path, code })
  let config
  try { config = typeof draft === 'string' ? JSON.parse(draft) : copy(draft) } catch { return { config: null, errors: [{ path: 'config', code: 'json' }] } }
  if (new TextEncoder().encode(JSON.stringify(config)).length > 128 * 1024) fail('config', 'size')
  if (!only(config, ['version', 'mode', 'auth', 'commands', ...Object.keys(websocketTimers)]) || config.version !== 1 || ![undefined, 'REQUEST_REPLY', 'PUSH'].includes(config.mode)) return { config: null, errors: [{ path: 'config', code: 'shape' }] }
  const push = config.mode === 'PUSH'
  function safe(value) {
    if (Array.isArray(value)) return value.every(safe)
    if (object(value)) return Object.entries(value).every(([key, item]) => !['__proto__', 'prototype', 'constructor'].includes(key) && !key.includes('{{') && !key.includes('${') && safe(item))
    return typeof value !== 'number' || Number.isFinite(value)
  }
  try { if (!safe(config)) fail('config') } catch { fail('config') }
  const defaults = push ? websocketPushTemplate('home', accessVariable) : websocketTemplate(accessVariable)
  for (const [key, [min, max]] of Object.entries(websocketTimers)) {
    const value = Object.hasOwn(config, key) ? config[key] : defaults[key]
    config[key] = value
    if (!Number.isInteger(value) || value < min || value > max || (key === 'heartbeat_interval_ms' && value > 0 && value < 1000)) fail(key, 'timer')
  }
  if (config.hold_open_ms >= config.max_session_ms || Math.max(config.connect_timeout_ms, config.command_timeout_ms) > config.max_session_ms) fail('max_session_ms', 'lifetime')
  function frame(value, field, auth = false) {
    if (!only(value, auth ? ['request', 'assertions', 'extractors'] : ['name', 'request', 'assertions', 'extractors', ...(push ? ['event_type', 'event_path', 'error_types'] : [])])) { fail(field); return }
    if (!auth && (typeof value.name !== 'string' || !value.name.trim() || value.name.length > 200)) fail(`${field}.name`)
    const request = value.request
    if (push) {
      if (!Object.hasOwn(value, 'event_path')) value.event_path = '$.type'
      if (!Object.hasOwn(value, 'error_types')) value.error_types = []
      if (typeof value.event_type !== 'string' || !/^[a-z][a-z0-9_.]{0,95}$/.test(value.event_type) || typeof value.event_path !== 'string' || !pathPattern.test(value.event_path)) fail(`${field}.event_type`)
      if (!Array.isArray(value.error_types) || value.error_types.length > 16 || value.error_types.some(type => typeof type !== 'string' || !/^[a-z][a-z0-9_.]{0,95}$/.test(type) || type === value.event_type)) fail(`${field}.error_types`)
      if (Object.hasOwn(value, 'request') && (!object(request) || !Object.keys(request).length)) fail(`${field}.request`)
      if (!Array.isArray(value.assertions) || !value.assertions.length) fail(`${field}.assertions`, 'rule')
    } else if (!only(request, ['version', 'action', 'payload']) || request.version !== 1 || typeof request.action !== 'string' || !/^[a-z][a-z0-9_.]{0,63}$/.test(request.action) || !object(request.payload)) fail(`${field}.request`)
    else if (auth) {
      if (request.action !== 'auth' || !only(request.payload, ['token']) || request.payload.token !== `{{${accessVariable || 'access_token'}}}`) fail(`${field}.request.payload.token`, 'tokenReference')
    } else if (['auth', 'ping'].includes(request.action)) fail(`${field}.request.action`)
    for (const kind of ['assertions', 'extractors']) {
      const rules = Object.hasOwn(value, kind) ? value[kind] : []
      value[kind] = rules
      if (!Array.isArray(rules) || rules.length > 64) { fail(`${field}.${kind}`); continue }
      const names = new Set()
      rules.forEach((rule, index) => {
        const rulePath = `${field}.${kind}[${index}]`
        if (!only(rule, kind === 'assertions' ? ['type', 'expr', 'expected', 'operator'] : ['type', 'expr', 'name']) || rule.type !== 'JSON_PATH' || typeof rule.expr !== 'string' || !pathPattern.test(rule.expr)) { fail(rulePath, 'rule'); return }
        if (kind === 'assertions') {
          if (!Object.hasOwn(rule, 'expected') || object(rule.expected) || Array.isArray(rule.expected) || ![undefined, null, '', 'eq', 'equals', '=='].includes(rule.operator)) fail(rulePath, 'rule')
        } else {
          if (typeof rule.name !== 'string' || !namePattern.test(rule.name) || reserved.has(rule.name) || names.has(rule.name)) fail(rulePath, 'rule')
          names.add(rule.name)
        }
      })
    }
  }
  if (push) {
    if (!only(config.auth, ['type', 'token']) || config.auth.type !== 'BEARER' || config.auth.token !== `{{${accessVariable || 'access_token'}}}`) fail('auth', 'tokenReference')
    if (config.heartbeat_interval_ms !== 0) fail('heartbeat_interval_ms', 'timer')
  } else frame(config.auth, 'auth', true)
  if (!Array.isArray(config.commands) || config.commands.length < 1 || config.commands.length > 64) fail('commands', 'commandCount')
  else config.commands.forEach((value, index) => frame(value, `commands[${index}]`))
  return { config: errors.length ? null : config, errors }
}

export function canChangeProtocol(step, isK6) {
  return canSelectProtocol(step, isK6, 'SSE') || canSelectProtocol(step, isK6, 'WEBSOCKET')
}

const httpFields = ['method', 'headers', 'params', 'body_type', 'body', 'files', 'assertions', 'extractors']
export function convertStepProtocol(step, protocol, accessVariable) {
  const result = copy(step)
  if (protocol === (step.protocol || 'HTTP')) return result
  if (!['HTTP', 'WEBSOCKET', 'SSE'].includes(protocol)) return result
  if ((step.protocol || 'HTTP') === 'HTTP') result._protocolBackup = Object.fromEntries(httpFields.map(key => [key, copy(step[key] ?? (['files', 'assertions', 'extractors'].includes(key) ? [] : ['params', 'headers'].includes(key) ? {} : ''))]))
  else if (step._protocolBackup) Object.assign(result, copy(step._protocolBackup))
  if (protocol === 'SSE') {
    Object.assign(result, { protocol, files: [], assertions: [], extractors: [] })
    result.sse_config = Object.keys(step.sse_config || {}).length ? copy(step.sse_config) : sseTemplate()
    result._sseDraft = step._sseDraft || JSON.stringify(result.sse_config, null, 2)
  } else if (protocol === 'WEBSOCKET') {
    Object.assign(result, { protocol, method: 'GET', params: {}, body_type: 'NONE', body: '', files: [], assertions: [], extractors: [] })
    result.headers = Object.fromEntries(Object.entries(result.headers || {}).filter(([key]) => !['authorization', 'cookie'].includes(key.toLowerCase())))
    result.websocket_config = Object.keys(step.websocket_config || {}).length ? copy(step.websocket_config) : websocketTemplate(accessVariable)
    result._websocketDraft = step._websocketDraft || JSON.stringify(result.websocket_config, null, 2)
  } else {
    Object.assign(result, result._protocolBackup || (step.protocol === 'SSE' ? {} : { method: 'GET', params: {}, body_type: 'NONE', body: '', files: [], assertions: [{ type: 'STATUS_CODE', expected: 200 }], extractors: [] }), { protocol: 'HTTP' })
    delete result._protocolBackup
  }
  return result
}

export function websocketDraftErrors(steps, accessVariable) {
  return steps.flatMap((step, index) => step.protocol === 'WEBSOCKET'
    ? parseWebsocketDraft(step._websocketDraft ?? step.websocket_config, accessVariable).errors.map(error => ({ index, ...error })) : [])
}

export function stepForSave(step, order, accessVariable) {
  const result = { ...step, order }
  for (const key of ['_uid', '_websocketDraft', '_sseDraft', '_protocolBackup', 'scenario', 'source_metadata', 'readiness']) delete result[key]
  result.websocket_config = step.protocol === 'WEBSOCKET'
    ? parseWebsocketDraft(step._websocketDraft ?? step.websocket_config, accessVariable).config
    : {}
  result.sse_config = step.protocol === 'SSE' ? parseSseDraft(step._sseDraft ?? step.sse_config).config : {}
  return result
}
