import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as Router from 'vue-router'
import * as sla from './slaReport.mjs'
import * as cursor from './sampleCursor.mjs'
import * as interfaces from './k6InterfaceStats.mjs'
import * as websocketMetrics from './websocketMetrics.mjs'
import * as lines from './nullableLine.mjs'

const helper = async () => {
  const module = await import('./nativeVuObservations.mjs').catch(() => null)
  assert.ok(module, 'Native VU projection must exist independently of script active_users')
  return module
}
const point = (seq = 1, start = 1000, value = 2, extra = {}) => ({ version: 'native_vu_v1', source: 'k6_rest_status_v1',
  execution_id: 41, runner_instance: 'a'.repeat(32), observation_seq: seq, request_start_offset_ms: start,
  request_end_offset_ms: start + 100, rtt_ms: 100, timestamp_kind: 'collector_request_interval', result: 'ok',
  active_vus: value, initialized_vus: 2, status: 7, running: true, paused: false, stopped: false, ...extra })
const summary = extra => ({ version: 'native_vu_v1', source: 'k6_rest_status_v1', execution_id: 41,
  evidence_scope: 'collector_received', attempt_count: 8, valid_count: 7, missing_count: 1,
  persistence_verified: null, sustained_concurrency_verified: null, ...extra })
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes }); return { promise, resolve } }

test('native timestamps use request midpoint, preserve zero, and never use rounded adapter seconds or script counts', async () => {
  const m = await helper()
  const rows = m.nativeVuFromSamples([{ ts_offset: 99, active_users: 999, native_vu_observations: [point(1, 1050, 0)] }], 41)
  const model = m.projectNativeVu(rows, 41)
  assert.deepEqual(model.points.map(item => item.value), [[1.1, 0]])
  assert.equal(model.latest, 0)
  assert.deepEqual(model.points[0].interval, [1050, 1150])
})

test('missing observation is a gap and the latest missing value never forwards the last known count', async () => {
  const m = await helper()
  const rows = [point(), point(2, 2000, 2, { result: 'timeout', active_vus: null }), point(3, 3000)]
  assert.deepEqual(m.projectNativeVu(rows, 41).points.map(item => item.value), [[1.05, 2], [2.05, null], [3.05, 2]])
  assert.equal(m.projectNativeVu(rows.slice(0, 2), 41).latest, null)
})

for (const boundary of ['sequence', 'runner', 'clock', 'gap', 'late', 'paused']) {
  test(`${boundary} boundary breaks the native line without dropping valid observations`, async () => {
    const m = await helper()
    const changed = { sequence: point(3, 2000), runner: point(2, 2000, 2, { runner_instance: 'b'.repeat(32) }),
      clock: point(2, 900), gap: point(2, 5000), late: point(2, 2000, 1, { request_end_offset_ms: 2900, rtt_ms: 900 }),
      paused: point(2, 2000, 0, { paused: true }) }[boundary]
    const model = m.projectNativeVu([point(), changed, point(4, 6000)], 41)
    assert.equal(model.points.filter(item => item.value[1] !== null).length, 3)
    assert.ok(model.points.some(item => item.value[1] === null))
  })
}

test('projection rejects wrong executions, unsupported versions, invalid values and foreign summary identity', async () => {
  const m = await helper()
  const invalid = [point(1, 1000, 2, { execution_id: 45 }), point(1, 1000, 2, { version: 'other' }),
    point(1, 1000, true), point(1, 1000, '2'), point(1, 1000, -1), point(1, 1000, null),
    point(1, 1000, 2, { request_end_offset_ms: 900 }), point(1, 1000, 2, { runner_instance: 'unknown' })]
  assert.deepEqual(m.appendNativeVu([], invalid, 41), [])
  assert.equal(m.nativeVuEvidence(summary({ execution_id: 45 }), 41).received, null)
})

test('duplicate samples do not double points and monitoring keeps a bounded recent window', async () => {
  const m = await helper()
  const rows = Array.from({ length: m.NATIVE_VU_DISPLAY_LIMIT + 10 }, (_, i) => point(i + 1, i * 1000))
  const kept = m.appendNativeVu([], rows, 41)
  assert.equal(kept.length, m.NATIVE_VU_DISPLAY_LIMIT); assert.equal(kept[0].observation_seq, 11)
  assert.deepEqual(m.appendNativeVu(kept, rows.slice(-5), 41), kept)
})

test('a delayed polling batch cannot replace a newer native observation already received by WebSocket', async () => {
  const m = await helper()
  const rows = m.appendNativeVu([point(1, 1000), point(4, 4000, 0)], [point(2, 2000), point(3, 3000)], 41)
  assert.deepEqual(rows.map(row => row.observation_seq), [1, 2, 3, 4])
  assert.equal(m.projectNativeVu(rows, 41).latest, 0)
  const current = Array.from({ length: m.NATIVE_VU_DISPLAY_LIMIT }, (_, i) => point(i + 10, (i + 10) * 1000))
  assert.deepEqual(m.appendNativeVu(current, [point()], 41), m.appendNativeVu([], current, 41))
})

test('mixed runners never assert a latest value when a previously unseen runner arrives late', async () => {
  const m = await helper()
  const current = point(2, 5000, 9, { runner_instance: 'b'.repeat(32) })
  const rows = m.appendNativeVu([current], [point(1, 1000, 1)], 41)
  const model = m.projectNativeVu(rows, 41)
  assert.equal(model.latest, null)
  assert.equal(model.mixedRunners, true)
  assert.equal(model.points.filter(item => item.value[1] !== null).length, 2)
  assert.ok(model.points.some(item => item.value[1] === null), 'each runner remains a separate segment')
})

test('a late unseen runner cannot evict the current full window; mixed identity remains explicit after trimming', async () => {
  const m = await helper()
  const current = Array.from({ length: m.NATIVE_VU_DISPLAY_LIMIT }, (_, i) =>
    point(i + 1, (i + 5) * 1000, 9, { runner_instance: 'b'.repeat(32) }))
  const rows = m.appendNativeVu(current, [point(1, 1000, 1)], 41)
  assert.deepEqual(rows.map(row => [row.runner_instance, row.observation_seq]),
    current.map(row => [row.runner_instance, row.observation_seq]))
  assert.equal(m.projectNativeVu(rows, 41).latest, null)
  const advanced = m.appendNativeVu(rows, [point(m.NATIVE_VU_DISPLAY_LIMIT + 1, 3000000, 0, { runner_instance: 'b'.repeat(32) })], 41)
  assert.equal(advanced.length, m.NATIVE_VU_DISPLAY_LIMIT)
  assert.equal(advanced[0].observation_seq, 2)
  assert.equal(m.projectNativeVu(advanced, 41).mixedRunners, true)
  const nextExecution = m.appendNativeVu(advanced, [point(1, 1000, 0, { execution_id: 45 })], 45)
  assert.equal(m.projectNativeVu(nextExecution, 45).mixedRunners, false)
  assert.equal(m.projectNativeVu(nextExecution, 45).latest, 0)
})

test('an incoming mixed batch retains uncertainty even if its older instance is outside the display bound', async () => {
  const m = await helper()
  const rows = [point(1, 1000, 1), ...Array.from({ length: m.NATIVE_VU_DISPLAY_LIMIT }, (_, i) =>
    point(i + 1, (i + 5) * 1000, 9, { runner_instance: 'b'.repeat(32) }))]
  const model = m.projectNativeVu(rows, 41)
  assert.equal(model.count, m.NATIVE_VU_DISPLAY_LIMIT)
  assert.equal(model.latest, null)
  assert.equal(model.mixedRunners, true)
})

test('later batches from an already-seen foreign runner cannot grow at the expense of another runner window', async () => {
  const m = await helper()
  const current = Array.from({ length: m.NATIVE_VU_DISPLAY_LIMIT - 1 }, (_, i) =>
    point(i + 1, (i + 5) * 1000, 9, { runner_instance: 'b'.repeat(32) }))
  const mixed = m.appendNativeVu(current, [point(1, 1000, 1)], 41)
  const expanded = m.appendNativeVu(mixed, Array.from({ length: 20 }, (_, i) => point(i + 2, (i + 2) * 1000, 1)), 41)
  assert.deepEqual(expanded.filter(row => row.runner_instance === 'b'.repeat(32)).map(row => row.observation_seq),
    current.map(row => row.observation_seq))
  assert.equal(expanded.filter(row => row.runner_instance === 'a'.repeat(32)).length, 1)
  assert.equal(expanded.at(-1).observation_seq, 21)
  assert.equal(m.projectNativeVu(expanded, 41).latest, null)
})

test('missing results discard any stale numeric value and protocol validation rejects invalid timing or state', async () => {
  const m = await helper()
  assert.equal(m.projectNativeVu([point(1, 1000, 99, { result: 'timeout' })], 41).latest, null)
  assert.deepEqual(m.appendNativeVu([], [point(1, 1000, 2, { rtt_ms: 99 }), point(1, 1000, 2, { running: 1 }),
    point(1, 1000, 2, { observation_seq: Number.MAX_SAFE_INTEGER + 1 })], 41), [])
  assert.equal(m.nativeVuEvidence(summary({ attempt_count: -1 }), 41).received, null)
  assert.equal(m.nativeVuEvidence({ version: 'native_vu_v1', source: 'k6_rest_status_v1', reason: 'unsupported_runner' }, 41).reason, 'unsupported_runner')
})

test('historical and unsupported native records stay unknown, received summary does not become persistence proof', async () => {
  const m = await helper()
  assert.deepEqual(m.nativeVuFromSamples([{ active_users: 800, distinct_vus: 1000 }], 41), [])
  assert.equal(m.projectNativeVu([], 41).latest, null)
  const unknown = m.nativeVuEvidence(null, 41)
  assert.equal(unknown.received, null); assert.equal(unknown.persistence, '未验证')
  const received = m.nativeVuEvidence(summary(), 41)
  assert.equal(received.received, 8); assert.equal(received.persistence, '未验证')
  assert.equal(received.sustained, '未验证')
})

const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
async function compile(file, dependencies) {
  const descriptor = parse(await readFile(new URL(file, import.meta.url), 'utf8')).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: file }).errors, [])
  const modules = []
  const code = compileScript(descriptor, { id: file }).content.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(dependencies[name] || {}) - 1
    if (binding.trim().startsWith('* as ')) return `const ${binding.trim().slice(5)} = __modules[${index}];\n`
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
  }).replace('export default', 'return')
  return new Function('__modules', 'window', 'setInterval', 'clearInterval', code)(modules,
    { addEventListener() {}, removeEventListener() {} }, () => 1, () => {})
}

test('actual shared component clears the old chart and preserves explicit unknown evidence on prop changes', async () => {
  const m = await helper(); const options = [], charts = []
  const component = await compile('./components/NativeVUObservations.vue', { vue: Vue, '../nativeVuObservations.mjs': m,
    echarts: { init: () => { const chart = { setOption: option => options.push(option), dispose() { this.disposed = true }, resize() {} }; charts.push(chart); return chart } } })
  let state
  const wrapped = { ...component, setup(props, context) { state = component.setup(props, context); return state }, render: () => null }
  const container = {}
  const update = props => renderer.render(Vue.h(wrapped, props), container)
  try {
    update({ executionId: 41, observations: [point(1, 1000, 0)], summary: summary(), mode: 'report' })
    state.chartRef.value = {}; await settle(); state.renderChart()
    assert.equal(state.model.value.latest, 0); assert.equal(state.evidence.value.persistence, '未验证')
    assert.match(state.displayNotice.value, /1000/); assert.match(state.displayNotice.value, /全程/)
    assert.equal(options.at(-1).series[0].connectNulls, false)
    update({ executionId: 41, observations: [point(), point(1, 5000, 9, { runner_instance: 'b'.repeat(32) })], summary: summary(), mode: 'report' })
    await settle()
    assert.equal(state.model.value.mixedRunners, true)
    assert.equal(state.model.value.latest, null)
    update({ executionId: 45, observations: [point()], summary: summary(), mode: 'report' }); await settle()
    assert.equal(state.model.value.latest, null); assert.deepEqual(options.at(-1).series[0].data, [])
    assert.equal(state.model.value.mixedRunners, false)
    assert.match(state.emptyNotice.value, /未采集|未取得/)
  } finally { renderer.render(null, container) }
  assert.equal(charts[0].disposed, true)
  state.renderChart()
  assert.equal(charts.length, 1, 'queued chart rendering after unmount must not recreate a chart')
})

for (const page of ['ExecutionReport', 'ExecutionMonitor']) {
  test(`actual ${page} resets native observations on route changes and discards old responses`, async () => {
    const m = await helper(); const old = deferred(); let delayed = false
    const nativeRows = id => [point(1, 1000, id, { execution_id: id }),
      ...(id === 41 ? [point(1, 5000, 9, { runner_instance: 'b'.repeat(32) })] : [])]
    const api = {
      getPerfExecution: async id => ({ data: { id, execution_no: `run-${id}`, status: page === 'ExecutionMonitor' ? 'RUNNING' : 'COMPLETED',
        load_snapshot: { _engine: 'K6', concurrency: 2 }, summary: { native_vu: summary({ execution_id: id }) }, report_url: 'ready' } }),
      getPerfSamples: async id => delayed && id === 41 ? old.promise : { data: { samples: [{ native_vu_observations: nativeRows(id) }] } },
      getPerfRealtime: async (id, params) => delayed && id === 41 ? old.promise : { data: { status: 'RUNNING', samples: params.after_id ? [] : [{ id: 1, native_vu_observations: nativeRows(id) }], next_after_id: 1 } },
      getPerfRunLog: async () => ({ data: {} }), getPerfRequestStats: async () => ({ data: [] }),
      getEngineStatus: async () => ({ data: {} }), compareWithBaseline: async () => ({ data: {} })
    }
    const component = await compile(`./${page}.vue`, { vue: Vue, 'vue-router': Router, './nativeVuObservations.mjs': m,
      './sampleCursor.mjs': cursor, './slaReport.mjs': sla, './k6InterfaceStats.mjs': interfaces, './websocketMetrics.mjs': websocketMetrics, './nullableLine.mjs': lines,
      '@/api/performance-testing': api, './shared': { apiError: (_, fallback) => fallback },
      'vue-i18n': { useI18n: () => ({ t: key => key }) }, 'element-plus': { ElMessage: { info() {}, error() {}, success() {} } } })
    let state
    const wrapped = { ...component, setup(props, context) { state = component.setup(props, context); return state }, render: () => null }
    const router = Router.createRouter({ history: Router.createMemoryHistory(), routes: [{ path: '/runs/:id', component: wrapped }] })
    await router.push('/runs/41')
    const app = renderer.createApp({ render: () => Vue.h(Router.RouterView) }); app.use(router); app.mount({})
    try {
      await settle(); assert.equal(state.nativeObservations.value[0].execution_id, 41)
      assert.equal(m.projectNativeVu(state.nativeObservations.value, 41).mixedRunners, true)
      delayed = true; const pending = page === 'ExecutionMonitor' ? state.pollOnce() : state.loadAll()
      await router.push('/runs/45'); await settle()
      assert.equal(state.nativeObservations.value.length, 1); assert.equal(state.nativeObservations.value[0].execution_id, 45)
      assert.equal(m.projectNativeVu(state.nativeObservations.value, 45).mixedRunners, false)
      old.resolve({ data: { status: 'RUNNING', samples: [{ id: 2, native_vu_observations: [point(2, 2000)] }], next_after_id: 2 } })
      await pending; await settle()
      assert.equal(state.nativeObservations.value.length, 1); assert.equal(state.nativeObservations.value[0].execution_id, 45)
    } finally { app.unmount() }
  })
}

test('existing realtime chart keeps default label and accepts explicit K6 script activity label', async () => {
  const component = await compile('./components/RealtimeChart.vue', { vue: Vue, '../nullableLine.mjs': lines })
  assert.equal(component.props.activeUsersLabel.default, '并发')
  const descriptor = parse(await readFile(new URL('./components/RealtimeChart.vue', import.meta.url), 'utf8')).descriptor
  assert.match(descriptor.scriptSetup.content, /name: props\.activeUsersLabel/)
})
