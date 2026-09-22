import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'
import * as environmentHelpers from './environmentForm.mjs'
import * as scenarioActions from './scenarioActionState.mjs'
import * as scenarioDefaults from './scenarioDefaults.mjs'
import * as websocketForm from './websocketStepForm.mjs'
import * as sseForm from './sseStepForm.mjs'
import * as recoveryForm from './reminderRecovery.mjs'

const renderer = Vue.createRenderer({
  createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {}
})

async function compileComponent(file, dependencies) {
  const source = await readFile(new URL(file, import.meta.url), 'utf8')
  const compiled = compileScript(parse(source).descriptor, { id: file }).content
  const modules = []
  const code = compiled.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(name === 'vue' ? Vue : name.endsWith('environmentForm.mjs') ? environmentHelpers
      : name.endsWith('scenarioActionState.mjs') ? scenarioActions : name.endsWith('scenarioDefaults.mjs') ? scenarioDefaults : name.endsWith('reminderRecovery.mjs') ? recoveryForm : name.endsWith('sseStepForm.mjs') ? sseForm : name.endsWith('websocketStepForm.mjs') ? websocketForm : dependencies[name] || {}) - 1
    return binding.trim().startsWith('{')
      ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n`
      : `const ${binding} = __modules[${index}].default;\n`
  }).replace('export default', 'return')
  return new Function('__modules', code)(modules)
}

function mount(component, initialProps = {}) {
  let state
  const setup = component.setup
  const mounted = { ...component, setup(props, context) { state = setup(props, context); return state }, render: () => null }
  const container = {}
  const update = props => renderer.render(Vue.h(mounted, props), container)
  update(initialProps)
  return { state, update, unmount: () => renderer.render(null, container) }
}

const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const sourceEnvironment = () => ({ id: 1, name: 'QA', scope: 'PROJECT', project: 7, base_url: 'https://example.test',
  headers: { Authorization: '******', Accept: 'application/json' }, verify_ssl: true, is_active: false,
  variables: [{ name: 'token', type: 'CONSTANT', secret: true, value: '******' },
    { name: 'public', type: 'ENUM', values: ['a', 'b'], strategy: 'ROUND_ROBIN' }] })

async function copiedEnvironmentHarness() {
  const created = []
  const api = {
    getPerfEnvironmentPermissions: async () => ({ data: { can_manage_global: true, project_ids: [7] } }),
    getPerfProjects: async () => ({ data: [{ id: 7, name: 'QA' }] }),
    getPerfEnvironments: async () => ({ data: [] }),
    getPerfEnvironment: async () => ({ data: sourceEnvironment() }),
    getPerfDataFiles: async () => ({ data: [] }),
    createPerfEnvironment: async payload => { created.push(JSON.parse(JSON.stringify(payload))); return { data: { id: 2 } } }
  }
  const dependencies = { '@/api/performance-testing': api,
    'vue-i18n': { useI18n: () => ({ t: key => key }) },
    'vue-router': { useRoute: () => ({ query: {} }) },
    'element-plus': { ElMessage: { success() {}, error() {} }, ElMessageBox: {} },
    './shared': { apiError: error => error.message } }
  const page = mount(await compileComponent('./EnvironmentManagement.vue', dependencies))
  await settle()
  await page.state.openForm({ id: 1 }, true)
  const fieldsComponent = await compileComponent('./components/EnvironmentFields.vue', dependencies)
  let fields
  const fieldProps = () => ({
    headers: page.state.form.value.headers, variables: page.state.form.value.variables, project: 7,
    copyState: page.state.copySecrets.value,
    'onUpdate:headers': value => { page.state.form.value.headers = value; fields.update(fieldProps()) },
    'onUpdate:variables': value => { page.state.form.value.variables = value; fields.update(fieldProps()) },
    onInvalid: value => { page.state.fieldsInvalid.value = value }
  })
  fields = mount(fieldsComponent, fieldProps())
  await settle()
  return { page: page.state, get fields() { return fields.state }, created,
    async remountFields() { fields.unmount(); fields = mount(fieldsComponent, fieldProps()); await settle() },
    cleanup() { fields.unmount(); page.unmount() } }
}

for (const name of ['changed_token', ' token ', 'token']) {
  test(`actual copy form blocks renamed empty secrets (${JSON.stringify(name)}) through props feedback and remount`, async () => {
    const harness = await copiedEnvironmentHarness()
    try {
      const { page, fields, created } = harness
      fields.headerRows.value[0].key = ' X-Auth-Token '
      fields.emitHeaders()
      fields.updateVariable(0, 'name', name)
      await settle()
      await harness.remountFields()
      await page.save()
      assert.equal(created.length, 0, 'blank copied secrets must not reach create API')
      assert.equal(page.dialogError.value, 'performanceTesting.environment.copyNeedsSecrets')
    } finally { harness.cleanup() }
  })
}

for (const target of ['header', 'variable']) {
  test(`renaming only the empty copied ${target} stays blocked when the other secret is already refilled`, async () => {
    const harness = await copiedEnvironmentHarness()
    try {
      if (target === 'header') {
        harness.fields.headerRows.value[0].key = ' X-Auth-Token '
        harness.fields.emitHeaders()
        harness.fields.updateVariable(0, 'value', 'refilled-token')
      } else {
        harness.fields.headerRows.value[0].value = 'refilled-header'
        harness.fields.emitHeaders()
        harness.fields.updateVariable(0, 'name', ' token ')
      }
      await settle()
      await harness.remountFields()
      await harness.page.save()
      assert.equal(harness.created.length, 0)
      assert.equal(harness.page.dialogError.value, 'performanceTesting.environment.copyNeedsSecrets')
    } finally { harness.cleanup() }
  })
}

test('actual copy form allows refill after renaming and sends only API fields', async () => {
  const harness = await copiedEnvironmentHarness()
  try {
    harness.fields.headerRows.value[0].key = ' X-Auth-Token '
    harness.fields.emitHeaders()
    harness.fields.updateVariable(0, 'name', ' token ')
    await settle()
    await harness.remountFields()
    harness.fields.headerRows.value[0].value = 'refilled-header'
    harness.fields.emitHeaders()
    harness.fields.updateVariable(0, 'value', 'refilled-token')
    await settle()
    await harness.page.save()
    assert.equal(harness.created.length, 1)
    const payload = harness.created[0]
    assert.deepEqual(payload.headers, { 'X-Auth-Token': 'refilled-header', Accept: 'application/json' })
    assert.deepEqual(payload.variables[0], { name: ' token ', type: 'CONSTANT', secret: true, value: 'refilled-token' })
    assert.deepEqual(payload.variables[1], sourceEnvironment().variables[1])
    assert.deepEqual(Object.keys(payload).sort(), ['name', 'scope', 'project', 'base_url', 'headers', 'variables', 'verify_ssl', 'is_active'].sort())
  } finally { harness.cleanup() }
})

test('only deleting the actual copied rows clears empty secret requirements', async () => {
  const harness = await copiedEnvironmentHarness()
  try {
    harness.fields.headerRows.value[0].key = ''
    harness.fields.emitHeaders()
    harness.fields.updateVariable(0, 'name', '')
    await settle()
    await harness.page.save()
    assert.equal(harness.created.length, 0)
    harness.fields.removeHeader(0)
    await settle()
    await harness.page.save()
    assert.equal(harness.created.length, 0, 'removing a header must not clear the remaining variable requirement')
    harness.fields.removeVariable(0)
    await settle()
    await harness.remountFields()
    await harness.page.save()
    assert.equal(harness.created.length, 1)
    assert.deepEqual(harness.created[0].headers, { Accept: 'application/json' })
    assert.deepEqual(harness.created[0].variables, [sourceEnvironment().variables[1]])
  } finally { harness.cleanup() }
})

test('saved inherited HTTPS resolution uses the runner TLS default and never adds an override', () => {
  const resolution = { env_config: { base_url: 'https://example.test', headers: {} }, sources: [] }
  assert.equal(environmentHelpers.effectiveTlsVerified(resolution.env_config), true)
  assert.equal(environmentHelpers.effectiveTlsVerified({ verify_ssl: true }), true)
  assert.equal(environmentHelpers.effectiveTlsVerified({ verify_ssl: false }), false)
  assert.equal(Object.hasOwn(resolution.env_config, 'verify_ssl'), false)
})

test('the actual scenario saved-resolution template displays TLS on for inherit and only off for false', async () => {
  const source = await readFile(new URL('./ScenarioEditor.vue', import.meta.url), 'utf8')
  const fragment = parse(source).descriptor.template.content.match(/<el-descriptions-item[^>]*performanceTesting\.editor\.verifySsl[^>]*>[\s\S]*?<\/el-descriptions-item>/)?.[0]
  assert.ok(fragment, 'saved TLS summary must remain present')
  const compiled = compileTemplate({ source: fragment, id: 'saved-tls-regression' })
  assert.deepEqual(compiled.errors, [])
  const render = new Function('__vue', compiled.code
    .replace(/import\s+\{([\s\S]*?)\}\s+from\s+['"]vue['"]/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = __vue`)
    .replace('export function render', 'return function render'))(Vue)
  for (const [config, expected] of [[{}, 'tlsOn'], [{ verify_ssl: true }, 'tlsOn'], [{ verify_ssl: false }, 'tlsOff']]) {
    const app = Vue.createSSRApp({
      components: { ElDescriptionsItem: { setup(_, { slots }) { return () => Vue.h('span', slots.default?.()) } } },
      setup: () => ({ savedEnvironment: { env_config: { base_url: 'https://example.test', ...config } },
        effectiveTlsVerified: environmentHelpers.effectiveTlsVerified, t: key => key }), render
    })
    const html = await renderToString(app)
    assert.ok(html.includes(`performanceTesting.environment.${expected}`), html)
  }
})

async function scenarioProjectHarness({ failure = 'permissions', existing = false, projectList = [{ id: 7, name: 'QA' }] } = {}) {
  let failing = Boolean(failure)
  const calls = { projects: 0, permissions: 0, create: 0, debug: 0, execute: 0 }
  const saved = { id: 13, project: 7, name: 'Saved scene', engine: 'K6', environment: 10, global_environment: 11,
    env_config: { base_url: '', headers: { Authorization: '******' } },
    variables: [{ name: 'token', type: 'CONSTANT', secret: true, value: '******' }], steps: [],
    resolved_environment: { env_config: { base_url: 'https://example.test' }, sources: [{ id: 10, name: 'QA', version: 1 }] } }
  const api = {
    getEngineStatus: async () => ({ data: { engines: [{ name: 'K6', available: true, capabilities: {
      items: [{ id: 'debug', enabled: true }, { id: 'stop', enabled: true }], verification: { state: 'matched' } } }] } }),
    getPerfProjects: async () => { calls.projects++; if (failing && failure === 'projects') throw new Error('Network Error'); return { data: projectList } },
    getPerfEnvironmentPermissions: async () => { calls.permissions++; if (failing && failure === 'permissions') throw { response: { status: 403 } }; return { data: { can_manage_global: false, project_ids: [7] } } },
    getPerfEnvironments: async params => ({ data: params.scope === 'GLOBAL'
      ? [{ id: 11, scope: 'GLOBAL', project: null }] : [{ id: 10, scope: 'PROJECT', project: 7 }] }),
    getPerfDataFiles: async () => ({ data: [] }),
    getPerfScenario: async () => ({ data: JSON.parse(JSON.stringify(saved)) }),
    getPerfScenarioDefaults: async project => ({ data: { project, defaults: { environment: null, global_environment: null, account_pool_version: null, account_pool_group: '', runtime_config: {} } } }),
    createPerfScenario: async payload => { calls.create++; return { data: { ...payload, id: 1 } } },
    savePerfScenarioSteps: async () => ({ data: [] }),
    debugPerfScenario: async () => { calls.debug++; return { data: {} } },
    executePerfScenario: async () => { calls.execute++; return { data: {} } }
  }
  const dependencies = { '@/api/performance-testing': api,
    'vue-router': { useRoute: () => ({ params: existing ? { id: '13' } : {}, query: { project: '7' } }), useRouter: () => ({ push() {}, replace() {} }), onBeforeRouteLeave() {} },
    'vue-i18n': { useI18n: () => ({ t: key => key }) },
    'element-plus': { ElMessage: { success() {}, error() {}, warning() {}, info() {} }, ElMessageBox: {} },
    './shared': { apiError: (_, fallback) => fallback, formatDuration: String } }
  const previousWindow = globalThis.window
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const mounted = mount(await compileComponent('./ScenarioEditor.vue', dependencies))
  await settle()
  return { page: mounted.state, calls, recover: () => { failing = false },
    cleanup() { mounted.unmount(); if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow } }
}

for (const failure of ['permissions', 'projects']) {
  test(`new scenario exposes ${failure} load failure, blocks actions, and retries both metadata sources without clearing the draft`, async () => {
    const harness = await scenarioProjectHarness({ failure })
    try {
      const { page, calls } = harness
      assert.equal(page.projectsLoadError?.value, true)
      assert.equal(page.scenarioReady.value, false)
      page.form.name = 'Draft to keep'
      page.form.engine = 'K6'
      await settle()
      assert.equal(page.canDebug.value, false)
      assert.equal(page.canExecute.value, false)
      assert.equal(await page.handleSave(), false)
      await page.handleDebug()
      await page.handleSaveAndExecute()
      assert.deepEqual([calls.create, calls.debug, calls.execute], [0, 0, 0])
      const previous = { projects: calls.projects, permissions: calls.permissions }
      harness.recover()
      await page.loadProjects()
      await settle()
      assert.equal(calls.projects, previous.projects + 1)
      assert.equal(calls.permissions, previous.permissions + 1)
      assert.equal(page.projectsLoadError.value, false)
      assert.equal(page.projectsLoaded.value, true)
      assert.equal(page.scenarioReady.value, true)
      assert.equal(page.form.project, 7)
      assert.equal(page.form.name, 'Draft to keep')
      assert.equal(page.canDebug.value, true)
      assert.equal(page.canExecute.value, true)
    } finally { harness.cleanup() }
  })
}

test('a successful empty project list stays distinct from a failed load and cannot submit a new scenario', async () => {
  const harness = await scenarioProjectHarness({ failure: null, projectList: [] })
  try {
    assert.equal(harness.page.projectsLoadError?.value, false)
    assert.equal(harness.page.projectsLoaded?.value, true)
    assert.equal(harness.page.projects.value.length, 0)
    assert.equal(harness.page.projectSelectionBlocked?.value, true)
    assert.equal(await harness.page.handleSave(), false)
    assert.equal(harness.calls.create, 0)
  } finally { harness.cleanup() }
})

test('metadata failure and recovery preserve an existing scenario and its environment references', async () => {
  const harness = await scenarioProjectHarness({ existing: true })
  try {
    const before = JSON.parse(JSON.stringify(harness.page.buildPayload()))
    assert.equal(harness.page.projectsLoadError?.value, true)
    assert.equal(harness.page.scenarioReady.value, true)
    assert.equal(harness.page.form.environment, 10)
    harness.recover()
    await harness.page.loadProjects()
    await settle()
    assert.deepEqual(JSON.parse(JSON.stringify(harness.page.buildPayload())), before)
  } finally { harness.cleanup() }
})
