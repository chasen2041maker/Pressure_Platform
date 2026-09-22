import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as Router from 'vue-router'
import * as cursor from './sampleCursor.mjs'
import * as sla from './slaReport.mjs'
import * as interfaces from './k6InterfaceStats.mjs'
import * as websocketMetrics from './websocketMetrics.mjs'
import * as sseMetrics from './sseMetrics.mjs'
import * as lines from './nullableLine.mjs'

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const detail = (id, status = 'RUNNING', engine = 'K6') => ({ id, execution_no: `run-${id}`, scenario: id, scenario_name: `scenario-${id}`, status,
  summary: {}, sla_detail: [], load_snapshot: { _engine: engine, duration: 100 }, steps_snapshot: [{ id, name: `step-${id}` }] })
const sample = id => ({ id: id * 100, ts_offset: id, business_total: id, http_total: id, total_requests: id, tps: id,
  steps: [{ step_id: id, total: id, failed: 0 }], throughput: { latest_bucket_start_ms: id * 1000 } })

test('recovery status refresh and live updates stay on the current monitor and leave business metrics alone', async () => {
  let state = 'PENDING', pendingRead
  const h = await harness({ getPerfExecution: async id => id === 41 && pendingRead ? pendingRead.promise : ({ data: { ...detail(id), resource_recovery: { version: 1, kind: 'portfolio_reminder', state } } }),
    getPerfRealtime: async () => ({ data: { status: 'RUNNING', samples: [], has_more: false, next_after_id: 0, resource_recovery: { state } } }) })
  try {
    await settle(); assert.equal(h.state.execution.resource_recovery.state, 'PENDING')
    assert.equal(typeof h.state.refreshRecovery, 'function')
    state = 'RECOVERED'; await h.state.refreshRecovery(); assert.equal(h.state.execution.resource_recovery.state, 'RECOVERED')
    state = 'CONFLICT'; await h.state.pollOnce()
    assert.equal(h.state.execution.resource_recovery.state, 'CONFLICT')
    assert.deepEqual(h.state.execution.summary, {})
    pendingRead = deferred(); const late = h.state.refreshRecovery(); await settle()
    state = 'RECOVERED'; await h.navigate(45); assert.equal(h.state.execution.id, 45)
    pendingRead.resolve({ data: { ...detail(41), resource_recovery: { state: 'CORRUPT' } } }); await late
    assert.equal(h.state.execution.resource_recovery.state, 'RECOVERED')
    assert.equal(h.state.recoveryLoading.value, false)
  } finally { h.unmount() }
})

async function harness(overrides = {}, options = {}) {
  const calls = [], messages = [], stops = [], sockets = [], timers = new Map(), pushed = [], charts = []
  let timerId = 0, resets = 0
  const addTimer = (fn, delay, repeat) => { const id = ++timerId; timers.set(id, { fn, delay, repeat }); return id }
  const api = {
    getPerfExecution: async id => { calls.push(['detail', id]); return { data: detail(id, options.status || 'RUNNING', options.engine || 'K6') } },
    getPerfRealtime: async (id, params) => { calls.push(['samples', id, params.after_id]); return { data: { status: options.status || 'RUNNING',
      samples: params.after_id < id * 100 ? [sample(id)] : [], has_more: false, next_after_id: id * 100 } } },
    getPerfRunLog: async id => { calls.push(['log', id]); return { data: { content: `log-${id}` } } },
    getPerfRequestStats: async id => ({ data: [{ step: id }] }),
    getPerfScenario: async id => ({ data: { sla_config: { enabled: true, thresholds: { min_tps: id } } } }),
    getEngineStatus: async () => ({ data: { websocket: !!options.websocket } }),
    stopPerfExecution: async id => { stops.push(id); return { data: {} } }, ...overrides
  }
  class Socket {
    constructor(url) { this.url = url; this.closed = false; sockets.push(this) }
    close() { this.closed = true; this.onclose?.() }
  }
  const dependencies = {
    vue: Vue, 'vue-router': Router, './sampleCursor.mjs': cursor, './slaReport.mjs': sla, './k6InterfaceStats.mjs': interfaces, './websocketMetrics.mjs': websocketMetrics, './sseMetrics.mjs': sseMetrics,
    '../nullableLine.mjs': lines,
    echarts: { init: () => {
      const chart = { options: [], disposed: false, setOption(option) { this.options.push(option) }, clear() { this.options = [] }, resize() {}, dispose() { this.disposed = true } }
      charts.push(chart); return chart
    } },
    'vue-i18n': { useI18n: () => ({ t: key => key }) }, '@/api/performance-testing': api,
    './shared': { apiError: (error, fallback) => error?.message || fallback, formatDuration: String },
    'element-plus': { ElMessage: Object.fromEntries(['success', 'error', 'warning', 'info'].map(kind => [kind, message => messages.push([kind, message])])),
      ElMessageBox: { confirm: options.confirm || (async () => true) } }
  }
  const descriptor = parse(await readFile(new URL('./ExecutionMonitor.vue', import.meta.url), 'utf8')).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: 'monitor-route-test' }).errors, [])
  const componentWindow = { location: { protocol: 'http:', host: 'offline.test' }, addEventListener() {}, removeEventListener() {} }
  const modules = []
  const compile = descriptor => compileScript(descriptor, { id: 'monitor-route-test' }).content.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(dependencies[name] || {}) - 1
    if (binding.trim().startsWith('* as ')) return `const ${binding.trim().slice(5)} = __modules[${index}];\n`
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
  }).replace('export default', 'return')
  const component = new Function('__modules', 'WebSocket', 'window', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', compile(descriptor))(modules, Socket,
    componentWindow, (fn, delay) => addTimer(fn, delay, true), id => timers.delete(id),
    (fn, delay) => addTimer(fn, delay, false), id => timers.delete(id))
  let chartComponent
  if (options.realChart) {
    const chartDescriptor = parse(await readFile(new URL('./components/RealtimeChart.vue', import.meta.url), 'utf8')).descriptor
    const compiledChart = new Function('__modules', 'window', compile(chartDescriptor))(modules, componentWindow)
    chartComponent = { ...compiledChart, setup(props, context) { const chartState = compiledChart.setup(props, context); return () => Vue.h('div', { ref: chartState.chartRef }) } }
  }
  const chartKey = descriptor.template.content.match(/<RealtimeChart[^>]*:key="([^"]+)"/)?.[1]
  let state, mounts = 0
  const wrapped = { ...component, setup(props, context) { mounts++; state = component.setup(props, context); return state }, render: () => options.realChart
    ? Vue.h(chartComponent, { ref: state.chartRef, key: state[chartKey]?.value, preserveMissing: state.isK6.value, rateLabel: state.isK6.value ? '业务 RPS' : 'TPS' }) : null }
  const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
  const router = Router.createRouter({ history: Router.createMemoryHistory(), routes: [
    { path: '/performance-testing/executions/:id/monitor', component: wrapped },
    { path: '/performance-testing/executions/:id', component: { render: () => null } }
  ] })
  await router.push('/performance-testing/executions/41/monitor')
  const app = renderer.createApp({ render: () => Vue.h(Router.RouterView) }); app.use(router); app.mount({})
  if (!options.realChart) state.chartRef.value = { push: row => pushed.push(row), reset() { resets++; pushed.length = 0 } }
  let unmounted = false
  return { state, calls, messages, stops, sockets, timers, pushed, charts, router, get mounts() { return mounts }, get resets() { return resets },
    navigate: async id => { await router.push(`/performance-testing/executions/${id}/monitor`); await settle() },
    unmount() { if (!unmounted) { app.unmount(); unmounted = true } }
  }
}

test('reused monitor follows route 41 → 45 and browser history, resets samples/cursor and targets current report/stop', async () => {
  const h = await harness()
  try {
    await settle(); assert.equal(h.state.execution.id, 41); assert.equal(h.state.latest.value.tps, 41)
    await h.navigate(45)
    assert.equal(h.mounts, 1); assert.equal(h.state.execution.id, 45); assert.equal(h.state.latest.value.tps, 45)
    assert.equal(h.state.runLog.value, 'log-45'); assert.equal(h.state.liveStepStats.value[0].step_id, 45)
    assert.ok(h.calls.some(call => call[0] === 'samples' && call[1] === 45 && call[2] === 0))
    assert.deepEqual(h.pushed.map(row => row.id), [4500]); assert.equal(h.timers.size, 2)
    h.router.back(); await settle(); assert.equal(h.state.execution.id, 41)
    h.router.forward(); await settle(); assert.equal(h.state.execution.id, 45)
    await h.state.handleStop(); assert.deepEqual(h.stops, [45])
    h.state.goReport(); await settle(); assert.equal(h.router.currentRoute.value.path, '/performance-testing/executions/45')
  } finally { h.unmount() }
})

test('stop confirmation begun on old route is cancelled even when user later confirms', async () => {
  const confirmation = deferred()
  const h = await harness({}, { confirm: () => confirmation.promise })
  try {
    await settle(); const stopping = h.state.handleStop(); await h.navigate(45)
    confirmation.resolve(true); await stopping
    assert.deepEqual(h.stops, []); assert.deepEqual(h.messages, [])
  } finally { h.unmount() }
})

for (const outcome of ['success', 'error']) {
  test(`stop request already sent keeps old identity and late ${outcome} cannot notify new route`, async () => {
    const stopped = deferred(), ids = []
    const h = await harness({ stopPerfExecution: id => { ids.push(id); return stopped.promise } })
    try {
      await settle(); const stopping = h.state.handleStop(); await settle(); await h.navigate(45)
      if (outcome === 'success') stopped.resolve({ data: {} }); else stopped.reject(new Error('old stop error'))
      await stopping; assert.deepEqual(ids, [41]); assert.deepEqual(h.messages, []); assert.equal(h.state.execution.id, 45)
    } finally { h.unmount() }
  })
}

test('pending navigation clears stale detail and blocks stop/report actions, failures expose retry', async () => {
  const pending = deferred(); let fail = true
  const h = await harness({ getPerfExecution: async id => id === 41 ? { data: { ...detail(id), old_only: true, error_message: 'old error' } } : fail ? pending.promise : { data: detail(id) } })
  try {
    await settle(); await h.navigate(45)
    assert.equal(h.state.execution.execution_no, ''); assert.equal(h.state.execution.old_only, undefined)
    assert.equal(h.state.runLog.value, ''); assert.deepEqual(h.state.latest.value, {}); assert.deepEqual(h.pushed, [])
    assert.equal(h.timers.size, 0); await h.state.handleStop(); h.state.goReport(); await settle()
    assert.deepEqual(h.stops, []); assert.match(h.router.currentRoute.value.path, /45\/monitor$/)
    pending.reject(new Error('current load failure')); await settle()
    assert.equal(h.state.loading.value, false); assert.ok(h.state.loadError.value)
    fail = false; await h.state.loadMonitor(); await settle(); assert.equal(h.state.execution.id, 45)
  } finally { h.unmount() }
})

for (const outcome of ['success', 'error']) {
  test(`late initial detail ${outcome} cannot overwrite new execution or restart old timers`, async () => {
    const old = deferred()
    const h = await harness({ getPerfExecution: id => id === 41 ? old.promise : Promise.resolve({ data: detail(id) }) })
    try {
      await settle(); await h.navigate(45)
      if (outcome === 'success') old.resolve({ data: detail(41) }); else old.reject(new Error('old detail failure'))
      await settle()
      assert.equal(h.state.execution.id, 45); assert.equal(h.state.latest.value.tps, 45); assert.equal(h.state.loading.value, false)
      assert.equal(h.timers.size, 2); assert.deepEqual(h.messages, [])
      assert.equal(h.calls.filter(call => call[0] === 'samples' && call[1] === 41).length, 0)
    } finally { h.unmount() }
  })
}

test('late poll pages cannot mutate new samples/cursor, finish new execution or clear its polling lock', async () => {
  const old = deferred(); let delayOld = false
  const reads = []
  const h = await harness({ getPerfRealtime: async (id, params) => {
    reads.push([id, params.after_id]); if (id === 41 && delayOld) return old.promise
    return { data: { status: 'RUNNING', samples: params.after_id ? [] : [sample(id)], next_after_id: id * 100 } }
  } })
  try {
    await settle(); delayOld = true; const polling = h.state.pollOnce(); await h.navigate(45)
    old.resolve({ data: { status: 'COMPLETED', samples: [{ ...sample(41), id: 99999 }], next_after_id: 99999, has_more: true } })
    await polling; await settle(); await h.state.pollOnce()
    assert.equal(h.state.execution.id, 45); assert.equal(h.state.execution.status, 'RUNNING'); assert.equal(h.state.latest.value.tps, 45)
    assert.deepEqual(h.pushed.map(row => row.id), [4500]); assert.deepEqual(reads.at(-1), [45, 4500])
    assert.equal(reads.filter(([id, after]) => id === 41 && after === 99999).length, 0)
    assert.deepEqual(h.messages, []); assert.equal(h.timers.size, 2)
  } finally { h.unmount() }
})

test('late log/SLA and history stats never replace current route detail or errors', async () => {
  const log = deferred(), slaConfig = deferred(), stats = deferred()
  const h = await harness({ getPerfRunLog: id => id === 41 ? log.promise : Promise.resolve({ data: { content: `log-${id}` } }),
    getPerfScenario: id => id === 41 ? slaConfig.promise : Promise.resolve({ data: { sla_config: { enabled: true, thresholds: { min_tps: id } } } }),
    getPerfRequestStats: id => id === 41 ? stats.promise : Promise.resolve({ data: [{ step: id }] }) }, { engine: 'BUILTIN' })
  try {
    await settle(); const oldStats = h.state.fetchRequestStats(); await h.navigate(45)
    log.resolve({ data: { content: 'old log' } }); slaConfig.resolve({ data: { sla_config: { enabled: true, thresholds: { min_tps: 999 } } } }); stats.reject(new Error('old stats failed'))
    await oldStats; await settle()
    assert.equal(h.state.runLog.value, 'log-45'); assert.deepEqual(h.state.slaThresholds.value, { min_tps: 45 })
    assert.deepEqual(h.state.requestStats.value, []); assert.equal(h.state.requestStatsState.value, 'pending')
  } finally { h.unmount() }
})

test('old WebSocket callbacks, watchdog and reconnect cannot touch new route or create extra transport', async () => {
  const h = await harness({}, { websocket: true, engine: 'BUILTIN' })
  try {
    await settle(); const old = h.sockets[0]; old.onopen()
    const stale = { open: old.onopen, message: old.onmessage, close: old.onclose, error: old.onerror }
    const oldTimers = [...h.timers.values()].map(timer => timer.fn)
    await h.navigate(45); assert.equal(old.closed, true)
    const current = h.sockets[1]; current.onopen()
    stale.open(); stale.message({ data: JSON.stringify({ status: 'COMPLETED', sample: sample(41), summary: { old: true } }) }); stale.error(); stale.close()
    for (const callback of oldTimers) await callback()
    await settle()
    assert.equal(h.sockets.length, 2); assert.equal(current.closed, false); assert.equal(h.state.execution.id, 45)
    assert.equal(h.state.execution.status, 'RUNNING'); assert.equal(h.state.channel.value, 'ws'); assert.equal(h.state.latest.value.tps, 45)
    assert.deepEqual(h.messages, [])
  } finally { h.unmount() }
})

test('late finish refresh cannot show old completion notification or fetch old log/stat for new route', async () => {
  const terminal = deferred(); let count = 0
  const h = await harness({ getPerfExecution: async id => id === 41 && ++count > 1 ? terminal.promise : { data: detail(id) } })
  try {
    await settle(); const finishing = h.state.onFinished(); await h.navigate(45)
    terminal.resolve({ data: detail(41, 'COMPLETED') }); await finishing; await settle()
    assert.equal(h.state.execution.id, 45); assert.equal(h.state.execution.status, 'RUNNING'); assert.deepEqual(h.messages, [])
    assert.equal(h.timers.size, 2)
  } finally { h.unmount() }
})

test('historical monitor replays each route once, resets cursor and does not start live transports', async () => {
  const h = await harness({}, { status: 'COMPLETED' })
  try {
    await settle(); await h.navigate(45)
    assert.equal(h.state.execution.id, 45); assert.equal(h.state.requestStats.value[0].step, 45)
    assert.deepEqual(h.pushed.map(row => row.id), [4500]); assert.equal(h.state.progress.value, 100)
    assert.equal(h.timers.size, 0); assert.equal(h.sockets.length, 0); assert.equal(h.state.channel.value, 'closed')
  } finally { h.unmount() }
})

test('unmount during initial load never opens transports or populates stale execution', async () => {
  const old = deferred()
  const h = await harness({ getPerfExecution: () => old.promise })
  await settle(); h.unmount(); old.resolve({ data: detail(41) }); await settle()
  assert.equal(h.state.execution.execution_no, ''); assert.equal(h.timers.size, 0); assert.equal(h.sockets.length, 0)
})

test('unmount clears WebSocket watchdog/retry/log timers and invalidates pending stop confirmation', async () => {
  const confirmation = deferred()
  const h = await harness({}, { websocket: true, engine: 'BUILTIN', confirm: () => confirmation.promise })
  await settle(); const ws = h.sockets[0]; ws.onopen(); ws.onclose()
  const callbacks = [...h.timers.values()].map(timer => timer.fn), stopping = h.state.handleStop()
  h.unmount(); confirmation.resolve(true); await stopping
  for (const callback of callbacks) await callback()
  await settle(); assert.equal(h.timers.size, 0); assert.deepEqual(h.stops, []); assert.equal(h.sockets.length, 1)
})

test('invalid monitor ID does not send requests or enable actions', async () => {
  const h = await harness()
  try {
    await settle(); const before = h.calls.length; await h.navigate('invalid')
    assert.equal(h.state.execution.execution_no, ''); assert.equal(h.state.loading.value, false); assert.ok(h.state.loadError.value)
    assert.equal(h.calls.length, before); await h.state.handleStop(); assert.deepEqual(h.stops, [])
    assert.equal(h.timers.size, 0)
  } finally { h.unmount() }
})

test('actual realtime chart keeps complete configuration and only current samples after route switch and retry', async () => {
  const h = await harness({}, { realChart: true, engine: 'BUILTIN' })
  try {
    await settle()
    await h.navigate(45)
    const current = h.charts.at(-1)
    assert.ok(current.options.some(option => option.series?.[0]?.type === 'line'))
    assert.deepEqual(current.options.at(-1).series[0].data, [45])
    await h.state.loadMonitor(); await settle()
    assert.deepEqual(h.charts.at(-1).options.at(-1).series[0].data, [45])
    assert.ok(h.charts.at(-1).options.some(option => option.series?.[0]?.type === 'line'))
  } finally { h.unmount() }
})
