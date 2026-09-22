import test from 'node:test'
import assert from 'node:assert/strict'
import { sseTemplate, parseSseDraft, sseDraftErrors, sourceDeclaresSse, canSelectProtocol } from './sseStepForm.mjs'
import { convertStepProtocol, stepForSave } from './websocketStepForm.mjs'
import { sseSummary, sseStreamRows } from './sseMetrics.mjs'

test('all business templates require meaningful completion and preserve typed conditions', () => {
  for (const kind of ['CHAT', 'MESSAGE_SPEECH', 'CONTENT_SPEECH']) {
    const config = sseTemplate(kind)
    assert.deepEqual(parseSseDraft(config).errors, [], kind)
    assert.ok(config.rules.some(rule => rule.terminal && (rule.assertions.length || rule.after.length)))
  }
  const chat = sseTemplate('CHAT')
  assert.deepEqual(chat.rules.find(rule => rule.data === '[DONE]').after, ['report_nonempty', 'finish'])
  const audio = sseTemplate('CONTENT_SPEECH')
  assert.equal(audio.rules.find(rule => rule.name === 'cached').match[0].expected, true)
  assert.equal(audio.rules.find(rule => rule.name === 'generated').match[0].expected, false)
  assert.deepEqual(audio.rules.find(rule => rule.name === 'cached').without, ['segment'])
  assert.deepEqual(chat.rules.find(rule => rule.name === 'finish').after, ['report_nonempty'])
  assert.equal(audio.rules.find(rule => rule.name === 'segment').sequence.start, 0)
  assert.equal(sseTemplate('MESSAGE_SPEECH').rules.find(rule => rule.name === 'segment').sequence.start, 1)
})

test('validation rejects naked DONE, dangling count/order references, malformed JSON, and unsupported fields', () => {
  const config = sseTemplate('CHAT')
  config.rules = [{ name: 'done', event: 'message', data: '[DONE]', terminal: true, assertions: [], after: [], extractors: [], min_events: 1, max_events: 1 }]
  assert.ok(parseSseDraft(config).errors.length)
  assert.ok(parseSseDraft('{').errors.length)
  const speech = sseTemplate('MESSAGE_SPEECH')
  speech.rules.find(rule => rule.name === 'done').assertions.push({ type: 'JSON_PATH', expr: '$.segments', operator: 'eq', expected_count: 'missing' })
  assert.ok(parseSseDraft(speech).errors.length)
  speech.rules[0].unknown = true
  assert.ok(parseSseDraft(speech).errors.length)
})

test('literal rules cannot bypass JSON validation or complete through unasserted sentinel chains', () => {
  const config = sseTemplate('CHAT')
  config.rules.at(-1).data = '{"gateway_error":"failure"}'
  assert.ok(parseSseDraft(config).errors.length)
  config.rules = [
    { name: 'start', event: 'message', data: 'START', max_events: 1 },
    { name: 'done', event: 'message', data: '[DONE]', max_events: 1, after: ['start'], terminal: true }
  ]
  assert.ok(parseSseDraft(config).errors.length)
  config.rules[0] = { name: 'start', event: 'message', assertions: [{ type: 'JSON_PATH', expr: '$.ok', operator: 'eq', expected: true }] }
  assert.deepEqual(parseSseDraft(config).errors, [])
})

test('protocol conversion keeps request identity and source while clearing HTTP-only validation; save and reload preserve SSE', () => {
  const source = { raw: { responses: { 200: { content: { 'text/event-stream': {} } } } } }
  const step = { id: 7, protocol: 'HTTP', method: 'POST', url: '/stream', headers: { Authorization: 'Bearer {{access_token}}' }, params: { cache: false }, body_type: 'JSON', body: '{"stream":true}', files: [], assertions: [{ type: 'STATUS_CODE', expected: 200 }], extractors: [], source_metadata: source, source_request: 9 }
  assert.equal(sourceDeclaresSse(source), true)
  assert.equal(canSelectProtocol(step, true, 'SSE'), true)
  assert.equal(canSelectProtocol(step, true, 'WEBSOCKET'), false)
  assert.equal(canSelectProtocol({ ...step, source_metadata: {} }, true, 'SSE'), false)
  assert.equal(canSelectProtocol(step, false, 'SSE'), false)
  const converted = convertStepProtocol(step, 'SSE')
  assert.equal(converted.method, 'POST'); assert.equal(converted.body, step.body)
  assert.deepEqual(converted.headers, step.headers); assert.deepEqual(converted.source_metadata, source)
  assert.deepEqual(converted.assertions, [])
  const saved = stepForSave(converted, 2)
  assert.deepEqual(saved.sse_config, converted.sse_config)
  assert.equal(saved._sseDraft, undefined)
  assert.equal(saved._protocolBackup, undefined)
  assert.deepEqual(sseDraftErrors([saved]), [])
  assert.deepEqual(convertStepProtocol(converted, 'HTTP').assertions, step.assertions)
})

test('invalid offscreen or disabled SSE drafts block saves; unsupported request methods and bodies are flagged', () => {
  const good = { protocol: 'SSE', method: 'POST', body_type: 'JSON', body: '{}', sse_config: sseTemplate(), assertions: [], extractors: [], files: [] }
  assert.deepEqual(sseDraftErrors([good]), [])
  assert.equal(sseDraftErrors([good, { ...good, enabled: false, _sseDraft: '{' }])[0].index, 1)
  assert.ok(sseDraftErrors([{ ...good, method: 'PUT' }]).length)
  assert.ok(sseDraftErrors([{ ...good, body_type: 'BINARY' }]).length)
  assert.ok(sseDraftErrors([{ ...good, method: 'GET' }]).length)
})

test('SSE metrics never invent old measurements and join stream names only by unambiguous frozen identity', () => {
  assert.equal(sseSummary({}).started, null)
  assert.equal(sseSummary({ first_event: { count: 0, avg_ms: 0 } }).firstEvent.avg_ms, null)
  const rows = sseStreamRows({ stream_metrics: [{ step_id: 8, name: 'mutable', streams: { started: 1, completed: 1, success: 1, failed: 0 }, first_event: { count: 1, avg_ms: 10 }, completion: { count: 1, avg_ms: 50 }, errors: [] }] }, [{ id: 8, protocol: 'SSE', name: 'Frozen audio' }, { id: 9, protocol: 'SSE', name: 'No samples' }])
  assert.equal(rows[0].name, 'Frozen audio'); assert.equal(rows[0].firstEvent.avg_ms, 10)
  assert.equal(rows[1].started, null)
  const duplicates = sseStreamRows({ stream_metrics: [{ step_id: 8, streams: { started: 1 } }, { step_id: 8, streams: { started: 2 } }] }, [{ id: 8, protocol: 'SSE', name: 'Frozen audio' }])
  assert.equal(duplicates[0].started, null)
})

test('SSE failure locations whitelist typed positions and keep truncation explicit', () => {
  const good = { reason: 'assertion_failed', scope: 'rule_assertion', event_index: 6, rule_index: 4, condition_index: 1, count: 2 }
  const result = sseSummary({ diagnostics: [good, { ...good, value: 'PRIVATE' }, { ...good, event_index: true }, { ...good, rule_index: 17 }, { ...good, condition_index: 33 }, { ...good, scope: 'PRIVATE' }, { ...good, reason: 'PRIVATE' }, null], diagnostics_truncated: true })
  assert.deepEqual(result.diagnostics, [good])
  assert.equal(result.diagnosticsTruncated, true)
  assert.deepEqual(sseSummary({}).diagnostics, [])
  assert.ok(!JSON.stringify(result).includes('PRIVATE'))
})
