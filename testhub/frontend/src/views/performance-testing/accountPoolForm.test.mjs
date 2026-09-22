import test from 'node:test'
import assert from 'node:assert/strict'
import { accountImportErrors, mappingIssue, buildAccountImport, bindingState } from './accountPoolForm.mjs'
import zh from '../../locales/lang/zh-cn/performance-testing.js'
import { readFile } from 'node:fs/promises'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as accountHelpers from './accountPoolForm.mjs'
import * as environmentHelpers from './environmentForm.mjs'
import * as scenarioActions from './scenarioActionState.mjs'
import * as scenarioDefaults from './scenarioDefaults.mjs'
import * as websocketForm from './websocketStepForm.mjs'
import * as sseForm from './sseStepForm.mjs'
import * as recoveryForm from './reminderRecovery.mjs'
import { renderToString } from '@vue/server-renderer'

const t = (key, params = {}) => {
  const value = key.split('.').slice(1).reduce((obj, part) => obj?.[part], zh)
  return String(value || key).replace(/\{(\w+)\}/g, (_, name) => params[name] ?? '')
}

test('structured DRF string leaves locate duplicate identities without echoing server values', () => {
  const error = { response: { data: { errors: [{ row: '3', field: '1', code: 'duplicate_identity', message: 'unsafe-secret' }] } } }
  const message = accountImportErrors(error, ['账号ID', '口令'], t)[0]
  assert.match(message, /第 3 行/)
  assert.match(message, /账号ID/)
  assert.match(message, /身份重复/)
  assert.doesNotMatch(message, /unsafe-secret|duplicate_identity/)
})

test('mapping accepts Chinese columns but rejects duplicated columns, names and reserved identifiers', () => {
  const columns = ['账号ID', '口令']
  assert.equal(mappingIssue([{ name: 'user_id', column: '账号ID' }], columns), '')
  for (const rows of [
    [{ name: '用户', column: '账号ID' }], [{ name: '__proto__', column: '账号ID' }],
    [{ name: 'id', column: '账号ID' }, { name: 'id', column: '口令' }],
    [{ name: 'id', column: '账号ID' }, { name: 'password', column: '账号ID' }]
  ]) assert.notEqual(mappingIssue(rows, columns), '')
  const data = buildAccountImport({ project: 7, name: '  QA  ', identity_column: '账号ID', group_column: '', mapping: [{ name: 'user_id', column: '账号ID' }] }, new Blob(['fictional']))
  assert.deepEqual(JSON.parse(data.get('field_mapping')), { user_id: '账号ID' })
  assert.equal(data.get('name'), 'QA')
})

test('binding requires the exact available version and group, separates 1 VU debug from formal capacity', () => {
  const version = { id: 9, row_count: 3, groups: [{ id: 'opaque', count: 1 }], field_mapping: { user_id: '账号ID' } }
  assert.deepEqual(bindingState({ versionId: 9, group: 'opaque', version, concurrency: 100 }), { invalid: false, conflict: false, capacity: 1, debugBlocked: false, executeBlocked: true })
  assert.equal(bindingState({ versionId: 9, version: null, concurrency: 1 }).invalid, true)
  assert.equal(bindingState({ versionId: 9, group: 'missing', version, concurrency: 1 }).invalid, true)
  assert.equal(bindingState({ versionId: 9, version, concurrency: 1, variableNames: ['user_id'] }).conflict, true)
})

const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
async function compileComponent(file, dependencies) {
  const source = await readFile(new URL(file, import.meta.url), 'utf8')
  const descriptor = parse(source).descriptor
  const template = compileTemplate({ source: descriptor.template.content, id: file })
  assert.deepEqual(template.errors, [], 'the actual Vue template compiles')
  const compiled = compileScript(descriptor, { id: file }).content
  const modules = []
  const code = compiled.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(name === 'vue' ? Vue : name.endsWith('environmentForm.mjs') ? environmentHelpers
      : name.endsWith('accountPoolForm.mjs') ? accountHelpers : name.endsWith('scenarioActionState.mjs') ? scenarioActions
        : name.endsWith('scenarioDefaults.mjs') ? scenarioDefaults : name.endsWith('reminderRecovery.mjs') ? recoveryForm : name.endsWith('sseStepForm.mjs') ? sseForm : name.endsWith('websocketStepForm.mjs') ? websocketForm : dependencies[name] || {}) - 1
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
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
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const dependenciesFor = (api, route = { query: {}, params: {} }) => ({ '@/api/performance-testing': api,
  'vue-i18n': { useI18n: () => ({ t }) },
  'vue-router': { useRoute: () => route, useRouter: () => ({ push() {}, replace() {} }), onBeforeRouteLeave() {} },
  'element-plus': { ElMessage: { success() {}, error() {}, info() {}, warning() {} }, ElMessageBox: { confirm: async () => true } },
  './shared': { apiError: (_, fallback) => fallback, formatDuration: String } })
const version = (overrides = {}) => ({ id: 9, pool: 2, version: 1, row_count: 3, columns: ['账号ID', '口令'], identity_column: '账号ID',
  field_mapping: { user_id: '账号ID', password: '口令' }, group_column: '', groups: [{ id: 'opaque', label: '分组 1', count: 1 }], ...overrides })
const baseApi = () => ({ getPerfProjects: async () => ({ data: [{ id: 7, name: 'QA' }, { id: 8, name: 'Other' }] }),
  getPerfEnvironmentPermissions: async () => ({ data: { project_ids: [7, 8] } }),
  getPerfAccountPools: async params => ({ data: [{ id: 2, project: params.project, name: 'QA pool', latest_version: version() }] }),
  getPerfAccountPoolVersion: async () => ({ data: version() }), getPerfAccountPoolVersions: async () => ({ data: [version()] }) })

test('actual import dialog preserves file and mapping after server rejection, then retries once with the same form data', async () => {
  const payloads = []
  const api = { ...baseApi(), inspectPerfAccountPool: async () => ({ data: { columns: ['账号ID', '口令'], row_count: 3, rows: [{ 账号ID: 'unexpected-visible-value' }] } }),
    createPerfAccountPool: async payload => { payloads.push(payload); if (payloads.length === 1) throw { response: { data: { errors: [{ row: '3', field: '1', code: 'duplicate_identity' }] } } }; return { data: { id: 2 } } } }
  const mounted = mount(await compileComponent('./AccountPoolManagement.vue', dependenciesFor(api)))
  try {
    await settle()
    const page = mounted.state
    page.openImport()
    page.form.value.name = 'User pool'
    const file = new File(['id,password\n1,test'], 'accounts.csv')
    page.onFileChange({ target: { files: [file] } })
    await settle()
    page.form.value.identity_column = '账号ID'
    page.form.value.mapping = [{ name: 'user_id', column: '账号ID' }, { name: 'password', column: '口令' }]
    await page.submitImport()
    assert.equal(page.importVisible.value, true)
    assert.equal(page.file.value, file)
    assert.match(page.importErrors.value[0], /第 3 行.*身份重复/)
    assert.equal(page.saving.value, false)
    await page.submitImport()
    assert.equal(payloads.length, 2)
    assert.equal(payloads[0].get('field_mapping'), payloads[1].get('field_mapping'))
    assert.equal(page.importVisible.value, false)
  } finally { mounted.unmount() }
})

test('actual import dialog ignores obsolete file inspection and project-change responses', async () => {
  const first = deferred(), second = deferred(), third = deferred()
  const queue = [first, second, third]
  const mounted = mount(await compileComponent('./AccountPoolManagement.vue', dependenciesFor({ ...baseApi(), inspectPerfAccountPool: () => queue.shift().promise })))
  try {
    await settle()
    const page = mounted.state
    page.openImport()
    page.onFileChange({ target: { files: [new File(['a'], 'a.csv')] } })
    page.onFileChange({ target: { files: [new File(['b'], 'b.json')] } })
    second.resolve({ data: { columns: ['second'], row_count: 2, rows: [] } }); await settle()
    first.resolve({ data: { columns: ['stale'], row_count: 1000, rows: [] } }); await settle()
    assert.deepEqual(page.inspection.value.columns, ['second'])
    page.onFileChange({ target: { files: [new File(['c'], 'c.csv')] } })
    page.project.value = 8
    page.changeProject()
    third.resolve({ data: { columns: ['old-project'], row_count: 1000, rows: [] } }); await settle()
    assert.equal(page.inspection.value, null)
    assert.equal(page.file.value, null)
    assert.equal(page.importVisible.value, false)
    assert.equal(page.pools.value[0].project, 8)
  } finally { mounted.unmount() }
})

test('actual new-version import retains a compatible previous mapping and calls the version endpoint', async () => {
  const payloads = []
  const mounted = mount(await compileComponent('./AccountPoolManagement.vue', dependenciesFor({ ...baseApi(),
    inspectPerfAccountPool: async () => ({ data: { columns: ['账号ID', '口令'], row_count: 6, rows: [] } }),
    createPerfAccountPoolVersion: async (id, payload) => { payloads.push({ id, mapping: payload.get('field_mapping') }); return { data: version({ id: 10, version: 2 }) } } })))
  try {
    await settle(); const page = mounted.state
    page.openImport(page.pools.value[0])
    page.onFileChange({ target: { files: [new File(['x'], 'new.json')] } }); await settle()
    assert.equal(page.form.value.identity_column, '账号ID')
    await page.submitImport()
    assert.deepEqual(payloads, [{ id: 2, mapping: JSON.stringify(version().field_mapping) }])
  } finally { mounted.unmount() }
})

async function selectorHarness(api, initial = {}) {
  let selector
  const props = { project: 7, modelValue: 9, group: '', engine: 'K6', concurrency: 100, ...initial }
  let emitted
  const feedback = () => ({ ...props, 'onUpdate:modelValue': value => { props.modelValue = value; Vue.nextTick(() => selector.update(feedback())) },
    'onUpdate:group': value => { props.group = value; Vue.nextTick(() => selector.update(feedback())) }, onState: value => { emitted = value } })
  selector = mount(await compileComponent('./components/AccountPoolSelector.vue', dependenciesFor(api)), feedback())
  await settle()
  return { selector, props, get state() { return emitted }, async update(values) { Object.assign(props, values); selector.update(feedback()); await settle() } }
}

test('actual selector restores the pinned version, retries failures, updates capacity by group and preserves legacy variables', async () => {
  let fail = true
  const api = { ...baseApi(), getPerfAccountPoolVersion: async () => { if (fail) throw new Error('offline'); return { data: version() } } }
  const harness = await selectorHarness(api)
  try {
    assert.equal(harness.state.error, true)
    assert.equal(harness.props.modelValue, 9)
    fail = false
    await harness.selector.state.reload(); await settle()
    assert.equal(harness.state.error, false)
    assert.equal(harness.state.capacity, 3)
    assert.equal(harness.state.debugBlocked, false)
    assert.equal(harness.state.executeBlocked, true)
    await harness.update({ group: 'opaque' })
    assert.equal(harness.state.capacity, 1)
    await harness.update({ variableNames: ['user_id'], hasLegacyCsv: true })
    assert.equal(harness.state.conflict, true)
    assert.equal(harness.props.modelValue, 9)
    await harness.update({ project: 8, modelValue: null, group: '' })
    assert.equal(harness.selector.state.poolId.value, null)
    assert.equal(harness.selector.state.versions.value.length, 0)
  } finally { harness.selector.unmount() }
})

test('selecting a pool with a failed version load remains blocked and retry can complete the pending selection', async () => {
  let fail = true
  const harness = await selectorHarness({ ...baseApi(), getPerfAccountPoolVersions: async () => { if (fail) throw new Error('offline'); return { data: [version()] } } }, { modelValue: null })
  try {
    await harness.selector.state.selectPool(2); await settle()
    assert.equal(harness.state.pending, true)
    assert.equal(harness.state.error, true)
    fail = false
    await harness.selector.state.reload(); await settle()
    assert.equal(harness.props.modelValue, 9)
    assert.equal(harness.state.pending, false)
    assert.equal(harness.state.capacity, 3)
  } finally { harness.selector.unmount() }
})

test('a pending pool choice stays blocked throughout retry and can be explicitly cleared after failure', async () => {
  const retry = deferred()
  let loads = 0
  const harness = await selectorHarness({ ...baseApi(), getPerfAccountPools: async params => {
    loads++
    if (loads === 2) return retry.promise
    return { data: [{ id: 2, project: params.project, name: 'Pool' }] }
  }, getPerfAccountPoolVersions: async () => { throw new Error('offline') } }, { modelValue: null })
  try {
    await harness.selector.state.selectPool(2); await settle()
    const pending = harness.selector.state.reload()
    assert.equal(harness.state.pending, true, 'retry must not silently make an unbound scenario runnable')
    retry.reject(new Error('offline')); await pending
    harness.selector.state.clear(); await settle()
    assert.equal(harness.state.pending, false)
    assert.equal(harness.props.modelValue, null)
  } finally { harness.selector.unmount() }
})

test('actual scenario round-trips pool references and old CSV variables; capacity blocks formal handlers while 1 VU debug saves and proceeds', async () => {
  const calls = { save: [], debug: 0, execute: 0, preflight: 0 }
  const oldCsv = { name: 'old_id', type: 'CSV', data_file_id: 44, column: 'ID', secret: true }
  let saved = { id: 13, project: 7, name: 'QA', engine: 'K6', account_pool_version: 9, account_pool_group: '', variables: [oldCsv],
    load_config: { model: 'CONCURRENCY', concurrency: 100, iterations_per_vu: 10 }, steps: [] }
  const api = { ...baseApi(), getEngineStatus: async () => ({ data: { engines: [{ name: 'K6', available: true, capabilities: {
    items: [{ id: 'debug', enabled: true }, { id: 'stop', enabled: true }], verification: { state: 'matched' } } }] } }),
  getPerfEnvironments: async () => ({ data: [] }), getPerfDataFiles: async () => ({ data: [] }), getPerfScenario: async () => ({ data: saved }),
  updatePerfScenario: async (id, payload) => { calls.save.push(structuredClone(payload)); saved = { ...payload, id }; return { data: saved } },
  savePerfScenarioSteps: async () => ({ data: [] }), debugPerfScenario: async () => { calls.debug++; return { data: { execution: { id: 77 } } } },
  preflightPerfScenario: async () => { calls.preflight++; return { data: { passed: true } } }, executePerfScenario: async () => { calls.execute++; return { data: { execution: { id: 77 } } } } }
  const originalWindow = globalThis.window
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const mounted = mount(await compileComponent('./ScenarioEditor.vue', dependenciesFor(api, { params: { id: '13' }, query: {} })))
  try {
    await settle(); const page = mounted.state
    assert.equal(page.accountPoolBlocked.value, true, 'no metadata must fail closed')
    assert.equal(await page.handleSave(), false)
    page.accountPoolState.value = { project: 7, versionId: 9, group: '', capacity: 3, mappedNames: ['user_id'], loading: false, error: false, invalid: false }
    assert.equal(page.canExecute.value, false)
    assert.equal(page.canDebug.value, true)
    await page.handleSaveAndExecute(); await page.doExecute()
    assert.deepEqual([calls.preflight, calls.execute], [0, 0])
    page.dirty.value = true
    await page.handleDebug()
    assert.equal(calls.debug, 1)
    assert.equal(calls.save[0].account_pool_version, 9)
    assert.equal(calls.save[0].load_config.concurrency, 100)
    assert.deepEqual(calls.save[0].variables, [oldCsv])
    page.applyScenario(saved)
    assert.equal(page.form.account_pool_version, 9)
    assert.equal(page.form.load_config.concurrency, 100)
    page.form.account_pool_group = 'missing'
    assert.equal(await page.handleSave(), false)
    page.form.account_pool_group = ''
    page.accountPoolState.value.error = true
    assert.equal(await page.handleSave(), false)
    await page.handleDebug(); await page.doExecute()
    assert.deepEqual([calls.debug, calls.execute], [1, 0])
    page.accountPoolState.value.error = false
    page.form.engine = 'BUILTIN'
    assert.equal(page.accountPoolBlocked.value, true)
    assert.equal(page.form.account_pool_version, 9, 'changing engine must not erase the reference')
    page.form.engine = 'K6'
    page.form.load_config.concurrency = 1
    page.form.variables.push({ name: 'user_id', type: 'CONSTANT', value: 'old' })
    assert.equal(await page.handleSave(), false, 'conflicts must block the handler before child rerender')
    page.form.variables.pop()
    page.form.project = 8; await settle()
    assert.equal(page.form.account_pool_version, null)
    assert.equal(page.form.account_pool_group, '')
    assert.deepEqual(JSON.parse(JSON.stringify(page.form.variables)), [oldCsv])
  } finally { mounted.unmount(); if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow }
})

test('management distinguishes a failed list from an empty result and refresh recovers', async () => {
  let fail = true
  const mounted = mount(await compileComponent('./AccountPoolManagement.vue', dependenciesFor({ ...baseApi(), getPerfAccountPools: async () => {
    if (fail) throw { response: { status: 403, data: { detail: 'private-content' } } }
    return { data: [] }
  } })))
  try {
    await settle(); const page = mounted.state
    assert.match(page.pageError.value, /无权/)
    assert.doesNotMatch(page.pageError.value, /private-content/)
    page.openImport(); assert.equal(page.importVisible.value, false)
    fail = false; await page.initialize(); await settle()
    assert.equal(page.pageError.value, '')
    assert.deepEqual(page.pools.value, [])
    page.openImport(); assert.equal(page.importVisible.value, true)
  } finally { mounted.unmount() }
})

test('version history retries preview failure and displays historical usage without treating failure as empty usage', async () => {
  let fail = true
  const mounted = mount(await compileComponent('./AccountPoolManagement.vue', dependenciesFor({ ...baseApi(),
    previewPerfAccountPoolVersion: async () => { if (fail) throw new Error('offline'); return { data: { columns: ['账号ID'], rows: [{ 账号ID: '******' }] } } },
    getPerfAccountPoolUsage: async () => ({ data: { scenarios: [{ id: 13, name: 'Saved scenario' }], executions: [{ id: 77, status: 'COMPLETED' }] } }) })))
  try {
    await settle(); const page = mounted.state
    await page.openHistory(page.pools.value[0])
    assert.notEqual(page.historyError.value, '')
    assert.equal(page.historyVersion.value, null)
    fail = false; await page.openHistory(page.historyPool.value)
    assert.equal(page.historyError.value, '')
    assert.equal(page.historyVersion.value.id, 9)
    assert.equal(page.usage.value.scenarios[0].id, 13)
    assert.equal(page.usage.value.executions[0].id, 77)
  } finally { mounted.unmount() }
})

test('actual management template masks all preview cells even if an upstream response contains visible values', async () => {
  const mounted = mount(await compileComponent('./AccountPoolManagement.vue', dependenciesFor(baseApi())))
  try {
    await settle()
    const state = mounted.state
    state.importVisible.value = true
    state.inspection.value = { columns: ['账号ID', '口令'], row_count: 1, rows: [{ 账号ID: 'fictional-id-should-stay-hidden', 口令: 'fictional-secret-should-stay-hidden' }] }
    const source = await readFile(new URL('./AccountPoolManagement.vue', import.meta.url), 'utf8')
    const template = compileTemplate({ source: parse(source).descriptor.template.content, id: 'mask-preview' })
    const code = template.code.replace(/import\s+\{([^}]+)\}\s+from\s+["']vue["']/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = Vue;`)
      .replace('export function render', 'return function render')
    const render = new Function('Vue', code)(Vue)
    const app = Vue.createSSRApp({ setup: () => Vue.proxyRefs({ ...state, router: { push() {} } }), render })
    const wrapper = { setup(_, { slots }) { return () => Vue.h('div', slots.default?.()) } }
    for (const name of ['ElButton', 'ElForm', 'ElFormItem', 'ElAlert', 'ElInput', 'ElSelect', 'ElOption', 'ElEmpty', 'ElDescriptions', 'ElDescriptionsItem']) app.component(name, wrapper)
    app.component('ElDialog', { props: ['modelValue'], setup(props, { slots }) { return () => props.modelValue ? Vue.h('section', slots.default?.()) : null } })
    app.component('ElTable', { props: ['data'], setup(props, { slots }) {
      Vue.provide('rows', () => props.data || [])
      return () => Vue.h('table', slots.default?.())
    } })
    app.component('ElTableColumn', { setup(_, { slots }) { const rows = Vue.inject('rows'); return () => Vue.h('tbody', rows().map(row => Vue.h('tr', slots.default?.({ row })))) } })
    app.directive('loading', {})
    const html = await renderToString(app)
    assert.match(html, /\*\*\*\*\*\*/)
    assert.doesNotMatch(html, /fictional-id-should-stay-hidden|fictional-secret-should-stay-hidden/)
  } finally { mounted.unmount() }
})
