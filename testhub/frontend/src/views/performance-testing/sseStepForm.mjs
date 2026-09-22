const copy = value => JSON.parse(JSON.stringify(value))
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const only = (value, keys) => object(value) && Object.keys(value).every(key => keys.includes(key))
const namePattern = /^[A-Za-z][A-Za-z0-9_]{0,47}$/
const outputPattern = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/
const pathPattern = /^\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$/
const reserved = new Set(['base_url', 'baseUrl', 'vu_id', 'iteration', 'request_id', '__proto__', 'prototype', 'constructor'])
export const sseBounds = { total_ms: [1, 300000], idle_ms: [1, 300000], max_event_bytes: [1, 1048576], max_total_bytes: [1, 16777216], max_events: [1, 4096] }
const condition = (expr, operator, expected) => ({ type: 'JSON_PATH', expr, operator, ...(expected === undefined ? {} : { expected }) })
const eq = (expr, expected) => condition(expr, 'eq', expected)
const rule = (name, event, fields = {}) => ({ name, event, match: [], assertions: [], extractors: [], min_events: 0, max_events: 256, after: [], without: [], terminal: false, ...fields })

export function sseTemplate(kind = 'CHAT') {
  const config = { version: 1, total_ms: 30000, idle_ms: 10000, max_event_bytes: 262144, max_total_bytes: 4194304, max_events: 256, assertions: [], error_conditions: [], rules: [] }
  if (kind === 'MESSAGE_SPEECH') {
    config.rules = [
      rule('meta', 'meta', { min_events: 1, max_events: 1, assertions: [eq('$.session_id', '{{session_id}}'), eq('$.data_id', '{{data_id}}'), condition('$.speech_id', 'nonempty'), eq('$.format', 'mp3'), condition('$.total_chars', 'positive_int'), condition('$.units', 'positive_int')], extractors: [{ name: 'speech_id', type: 'JSON_PATH', expr: '$.speech_id' }] }),
      rule('segment', 'segment', { min_events: 1, after: ['meta'], sequence: { expr: '$.seq', start: 1 }, assertions: [eq('$.speech_id', '{{speech_id}}'), condition('$.chars', 'positive_int'), condition('$.duration_ms', 'nonnegative_int'), condition('$.audio_base64', 'base64')] }),
      rule('done', 'done', { min_events: 1, max_events: 1, after: ['meta', 'segment'], terminal: true, assertions: [eq('$.speech_id', '{{speech_id}}'), { type: 'JSON_PATH', expr: '$.segments', operator: 'eq', expected_count: 'segment' }, condition('$.total_chars', 'positive_int'), condition('$.units', 'positive_int')] })
    ]
  } else if (kind === 'CONTENT_SPEECH') {
    const assertions = [condition('$.audio_url', 'url'), condition('$.duration_ms', 'nonnegative_int'), condition('$.total_chars', 'positive_int'), condition('$.audio_version', 'nonempty')]
    config.rules = [
      rule('segment', 'segment', { sequence: { expr: '$.seq', start: 0 }, assertions: [condition('$.chars', 'positive_int'), condition('$.duration_ms', 'nonnegative_int'), condition('$.audio_b64', 'base64')] }),
      rule('cached', 'done', { max_events: 1, match: [eq('$.cached', true)], without: ['segment'], terminal: true, assertions: copy(assertions) }),
      rule('generated', 'done', { max_events: 1, match: [eq('$.cached', false)], after: ['segment'], terminal: true, assertions: copy(assertions) })
    ]
  } else {
    config.assertions = [eq('$.session_id', '{{session_id}}')]
    config.error_conditions = [condition('$.gateway_error', 'nonempty'), eq('$.choices[0].delta.phase', 'error')]
    config.rules = [
      rule('progress', 'message', { match: [eq('$.choices[0].delta.phase', 'progress')] }),
      rule('report_nonempty', 'message', { min_events: 1, match: [eq('$.choices[0].delta.phase', 'report'), condition('$.choices[0].delta.content', 'nonempty')], assertions: [condition('$.choices[0].delta.content', 'nonempty')] }),
      rule('report_blank', 'message', { match: [eq('$.choices[0].delta.phase', 'report'), condition('$.choices[0].delta.content', 'blank')] }),
      rule('finish', 'message', { min_events: 1, max_events: 1, after: ['report_nonempty'], match: [eq('$.choices[0].delta.phase', 'finish')], assertions: [eq('$.choices[0].finish_reason', 'stop')] }),
      rule('done', 'message', { data: '[DONE]', after: ['report_nonempty', 'finish'], min_events: 1, max_events: 1, terminal: true })
    ]
  }
  return config
}

export function parseSseDraft(draft) {
  const errors = [], fail = (path, code = 'shape') => errors.push({ path, code })
  let config
  try { config = typeof draft === 'string' ? JSON.parse(draft) : copy(draft) } catch { return { config: null, errors: [{ path: 'config', code: 'json' }] } }
  if (!only(config, ['version', ...Object.keys(sseBounds), 'assertions', 'error_conditions', 'rules']) || config.version !== 1) return { config: null, errors: [{ path: 'config', code: 'shape' }] }
  if (new TextEncoder().encode(JSON.stringify(config)).length > 128 * 1024) fail('config', 'size')
  function safe(value) {
    if (Array.isArray(value)) return value.every(safe)
    if (object(value)) return Object.entries(value).every(([key, item]) => !['__proto__', 'prototype', 'constructor'].includes(key) && safe(item))
    return typeof value !== 'number' || Number.isFinite(value)
  }
  try { if (!safe(config)) fail('config') } catch { fail('config') }
  const defaults = sseTemplate()
  for (const [key, [min, max]] of Object.entries(sseBounds)) {
    if (!Object.hasOwn(config, key)) config[key] = defaults[key]
    if (!Number.isInteger(config[key]) || config[key] < min || config[key] > max) fail(key, 'bound')
  }
  if (config.idle_ms > config.total_ms) fail('idle_ms', 'lifetime')
  const validPath = expr => typeof expr === 'string' && expr !== '$' && pathPattern.test(expr)
  const previous = new Set(), outputs = new Set(), asserted = new Set()
  function conditions(value, path) {
    if (!Array.isArray(value) || value.length > 32) { fail(path, 'condition'); return }
    value.forEach((item, index) => {
      const field = `${path}[${index}]`
      if (object(item) && !Object.hasOwn(item, 'operator')) item.operator = 'eq'
      if (!only(item, ['type', 'expr', 'operator', 'expected', 'expected_count']) || item.type !== 'JSON_PATH' || !validPath(item.expr) || !['eq', 'ne', 'exists', 'nonempty', 'blank', 'positive_int', 'nonnegative_int', 'base64', 'url'].includes(item.operator)) { fail(field, 'condition'); return }
      const expected = Object.hasOwn(item, 'expected'), count = Object.hasOwn(item, 'expected_count')
      if (['eq', 'ne'].includes(item.operator)) {
        if (expected === count || (count && !previous.has(item.expected_count)) || (expected && (object(item.expected) || Array.isArray(item.expected)))) fail(field, 'condition')
      } else if (expected || count) fail(field, 'condition')
    })
  }
  for (const field of ['assertions', 'error_conditions']) { if (!Object.hasOwn(config, field)) config[field] = []; conditions(config[field], field) }
  if (!Array.isArray(config.rules) || config.rules.length < 1 || config.rules.length > 16) fail('rules', 'rules')
  else {
    let terminals = 0
    config.rules.forEach((item, index) => {
      const path = `rules[${index}]`
      if (!only(item, ['name', 'event', 'data', 'match', 'assertions', 'extractors', 'min_events', 'max_events', 'after', 'without', 'terminal', 'sequence'])) { fail(path); return }
      if (typeof item.name !== 'string' || !namePattern.test(item.name) || previous.has(item.name)) fail(`${path}.name`)
      if (typeof item.event !== 'string' || !namePattern.test(item.event) || item.event === 'error') fail(`${path}.event`)
      for (const [key, value] of Object.entries({ min_events: 0, max_events: config.max_events, terminal: false, after: [], without: [], match: [], assertions: [], extractors: [] })) if (!Object.hasOwn(item, key)) item[key] = value
      if (!Number.isInteger(item.min_events) || !Number.isInteger(item.max_events) || item.min_events < 0 || item.max_events < 1 || item.min_events > item.max_events || item.max_events > config.max_events) fail(`${path}.max_events`, 'bound')
      if (!Array.isArray(item.after) || item.after.some(name => !previous.has(name)) || new Set(item.after).size !== item.after.length) fail(`${path}.after`, 'order')
      if (!Array.isArray(item.without) || item.without.some(name => !previous.has(name)) || new Set(item.without).size !== item.without.length || item.without.some(name => Array.isArray(item.after) && item.after.includes(name))) fail(`${path}.without`, 'order')
      if (typeof item.terminal !== 'boolean') fail(`${path}.terminal`)
      if (item.terminal) {
        terminals++
        if (!item.assertions?.length && !(Array.isArray(item.after) && item.after.some(name => asserted.has(name)))) fail(`${path}.terminal`, 'terminal')
      }
      conditions(item.match, `${path}.match`); conditions(item.assertions, `${path}.assertions`)
      if (!Array.isArray(item.extractors) || item.extractors.length > 16) fail(`${path}.extractors`, 'extractor')
      else {
        item.extractors.forEach((extractor, n) => {
          if (!only(extractor, ['name', 'type', 'expr']) || extractor.type !== 'JSON_PATH' || !validPath(extractor.expr) || typeof extractor.name !== 'string' || !outputPattern.test(extractor.name) || reserved.has(extractor.name) || outputs.has(extractor.name)) fail(`${path}.extractors[${n}]`, 'extractor')
          if (object(extractor)) outputs.add(extractor.name)
        })
      }
      if (Object.hasOwn(item, 'sequence') && (!only(item.sequence, ['expr', 'start']) || !validPath(item.sequence.expr) || !Number.isInteger(item.sequence.start) || item.sequence.start < 0 || item.sequence.start > 1000000)) fail(`${path}.sequence`, 'sequence')
      if (Object.hasOwn(item, 'data') && (typeof item.data !== 'string' || item.data.length < 1 || item.data.length > 256 || item.match?.length || item.assertions?.length || item.extractors?.length || item.sequence)) fail(`${path}.data`, 'literal')
      if (typeof item.data === 'string') { try { JSON.parse(item.data); fail(`${path}.data`, 'literal') } catch { /* Non-JSON sentinels use raw matching. */ } }
      if (item.assertions?.length || (Array.isArray(item.after) && item.after.some(name => asserted.has(name)))) asserted.add(item.name)
      previous.add(item.name)
    })
    if (!terminals) fail('rules', 'terminal')
  }
  return { config: errors.length ? null : config, errors }
}

export function sourceDeclaresSse(metadata) {
  const raw = metadata?.raw
  const sse = value => typeof value === 'string' && value.split(';')[0].trim().toLowerCase() === 'text/event-stream'
  return Array.isArray(raw?.produces) && raw.produces.some(sse) || Object.entries(raw?.responses || {}).some(([status, response]) => /^2(?:\d\d|XX)$/i.test(status) && Object.keys(response?.content || {}).some(sse))
}

export function canSelectProtocol(step, isK6, protocol) {
  if (!isK6) return false
  if (!step.source_request && !step.source_request_id && !Object.keys(step.source_metadata || {}).length) return ['HTTP', 'WEBSOCKET', 'SSE'].includes(protocol)
  return sourceDeclaresSse(step.source_metadata) && ['HTTP', 'SSE'].includes(protocol)
}

export function sseDraftErrors(steps) {
  return steps.flatMap((step, index) => {
    if (step.protocol !== 'SSE') return []
    const errors = parseSseDraft(step._sseDraft ?? step.sse_config).errors
    if (!['GET', 'POST'].includes(step.method || 'GET') || !['NONE', 'JSON'].includes(step.body_type || 'NONE') || (step.method === 'GET' && step.body_type === 'JSON') || ['files', 'assertions', 'extractors'].some(key => step[key]?.length)) errors.push({ path: 'request', code: 'request' })
    if (step.body_type === 'JSON') { try { JSON.parse(step.body) } catch { errors.push({ path: 'body', code: 'json' }) } }
    return errors.map(error => ({ index, ...error }))
  })
}
