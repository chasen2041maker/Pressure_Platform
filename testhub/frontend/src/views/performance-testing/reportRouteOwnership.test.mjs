import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as Router from 'vue-router'
import * as sla from './slaReport.mjs'
import * as interfaces from './k6InterfaceStats.mjs'
import * as websocketMetrics from './websocketMetrics.mjs'
import * as sseMetrics from './sseMetrics.mjs'
import * as lines from './nullableLine.mjs'

const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const detail = id => ({ id, execution_no: `run-${id}`, scenario_name: `scenario-${id}`, status: 'COMPLETED',
  summary: { business_total: id, http_total: id }, report_url: `report-${id}`, has_raw_detail: true,
  load_snapshot: { _engine: 'K6' }, steps_snapshot: [{ id, name: `step-${id}` }] })
const sample = id => ({ ts_offset: id, tps: id, active_users: id, avg_rt: id, p90_rt: id, p95_rt: id, p99_rt: id, error_rate: 0, business_total: id })

test('recovery refresh uses live execution detail and does not inherit a previous report state', async () => {
  let state = 'PENDING', fail = false, pendingRead
  const h = await harness({ getPerfExecution: async id => {
    if (fail) throw new Error('offline')
    if (id === 28 && pendingRead) return pendingRead.promise
    return { data: { ...detail(id), ...(id === 28 ? { resource_recovery: { version: 1, kind: 'portfolio_reminder', state } } : {}) } }
  } })
  try {
    await settle(); assert.equal(h.state.execution.resource_recovery.state, 'PENDING')
    assert.equal(typeof h.state.refreshRecovery, 'function')
    const summary = { ...h.state.execution.summary }
    state = 'RECOVERED'; await h.state.refreshRecovery(); assert.equal(h.state.execution.resource_recovery.state, 'RECOVERED')
    assert.deepEqual({ ...h.state.execution.summary }, summary)
    fail = true; await h.state.refreshRecovery(); assert.equal(h.state.execution.resource_recovery, null)
    fail = false; pendingRead = deferred(); const late = h.state.refreshRecovery(); await settle()
    await h.navigate(32); assert.equal(h.state.execution.resource_recovery, null)
    pendingRead.resolve({ data: { ...detail(28), resource_recovery: { state: 'RECOVERED' } } }); await late
    assert.equal(h.state.execution.resource_recovery, null); assert.equal(h.state.recoveryLoading.value, false)
  } finally { h.cleanup() }
})

async function harness(overrides = {}, requestOverride, fetchOverride) {
  const reads = [], calls = [], messages = [], downloads = [], popups = [], charts = [], listeners = new Set()
  const objectUrls = [], revokedUrls = []
  const api = {
    getPerfExecution: async id => { reads.push(id); return { data: detail(id) } },
    getPerfSamples: async id => ({ data: { samples: [sample(id)] } }),
    getPerfRequestStats: async id => ({ data: [{ step: id, step_name: `step-${id}`, total_requests: id }] }),
    compareWithBaseline: async ({ execution_id: id }) => ({ data: { has_baseline: true, marker: id } }),
    generatePerfReport: async id => { calls.push(['regenerate', id]); return { data: { report_url: `new-${id}` } } },
    generatePerfShareLink: async id => { calls.push(['share', id]); return { data: { token: `token-${id}`, share_url: `share-${id}` } } },
    revokePerfShareLink: async id => { calls.push(['revoke', id]); return { data: {} } }, ...overrides
  }
  const dependencies = {
    vue: Vue, 'vue-router': Router, './slaReport.mjs': sla, './k6InterfaceStats.mjs': interfaces, './websocketMetrics.mjs': websocketMetrics, './sseMetrics.mjs': sseMetrics, './nullableLine.mjs': lines,
    'vue-i18n': { useI18n: () => ({ t: key => key }) },
    'element-plus': { ElMessage: Object.fromEntries(['success', 'error', 'warning', 'info'].map(kind => [kind, message => messages.push([kind, message])])) },
    './shared': { apiError: (error, fallback) => error?.message || fallback }, '@/stores/user': { useUserStore: () => ({}) },
    '@/api/performance-testing': api, marked: { marked: { parse: text => text } },
    '@/utils/api': { default: config => { calls.push(['request', config]); return requestOverride ? requestOverride(config) : Promise.resolve({ data: new Blob(['report']) }) } },
    echarts: { init: () => {
      const chart = { options: [], clears: 0, disposed: false, setOption(option) { this.options.push(option) }, clear() { this.clears++; this.options = [] }, resize() {}, dispose() { this.disposed = true } }
      charts.push(chart); return chart
    } }
  }
  const descriptor = parse(await readFile(new URL('./ExecutionReport.vue', import.meta.url), 'utf8')).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: 'report-route-test' }).errors, [])
  const modules = []
  const source = compileScript(descriptor, { id: 'report-route-test' }).content.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(dependencies[name] || {}) - 1
    if (binding.trim().startsWith('* as ')) return `const ${binding.trim().slice(5)} = __modules[${index}];\n`
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
  }).replace('export default', 'return')
  const component = new Function('__modules', source)(modules)
  let state, mounts = 0
  const wrapped = { ...component, setup(props, context) { mounts++; state = component.setup(props, context); return state }, render: () => null }
  const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
  const router = Router.createRouter({ history: Router.createMemoryHistory(), routes: [
    { path: '/performance-testing/executions/:id', component: wrapped }, { path: '/elsewhere', component: { render: () => null } }
  ] })
  const previous = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch }
  const previousCreateUrl = URL.createObjectURL, previousRevokeUrl = URL.revokeObjectURL
  URL.createObjectURL = blob => { const url = previousCreateUrl(blob); objectUrls.push({ url, blob }); return url }
  URL.revokeObjectURL = url => { revokedUrls.push(url); previousRevokeUrl(url) }
  globalThis.window = {
    addEventListener: name => listeners.add(name), removeEventListener: name => listeners.delete(name),
    open: () => {
      const popup = { closed: false, content: '', close() { this.closed = true }, document: { open() {}, close() {}, write(value) { popup.content = value } } }
      popups.push(popup); return popup
    }
  }
  globalThis.document = { body: { appendChild() {}, removeChild() {} }, createElement: () => ({ click() { downloads.push({ filename: this.download, href: this.href }) } }) }
  globalThis.fetch = (...args) => fetchOverride ? fetchOverride(...args) : Promise.resolve({ ok: true, headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({ analysis: 'current analysis' }) })
  await router.push('/performance-testing/executions/28')
  const app = renderer.createApp({ render: () => Vue.h(Router.RouterView) })
  app.use(router); app.mount({})
  let unmounted = false
  return { state, router, reads, calls, messages, downloads, popups, charts, listeners, objectUrls, revokedUrls, get mounts() { return mounts },
    async clickButton(label) {
      const button = [...descriptor.template.content.matchAll(/<el-button\b([^>]*?)>([\s\S]*?)<\/el-button>/g)].find(([, , content]) => content.includes(label))
      assert.ok(button, `Report button exists: ${label}`)
      const bindings = Vue.proxyRefs(state)
      const disabled = button[1].match(/:disabled="([^"]+)"/)
      if (disabled && new Function('state', `with (state) { return ${disabled[1]} }`)(bindings)) return false
      const action = button[1].match(/@click="([^"]+)"/)
      assert.ok(action, `Report button has a click handler: ${label}`)
      const result = new Function('state', `with (state) { return ${action[1]} }`)(bindings)
      await (typeof result === 'function' ? result() : result)
      await settle()
      return true
    },
    navigate: async id => { await router.push(`/performance-testing/executions/${id}`); await settle() },
    unmount() { if (!unmounted) { app.unmount(); unmounted = true } },
    cleanup() { this.unmount(); Object.assign(globalThis, previous); URL.createObjectURL = previousCreateUrl; URL.revokeObjectURL = previousRevokeUrl }
  }
}

test('reused report follows route 28 → 32 and browser back/forward, replacing overview, detail and timeline', async () => {
  const h = await harness()
  try {
    await settle(); assert.equal(h.state.execution.id, 28)
    h.state.activeTab.value = 'timeline'
    h.state.tpsChartRef.value = {}; h.state.rtChartRef.value = {}; h.state.errChartRef.value = {}
    await h.state.onTabChange('timeline')
    await h.navigate(32)
    assert.equal(h.mounts, 1); assert.equal(h.state.execution.id, 32)
    assert.equal(h.state.execution.summary.business_total, 32)
    assert.equal(h.state.requestStats.value[0].step, 32); assert.equal(h.state.baseline.value.marker, 32)
    assert.deepEqual(h.charts[0].options.at(-1).xAxis.data, ['32s'])
    assert.deepEqual(h.charts[0].options.at(-1).series[0].data, [32])
    h.router.back(); await settle(); assert.equal(h.state.execution.id, 28)
    h.router.forward(); await settle(); assert.equal(h.state.execution.id, 32)
    await h.state.exportReport('json'); assert.match(h.calls.at(-1)[1].url, /executions\/32\/report/)
    assert.equal(h.downloads.at(-1).filename, 'run-32.json')
  } finally { h.cleanup() }
})

test('SSE-only report uses stream semantics and keeps missing per-stream history unknown', async () => {
  const h = await harness({ getPerfExecution: async id => ({ data: { ...detail(id), steps_snapshot: [{ id: 8, protocol: 'SSE', name: 'Frozen stream' }], summary: { sse: { version: 1, streams: { started: 1, completed: 1, success: 1, failed: 0, incomplete: 0 } } } } }) })
  try {
    await settle()
    assert.equal(h.state.hasWebsocket.value, false)
    assert.equal(h.state.hasSse.value, true)
    assert.equal(h.state.hasProtocolStreams.value, true)
    assert.equal(h.state.protocolLatencyKey('SSE'), 'performanceTesting.sse.completionLatency')
    assert.equal(h.state.businessRateLabel.value, 'performanceTesting.websocket.mixedRate')
    assert.equal(sseMetrics.sseStreamRows(h.state.execution.summary.sse, h.state.execution.steps_snapshot)[0].completion.avg_ms, null)
  } finally { h.cleanup() }
})

test('navigation clears all old fields and disables actions until new report is ready; failure remains retryable', async () => {
  const pending = deferred(); let failed = true
  const h = await harness({ getPerfExecution: async id => id === 28 ? { data: { ...detail(id), error_message: 'old error', old_only: true } } : failed ? pending.promise : { data: detail(id) } })
  try {
    await settle(); await h.navigate(32)
    assert.equal(h.state.loading.value, true); assert.equal(h.state.execution.execution_no, '')
    assert.equal(h.state.execution.old_only, undefined); assert.equal(h.state.execution.error_message, undefined)
    assert.deepEqual(h.state.samples.value, []); assert.deepEqual(h.state.requestStats.value, []); assert.equal(h.state.baseline.value, null)
    await h.state.exportReport('csv'); h.state.openHtml(); h.state.downloadRaw(); h.state.openShare(); await h.state.handleRegenerate(); await h.state.loadAiAnalysis()
    assert.equal(h.calls.length, 0); assert.equal(h.popups.length, 0)
    pending.reject(new Error('current report failed')); await settle()
    assert.equal(h.state.loading.value, false); assert.equal(h.state.execution.execution_no, '')
    await h.state.exportReport('json'); assert.equal(h.calls.length, 0)
    failed = false; await h.state.loadAll(); await settle(); assert.equal(h.state.execution.id, 32)
  } finally { h.cleanup() }
})

for (const outcome of ['success', 'error']) {
  test(`late old route ${outcome} cannot overwrite new report, warnings or loading`, async () => {
    const oldDetail = deferred(), oldStats = deferred(), oldSamples = deferred()
    const h = await harness({ getPerfExecution: id => id === 28 ? oldDetail.promise : Promise.resolve({ data: detail(id) }),
      getPerfRequestStats: id => id === 28 ? oldStats.promise : Promise.resolve({ data: [{ step: id }] }),
      getPerfSamples: id => id === 28 ? oldSamples.promise : Promise.resolve({ data: { samples: [sample(id)] } }) })
    try {
      await settle(); await h.navigate(32)
      assert.equal(h.state.execution.id, 32)
      if (outcome === 'success') { oldDetail.resolve({ data: detail(28) }); oldStats.resolve({ data: [{ step: 28 }] }); oldSamples.resolve({ data: { samples: [sample(28)] } }) }
      else { oldDetail.reject(new Error('old detail error')); oldStats.reject(new Error('old stats error')); oldSamples.reject(new Error('old samples error')) }
      await settle()
      assert.equal(h.state.execution.id, 32); assert.equal(h.state.samples.value[0].tps, 32)
      assert.equal(h.state.requestStats.value[0].step, 32); assert.equal(h.state.requestStatsFailed.value, false)
      assert.equal(h.state.baseline.value.marker, 32); assert.equal(h.state.loading.value, false); assert.deepEqual(h.messages, [])
    } finally { h.cleanup() }
  })
}

test('late baseline and a repeated same-ID reload cannot restore an older generation', async () => {
  const oldBaseline = deferred(); let comparisons = 0
  const h = await harness({ compareWithBaseline: async ({ execution_id: id }) => ++comparisons === 1 ? oldBaseline.promise : { data: { marker: id, has_baseline: true } } })
  try {
    await settle(); await h.navigate(32); await h.navigate(28)
    oldBaseline.resolve({ data: { marker: 'stale' } }); await settle()
    assert.equal(h.state.execution.id, 28); assert.equal(h.state.baseline.value.marker, 28)
    await h.state.loadAll(); assert.equal(h.state.baseline.value.marker, 28)
  } finally { h.cleanup() }
})

test('pending JSON, CSV, raw and HTML exports are discarded after navigation; current downloads retain identity', async () => {
  let delay = true
  const pending = []
  const h = await harness({}, config => { if (!delay) return Promise.resolve({ data: config.responseType === 'text' ? 'report32' : new Blob(['report32']) }); const result = deferred(); pending.push(result); return result.promise })
  try {
    await settle()
    const exports = [h.state.exportReport('json'), h.state.exportReport('csv')]
    h.state.downloadRaw(); h.state.openHtml(); await h.navigate(32)
    for (const result of pending) result.resolve({ data: new Blob(['old28']) })
    await Promise.all(exports); await settle()
    assert.equal(h.downloads.length, 0); assert.equal(h.popups[0].closed, true); assert.equal(h.popups[0].content, '')
    assert.ok(h.calls.slice(0, 4).every(([, config]) => config.url.includes('/28/')))
    delay = false; await h.state.exportReport('json'); await h.state.exportReport('csv'); h.state.downloadRaw(); h.state.openHtml(); await settle()
    assert.deepEqual(h.downloads.map(value => value.filename), ['run-32.json', 'run-32.csv', 'run-32_raw.csv.gz'])
    assert.equal(h.popups[1].content, 'report32'); assert.ok(h.calls.slice(4).every(([, config]) => config.url.includes('/32/')))
  } finally { h.cleanup() }
})

test('old regeneration and share generation/revocation cannot alter the current report or its dialog', async () => {
  const regen = deferred(), share = deferred(), revoke = deferred()
  const h = await harness({ generatePerfReport: id => id === 28 ? regen.promise : Promise.resolve({ data: { report_url: 'new32' } }),
    generatePerfShareLink: id => id === 28 ? share.promise : Promise.resolve({ data: { token: 'token32', share_url: 'share32' } }), revokePerfShareLink: () => revoke.promise })
  try {
    await settle(); const first = h.state.handleRegenerate(); h.state.openShare(); const revoking = h.state.handleRevoke()
    await h.navigate(32); assert.equal(h.state.shareVisible.value, false); assert.equal(h.state.shareToken.value, '')
    h.state.openShare(); await settle(); assert.equal(h.state.shareUrl.value, 'share32')
    regen.resolve({ data: { report_url: 'old28' } }); share.resolve({ data: { token: 'old28', share_url: 'old28' } }); revoke.resolve({ data: {} })
    await Promise.all([first, revoking]); await settle()
    assert.equal(h.state.execution.report_url, 'report-32'); assert.equal(h.state.shareUrl.value, 'share32')
    assert.equal(h.state.shareToken.value, 'token32'); assert.equal(h.state.shareGenerating.value, false); assert.equal(h.state.regenerating.value, false)
  } finally { h.cleanup() }
})

test('same-route share dialog close and newer request discard older share response', async () => {
  const old = deferred(); let count = 0
  const h = await harness({ generatePerfShareLink: async () => ++count === 1 ? old.promise : { data: { token: 'current', share_url: 'current' } } })
  try {
    await settle(); h.state.openShare(); h.state.onShareClosed(); h.state.openShare(); await settle()
    old.resolve({ data: { token: 'old', share_url: 'old' } }); await settle()
    assert.equal(h.state.shareToken.value, 'current'); assert.equal(h.state.shareUrl.value, 'current')
  } finally { h.cleanup() }
})

for (const streaming of [false, true]) {
  test(`AI ${streaming ? 'stream' : 'JSON'} from old report is aborted and cannot append to current analysis`, async () => {
    const old = deferred(); const signals = [], urls = []
    const h = await harness({}, undefined, async (url, options) => {
      urls.push(url); signals.push(options.signal)
      if (url.includes('/28/')) return { ok: true, headers: new Headers({ 'Content-Type': streaming ? 'text/event-stream' : 'application/json' }),
        json: () => old.promise, body: { getReader: () => ({ read: () => old.promise, cancel: async () => {}, releaseLock() {} }) } }
      return { ok: true, headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({ analysis: 'new32' }) }
    })
    try {
      await settle(); const first = h.state.loadAiAnalysis(); await settle(); await h.navigate(32)
      assert.equal(signals[0].aborted, true); assert.equal(h.state.aiAnalysis.text, '')
      await h.state.loadAiAnalysis(); assert.equal(h.state.aiAnalysis.text, 'new32')
      old.resolve(streaming ? { done: false, value: new TextEncoder().encode('data: {"chunk":"old28","done":true}\n\n') } : { analysis: 'old28' })
      await first; await settle()
      assert.equal(h.state.aiAnalysis.text, 'new32'); assert.equal(h.state.aiAnalysis.loaded, true); assert.equal(h.state.aiAnalysis.error, '')
      assert.ok(urls[1].includes('/32/'))
    } finally { h.cleanup() }
  })
}

test('unmount invalidates pending data, actions and AI without reattaching resize listener', async () => {
  const old = deferred()
  const h = await harness({ getPerfExecution: () => old.promise })
  try {
    await settle(); h.unmount(); old.resolve({ data: detail(28) }); await settle()
    assert.equal(h.state.execution.execution_no, ''); assert.equal(h.listeners.size, 0); assert.deepEqual(h.messages, [])
  } finally { h.cleanup() }
})

test('late action failures from old route do not report errors on the current report', async () => {
  const tasks = Array.from({ length: 8 }, deferred)
  let exports = 0
  const h = await harness({ generatePerfReport: () => tasks[4].promise, generatePerfShareLink: () => tasks[5].promise,
    revokePerfShareLink: () => tasks[6].promise }, () => tasks[exports++].promise, () => tasks[7].promise)
  try {
    await settle()
    const pending = [h.state.exportReport('json'), h.state.exportReport('csv'), h.state.handleRegenerate(), h.state.loadAiAnalysis()]
    h.state.downloadRaw(); h.state.openHtml(); h.state.openShare(); pending.push(h.state.handleRevoke())
    await h.navigate(32)
    tasks.forEach(task => task.reject(new Error('obsolete action failure')))
    await Promise.all(pending); await settle()
    assert.deepEqual(h.messages, []); assert.equal(h.state.aiAnalysis.error, '')
    assert.equal(h.state.execution.id, 32); assert.equal(h.state.shareGenerating.value, false); assert.equal(h.state.regenerating.value, false)
  } finally { h.cleanup() }
})

test('newer load of the same execution wins over a late previous load', async () => {
  const first = deferred(); let count = 0
  const h = await harness({ getPerfExecution: async id => ++count === 1 ? first.promise : { data: { ...detail(id), execution_no: 'newest-28' } } })
  try {
    await settle(); await h.state.loadAll(); first.resolve({ data: detail(28) }); await settle()
    assert.equal(h.state.execution.execution_no, 'newest-28'); assert.equal(h.state.loading.value, false)
  } finally { h.cleanup() }
})

test('unmount of loaded report closes pending popup, aborts AI, disposes charts and discards action results', async () => {
  const action = deferred(), ai = deferred(); let signal
  const h = await harness({}, () => action.promise, async (_, options) => { signal = options.signal; return ai.promise })
  try {
    await settle(); h.state.tpsChartRef.value = {}; await h.state.onTabChange('timeline')
    const exporting = h.state.exportReport('json'), analyzing = h.state.loadAiAnalysis()
    h.state.downloadRaw(); h.state.openHtml(); h.unmount()
    assert.equal(signal.aborted, true); assert.equal(h.popups[0].closed, true); assert.equal(h.charts[0].disposed, true)
    action.resolve({ data: new Blob(['late']) }); ai.resolve({ ok: true, headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({ analysis: 'late' }) })
    await Promise.all([exporting, analyzing]); await settle()
    assert.equal(h.downloads.length, 0); assert.equal(h.state.aiAnalysis.text, ''); assert.equal(h.listeners.size, 0); assert.deepEqual(h.messages, [])
  } finally { h.cleanup() }
})

test('invalid route and mismatched detail identity never enable report actions', async () => {
  const h = await harness({ getPerfExecution: async () => ({ data: detail(28) }) })
  try {
    await settle(); await h.navigate(32)
    assert.equal(h.state.execution.execution_no, ''); assert.equal(h.state.reportReady.value, false); assert.ok(h.state.loadError.value)
    await h.navigate('invalid'); assert.equal(h.state.loading.value, false); assert.ok(h.state.loadError.value)
    await h.state.exportReport('json'); assert.equal(h.calls.length, 0)
  } finally { h.cleanup() }
})

test('HTML export button downloads the authenticated report bytes with the execution filename and releases its URL', async () => {
  const html = '<!doctype html><html><body>报告 28</body></html>'
  const h = await harness({}, async () => ({ data: new Blob([html], { type: 'text/html;charset=utf-8' }) }))
  try {
    await settle(); await h.clickButton('performanceTesting.report.exportHtml')
    assert.equal(h.downloads.length, 1); assert.equal(h.popups.length, 0)
    assert.equal(h.downloads[0].filename, 'run-28.html')
    assert.deepEqual(h.calls[0], ['request', { url: '/perf-testing/executions/28/report/', method: 'get', responseType: 'blob' }])
    assert.equal(await h.objectUrls[0].blob.text(), html)
    await new Promise(resolve => setTimeout(resolve, 1100))
    assert.ok(h.revokedUrls.includes(h.downloads[0].href))
  } finally { h.cleanup() }
})

test('separate HTML preview button retains the authenticated popup preview', async () => {
  const h = await harness({}, async () => ({ data: '<html>preview 28</html>' }))
  try {
    await settle(); await h.clickButton('预览 HTML')
    assert.equal(h.popups.length, 1); assert.equal(h.popups[0].content, '<html>preview 28</html>')
    assert.equal(h.downloads.length, 0)
    assert.deepEqual(h.calls[0], ['request', { url: '/perf-testing/executions/28/report/', responseType: 'text' }])
  } finally { h.cleanup() }
})

test('HTML download permits only ready terminal reports with an existing HTML report', async () => {
  const h = await harness()
  try {
    await settle()
    for (const status of ['PENDING', 'PREPARING', 'RUNNING', 'STOPPING', '', 'UNKNOWN']) {
      h.state.execution.status = status
      assert.equal(await h.clickButton('performanceTesting.report.exportHtml'), false)
      await h.state.exportReport('html')
    }
    assert.equal(h.calls.length, 0)
    h.state.execution.status = 'COMPLETED'; h.state.execution.report_url = ''
    assert.equal(await h.clickButton('performanceTesting.report.exportHtml'), false)
    await h.state.exportReport('html'); assert.equal(h.calls.length, 0)
    h.state.execution.report_url = 'report-28'
    for (const status of ['COMPLETED', 'FAILED', 'STOPPED', 'TIMEOUT']) {
      h.state.execution.status = status
      assert.equal(await h.clickButton('performanceTesting.report.exportHtml'), true)
    }
    assert.deepEqual(h.downloads.map(item => item.filename), Array(4).fill('run-28.html'))
  } finally { h.cleanup() }
})

test('pending HTML success or failure cannot download or notify after navigation, reload or unmount', async () => {
  for (const transition of ['navigate', 'reload', 'unmount']) {
    for (const outcome of ['success', 'error']) {
      const pending = deferred()
      const h = await harness({}, () => pending.promise)
      try {
        await settle(); const exporting = h.state.exportReport('html')
        if (transition === 'navigate') await h.navigate(32)
        else if (transition === 'reload') await h.state.loadAll()
        else h.unmount()
        if (outcome === 'success') pending.resolve({ data: new Blob(['obsolete html']) })
        else pending.reject(new Error('obsolete HTML failure'))
        await exporting; await settle()
        assert.equal(h.calls[0][1].url, '/perf-testing/executions/28/report/')
        assert.deepEqual(h.downloads, []); assert.deepEqual(h.objectUrls, []); assert.deepEqual(h.messages, [])
        if (transition !== 'unmount') assert.equal(h.state.execution.id, transition === 'navigate' ? 32 : 28)
      } finally { h.cleanup() }
    }
  }
})

test('HTML export cannot run while the next route is loading and later downloads only its new identity', async () => {
  const pending = deferred()
  const h = await harness({ getPerfExecution: id => id === 28 ? Promise.resolve({ data: detail(id) }) : pending.promise })
  try {
    await settle(); await h.navigate(32)
    assert.equal(await h.clickButton('performanceTesting.report.exportHtml'), false)
    await h.state.exportReport('html'); assert.equal(h.calls.length, 0)
    pending.resolve({ data: detail(32) }); await settle()
    await h.clickButton('performanceTesting.report.exportHtml')
    assert.equal(h.calls[0][1].url, '/perf-testing/executions/32/report/')
    assert.equal(h.downloads[0].filename, 'run-32.html')
  } finally { h.cleanup() }
})

test('current HTML request failure reports an error without a download or object URL', async () => {
  const h = await harness({}, async () => { throw new Error('HTML unavailable') })
  try {
    await settle(); await h.clickButton('performanceTesting.report.exportHtml')
    assert.deepEqual(h.messages, [['error', 'HTML unavailable']])
    assert.deepEqual(h.downloads, []); assert.deepEqual(h.objectUrls, [])
  } finally { h.cleanup() }
})

test('HTML download releases its object URL even when the browser rejects the click', async () => {
  const h = await harness()
  try {
    await settle()
    globalThis.document.createElement = () => ({ click() { throw new Error('Download blocked') } })
    await h.clickButton('performanceTesting.report.exportHtml')
    assert.deepEqual(h.messages, [['error', 'Download blocked']])
    assert.equal(h.objectUrls.length, 1)
    await new Promise(resolve => setTimeout(resolve, 1100))
    assert.ok(h.revokedUrls.includes(h.objectUrls[0].url))
  } finally { h.cleanup() }
})
