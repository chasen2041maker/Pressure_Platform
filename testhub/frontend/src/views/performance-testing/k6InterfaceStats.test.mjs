import test from 'node:test'
import assert from 'node:assert/strict'
import { buildK6InterfaceRows, buildK6DebugRows, K6_DEBUG_LABELS } from './k6InterfaceStats.mjs'

test('WS metrics and diagnostics distinguish session duration from HTTP request latency', () => {
  const steps = [{ id: 1, protocol: 'WEBSOCKET', websocket_commands: [{ index: 0, name: 'Frozen history' }] }, { id: 2 }]
  assert.deepEqual(buildK6InterfaceRows({ steps }).map(row => [row.protocol, row.latencyKind]), [['WEBSOCKET', 'session'], ['HTTP', 'request']])
  const [debug] = buildK6DebugRows({ steps: [{ step_id: 1, command_index: 0, stage: 'command', outcome: 'failed' }] }, steps)
  assert.equal(debug.commandName, 'Frozen history')
  assert.equal(K6_DEBUG_LABELS.body_omitted, '正文未显示')
  assert.equal(K6_DEBUG_LABELS.failed, '失败')
})

test('API paths stay with frozen step IDs and never display synthetic metric URLs', () => {
  const rows = buildK6InterfaceRows({ steps: [
    { id: 1, name: 'same', method: 'GET', request_path: '/api/a' },
    { id: 2, name: 'same', method: 'GET', request_path: '/api/b/{{id}}' },
    { id: 3, name: 'historical', method: 'GET' }
  ], stats: [{ step_id: 2, url: 'step:2', total: 2 }, { step_id: 1, url: 'step:1', total: 1 }] })
  assert.deepEqual(rows.map(row => row.requestPath), ['/api/a', '/api/b/{{id}}', null])
  assert.deepEqual(rows.map(row => row.total), [1, 2, null])
})

test('authentication phases depend on explicit metadata and never on display names', () => {
  const steps = [{ id: 1, name: '认证登录', method: 'GET' }, { id: 'auth:login', name: 'Login renamed', is_setup: true, auth_phase: 'login' },
    { id: 'auth:refresh', name: 'Refresh renamed', is_setup: true, auth_phase: 'refresh' }]
  assert.deepEqual(buildK6InterfaceRows({ steps }).map(row => row.phase), ['business', 'login', 'refresh'])
  const rows = buildK6DebugRows({ steps: [{ step_id: 'auth:login', outcome: 'passed' }, { step_id: 1, outcome: 'http_failed' }, { step_id: 'auth:refresh', outcome: 'passed' }, { step_id: 1, outcome: 'passed' }] }, steps)
  assert.deepEqual(rows.map(row => row.phase), ['login', 'business', 'refresh', 'business'])
  assert.equal(new Set(rows.map(row => row.key)).size, 4)
  assert.match(K6_DEBUG_LABELS.http_failed, /HTTP/)
})

test('debug rows retain unsampled steps without inventing successes, and honor truncation', () => {
  const steps = [{ id: 1, name: 'blocked business' }]
  assert.equal(buildK6DebugRows({ steps: [] }, steps)[0].outcome, 'pending')
  assert.deepEqual(buildK6DebugRows({ steps: [], truncated: true }, steps), [])
  assert.deepEqual(buildK6DebugRows(null, steps), [])
})

test('historical results preserve each interface count and do not invent a transport root cause', () => {
  const rows = buildK6InterfaceRows({ stats: [
    { step_name: '当前用户信息', method: 'GET', total: 3000, success: 2420, failed: 580,
      error_detail: [{ type: 'TransportError', count: 580, message: 'TransportError' }] },
    { step_name: '未读通知数量', method: 'GET', total: 3000, success: 2936, failed: 64,
      error_detail: [{ type: 'TransportError', count: 64 }] }
  ] })
  assert.deepEqual(rows.map(row => [row.total, row.success, row.failed]), [[3000, 2420, 580], [3000, 2936, 64]])
  assert.match(rows[0].failureReason, /580 次.*网络传输失败.*未记录更细原因/)
  assert.doesNotMatch(rows[0].failureReason, /连接被拒绝/)
})

test('live snapshots derive successes but leave unsampled interfaces unknown', () => {
  const rows = buildK6InterfaceRows({ live: true, stats: [{ name: '列表', total: 17, failed: 2 }],
    steps: [{ name: '登录', method: 'POST', is_setup: true }, { name: '列表', method: 'GET' },
      { name: '详情', method: 'GET' }, { name: '禁用步骤', enabled: false }] })
  assert.equal(rows.length, 3)
  assert.equal(rows[0].isSetup, true)
  assert.deepEqual([rows[1].total, rows[1].success, rows[1].failed], [17, 15, 2])
  assert.match(rows[1].failureReason, /结束后汇总/)
  assert.equal(rows[2].total, null)
  assert.equal(rows[2].failed, null)
})

test('known new error categories are Chinese and arbitrary messages or types are not exposed', () => {
  const rows = buildK6InterfaceRows({ stats: [{ step_name: '详情', total: 12, success: 6, failed: 6,
    error_detail: [{ type: 'ConnectionRefused', count: 3, message: 'token=private-value' },
      { type: 'private-token-type', count: 1, message: 'Bearer secret' }] }] })
  assert.match(rows[0].failureReason, /3 次.*连接被拒绝/)
  assert.match(rows[0].failureReason, /另有 2 次失败未提供分类/)
  assert.doesNotMatch(JSON.stringify(rows), /private-value|Bearer secret|private-token-type/)
})

test('summary error_top is a per-interface fallback without double-counting detailed errors', () => {
  const rows = buildK6InterfaceRows({ stats: [{ step_name: '列表', total: 8, success: 6, failed: 2 }],
    errorTop: [{ sample_step: '列表', type: 'AssertionFailed', count: 2 },
      { sample_step: '其他接口', type: 'TransportError', count: 99 }] })
  assert.match(rows[0].failureReason, /2 次.*断言失败/)
  assert.doesNotMatch(rows[0].failureReason, /99/)
  const successful = buildK6InterfaceRows({ stats: [{ step_name: '列表', total: 8, success: 8, failed: 0 }] })
  assert.equal(successful[0].failureReason, '无失败')
})
