import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as auth from './authProfileForm.mjs'
import * as environment from './environmentForm.mjs'
import * as actionState from './scenarioActionState.mjs'
import * as scenarioDefaults from './scenarioDefaults.mjs'
import * as websocketForm from './websocketStepForm.mjs'
import * as sseForm from './sseStepForm.mjs'
import * as recoveryForm from './reminderRecovery.mjs'

const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
async function compile(file, dependencies = {}) {
  const descriptor = parse(await readFile(new URL(file, import.meta.url), 'utf8')).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: file }).errors, [])
  const modules = []
  const source = compileScript(descriptor, { id: file }).content.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const dependency = name === 'vue' ? Vue : name.endsWith('authProfileForm.mjs') ? auth : name.endsWith('environmentForm.mjs') ? environment : name.endsWith('scenarioActionState.mjs') ? actionState : name.endsWith('scenarioDefaults.mjs') ? scenarioDefaults : name.endsWith('reminderRecovery.mjs') ? recoveryForm : name.endsWith('sseStepForm.mjs') ? sseForm : name.endsWith('websocketStepForm.mjs') ? websocketForm : dependencies[name] || {}
    const index = modules.push(dependency) - 1
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
  }).replace('export default', 'return')
  return new Function('__modules', source)(modules)
}
function mount(component, initialProps = {}) {
  let state
  const instance = { ...component, setup(props, context) { state = component.setup(props, context); return state }, render: () => null }
  const container = {}
  const update = props => renderer.render(Vue.h(instance, props), container)
  update(initialProps)
  return { state, update, unmount: () => renderer.render(null, container) }
}
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const deps = (api = {}) => ({ 'vue-i18n': { useI18n: () => ({ t: key => key }) }, '@/api/performance-testing': api,
  'element-plus': { ElMessage: { success() {}, error() {}, warning() {}, info() {} } },
  'vue-router': { useRoute: () => ({ params: { id: '13' }, query: {} }), useRouter: () => ({ push() {}, replace() {} }), onBeforeRouteLeave() {} },
  './shared': { apiError: (error, fallback) => error.message || fallback, formatDuration: String } })

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const savedScene = engine => ({ id: 13, project: 7, name: 'Saved scenario', engine, env_config: {}, variables: [], runtime_config: {}, steps: [{ id: 1, name: 'business', url: '/x', enabled: true }] })
async function editor(overrides = {}, engine = 'K6') {
  const pushes = [], calls = { debug: 0, execute: 0 }
  const api = { getEngineStatus: async () => ({ data: { engines: [{ value: 'K6', capabilities: { items: [{ id: 'debug', enabled: true }, { id: 'stop', enabled: true }] } }] } }),
    getPerfProjects: async () => ({ data: [{ id: 7 }] }), getPerfEnvironmentPermissions: async () => ({ data: { project_ids: [7] } }), getPerfEnvironments: async () => ({ data: [] }),
    getPerfDataFiles: async () => ({ data: [] }), getPerfScenario: async () => ({ data: savedScene(engine) }),
    getPerfScenarioDefaults: async project => ({ data: { project, defaults: { environment: null, global_environment: null, account_pool_version: null, account_pool_group: '', runtime_config: {} } } }),
    debugPerfScenario: async () => { calls.debug++; return { data: {} } }, executePerfScenario: async () => { calls.execute++; return { data: { execution: { id: 20 } } } }, ...overrides }
  const route = Vue.reactive({ params: { id: '13' }, query: {} })
  const dependencies = deps(api); dependencies['vue-router'].useRoute = () => route; dependencies['vue-router'].useRouter = () => ({ push: value => pushes.push(value), replace() {} })
  const oldWindow = globalThis.window; globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const mounted = mount(await compile('./ScenarioEditor.vue', dependencies))
  return { ...mounted, calls, pushes, route, cleanup() { mounted.unmount(); globalThis.window = oldWindow } }
}
test('actual editor blocks mutation and start while model and file dependencies hydrate', async () => {
  const model = deferred(), files = deferred()
  const h = await editor({ getPerfScenario: () => model.promise, getPerfDataFiles: () => files.promise })
  try {
    await settle(); const initial = h.state.steps.value.length; h.state.addStep(); await h.state.handleDebug()
    assert.equal(h.state.steps.value.length, initial); assert.equal(h.calls.debug, 0)
    model.resolve({ data: savedScene('K6') }); await settle()
    assert.equal(h.state.canExecute.value, false); assert.equal(h.state.editorBlocked.value, true)
    files.resolve({ data: [] }); await settle()
    assert.equal(h.state.form.name, 'Saved scenario'); assert.equal(h.state.editorBlocked.value, false)
  } finally { h.cleanup() }
})
test('dependency retry preserves already loaded saved configuration', async () => {
  let fail = true, reads = 0
  const h = await editor({ getPerfScenario: async () => { reads++; return { data: savedScene('K6') } }, getPerfDataFiles: async () => { if (fail) throw new Error('temporary file failure'); return { data: [] } } })
  try {
    await settle(); assert.equal(h.state.editorBlocked.value, true); assert.ok(h.state.hydrationError.value)
    assert.equal(h.state.form.name, 'Saved scenario'); fail = false; await h.state.load(); await settle()
    assert.equal(h.state.editorBlocked.value, false); assert.equal(reads, 1); assert.equal(h.state.form.name, 'Saved scenario')
  } finally { h.cleanup() }
})
test('unconfirmed start disables second submit and navigates to scene history without invented execution', async () => {
  let attempts = 0
  const h = await editor({ executePerfScenario: async () => { attempts++; throw { response: { status: 500, data: { error: '启动状态未确认', code: 'k6_start_unconfirmed', retryable: false } } } } })
  try {
    await settle(); await h.state.doExecute(); await h.state.doExecute()
    assert.equal(attempts, 1); assert.equal(h.state.startUnconfirmed.value, true); assert.equal(h.state.canExecute.value, false)
    h.state.viewExecutionHistory(); assert.deepEqual(h.pushes.at(-1), { path: '/performance-testing/executions', query: { project: 7, scenario: 13 } })
  } finally { h.cleanup() }
})
test('before-create ordinary failure remains retryable and original engines keep start actions', async () => {
  for (const engine of ['BUILTIN', 'LOCUST', 'JMETER']) {
    let calls = 0
    const h = await editor({ executePerfScenario: async () => { calls++; throw { response: { status: 400, data: { error: 'preflight rejected' } } } } }, engine)
    try { await settle(); assert.equal(h.state.canExecute.value, true); await h.state.doExecute(); assert.equal(h.state.canExecute.value, true); assert.equal(h.state.startUnconfirmed.value, false); assert.equal(calls, 1) }
    finally { h.cleanup() }
  }
})

test('route change ignores old in-flight scenario response and failed model load can retry', async () => {
  const old = deferred(); let fail = true
  const h = await editor({ getPerfScenario: async id => {
    if (id === 13) return old.promise
    if (fail) throw new Error('temporary scene failure')
    return { data: { ...savedScene('K6'), id, name: 'Next saved scenario' } }
  } })
  try {
    await settle(); h.route.params.id = '14'; await settle()
    assert.ok(h.state.hydrationError.value); assert.equal(h.state.canExecute.value, false)
    fail = false; await h.state.load(); await settle()
    assert.equal(h.state.form.name, 'Next saved scenario'); assert.equal(h.state.canExecute.value, true)
    old.resolve({ data: savedScene('K6') }); await settle()
    assert.equal(h.state.form.name, 'Next saved scenario'); assert.equal(h.state.scenarioId.value, 14)
  } finally { h.cleanup() }
})
test('selected pool dependency blocks editing while pending and exposes its retry after a load failure', async () => {
  const h = await editor({ getPerfScenario: async () => ({ data: { ...savedScene('K6'), account_pool_version: 9 } }) })
  try {
    await settle(); assert.equal(h.state.editorBlocked.value, true); assert.equal(h.state.canExecute.value, false)
    h.state.accountPoolState.value = { project: 7, versionId: 9, group: '', loading: true }; await settle()
    assert.equal(h.state.editorBlocked.value, true)
    h.state.accountPoolState.value = { project: 7, versionId: 9, group: '', loading: false, error: true }; await settle()
    assert.equal(h.state.editorBlocked.value, false); assert.equal(h.state.canExecute.value, false)
  } finally { h.cleanup() }
})
for (const sameProject of [false, true]) {
  for (const outcome of ['success', 'error']) {
    test(`stale file ${outcome} from ${sameProject ? 'same' : 'other'} project cannot change current route options or script`, async () => {
      const pending = { CSV: deferred(), JMX: deferred(), UPLOAD: deferred() }
      let currentScene = 13
      const h = await editor({
        getPerfScenario: async id => { currentScene = id; return { data: { ...savedScene('JMETER'), id, project: sameProject || id === 13 ? 7 : 8,
          runtime_config: { script_ref: { mode: 'script', data_file_id: id === 13 ? 70 : 80 } } } } },
        getPerfDataFiles: async query => currentScene === 13 ? pending[query.file_type].promise : { data: [{ id: 80, marker: query.file_type }] }
      })
      try {
        await settle(); h.route.params.id = '14'; await settle()
        assert.equal(h.state.selectedScriptId.value, 80)
        for (const [kind, task] of Object.entries(pending)) {
          if (outcome === 'error') task.reject(new Error('obsolete ' + kind))
          else task.resolve({ data: [{ id: 70, marker: 'obsolete ' + kind }] })
        }
        await settle()
        assert.equal(h.state.selectedScriptId.value, 80)
        assert.deepEqual(h.state.scriptFiles.value, [{ id: 80, marker: 'JMX' }])
        assert.deepEqual(h.state.uploadFiles.value, [{ id: 80, marker: 'UPLOAD' }])
        assert.deepEqual(h.state.dataFiles.value, [{ id: 80, marker: 'CSV' }])
        assert.deepEqual({ ...h.state.dependencyErrors }, {})
        assert.equal(h.state.hydrationError.value, '')
      } finally { h.cleanup() }
    })
  }
}
test('same route repeated file requests keep only newest success and ignore obsolete errors', async () => {
  const pending = { CSV: deferred(), JMX: deferred(), UPLOAD: deferred() }; let delay = false
  const h = await editor({ getPerfDataFiles: async query => delay ? pending[query.file_type].promise : { data: [{ id: 80 }] } }, 'JMETER')
  try {
    await settle(); delay = true
    const earlier = [h.state.loadDataFiles(7), h.state.loadScriptFiles(7), h.state.loadUploadFiles(7)]
    delay = false; h.state.selectedScriptId.value = 80
    await Promise.all([h.state.loadDataFiles(7), h.state.loadScriptFiles(7), h.state.loadUploadFiles(7)])
    pending.CSV.reject(new Error('obsolete csv')); pending.JMX.resolve({ data: [] }); pending.UPLOAD.reject(new Error('obsolete upload'))
    await Promise.all(earlier)
    assert.equal(h.state.selectedScriptId.value, 80); assert.deepEqual({ ...h.state.dependencyErrors }, {})
    assert.deepEqual(h.state.uploadFiles.value, [{ id: 80 }]); assert.deepEqual(h.state.scriptFiles.value, [{ id: 80 }])
  } finally { h.cleanup() }
})
test('existing to new resets every saved form field, headers, steps, script, pool and transient execution state', async () => {
  const original = { ...savedScene('JMETER'), description: 'old description', enabled: false, environment: 4, global_environment: 5,
    account_pool_version: 9, account_pool_group: 'old-group', load_config: { concurrency: 789, duration: 99 },
    sla_config: { enabled: true, thresholds: { error_rate: 0.1 } }, perf_targets: { max_p95_rt: 7 },
    variables: [{ name: 'oldVariable', value: 'oldValue' }], env_config: { base_url: 'https://old.invalid', headers: { 'X-Old': 'old' }, verify_ssl: true },
    runtime_config: { timeout: 88, proxy: 'http://old.invalid', script_ref: { mode: 'script', data_file_id: 70 } }, resolved_environment: { env_config: { base_url: 'https://old.invalid' } } }
  const h = await editor({ getPerfScenario: async () => ({ data: original }), getPerfProjects: async () => ({ data: [{ id: 7 }, { id: 8 }] }),
    getPerfEnvironmentPermissions: async () => ({ data: { project_ids: [7, 8] } }), getPerfDataFiles: async () => ({ data: [{ id: 70 }] }) })
  try {
    await settle(); h.state.accountPoolState.value = { project: 7, versionId: 9 }; h.state.tlsTouched.value = true
    h.state.debugResult.value = { old: true }; h.state.preflight.value = { passed: true }; h.state.activeTab.value = 'script'
    h.route.query.project = '8'; h.route.params.id = 'new'; await settle()
    assert.deepEqual(JSON.parse(JSON.stringify(h.state.form)), {
      project: 8, name: '', description: '', engine: 'K6', environment: null, global_environment: null,
      account_pool_version: null, account_pool_group: '', enabled: true,
      load_config: { model: 'CONCURRENCY', concurrency: 1, iterations_per_vu: 1, duration: 30, ramp_up: 0, max_requests: 0 },
      sla_config: { enabled: true, thresholds: { p95_response_time: 2000, error_rate: 0 }, step_thresholds: [], abort_delay: 0, abort_on_breach: false, breach_window: 10 },
      perf_targets: { max_p95_rt: 2000, max_avg_rt: null, min_tps: null, max_error_rate: 0 }, variables: [],
      env_config: { base_url: '', headers: {} }, runtime_config: { timeout: 30, sample_interval: 1, keep_alive: true, proxy: '' }
    })
    assert.deepEqual(h.state.steps.value, []); assert.deepEqual(h.state.globalHeaders.value, [])
    assert.equal(h.state.savedEnvironment.value, null); assert.equal(h.state.selectedScriptId.value, null)
    assert.equal(h.state.scriptMode.value, 'scenario'); assert.equal(h.state.accountPoolState.value, null)
    assert.equal(h.state.tlsTouched.value, false); assert.equal(h.state.debugResult.value, null); assert.equal(h.state.preflight.value, null)
    assert.equal(h.state.activeTab.value, 'request'); assert.equal(h.state.dirty.value, true)
  } finally { h.cleanup() }
})
test('new scene successful save route replacement retains the submitted model and returned step IDs', async () => {
  let h, creates = 0
  h = await editor({ createPerfScenario: async payload => { creates++; return { data: { ...payload, id: 99 } } },
    savePerfScenarioSteps: async (_, steps) => ({ data: { steps: steps.map((step, index) => ({ ...step, id: 900 + index })) } }) })
  try {
    await settle(); h.route.params.id = 'new'; await settle()
    h.state.form.name = 'New saved'; h.state.form.description = 'New description'; h.state.form.load_config.concurrency = 42
    h.state.addStep(); h.state.steps.value[0].url = '/new-request'; h.state.steps.value[0].name = 'New step'
    assert.equal(await h.state.handleSave(), true); assert.equal(creates, 1)
    h.route.params.id = '99'; await settle()
    assert.equal(h.state.form.name, 'New saved'); assert.equal(h.state.form.description, 'New description')
    assert.equal(h.state.form.load_config.concurrency, 42); assert.equal(h.state.steps.value.length, 1)
    assert.equal(h.state.steps.value[0].id, 900); assert.equal(h.state.steps.value[0].url, '/new-request')
    assert.equal(h.state.dirty.value, false)
  } finally { h.cleanup() }
})

test('new K6 scenario saves inheriting TLS then loads bindings when verified APIs are imported', async () => {
  let stored = savedScene('BUILTIN'), created
  const importedStep = { id: 201, name: 'Verified GET', method: 'GET', url: '/healthz', enabled: true, source_request: 11 }
  const h = await editor({
    getPerfScenario: async () => ({ data: stored }),
    createPerfScenario: async payload => { created = JSON.parse(JSON.stringify(payload)); stored = { ...created, id: 99, steps: [] }; return { data: stored } },
    savePerfScenarioSteps: async () => ({ data: { steps: [] } }),
    getPerfScenarioReadiness: async () => ({ data: { results: [{ step_id: 201, ready: true, gaps: [] }], count: 1, next: null, version: { version: 2 } } }),
    getPerfEnvironments: async () => ({ data: [{ id: 5, name: 'Target', scope: 'PROJECT', project: 7 }] })
  })
  try {
    await settle(); h.route.query.project = '7'; h.route.params.id = 'new'; await settle()
    assert.equal(h.state.form.engine, 'K6')
    h.state.form.name = 'Reusable API scenario'
    assert.equal(await h.state.handleSave(), true)
    assert.equal(created.engine, 'K6'); assert.equal(created.load_config.ramp_up, 0); assert.equal(created.load_config.max_requests, 0)
    assert.equal(Object.hasOwn(created.env_config, 'verify_ssl'), false)
    h.route.params.id = '99'; await settle()
    stored = { ...stored, environment: 5, account_pool_version: 9, account_pool_group: '', runtime_config: { account_identity_variable: 'user_id' }, steps: [importedStep] }
    await h.state.onImported({ bindings_applied: true, steps: [importedStep] })
    assert.equal(h.state.form.engine, 'K6'); assert.equal(h.state.form.environment, 5); assert.equal(h.state.form.account_pool_version, 9)
    assert.equal(h.state.form.runtime_config.account_identity_variable, 'user_id'); assert.equal(h.state.dirty.value, false)
    assert.deepEqual(h.state.steps.value.map(step => step.id), [201])
    assert.equal(h.calls.execute, 0); assert.equal(h.calls.debug, 0)
  } finally { h.cleanup() }
})

test('saved scenarios retain their engine, load configuration and explicit TLS choice', async () => {
  for (const engine of ['BUILTIN', 'LOCUST', 'JMETER', 'K6']) {
    const stored = { ...savedScene(engine), load_config: { model: 'CONCURRENCY', concurrency: 17, duration: 99, ramp_up: 12, max_requests: 333 }, env_config: { base_url: 'https://example.invalid', headers: {}, verify_ssl: false } }
    const h = await editor({ getPerfScenario: async () => ({ data: stored }) }, engine)
    try {
      await settle(); assert.equal(h.state.form.engine, engine)
      assert.deepEqual({ ...h.state.form.load_config }, stored.load_config)
      assert.deepEqual({ ...h.state.form.env_config }, stored.env_config)
    } finally { h.cleanup() }
  }
})

test('actual scenario blocks offscreen invalid WS drafts and preserves config through copy, reorder and save', async () => {
  let submitted, saves = 0
  const h = await editor({ updatePerfScenario: async (_, payload) => ({ data: { ...payload, id: 13 } }),
    savePerfScenarioSteps: async (_, steps) => { saves++; submitted = steps; return { data: { steps: steps.map((step, index) => ({ ...step, id: index + 10 })) } } } })
  try {
    await settle()
    const config = websocketForm.websocketTemplate('pool_token')
    h.state.form.runtime_config.auth_profile = { access_token_variable: 'pool_token' }
    h.state.steps.value[0] = { ...h.state.steps.value[0], protocol: 'WEBSOCKET', websocket_config: config, _websocketDraft: '{', enabled: false }
    h.state.addStep(); assert.equal(h.state.activeIndex.value, 1)
    assert.equal(await h.state.handleSave(), false); assert.equal(saves, 0); assert.equal(h.state.activeIndex.value, 0)
    h.state.steps.value[0]._websocketDraft = JSON.stringify(config)
    h.state.onStepCommand('copy', 0)
    h.state.steps.value.reverse()
    assert.equal(await h.state.handleSave(), true)
    assert.equal(saves, 1)
    const ws = submitted.filter(step => step.protocol === 'WEBSOCKET')
    assert.equal(ws.length, 2); assert.deepEqual(ws[0].websocket_config, config)
    assert.ok(submitted.every(step => !('_websocketDraft' in step) && !('_protocolBackup' in step)))
  } finally { h.cleanup() }
})

test('actual scenario blocks offscreen invalid SSE drafts, copies and saves typed rules, and preserves concurrent edits', async () => {
  let submitted, saves = 0, pending
  const h = await editor({ updatePerfScenario: async (_, payload) => ({ data: { ...payload, id: 13 } }),
    savePerfScenarioSteps: async (_, steps) => { saves++; submitted = steps; return pending ? pending.promise : { data: { steps: steps.map((step, index) => ({ ...step, id: index + 10 })) } } } })
  try {
    await settle()
    const config = sseForm.sseTemplate('CONTENT_SPEECH')
    Object.assign(h.state.steps.value[0], { protocol: 'SSE', method: 'POST', body_type: 'JSON', body: '{}', sse_config: config, _sseDraft: '{', enabled: false })
    h.state.addStep()
    assert.equal(await h.state.handleSave(), false); assert.equal(saves, 0); assert.equal(h.state.activeIndex.value, 0)
    h.state.steps.value[0]._sseDraft = JSON.stringify(config)
    h.state.onStepCommand('copy', 0); h.state.steps.value.reverse()
    assert.equal(await h.state.handleSave(), true)
    const streams = submitted.filter(step => step.protocol === 'SSE')
    assert.equal(streams.length, 2); assert.deepEqual(streams[0].sse_config, config)
    assert.ok(submitted.every(step => !('_sseDraft' in step)))
    pending = deferred(); h.state.markDirty()
    const saving = h.state.handleSave(); await settle()
    const edited = h.state.steps.value.find(step => step.protocol === 'SSE')
    edited._sseDraft = '{'; h.state.markDirty()
    pending.resolve({ data: { steps: submitted.map((step, index) => ({ ...step, id: index + 10 })) } })
    assert.equal(await saving, false)
    assert.ok(h.state.steps.value.some(step => step._sseDraft === '{'))
    assert.equal(h.state.dirty.value, true); assert.equal(h.state.canDebug.value, false)
  } finally { h.cleanup() }
})

test('invalid WS typing during an in-flight save survives the response and remains dirty', async () => {
  const pending = deferred(); let submitted
  const h = await editor({ updatePerfScenario: async (_, payload) => ({ data: { ...payload, id: 13 } }),
    savePerfScenarioSteps: async (_, steps) => { submitted = steps; return pending.promise } })
  try {
    await settle()
    const config = websocketForm.websocketTemplate('access_token')
    Object.assign(h.state.steps.value[0], { protocol: 'WEBSOCKET', websocket_config: config, _websocketDraft: JSON.stringify(config) })
    h.state.markDirty(); const saving = h.state.handleSave(); await settle()
    h.state.steps.value[0]._websocketDraft = '{'; h.state.markDirty()
    pending.resolve({ data: { steps: submitted.map(step => ({ ...step, id: 10 })) } })
    assert.equal(await saving, false)
    assert.equal(h.state.steps.value[0]._websocketDraft, '{'); assert.equal(h.state.dirty.value, true)
    assert.equal(h.state.canDebug.value, false)
  } finally { h.cleanup() }
})

test('profile change makes an offscreen WS draft valid and saves that visible draft instead of cached config', async () => {
  let submitted, updates = 0, wsEditor
  const h = await editor({ updatePerfScenario: async (_, payload) => { updates++; return { data: { ...payload, id: 13 } } },
    savePerfScenarioSteps: async (_, steps) => { submitted = steps; return { data: { steps: steps.map((step, index) => ({ ...step, id: index + 10 })) } } } })
  try {
    await settle()
    const oldConfig = websocketForm.websocketTemplate('old_token')
    const newConfig = websocketForm.websocketTemplate('new_token')
    Object.assign(h.state.steps.value[0], { protocol: 'WEBSOCKET', websocket_config: oldConfig })
    h.state.form.runtime_config.auth_profile = { access_token_variable: 'old_token' }
    const props = () => ({ modelValue: h.state.steps.value[0].websocket_config, draft: h.state.steps.value[0]._websocketDraft,
      accessVariable: h.state.websocketAccessVariable.value,
      'onUpdate:modelValue': value => { h.state.steps.value[0].websocket_config = value; wsEditor?.update(props()) },
      'onUpdate:draft': value => { h.state.steps.value[0]._websocketDraft = value; h.state.markDirty(); wsEditor?.update(props()) } })
    wsEditor = mount(await compile('./components/WebSocketStepEditor.vue', deps()), props())
    wsEditor.state.updateDraft(JSON.stringify(newConfig)); await settle()
    assert.ok(h.state.websocketErrors.value.length)
    assert.equal(h.state.steps.value[0].websocket_config.auth.request.payload.token, '{{old_token}}')
    assert.equal(await h.state.handleSave(), false); assert.equal(updates, 0)
    h.state.form.runtime_config.auth_profile.access_token_variable = 'new_token'; wsEditor.update(props()); await settle()
    assert.equal(h.state.websocketErrors.value.length, 0)
    assert.equal(wsEditor.state.parsed.value.config.auth.request.payload.token, '{{new_token}}')
    h.state.addStep(); assert.equal(h.state.activeIndex.value, 1)
    assert.equal(await h.state.handleSave(), true)
    assert.deepEqual(submitted[0].websocket_config, newConfig)
    assert.equal(h.state.steps.value[0].websocket_config.auth.request.payload.token, '{{new_token}}')
    assert.ok(!JSON.stringify(submitted).includes('old_token'))
  } finally { wsEditor?.unmount(); h.cleanup() }
})
