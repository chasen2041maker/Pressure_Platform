import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as auth from './authProfileForm.mjs'
import * as environment from './environmentForm.mjs'
import * as actionState from './scenarioActionState.mjs'
import * as defaults from './scenarioDefaults.mjs'
import * as websocketForm from './websocketStepForm.mjs'
import * as sseForm from './sseStepForm.mjs'
import * as recoveryForm from './reminderRecovery.mjs'

const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
const clone = value => JSON.parse(JSON.stringify(value))
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

async function compileEditor(dependencies) {
  const descriptor = parse(await readFile(new URL('./ScenarioEditor.vue', import.meta.url), 'utf8')).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: 'defaults-editor' }).errors, [])
  const modules = []
  const helpers = { './authProfileForm.mjs': auth, './environmentForm.mjs': environment,
    './scenarioActionState.mjs': actionState, './scenarioDefaults.mjs': defaults, './websocketStepForm.mjs': websocketForm, './sseStepForm.mjs': sseForm, './reminderRecovery.mjs': recoveryForm }
  const code = compileScript(descriptor, { id: 'defaults-editor' }).content
    .replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
      const index = modules.push(name === 'vue' ? Vue : helpers[name] || dependencies[name] || {}) - 1
      return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
    }).replace('export default', 'return')
  return new Function('__modules', code)(modules)
}

const response = project => ({ data: { project, defaults: {
  environment: project * 10, global_environment: null, account_pool_version: project * 100,
  account_pool_group: `project-${project}`, load_config: { concurrency: 90, duration: 900 },
  perf_targets: { max_p95_rt: 9000 }, runtime_config: { timeout: 99,
    auth_profile: { mode: 'bearer', access_token_variable: `token_${project}` }, account_identity_variable: `user_${project}` }
} } })
const saved = (id = 13) => ({ ...defaults.newScenarioForm(), id, project: 7, name: 'Saved or copied scenario',
  environment: null, account_pool_version: null, enabled: false, steps: [],
  load_config: { concurrency: 17, duration: 88, iterations_per_vu: 0 },
  runtime_config: { timeout: 67, keep_alive: false, auth_profile: {}, account_identity_variable: '' },
  sla_config: { enabled: false, thresholds: { p95_response_time: 321, error_rate: 2 }, abort_on_breach: false },
  perf_targets: { max_p95_rt: 321, max_error_rate: 2 } })

async function editor(overrides = {}, initialId = 'new') {
  const route = Vue.reactive({ params: { id: initialId }, query: { project: '7' } })
  const calls = []
  const api = {
    getEngineStatus: async () => ({ data: { engines: [{ value: 'K6', available: true }] } }),
    getPerfProjects: async () => ({ data: [{ id: 7 }, { id: 8 }] }),
    getPerfEnvironmentPermissions: async () => ({ data: { project_ids: [7, 8] } }),
    getPerfEnvironments: async ({ project }) => ({ data: project ? [{ id: project * 10, project, scope: 'PROJECT' }] : [] }),
    getPerfDataFiles: async () => ({ data: [] }), getPerfScenario: async id => ({ data: saved(id) }),
    getPerfScenarioDefaults: async project => { calls.push(project); return response(project) }, ...overrides
  }
  const dependencies = {
    '@/api/performance-testing': api,
    'vue-router': { useRoute: () => route, useRouter: () => ({ push() {}, replace() {} }), onBeforeRouteLeave() {} },
    'vue-i18n': { useI18n: () => ({ t: key => key }) },
    'element-plus': { ElMessage: { success() {}, warning() {}, error() {}, info() {} } },
    './shared': { apiError: (error, fallback) => error.message || fallback, formatDuration: String }
  }
  const oldWindow = globalThis.window
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const component = await compileEditor(dependencies)
  let state
  const mounted = { ...component, setup(props, context) { state = component.setup(props, context); return state }, render: () => null }
  const container = {}
  renderer.render(Vue.h(mounted), container)
  return { state, route, calls, cleanup() { renderer.render(null, container); globalThis.window = oldWindow } }
}

test('actual new editor loads project bindings once during hydration and keeps safe load and SLA defaults', async () => {
  const h = await editor()
  try {
    await settle()
    assert.deepEqual(h.calls, [7])
    assert.equal(h.state.form.environment, 70)
    assert.equal(h.state.form.account_pool_version, 700)
    assert.equal(h.state.form.runtime_config.auth_profile.access_token_variable, 'token_7')
    assert.equal(h.state.form.runtime_config.account_identity_variable, 'user_7')
    assert.equal(h.state.form.runtime_config.timeout, 30)
    assert.equal(h.state.form.load_config.concurrency, 1)
    assert.equal(h.state.form.load_config.iterations_per_vu, 1)
    assert.equal(h.state.form.load_config.duration, 30)
    assert.equal(h.state.form.perf_targets.max_p95_rt, 2000)
    assert.equal(h.state.form.sla_config.enabled, true)
    assert.deepEqual(clone(h.state.form.sla_config.thresholds), { p95_response_time: 2000, error_rate: 0 })
    assert.equal(h.state.form.sla_config.abort_on_breach, false)
    assert.equal(h.state.defaultsLoading.value, false)
  } finally { h.cleanup() }
})

test('actual editor preserves user settings changed while defaults load', async () => {
  const pending = deferred()
  const h = await editor({ getPerfScenarioDefaults: () => pending.promise })
  try {
    await settle()
    h.state.form.runtime_config.timeout = 77
    h.state.form.load_config.concurrency = 6
    h.state.form.load_config.iterations_per_vu = 0
    h.state.form.perf_targets.max_p95_rt = null
    h.state.form.sla_config.enabled = false
    pending.resolve(response(7)); await settle()
    assert.equal(h.state.form.runtime_config.timeout, 77)
    assert.equal(h.state.form.load_config.concurrency, 6)
    assert.equal(h.state.form.load_config.iterations_per_vu, 0)
    assert.equal(h.state.form.perf_targets.max_p95_rt, null)
    assert.equal(h.state.form.sla_config.enabled, false)
    assert.equal(h.state.form.account_pool_version, 700)
  } finally { h.cleanup() }
})

test('changing an environment while project defaults load does not mix in the default account pool', async () => {
  const pending = deferred()
  const h = await editor({ getPerfScenarioDefaults: () => pending.promise })
  try {
    await settle(); h.state.form.environment = 71
    pending.resolve(response(7)); await settle()
    assert.equal(h.state.form.environment, 71)
    assert.equal(h.state.form.account_pool_version, null)
    assert.equal(h.state.form.runtime_config.auth_profile, undefined)
  } finally { h.cleanup() }
})

test('project switch clears old identities and ignores a late response from the previous selection', async () => {
  let failInitial = true
  const old = deferred(), current = deferred()
  const h = await editor({ getPerfScenarioDefaults: async project => {
    if (failInitial) { failInitial = false; throw new Error('first load unavailable') }
    return project === 7 ? old.promise : current.promise
  } })
  try {
    await settle()
    const retry = h.state.loadProjectDefaults(7)
    h.state.form.runtime_config.timeout = 57
    h.state.form.project = 8; await settle()
    current.resolve(response(8)); await settle()
    old.resolve(response(7)); await retry; await settle()
    assert.equal(h.state.form.project, 8)
    assert.equal(h.state.form.environment, 80)
    assert.equal(h.state.form.account_pool_version, 800)
    assert.equal(h.state.form.runtime_config.account_identity_variable, 'user_8')
    assert.equal(h.state.form.runtime_config.timeout, 57)
    assert.equal(h.state.defaultsLoading.value, false)
    assert.equal(h.state.defaultsError.value, '')
  } finally { h.cleanup() }
})

test('leaving a new route prevents its pending defaults from changing the saved scenario', async () => {
  const pending = deferred()
  const h = await editor({ getPerfScenarioDefaults: () => pending.promise })
  try {
    await settle(); h.route.params.id = '13'; await settle()
    const before = clone(h.state.form)
    pending.resolve(response(7)); await settle()
    assert.deepEqual(clone(h.state.form), before)
    assert.equal(h.state.form.name, 'Saved or copied scenario')
    assert.equal(h.state.form.account_pool_version, null)
    assert.equal(h.state.defaultsLoading.value, false)
  } finally { h.cleanup() }
})

test('saved and copied scenario records never request creation defaults or replace explicit choices', async () => {
  for (const id of ['13', '14']) {
    const h = await editor({}, id)
    try {
      await settle()
      assert.deepEqual(h.calls, [])
      assert.equal(h.state.form.load_config.concurrency, 17)
      assert.equal(h.state.form.load_config.iterations_per_vu, 0)
      assert.equal(h.state.form.runtime_config.keep_alive, false)
      assert.deepEqual(clone(h.state.form.runtime_config.auth_profile), {})
      assert.equal(h.state.form.sla_config.enabled, false)
      assert.equal(h.state.form.perf_targets.max_p95_rt, 321)
    } finally { h.cleanup() }
  }
})

test('retry after defaults failure uses the original baseline and retains a cleared authentication choice', async () => {
  let failing = true
  const h = await editor({ getPerfScenarioDefaults: async project => {
    if (failing) throw new Error('defaults offline')
    return response(project)
  } })
  try {
    await settle(); assert.match(h.state.defaultsError.value, /offline/)
    assert.equal(h.state.projectSelectionBlocked.value, true)
    h.state.form.runtime_config.auth_profile = {}
    h.state.form.runtime_config.account_identity_variable = ''
    h.state.form.runtime_config.timeout = 81
    failing = false; await h.state.loadProjectDefaults(7); await settle()
    assert.equal(h.state.defaultsError.value, '')
    assert.deepEqual(clone(h.state.form.runtime_config.auth_profile), {})
    assert.equal(h.state.form.runtime_config.account_identity_variable, '')
    assert.equal(h.state.form.runtime_config.timeout, 81)
  } finally { h.cleanup() }
})

test('engine changes invalidate a pending request and returning to K6 uses only the latest response', async () => {
  let firstFailure = true
  const stale = deferred(), latest = deferred()
  let requests = 0
  const h = await editor({ getPerfScenarioDefaults: async () => {
    if (firstFailure) { firstFailure = false; throw new Error('temporary') }
    return ++requests === 1 ? stale.promise : latest.promise
  } })
  try {
    await settle()
    const oldTask = h.state.loadProjectDefaults(7)
    h.state.form.engine = 'BUILTIN'; await settle()
    stale.resolve(response(7)); await oldTask; await settle()
    assert.equal(h.state.form.account_pool_version, null)
    h.state.form.engine = 'K6'; await settle()
    latest.resolve(response(7)); await settle()
    assert.equal(h.state.form.account_pool_version, 700)
    assert.equal(h.state.defaultsLoading.value, false)
  } finally { h.cleanup() }
})
