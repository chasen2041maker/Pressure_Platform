import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import { compile } from '@vue/compiler-dom'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'
import * as echarts from 'echarts'
import { nullableLineOptions } from './nullableLine.mjs'
import { observedMetrics } from './slaReport.mjs'

function reportOptions(values, k6 = true) {
  const source = readFileSync(new URL('./ExecutionReport.vue', import.meta.url), 'utf8')
  const render = source.slice(source.indexOf('function renderCharts()'), source.indexOf('function resizeCharts()'))
  const captured = []
  const rows = values.map((value, i) => ({ ts_offset: i + 1, business_total: 5, window_count: value == null ? 0 : 1,
    tps: value, active_users: 7, avg_rt: value, p90_rt: value, p95_rt: value, p99_rt: value, error_rate: value }))
  new Function('echarts', 'observedMetrics', 'samples', 'isK6', 'nullableLineOptions', `
    let tpsChart,rtChart,errChart;
    const tpsChartRef={value:1},rtChartRef={value:2},errChartRef={value:3};
    const rateLabel={value:'RPS'},t=k=>k,AXIS_BASE={};
    const xAxisData=()=>samples.value.map(s=>s.ts_offset);
    ${render};renderCharts();
  `)({ init: () => ({ setOption: option => captured.push(option) }) }, observedMetrics, { value: rows }, { value: k6 }, nullableLineOptions)
  return captured
}

function svgEvidence(option, index = 0) {
  const selected = option.series[index]
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 300 })
  chart.setOption({ ...option, animation: false, legend: { show: false },
    series: [{ ...selected, yAxisIndex: 0 }], yAxis: { type: 'value' } })
  const svg = chart.renderToSVGString()
  chart.dispose()
  const paths = [...svg.matchAll(/<path\b[^>]*>/g)].map(match => match[0])
  const markers = paths.filter(path => path.includes('ecmeta_data_index=') && !/matrix\(0[, ]/.test(path))
  const line = paths.filter(path => path.includes('pointer-events="visible"') && path.includes('fill="none"') && path.includes(`stroke="${selected.itemStyle.color}"`))
    .map(path => path.match(/ d="([^"]*)"/)?.[1] || '').join(' ')
  return { markers: markers.map(path => Number(path.match(/ecmeta_data_index="(\d+)"/)[1])), line }
}

const cases = [
  ['middle', [null, 50, null], [1]], ['zero', [null, 0, null], [1]],
  ['first', [0, null, null], [0]], ['last', [null, null, 10], [2]],
  ['single', [0], [0]], ['separated', [2, null, 3], [0, 2]], ['empty', [null, null, null], []]
]
for (const [name, values, indices] of cases) {
  test(`actual report SVG displays ${name} observations without inventing a segment`, () => {
    const options = reportOptions(values)
    for (const [chartIndex, seriesCount] of [[0, 1], [1, 4], [2, 1]]) {
      for (let seriesIndex = 0; seriesIndex < seriesCount; seriesIndex++) {
        const rendered = svgEvidence(options[chartIndex], seriesIndex)
        assert.deepEqual(rendered.markers, indices, `chart ${chartIndex}, series ${seriesIndex}`)
        assert.doesNotMatch(rendered.line, /[LC]/, 'missing windows must not acquire line segments')
      }
    }
  })
}
test('report keeps adjacent lines uncluttered and legacy step behavior unchanged', () => {
  const options = reportOptions([1, 2, 3])
  assert.deepEqual(svgEvidence(options[1]).markers, [])
  assert.match(svgEvidence(options[1]).line, /[LC]/)
  const legacy = reportOptions([null, 50, null], false)
  assert.deepEqual(svgEvidence(legacy[1]).markers, [])
  assert.match(svgEvidence(legacy[2]).line, /L/)
  assert.deepEqual(options[0].series[1].data, [7, 7, 7])
})

function realtime(preserveMissing) {
  const descriptor = parse(readFileSync(new URL('./components/RealtimeChart.vue', import.meta.url), 'utf8')).descriptor
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 300 })
  const source = compileScript(descriptor, { id: 'nullable-test' }).content
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = Vue`)
    .replace(/import\s+\*\s+as\s+echarts\s+from\s+['"]echarts['"]/, 'const echarts = deps.echarts')
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"][^'"]+['"]/g, (_, names) => `const {${names}} = deps`)
    .replace('export default', 'return')
  const component = new Function('Vue', 'deps', source)({ ...Vue, onMounted() {}, onUnmounted() {} },
    { nullableLineOptions, echarts: { init: () => chart } })
  const state = component.setup({ preserveMissing, maxPoints: 3, rateLabel: 'RPS' }, { expose() {} })
  state.chartRef.value = {}
  return { state, chart }
}
test('actual realtime push/trim/reset honors null gaps and recalculates isolated symbols', () => {
  const { state, chart } = realtime(true)
  try {
    for (const value of [null, 0, null]) state.push({ tps: value, avg_rt: value, p95_rt: value, error_rate: value, active_users: 7 })
    for (const i of [0, 1, 2, 3]) assert.deepEqual(svgEvidence(chart.getOption(), i).markers, [1])
    state.push({ tps: 5, avg_rt: 5, p95_rt: 5, error_rate: 5, active_users: 7 })
    assert.deepEqual(svgEvidence(chart.getOption(), 1).markers, [0, 2])
    state.push({ tps: 6, avg_rt: 6, p95_rt: 6, error_rate: 6, active_users: 7 })
    assert.deepEqual(svgEvidence(chart.getOption(), 1).markers, [])
    assert.match(svgEvidence(chart.getOption(), 1).line, /[LC]/)
    state.reset(); state.ensureChart(); state.push({ avg_rt: 0 })
    assert.deepEqual(svgEvidence(chart.getOption(), 1).markers, [0])
  } finally { chart.dispose() }
})
test('realtime legacy default retains zero-fill and no symbols', () => {
  const { state, chart } = realtime(false)
  try {
    for (const value of [null, 5, null]) state.push({ avg_rt: value })
    assert.deepEqual(chart.getOption().series[1].data, [0, 5, 0])
    assert.deepEqual(svgEvidence(chart.getOption(), 1).markers, [])
  } finally { chart.dispose() }
})

test('actual history rate cell renders frozen engine units without changing values', async () => {
  const source = readFileSync(new URL('./ExecutionList.vue', import.meta.url), 'utf8')
  const column = source.match(/<el-table-column label="TPS \/ 业务 RPS"[\s\S]*?<\/el-table-column>/)?.[0]
  assert.ok(column)
  const render = new Function('Vue', compile(column, { mode: 'function' }).code)(Vue)
  const fmtNum = new Function(source.match(/function fmtNum\(v\) \{[\s\S]*?\n\}/)[0] + ';return fmtNum')()
  for (const [engine, value, expected] of [['K6', 0, '0.0 RPS'], ['K6', 12.3, '12.3 RPS'], ['K6', null, '- RPS'], ['BUILTIN', 12.3, '12.3 TPS']]) {
    const columnStub = { props: ['label'], setup(props, { slots }) {
      return () => Vue.h('div', [Vue.h('h2', props.label), slots.default({ row: { engine, tps: value } })])
    } }
    const result = await renderToString(Vue.createSSRApp({ render, setup: () => ({ fmtNum }), components: { ElTableColumn: columnStub } }))
    assert.ok(result.includes('TPS / 业务 RPS')); assert.ok(result.includes(expected), result)
  }
})

test('actual baseline card describes speed tolerance without claiming better performance or business success', async () => {
  const source = readFileSync(new URL('./ExecutionReport.vue', import.meta.url), 'utf8')
  const heading = source.indexOf("t('performanceTesting.report.baselineCompare')")
  const start = source.lastIndexOf('<el-card', heading)
  const card = source.slice(start, source.indexOf('</el-card>', heading) + '</el-card>'.length)
  const render = new Function('Vue', compile(card, { mode: 'function' }).code)(Vue)
  const text = { 'performanceTesting.report.better': '优于基线', 'performanceTesting.report.worse': '劣于基线' }
  const stubs = {
    ElCard: { setup: (_, { slots }) => () => Vue.h('section', [slots.header?.(), slots.default?.()]) },
    ElTag: { setup: (_, { slots }) => () => Vue.h('span', slots.default?.()) },
    ElAlert: { props: ['title'], setup: props => () => Vue.h('p', props.title) },
    ElTable: { setup: () => () => Vue.h('table') },
    ElTableColumn: { setup: () => () => null },
    ElEmpty: { props: ['description'], setup: props => () => Vue.h('p', props.description) }
  }
  // These are API verdict inputs, not a second implementation of comparison thresholds.
  const cases = [
    { name: 'equal', degraded: false, avg: 100, rate: 100, error: 0, sla: 'PASSED' },
    { name: 'within tolerance slowdown', degraded: false, avg: 119, rate: 86, error: 0, sla: 'PASSED' },
    { name: 'worse business outcome', degraded: false, avg: 100, rate: 100, error: 50, sla: 'FAILED' },
    { name: 'exceeded tolerance', degraded: true, avg: 121, rate: 100, error: 50, sla: 'FAILED' },
    { name: 'unknown', degraded: null, avg: null, rate: null, error: null, sla: 'NOT_EVALUATED' }
  ]
  for (const isK6 of [true, false]) {
    for (const fixture of cases) {
      const baseline = { has_baseline: true, degraded: fixture.degraded, baseline_execution_no: 'reference', items: [
        { metric: 'avg_rt', baseline: 100, current: fixture.avg }, { metric: 'tps', baseline: 100, current: fixture.rate }
      ] }
      const html = await renderToString(Vue.createSSRApp({ render, components: stubs, setup: () => ({
        isK6, baseline, execution: { summary: { error_rate: fixture.error }, sla_result: fixture.sla },
        t: key => text[key] || key, metricText: value => value
      }) }))
      const expected = fixture.degraded == null ? '未评估（无可比指标）'
        : isK6 ? (fixture.degraded ? '已超退化阈值' : '未超退化阈值')
          : (fixture.degraded ? '劣于基线' : '优于基线')
      assert.ok(html.includes(expected), `${isK6}/${fixture.name}: ${html}`)
      if (isK6) {
        assert.ok(html.includes('仅比较响应时间与吞吐量'))
        assert.ok(html.includes('不评估业务失败率或 SLA'))
        assert.doesNotMatch(html, /优于基线|劣于基线/)
      } else {
        assert.doesNotMatch(html, /退化阈值|不评估业务失败率/)
      }
    }
  }
})
