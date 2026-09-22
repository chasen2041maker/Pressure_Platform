const count = value => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null
const metric = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
export const hasSseSteps = steps => Array.isArray(steps) && steps.some(step => step.enabled !== false && step.protocol === 'SSE')
export const protocolLatencyKey = protocol => protocol === 'SSE' ? 'performanceTesting.sse.completionLatency' : protocol === 'WEBSOCKET' ? 'performanceTesting.websocket.sessionLatency' : 'performanceTesting.websocket.requestLatency'
const latency = value => ({ count: count(value?.count), ...Object.fromEntries(['avg_ms', 'min_ms', 'max_ms'].map(key => [key, count(value?.count) > 0 ? metric(value?.[key]) : null])) })
const reasons = new Set(['completed', 'invalid_options', 'cancelled', 'total_timeout', 'idle_timeout', 'transport_error', 'http_status', 'redirect', 'content_type', 'unexpected_eof', 'read_error', 'event_bytes_limit', 'total_bytes_limit', 'events_limit', 'invalid_utf8', 'event_error', 'business_error', 'callback_error', 'protocol_error', 'assertion_failed', 'extraction_failed', 'invalid_json', 'event_unmatched', 'event_ambiguous', 'event_order', 'event_count', 'sequence', 'terminal_missing', 'preparation_failed', 'runtime_unavailable'])
function diagnostic(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return null
  const shapes = { event: [], error_condition: ['condition_index'], global_assertion: ['condition_index'], rule: ['rule_index'], rule_assertion: ['rule_index', 'condition_index'], sequence: ['rule_index'], extractor: ['rule_index', 'condition_index'], terminal: ['rule_index'], transport: [] }
  if (!Object.hasOwn(shapes, item.scope) || !reasons.has(item.reason) || item.reason === 'completed' || !(count(item.count) > 0)) return null
  const fields = ['scope', 'reason', 'count', ...shapes[item.scope]]
  if (item.scope !== 'transport' || Object.hasOwn(item, 'event_index')) fields.push('event_index')
  if (Object.keys(item).length !== fields.length || Object.keys(item).some(key => !fields.includes(key))) return null
  for (const [key, bound] of [['event_index', 4096], ['rule_index', 16], ['condition_index', item.scope === 'extractor' ? 16 : 32]]) {
    if (fields.includes(key) && !(count(item[key]) > 0 && item[key] <= bound)) return null
  }
  return Object.fromEntries(fields.map(key => [key, item[key]]))
}
export function sseSummary(sse) {
  return { ...Object.fromEntries(['started', 'completed', 'success', 'failed', 'incomplete'].map(key => [key, count(sse?.streams?.[key])])), events: count(sse?.events), bytes: count(sse?.bytes), firstEvent: latency(sse?.first_event), completion: latency(sse?.completion),
    errors: (Array.isArray(sse?.errors) ? sse.errors : []).map(error => ({ phase: ['preparation', 'transport', 'business'].includes(error?.phase) ? error.phase : 'unknown', reason: reasons.has(error?.reason) ? error.reason : 'unknown', count: count(error?.count) })),
    diagnostics: (Array.isArray(sse?.diagnostics) ? sse.diagnostics : []).slice(0, 32).map(diagnostic).filter(Boolean), diagnosticsTruncated: sse?.diagnostics_truncated === true }
}
export function sseStreamRows(sse, steps = []) {
  const stats = Array.isArray(sse?.stream_metrics) ? sse.stream_metrics : [], used = new Set(), rows = []
  const definitions = steps.filter(step => step.enabled !== false && step.protocol === 'SSE')
  for (const step of definitions) {
    const matches = stats.map((stat, index) => ({ stat, index })).filter(({ stat }) => String(stat.step_id) === String(step.id))
    const match = step.id != null && matches.length === 1 && definitions.filter(item => String(item.id) === String(step.id)).length === 1 ? matches[0] : null
    if (match) used.add(match.index)
    rows.push({ stepId: step.id, name: step.name, ...sseSummary(match?.stat) })
  }
  stats.forEach((stat, index) => { if (!used.has(index)) rows.push({ stepId: stat.step_id, name: null, ...sseSummary(stat) }) })
  return rows.map((row, index) => ({ ...row, key: `${row.stepId}:${index}` }))
}
