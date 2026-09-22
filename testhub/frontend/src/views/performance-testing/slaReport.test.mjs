import test from 'node:test'
import assert from 'node:assert/strict'
import { buildK6InterfaceRows, buildK6DebugRows } from './k6InterfaceStats.mjs'
import { observedMetrics, metricValue, slaResult, slaScope } from './slaReport.mjs'
import { nullableLineOptions } from './nullableLine.mjs'

test('same-name steps retain independent stable identities, counts, latency and failures', () => {
  const steps = [{ id: 11, name: 'same', method: 'GET' }, { id: 12, name: 'same', method: 'GET' }]
  const rows = buildK6InterfaceRows({ steps, stats: [
    { step_id: 12, step_name: 'same', total: 6, success: 0, failed: 6, avg_rt: 30, p95_rt: 40 },
    { step_id: 11, step_name: 'same', total: 6, success: 6, failed: 0, avg_rt: 1, p95_rt: 2 }
  ], errorTop: [{ sample_step: 'same', type: 'AssertionFailed', count: 6 }] })
  assert.deepEqual(rows.map(r => [r.stepId, r.failed, r.p95_rt]), [[11, 0, 2], [12, 6, 40]])
  assert.doesNotMatch(rows[1].failureReason, /断言/)
  assert.equal(new Set(rows.map(r => r.key)).size, 2)
})
test('ambiguous legacy raw identifiers never fabricate attribution', () => {
  const rows = buildK6InterfaceRows({ steps: [{ id: 2, name: 'same', method: 'GET' }, { name: 'same', method: 'GET' }],
    stats: [{ url: 'step:2', step_name: 'same', method: 'GET', total: 9, failed: 1 }] })
  assert.deepEqual(rows.slice(0, 2).map(r => r.total), [null, null])
  assert.equal(rows[2].total, 9)
  assert.match(rows[2].attribution, /不明确/)
})
test('zero requests are preserved while absent latency and error observations stay unavailable', () => {
  const s = observedMetrics({ business_total: 0, avg_rt: 0, p95_rt: 0, error_rate: 0 }, true)
  assert.equal(s.business_total, 0)
  assert.equal(s.avg_rt, null)
  assert.equal(metricValue(s.error_rate), '未采集')
  assert.equal(metricValue(0), '0.00')
  assert.equal(metricValue(Infinity), '未采集')
  assert.equal(slaResult({ passed: null }), '未评估')
  assert.equal(slaResult({ passed: false }), '未通过')
  assert.match(slaScope({ scope: 'step', step_id: 12, step_name: 'same' }), /#12/)
})
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'

async function component(file) {
  const descriptor = parse(await readFile(new URL(file, import.meta.url), 'utf8')).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: file }).errors, [])
  const source = compileScript(descriptor, { id: file }).content
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = Vue`)
    .replace(/import \* as echarts from ['"]echarts['"]/g, 'const echarts = {}')
    .replace(/import \{ nullableLineOptions \} from ['"][^'"]+['"]/g, '')
    .replace('export default', 'return')
  return new Function('Vue', 'nullableLineOptions', source)(Vue, nullableLineOptions)
}
test('actual SLA form retains strict zero rules, filters disabled/setup/unsaved steps and emits changes', async () => {
  const form = await component('./components/SlaRuleEditor.vue')
  const model = Vue.reactive({ thresholds: { error_rate: 60 }, step_thresholds: [{ step_id: 11, thresholds: { error_rate: 0 } }], abort_delay: 5 })
  const events = []
  const state = form.setup({ modelValue: model, steps: [{ id: 11, name: 'same' }, { id: 12, name: 'same' },
    { id: 13, enabled: false }, { id: 14, is_setup: true }, { name: 'unsaved' }] }, { expose() {}, emit: (...args) => events.push(args) })
  assert.deepEqual(state.selectable.value.map(s => s.id), [11, 12])
  state.model.value.step_thresholds.push({ step_id: 12, thresholds: { error_rate: 0 } })
  state.changed()
  assert.deepEqual(JSON.parse(JSON.stringify(model)).step_thresholds.map(r => r.thresholds.error_rate), [0, 0])
  assert.equal(model.abort_delay, 5)
  assert.equal(events[0][0], 'change')
})

test('actual realtime chart preserves K6 missing observations and retains legacy zero behavior', async t => {
  const previousWindow = globalThis.window
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  t.after(() => { globalThis.window = previousWindow })
  const chart = await component('./components/RealtimeChart.vue')
  const renderer = Vue.createRenderer({ createComment: text => ({ text }), insert() {}, remove() {}, parentNode() {}, nextSibling() {} })
  for (const preserveMissing of [false, true]) {
    let state
    const instance = { ...chart, setup(props, context) { state = chart.setup(props, context); return state }, render: () => null }
    const container = {}
    renderer.render(Vue.h(instance, { preserveMissing }), container)
    state.push({ ts_offset: 1, tps: 0, avg_rt: null, p95_rt: null, error_rate: null })
    assert.deepEqual(state.avgRt, [preserveMissing ? null : 0])
    assert.deepEqual(state.tps, [0])
    renderer.render(null, container)
  }
})


test('actual debug title binds same-name and pending diagnostic stable IDs', async () => {
  const source = await readFile(new URL('./ExecutionReport.vue', import.meta.url), 'utf8')
  const block = source.slice(source.indexOf('<el-collapse-item v-for="row in k6DebugRows"'))
  const title = block.slice(0, block.indexOf('</template>'))
  assert.match(source.slice(0, source.indexOf('<el-collapse-item v-for="row in k6DebugRows"')), /row\.stepId/)
  const property = title.match(/#\{\{ row\.(\w+) \}\}/)[1]
  const rows = buildK6DebugRows({ steps: [{ step_id: 114, outcome: 'passed' }] },
    [{ id: 114, name: 'same' }, { id: 115, name: 'same' }])
  assert.deepEqual(rows.map(row => row[property]), [114, 115])
  assert.equal(rows[1].outcome, 'pending')
})

test('actual frozen-step enabled column distinguishes authentication policy and missing state', async () => {
  const source = await readFile(new URL('./ExecutionReport.vue', import.meta.url), 'utf8')
  const start = source.indexOf('<el-table-column :label="t(\'performanceTesting.editor.stepEnabled\')"')
  assert.notEqual(start, -1)
  const block = source.slice(start, source.indexOf('</el-table-column>', start))
  const expression = block.match(/\{\{\s*([\s\S]*?)\s*\}\}/)[1]
  const label = new Function('row', 'isK6', 't', `return (${expression})`)
  const t = key => key.endsWith('.yes') ? '是' : '否'
  assert.equal(label({ auth_phase: 'login' }, true, t), '按认证策略调用')
  assert.equal(label({ auth_phase: 'refresh' }, true, t), '按认证策略调用')
  assert.equal(label({}, true, t), '未记录')
  assert.equal(label({ enabled: true }, true, t), '是')
  assert.equal(label({ enabled: false }, true, t), '否')
  assert.equal(label({ enabled: true }, false, t), '是')
})
