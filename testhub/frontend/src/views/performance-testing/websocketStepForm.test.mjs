import test from 'node:test'
import assert from 'node:assert/strict'
import { websocketTemplate, websocketPushTemplate, parseWebsocketDraft, canChangeProtocol, convertStepProtocol, websocketDraftErrors, stepForSave } from './websocketStepForm.mjs'

test('push templates retain handshake authentication and real typed event assertions on save', () => {
  for (const kind of ['home', 'quotes', 'reminder']) {
    const config = websocketPushTemplate(kind, 'pool_token')
    const parsed = parseWebsocketDraft(config, 'pool_token')
    assert.deepEqual(parsed.errors, [])
    assert.equal(parsed.config.auth.token, '{{pool_token}}')
    assert.equal(parsed.config.heartbeat_interval_ms, 0)
    assert.equal(parsed.config.commands[0].event_path, kind === 'reminder' ? '$.event_type' : '$.type')
    const saved = stepForSave({ protocol: 'WEBSOCKET', _websocketDraft: JSON.stringify(config) }, 2, 'pool_token')
    assert.deepEqual(saved.websocket_config, config)
    config.commands[0].assertions = []
    assert.ok(parseWebsocketDraft(config, 'pool_token').errors.length)
  }
})

test('push draft rejects invalid matchers, request shape, authentication and application heartbeat', () => {
  for (const mutate of [c => c.auth.token = 'literal-secret', c => c.heartbeat_interval_ms = 25000,
    c => c.commands[0].event_path = null, c => c.commands[0].event_type = '{{type}}',
    c => c.commands[0].request = null, c => c.commands[0].error_types = null,
    c => c.commands[0].error_types = [c.commands[0].event_type]]) {
    const config = websocketPushTemplate(); mutate(config)
    assert.ok(parseWebsocketDraft(config).errors.length)
  }
})

test('readonly template uses the configured token reference and typed assertions', () => {
  const config = websocketTemplate('pool_access')
  assert.equal(config.auth.request.payload.token, '{{pool_access}}')
  assert.deepEqual(config.commands.map(c => c.request.action), ['timeline.list', 'timeline.unread'])
  assert.equal(config.commands[0].assertions[0].expected, true)
  assert.equal(parseWebsocketDraft(JSON.stringify(config), 'pool_access').errors.length, 0)
})

test('conversion is manual K6 only and retains original input in a reversible editor backup', () => {
  for (const step of [{ source_request: 7 }, { source_request_id: 7 }, { source_metadata: { version: 1 } }]) assert.equal(canChangeProtocol(step, true), false)
  assert.equal(canChangeProtocol({}, false), false)
  const original = { method: 'POST', body_type: 'JSON', body: '{"data":2}', params: { q: 1 }, headers: { Authorization: '{{token}}', 'X-Test': '1' }, assertions: [{ type: 'STATUS_CODE', expected: 200 }] }
  const ws = convertStepProtocol(original, 'WEBSOCKET', 'token')
  assert.equal(ws.method, 'GET')
  assert.deepEqual(ws.params, {})
  assert.deepEqual(ws.headers, { 'X-Test': '1' })
  assert.equal(original.body_type, 'JSON')
  const restored = convertStepProtocol(ws, 'HTTP', 'token')
  for (const key of Object.keys(original)) assert.deepEqual(restored[key], original[key])
  assert.equal(stepForSave(ws, 0)._protocolBackup, undefined)
})

test('invalid drafts on nonselected and disabled steps block saving without replacing prior valid config', () => {
  const valid = websocketTemplate('token')
  const steps = [{ protocol: 'HTTP' }, { protocol: 'WEBSOCKET', enabled: false, websocket_config: valid, _websocketDraft: '{' }]
  assert.equal(websocketDraftErrors(steps, 'token')[0].index, 1)
  assert.equal(steps[1].websocket_config, valid)
  const saved = stepForSave({ ...steps[1], _uid: 's2', _websocketDraft: JSON.stringify(valid) }, 4, 'token')
  assert.equal(saved._websocketDraft, undefined)
  assert.equal(saved._uid, undefined)
  assert.equal(saved.order, 4)
  assert.deepEqual(saved.websocket_config, valid)
})

test('validation points to invalid command and preserves JSON scalar types', () => {
  const config = websocketTemplate('token')
  config.commands[1].assertions[0].expected = 3
  assert.equal(parseWebsocketDraft(JSON.stringify(config), 'token').config.commands[1].assertions[0].expected, 3)
  config.commands[1].request.id = 'caller-id'
  assert.ok(parseWebsocketDraft(JSON.stringify(config), 'token').errors.some(e => e.path === 'commands[1].request'))
  delete config.commands[1].request.id
  config.auth.request.payload.token = 'raw-token'
  assert.ok(parseWebsocketDraft(JSON.stringify(config), 'token').errors.some(e => e.code === 'tokenReference'))
  assert.ok(parseWebsocketDraft(JSON.stringify(config), 'token').errors.every(e => !JSON.stringify(e).includes('raw-token')))
})

test('invalid scalar rules, unsupported keys, lifetime and timer ranges cannot save', () => {
  for (const mutate of [c => c.commands[0].assertions[0].expected = {}, c => c.hold_open_ms = c.max_session_ms,
    c => c.heartbeat_interval_ms = 1, c => c.commands = [], c => c.commands[0].request.action = 'ping',
    c => c.commands[0].extractors = [{ name: 'bad name', type: 'JSON_PATH', expr: '$.ok' }], c => c.extra = 1,
    c => c.connect_timeout_ms = null, c => c.commands[0].assertions = null, c => c.commands[0].request.action = null]) {
    const config = websocketTemplate('token'); mutate(config)
    assert.ok(parseWebsocketDraft(JSON.stringify(config), 'token').errors.length)
  }
})
