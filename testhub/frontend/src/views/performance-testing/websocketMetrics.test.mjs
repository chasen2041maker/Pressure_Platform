import test from 'node:test'
import assert from 'node:assert/strict'
import { websocketConnectionCounts, websocketCommandRows, hasWebsocketSteps } from './websocketMetrics.mjs'

test('unattributed event rows retain the measured latency kind without inventing labels', () => {
  const [row] = websocketCommandRows({ command_metrics: [{ step_id: 9, command_index: 0, total: 1, latency_kind: 'event_wait' }] })
  assert.equal(row.latencyKind, 'event_wait')
  assert.equal(row.name, null)
  const [unknown] = websocketCommandRows({ command_metrics: [{ step_id: 9, command_index: 0, total: 1 }] })
  assert.equal(unknown.latencyKind, null)
})

test('socket counts are observations, never inferred from virtual users or absent data', () => {
  assert.deepEqual(websocketConnectionCounts({ connections: { observed: false, current: 0, peak: 0 }, max_concurrency: 1000 }), { current: null, peak: null, unclosed: null })
  assert.deepEqual(websocketConnectionCounts({ connections: { observed: true, current: null, peak: 2, unclosed: 1 } }), { current: null, peak: 2, unclosed: 1 })
  assert.equal(websocketConnectionCounts({ connections: { observed: true, current: 0, peak: 2, unclosed: 0 } }).current, 0)
})

test('command labels only use frozen step ID and zero-based command index', () => {
  const steps = [{ id: 7, name: 'Frozen', protocol: 'WEBSOCKET', websocket_commands: [{ index: 0, name: 'History', action: 'timeline.list' }, { index: 1, name: 'Unread', action: 'timeline.unread' }], websocket_config: { commands: [{ name: 'Mutable' }] } }]
  const rows = websocketCommandRows({ command_metrics: [{ step_id: 7, command_index: 1, name: 'Untrusted metric label', action: 'send', total: 2, success: 1, failed: 1, p95_rt: 4 }] }, steps)
  assert.equal(rows[0].total, null)
  assert.equal(rows[1].name, 'Unread')
  assert.equal(rows[1].action, 'timeline.unread')
  assert.equal(rows[1].total, 2)
  assert.equal(rows[1].p95_rt, 4)
  assert.equal(hasWebsocketSteps(steps), true)
  assert.equal(hasWebsocketSteps([{ protocol: 'HTTP' }]), false)
})

test('unknown command attribution is retained without falsely labelling it as a matching name', () => {
  const rows = websocketCommandRows({ command_metrics: [{ step_id: 8, command_index: 1, total: 1, name: 'same', action: 'secret' }] }, [{ id: 7, protocol: 'WEBSOCKET', websocket_commands: [{ index: 1, name: 'same', action: 'timeline.list' }] }])
  assert.equal(rows.length, 2)
  assert.equal(rows[1].name, null)
  assert.equal(rows[1].action, null)
  assert.equal(rows[1].total, 1)
})
