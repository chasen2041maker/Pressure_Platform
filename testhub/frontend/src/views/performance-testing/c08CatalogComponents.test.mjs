import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as catalog from './apiCatalogForm.mjs'
import * as environment from './environmentForm.mjs'
import * as actionState from './scenarioActionState.mjs'
import * as scenarioDefaults from './scenarioDefaults.mjs'
import * as websocketForm from './websocketStepForm.mjs'
import * as sseForm from './sseStepForm.mjs'
import * as recoveryForm from './reminderRecovery.mjs'
import zh from '../../locales/lang/zh-cn/performance-testing.js'
import en from '../../locales/lang/en/performance-testing.js'
import { baseCompile } from '@intlify/message-compiler'
import { renderToString } from '@vue/server-renderer'
import { ElButton } from 'element-plus'
const clone = catalog.clone
const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
async function compileComponent(file, dependencies) {
  const source = await readFile(new URL(file, import.meta.url), 'utf8')
  const descriptor = parse(source).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: file }).errors, [])
  const compiled = compileScript(descriptor, { id: file }).content
  const modules = []
  const code = compiled.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(name === 'vue' ? Vue : name.endsWith('apiCatalogForm.mjs') ? catalog : name.endsWith('environmentForm.mjs') ? environment
      : name.endsWith('scenarioActionState.mjs') ? actionState : name.endsWith('scenarioDefaults.mjs') ? scenarioDefaults : name.endsWith('reminderRecovery.mjs') ? recoveryForm : name.endsWith('sseStepForm.mjs') ? sseForm : name.endsWith('websocketStepForm.mjs') ? websocketForm : dependencies[name] || {}) - 1
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
  }).replace('export default', 'return')
  return new Function('__modules', code)(modules)
}
function mount(component, initialProps = {}) {
  let state
  const mounted = { ...component, setup(props, context) { state = component.setup(props, context); return state }, render: () => null }
  const container = {}
  const update = props => renderer.render(Vue.h(mounted, props), container)
  update(initialProps)
  return { state, update, unmount: () => renderer.render(null, container) }
}
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const deps = (api, route = { params: {}, query: {} }) => ({ '@/api/performance-testing': api,
  'vue-i18n': { useI18n: () => ({ t: key => key }) }, 'element-plus': { ElMessage: { success() {}, error() {}, info() {}, warning() {} }, ElMessageBox: { confirm: async () => true } },
  'vue-router': { useRoute: () => route, useRouter: () => ({ push() {}, replace() {} }), onBeforeRouteLeave() {} },
  './shared': { apiError: (error, fallback) => error.message || fallback, formatDuration: String } })
const row = id => ({ id, name: `Request ${id}`, method: 'GET', path: `/${id}`, tags: ['group'], readiness: { ready: true, gaps: [] } })
const response = (rows, overrides = {}) => ({ data: { results: rows, count: rows.length, next: null, version: { version: 1 }, tags: ['group'], ...overrides } })
async function catalogHarness(api, extra = {}) {
  let selected = [], projectId = 7, mounted
  const props = () => ({ projectId, selectable: true, selectedIds: selected, ...extra, 'onUpdate:selectedIds': value => { selected = value; if (mounted) mounted.update(props()) } })
  mounted = mount(await compileComponent('./components/ProjectApiCatalog.vue', deps(api)), props())
  await settle()
  return { page: mounted.state, get selected() { return selected }, switchProject(value) { projectId = value; mounted.update(props()) }, cleanup: mounted.unmount }
}

test('catalog component keeps selections across pagination and filtering and selects all ready filtered results', async () => {
  const calls = []
  const api = { getPerfApiCatalog: async (_, params) => { calls.push(params); return response(Array.from({ length: params.page === 1 ? 50 : 7 }, (_, i) => row((params.page - 1) * 50 + i + 1)), { count: 57, next: params.page === 1 ? 'next' : null }) } }
  const h = await catalogHarness(api)
  try {
    h.page.toggle(1, true); await settle(); h.page.page.value = 2; await h.page.load()
    h.page.toggle(57, true); await settle(); h.page.filters.tag = 'group'; await settle(); await h.page.search(); await settle()
    assert.deepEqual(h.selected, [1, 57])
    await h.page.selectAll(); await settle()
    assert.equal(h.selected.length, 57)
    assert.ok(calls.slice(-2).every(call => call.ready === 'true' && call.tag === 'group'))
    h.page.clearSelection(); await settle(); assert.deepEqual(h.selected, [])
    h.page.toggle(2, true); await settle(); h.switchProject(8); await settle(); assert.deepEqual(h.selected, [])
  } finally { h.cleanup() }
})

test('catalog ignores old project list/detail/all-selection responses and recovers after 403', async () => {
  let delayed = false, forbidden = false
  const oldList = deferred(), oldDetail = deferred(), oldAll = deferred()
  const api = { getPerfApiCatalog: async (project, params) => { if (forbidden) throw { response: { status: 403 } }; if (delayed && project === 7) return params.ready ? oldAll.promise : oldList.promise; return response([row(project)]) },
    getPerfApiCatalogRequest: () => oldDetail.promise }
  const h = await catalogHarness(api)
  try {
    h.page.showDetail(7); delayed = true
    const listPromise = h.page.load(), allPromise = h.page.selectAll()
    h.switchProject(8); await settle()
    oldList.resolve(response([row(70)])); oldDetail.resolve({ data: { operation: { id: 70 }, version: { version: 1 } } }); oldAll.resolve(response([row(70)]))
    await Promise.all([listPromise, allPromise]); await settle()
    assert.equal(h.page.rows.value[0].id, 8); assert.equal(h.page.detail.value, null); assert.deepEqual(h.selected, [])
    forbidden = true; await h.page.load(); assert.match(h.page.error.value, /error403/); assert.equal(h.page.loading.value, false)
    forbidden = false; await h.page.load(); assert.equal(h.page.error.value, ''); assert.equal(h.page.rows.value[0].id, 8)
  } finally { h.cleanup() }
})

test('catalog all-selection is atomic on later-page failure and clear cancels an in-flight selection', async () => {
  let fail = false, pending = false
  const wait = deferred()
  const h = await catalogHarness({ getPerfApiCatalog: async (_, params) => { if (!params.ready) return response([row(1)]); if (pending) return wait.promise; if (params.page === 2 && fail) throw new Error('offline'); return response([row(params.page)], { count: 2, next: params.page === 1 ? 'next' : null }) } })
  try {
    h.page.toggle(99, true); await settle(); fail = true; await h.page.selectAll(); assert.deepEqual(h.selected, [99])
    fail = false; await h.page.selectAll(); await settle(); assert.deepEqual(h.selected, [99, 1, 2])
    pending = true; const task = h.page.selectAll(); h.page.clearSelection(); wait.resolve(response([row(5)])); await task; await settle(); assert.deepEqual(h.selected, [])
  } finally { h.cleanup() }
})

test('created project upload failure retries in the same project; preview and 409 require explicit fresh confirmation', async () => {
  const created = [], imports = []
  const api = { getPerfProjects: async () => ({ data: [] }), createPerfProject: async data => { created.push(data); return { data: { ...data, id: 40 } } }, getPerfApiCatalog: async () => response([]),
    previewPerfApiCatalog: async () => ({ data: { operation_count: 1, current_version: { version: 2 }, diff: { added: ['GET /'], removed: [], changed: [] } } }),
    importPerfApiCatalog: async (id, data) => { imports.push({ id, expected: data.get('expected_version') }); if (imports.length === 1) throw new Error('offline'); if (imports.length === 2) throw { response: { status: 409 } }; return { data: { changed: true } } } }
  const dependencies = { ...deps(api), '@/api/api-testing': { getUsers: async () => ({ data: [] }) } }
  const management = mount(await compileComponent('./ProjectManagement.vue', dependencies))
  let catalogPage
  try {
    await settle(); management.state.openCreate(); management.state.form.value.name = 'New project'; management.state.contractFile.value = new File(['{}'], 'api.json'); await management.state.save()
    assert.equal(created.length, 1); assert.equal(management.state.editingId.value, 40)
    catalogPage = await catalogHarness(api, { selectable: false, projectId: 40, initialFile: management.state.catalogInitialFile.value })
    assert.equal(imports.length, 0, 'preview must not import')
    await catalogPage.page.confirmImport(); assert.ok(catalogPage.page.preview.value); assert.ok(catalogPage.page.uploadError.value)
    await catalogPage.page.confirmImport(); assert.equal(catalogPage.page.preview.value, null); assert.match(catalogPage.page.uploadError.value, /error409/)
    await catalogPage.page.confirmImport(); assert.equal(imports.length, 2, 'stale preview cannot submit again')
    await catalogPage.page.previewFile(); await catalogPage.page.confirmImport()
    assert.deepEqual(imports.map(item => item.id), [40, 40, 40]); assert.ok(imports.every(item => item.expected === '2')); assert.equal(created.length, 1)
  } finally { management.unmount(); catalogPage?.cleanup() }
})

const sourceStep = (id = 1, overrides = {}) => ({ id, name: `Step ${id}`, enabled: true, is_setup: false, method: 'GET', url: '/items', headers: {}, params: { limit: 0, active: false }, body_type: 'NONE', body: '', files: [], extractors: [], assertions: [], think_time: { type: 'FIXED', min: 0 }, weight: 1, source_request: id,
  source_metadata: { version: 1, source_key: 'GET /items', request: { params: { limit: 0, active: false } }, requirements: [], gaps: [] }, preparation: { confirmed_fields: ['query/id'] }, readiness: { ready: true, gaps: [] }, ...overrides })

test('imported assertion aliases and typed expectations survive selection, unrelated edits and save responses', async () => {
  const assertions = [
    { type: 'STATUS_CODE', expected: 200 },
    { type: 'JSON_PATH', expr: '$.code', operator: 'eq', expected: 'OK' },
    { type: 'JSON_PATH', json_path: '$.data.active', operator: 'equals', expected: false },
    { type: 'JSON_PATH', expr: '$.data.count', json_path: '$.data.count', operator: '==', expected: 0 }
  ]
  let model = sourceStep(1, { assertions }), editor
  const emitted = []
  const props = () => ({ modelValue: model, 'onUpdate:modelValue': value => {
    emitted.push(clone(value)); model = value; editor?.update(props())
  } })
  editor = mount(await compileComponent('./components/StepEditor.vue', deps({})), props())
  try {
    await settle()
    assert.equal(emitted.length, 0)
    editor.state.form.name = 'Renamed'; await settle()
    assert.deepEqual(model.assertions, assertions)
    model = clone(model); editor.update(props()); await settle()
    assert.deepEqual(model.assertions, assertions)
    assert.equal(emitted.length, 1)
    editor.state.setAssertionPath(editor.state.form.assertions[1], '$.result.code'); await settle()
    assert.deepEqual(model.assertions[1], { ...assertions[1], expr: '$.result.code' })
    editor.state.setAssertionPath(editor.state.form.assertions[3], '$.data.total'); await settle()
    assert.deepEqual(model.assertions[3], { ...assertions[3], expr: '$.data.total', json_path: '$.data.total' })
  } finally { editor.unmount() }
})

test('editing another field never silently deletes incomplete or null assertion rows', async () => {
  const assertions = [{ type: 'JSON_PATH', expr: '$.empty', expected: '' },
    { type: 'JSON_PATH', expr: '$.missing', expected: null }, { type: 'STATUS_CODE', expected: '' }]
  let model = sourceStep(1, { assertions }), editor
  const props = () => ({ modelValue: model, 'onUpdate:modelValue': value => { model = value; editor?.update(props()) } })
  editor = mount(await compileComponent('./components/StepEditor.vue', deps({})), props())
  try {
    await settle(); editor.state.form.name = 'Edited'; await settle()
    assert.deepEqual(model.assertions, assertions)
  } finally { editor.unmount() }
})

test('actual StepEditor preserves typed defaults through shared KV feedback and clears previous source and preparation when switching', async () => {
  let model = sourceStep(), mounted
  const props = () => ({ modelValue: model, 'onUpdate:modelValue': value => { model = value; if (mounted) mounted.update(props()) } })
  mounted = mount(await compileComponent('./components/StepEditor.vue', deps({})), props())
  try {
    await settle()
    mounted.state.paramsKV.value = mounted.state.paramsKV.value.map(({ key, value }) => ({ key, value, enabled: true }))
    mounted.state.form.name = 'Renamed'; await settle()
    assert.deepEqual(model.params, { limit: 0, active: false }); assert.equal(model.source_metadata.version, 1)
    model = { name: 'Manual', enabled: true, method: 'GET', url: '/manual' }; mounted.update(props()); await settle()
    assert.equal(mounted.state.form.source_metadata, undefined); assert.equal(mounted.state.form.preparation, undefined)
    mounted.state.form.name = 'Manual edited'; await settle(); assert.equal(model.source_metadata, undefined); assert.equal(model.preparation, undefined)
  } finally { mounted.unmount() }
})

async function scenarioHarness(overrides = {}, initialSteps = [sourceStep()]) {
  let saved = { id: 13, project: 7, name: 'Scenario', engine: 'K6', env_config: { base_url: 'https://example.invalid', headers: {} }, variables: [], steps: initialSteps }
  const calls = { savedSteps: [], debug: 0, updates: [] }
  const api = { getEngineStatus: async () => ({ data: { engines: [{ name: 'K6', available: true, capabilities: { items: [{ id: 'debug', enabled: true }, { id: 'stop', enabled: true }], verification: { state: 'matched' } } }] } }),
    getPerfProjects: async () => ({ data: [{ id: 7 }] }), getPerfEnvironmentPermissions: async () => ({ data: { project_ids: [7] } }), getPerfEnvironments: async () => ({ data: [] }), getPerfDataFiles: async () => ({ data: [] }),
    getPerfScenario: async () => ({ data: clone(saved) }), updatePerfScenario: async (_, payload) => { saved = { ...saved, ...clone(payload) }; return { data: clone(saved) } },
    savePerfScenarioSteps: async (_, steps) => { calls.savedSteps.push(clone(steps)); saved.steps = steps.map((step, i) => ({ ...clone(step), id: step.id || 100 + i, source_metadata: initialSteps.find(old => old.id === step.id)?.source_metadata || { version: 1 } })); return { data: clone(saved) } },
    getPerfScenarioReadiness: async () => response(saved.steps.filter(step => step.enabled).map(step => ({ step_id: step.id, ready: true, gaps: [] }))),
    debugPerfScenario: async () => { calls.debug++; return { data: { execution: { id: 90 } } } },
    getPerfScenarioCatalogDiff: async () => response([{ step_id: 1, changed: true, removed: false, from_version: 1, to_version: 2, fields: [{ field: 'params', before: { limit: 0 }, after: { limit: 20 }, customized: true }] }], { version: { version: 2 } }),
    updatePerfScenarioCatalog: async (_, payload) => { calls.updates.push(payload); saved.steps = saved.steps.map(step => payload.step_ids.includes(step.id) ? { ...step, source_metadata: { ...step.source_metadata, version: 2 } } : step); return { data: { updated: payload.step_ids.length } } }, ...overrides }
  const previousWindow = globalThis.window; globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const mounted = mount(await compileComponent('./ScenarioEditor.vue', deps(api, { params: { id: '13' }, query: {} })))
  await settle()
  return { page: mounted.state, calls, api, cleanup() { mounted.unmount(); globalThis.window = previousWindow } }
}

test('scenario repeated saves and reorder retain real IDs and source versions; only copied steps receive new IDs', async () => {
  const h = await scenarioHarness({}, [sourceStep(1), sourceStep(2)])
  try {
    assert.equal(await h.page.handleSave(), true)
    h.page.steps.value.reverse(); h.page.markDirty(); assert.equal(await h.page.handleSave(), true)
    assert.deepEqual(h.calls.savedSteps[1].map(step => step.id), [2, 1]); assert.deepEqual(h.page.steps.value.map(step => step.source_metadata.version), [1, 1])
    h.page.onStepCommand('copy', 0); assert.equal(h.page.steps.value[1].id, undefined); assert.equal(await h.page.handleSave(), true)
    assert.equal(h.calls.savedSteps[2][1].id, undefined); assert.ok(h.page.steps.value[1].id); assert.equal(h.page.dirty.value, false)
  } finally { h.cleanup() }
})

test('scenario save response fills IDs without swallowing edits, additions, removals or reordering during save', async () => {
  const wait = deferred(); let submitted
  const h = await scenarioHarness({ savePerfScenarioSteps: async (_, rows) => { submitted = clone(rows); return wait.promise } }, [sourceStep(1), sourceStep(2)])
  try {
    h.page.addStep(); const newUid = h.page.steps.value[2]._uid
    const saving = h.page.handleSave(); await settle()
    h.page.steps.value[0].params.limit = '77'; h.page.steps.value.splice(1, 1); h.page.steps.value.reverse(); h.page.addStep(); h.page.markDirty()
    wait.resolve({ data: { steps: submitted.map((step, i) => ({ ...step, id: step.id || 100 + i, source_metadata: { version: 1 } })) } })
    assert.equal(await saving, false); assert.equal(h.page.dirty.value, true)
    assert.equal(h.page.steps.value.find(step => step._uid === newUid).id, 102)
    assert.equal(h.page.steps.value.find(step => step.id === 1).params.limit, '77')
    assert.equal(h.page.steps.value.some(step => step.id === 2), false); assert.equal(h.page.steps.value.at(-1).id, undefined)
  } finally { h.cleanup() }
})

test('scenario import saves pending edits first and appends returned steps without GET overwriting newer edits', async () => {
  const h = await scenarioHarness()
  try {
    h.page.steps.value[0].params.limit = 77; h.page.markDirty(); await h.page.openImport()
    assert.equal(h.page.importVisible.value, true); assert.equal(h.calls.savedSteps.at(-1)[0].params.limit, 77)
    h.page.form.name = 'New edit'; h.page.markDirty(); await h.page.onImported({ steps: [sourceStep(2)] })
    assert.equal(h.page.form.name, 'New edit'); assert.equal(h.page.steps.value.length, 2); assert.equal(h.page.steps.value[0].params.limit, 77); assert.equal(h.page.dirty.value, true)
  } finally { h.cleanup() }
})

test('verified import into an empty scenario reloads adopted environment and account bindings', async () => {
  let imported = false
  const h = await scenarioHarness({ getPerfScenario: async () => ({ data: imported ? { id: 13, project: 7, name: 'Scenario', engine: 'K6', environment: 3, account_pool_version: 9, account_pool_group: 'a',
    env_config: { base_url: 'https://example.invalid' }, runtime_config: { account_identity_variable: 'user_id' }, variables: [], steps: [sourceStep(2)] } : { id: 13, project: 7, name: 'Scenario', engine: 'K6', steps: [] } }) }, [])
  try {
    imported = true
    await h.page.onImported({ bindings_applied: true, steps: [sourceStep(2)] })
    assert.equal(h.page.form.environment, 3); assert.equal(h.page.form.account_pool_version, 9)
    assert.equal(h.page.form.runtime_config.account_identity_variable, 'user_id')
    assert.deepEqual(h.page.steps.value.map(step => step.id), [2]); assert.equal(h.page.dirty.value, false)
  } finally { h.cleanup() }
})

test('a late adopted-binding response cannot overwrite a newer scenario draft', async () => {
  const wait = deferred(); let imported = false
  const h = await scenarioHarness({ getPerfScenario: async () => imported ? wait.promise : { data: { id: 13, project: 7, name: 'Scenario', engine: 'K6', steps: [] } } }, [])
  try {
    imported = true
    const task = h.page.onImported({ bindings_applied: true, steps: [sourceStep(2)] }); await settle()
    h.page.form.name = 'Keep my new draft'; h.page.markDirty()
    wait.resolve({ data: { project: 7, name: 'Stale', engine: 'K6', steps: [] } }); await task
    assert.equal(h.page.form.name, 'Keep my new draft'); assert.equal(h.page.dirty.value, true)
    assert.ok(h.page.hydrationError.value, 'saving is blocked until bindings are reconciled explicitly')
  } finally { h.cleanup() }
})

test('final scenario readiness is server-authoritative, handles all pages and blocks debug until gaps are resolved', async () => {
  let missing = true
  const checks = []
  const h = await scenarioHarness({ getPerfScenarioReadiness: async (_, params) => { checks.push(params); return response([{ step_id: params.page, ready: params.page !== 2 || !missing, gaps: params.page === 2 && missing ? [{ field: 'auth', code: 'authentication', message: 'Missing auth' }] : [] }], { count: 2, next: params.page === 1 ? 'next' : null }) } }, [sourceStep(1), sourceStep(2)])
  try {
    assert.equal(h.page.readinessBlocked.value, true); await h.page.handleDebug(); assert.equal(h.calls.debug, 0)
    missing = false; await h.page.checkReadiness(); assert.equal(h.page.readinessBlocked.value, false)
    await h.page.handleDebug(); assert.equal(h.calls.debug, 1); assert.ok(checks.some(item => item.page === 2))
  } finally { h.cleanup() }
})

test('readiness responses after new edits are stale and cannot clear gaps or start a request', async () => {
  const wait = deferred(); let pending = false
  const h = await scenarioHarness({ getPerfScenarioReadiness: async () => pending ? wait.promise : response([{ step_id: 1, ready: false, gaps: [{ field: 'auth', code: 'authentication' }] }]) })
  try {
    pending = true; const checking = h.page.loadReadiness(); h.page.form.name = 'Newer draft'; h.page.markDirty()
    wait.resolve(response([{ step_id: 1, ready: true, gaps: [] }])); assert.equal(await checking, false)
    assert.equal(h.page.scenarioReadiness.value[0].ready, false); assert.equal(h.page.readinessStale.value, true); assert.equal(h.calls.debug, 0)
  } finally { h.cleanup() }
})

test('source sync previews before explicit IDs, retains custom values and rules, and rejects stale confirmation', async () => {
  const h = await scenarioHarness({}, [sourceStep(1, { params: { limit: 77 }, assertions: [{ type: 'STATUS_CODE', expected: 200 }] }), sourceStep(2)])
  try {
    await h.page.openCatalogDiff(); assert.equal(h.calls.updates.length, 0); assert.equal(h.page.catalogDiff.value[0].fields[0].customized, true)
    await h.page.confirmCatalogUpdate(); assert.equal(h.calls.updates.length, 0)
    h.page.toggleSourceStep(1, true); h.page.toggleSourceStep(1, false); assert.deepEqual(h.page.selectedDiffIds.value, [])
    h.page.toggleSourceStep(1, true); await h.page.confirmCatalogUpdate()
    assert.deepEqual(h.calls.updates[0], { step_ids: [1], expected_version: 2 })
    assert.equal(h.page.steps.value[0].source_metadata.version, 2); assert.equal(h.page.steps.value[1].source_metadata.version, 1)
    assert.equal(h.page.steps.value[0].params.limit, 77); assert.equal(h.page.steps.value[0].assertions[0].expected, 200)
    await h.page.openCatalogDiff(); h.page.selectedDiffIds.value = [1]; h.page.form.name = 'changed'; h.page.markDirty(); await h.page.confirmCatalogUpdate()
    assert.equal(h.calls.updates.length, 1); assert.match(h.page.diffError.value, /saveBeforeSync/)
  } finally { h.cleanup() }
})

test('source update 409 clears confirmation and exposes a refreshable error', async () => {
  const h = await scenarioHarness({ updatePerfScenarioCatalog: async () => { throw { response: { status: 409 } } } })
  try { await h.page.openCatalogDiff(); h.page.selectedDiffIds.value = [1]; await h.page.confirmCatalogUpdate(); assert.match(h.page.diffError.value, /error409/); assert.deepEqual(h.page.selectedDiffIds.value, []); assert.equal(h.page.steps.value[0].source_metadata.version, 1); await h.page.loadCatalogDiff(); assert.equal(h.page.diffError.value, '') }
  finally { h.cleanup() }
})

test('preparation confirmation writes only confirmation fields and leaves hard schema gaps intact', async () => {
  let result
  const h = mount(await compileComponent('./components/RequestReadinessPanel.vue', deps({})), { metadata: { requirements: [{ field: 'query/id', resource: true }], gaps: [{ code: 'schema_choice', field: 'body' }] }, preparation: { body_reviewed: true }, readiness: { ready: false, gaps: [{ code: 'schema_choice', field: 'body' }] }, 'onUpdate:preparation': value => { result = value } })
  try { assert.deepEqual(h.state.confirmable.value, ['query/id']); h.state.confirm('query/id', true); assert.deepEqual(result, { body_reviewed: true, confirmed_fields: ['query/id'] }) }
  finally { h.unmount() }
})

test('catalog Chinese and English locale keys match and every message compiles', () => {
  assert.deepEqual(Object.keys(zh.catalog).sort(), Object.keys(en.catalog).sort())
  for (const locale of [zh.catalog, en.catalog]) for (const text of Object.values(locale)) { const errors = []; baseCompile(text, { onError: error => errors.push(error) }); assert.deepEqual(errors, []) }
})

test('retry1: server rehydration with identical step values is not a user edit', async () => {
  const original = sourceStep(1, { params: { limit: 0, active: false, tags: [0, false] } })
  const emitted = []
  let model = clone(original), editor
  const props = () => ({ modelValue: model, 'onUpdate:modelValue': value => { emitted.push(clone(value)); model = value; if (editor) editor.update(props()) } })
  editor = mount(await compileComponent('./components/StepEditor.vue', deps({})), props())
  try {
    await settle()
    model = { id: original.id, scenario: 16, order: 0, ...clone(original), readiness: { ready: false, gaps: [{ code: 'authentication', field: 'auth', message: 'Configure auth' }] } }
    const response = clone(model)
    editor.update(props()); await settle()
    if (emitted.length) assert.deepEqual(emitted[0], response, 'the emitted value is semantically identical to the response')
    assert.equal(emitted.length, 0, 'server identity/readiness hydration must not emit an edited step')
    assert.deepEqual(editor.state.paramsKV.value.map(item => item.value), ['0', 'false', '[0,false]'])
  } finally { editor.unmount() }
})

test('retry1: actual ScenarioEditor and StepEditor remain clean through repeated save responses', async () => {
  const h = await scenarioHarness({}, [sourceStep(1), sourceStep(2)])
  let editor, emitted = 0
  const props = () => ({ modelValue: h.page.currentStep.value, readiness: h.page.scenarioReadiness.value.find(item => item.step_id === h.page.currentStep.value?.id) || null,
    readinessStale: h.page.readinessStale.value, 'onUpdate:modelValue': value => { emitted++; h.page.steps.value[h.page.activeIndex.value] = value; h.page.markDirty() } })
  editor = mount(await compileComponent('./components/StepEditor.vue', deps({})), props())
  const stop = Vue.watch(() => [h.page.currentStep.value, h.page.scenarioReadiness.value, h.page.readinessStale.value], () => editor.update(props()), { deep: true })
  try {
    await settle(); h.page.dirty.value = false
    assert.equal(await h.page.handleSave(), true)
    await settle()
    assert.equal(emitted, 0, 'server save response must not become a child update:modelValue event')
    assert.equal(h.page.dirty.value, false)
    assert.equal(h.page.readinessStale.value, false)
    assert.equal(await h.page.handleSave(), true); await settle()
    assert.equal(h.page.dirty.value, false)
    assert.deepEqual(h.page.steps.value.map(step => step.id), [1, 2])
  } finally { stop(); editor.unmount(); h.cleanup() }
})


test('retry1: readonly source refresh does not emit but real nested user edits during save stay dirty', async () => {
  const pending = deferred()
  let submitted
  const h = await scenarioHarness({ savePerfScenarioSteps: async (_, rows) => { submitted = clone(rows); return pending.promise } })
  let editor
  const props = () => ({ modelValue: h.page.currentStep.value, 'onUpdate:modelValue': value => { h.page.steps.value[h.page.activeIndex.value] = value; h.page.markDirty() } })
  editor = mount(await compileComponent('./components/StepEditor.vue', deps({})), props())
  const stop = Vue.watch(() => h.page.currentStep.value, () => editor.update(props()), { deep: true })
  try {
    await settle()
    const saving = h.page.handleSave(); await settle()
    editor.state.paramsKV.value.find(row => row.key === 'limit').value = '77'
    await settle()
    pending.resolve({ data: { steps: submitted.map(step => ({ ...step, source_metadata: { version: 1, request: { params: { active: false, limit: 0 } } }, readiness: { ready: true, gaps: [] } })) } })
    assert.equal(await saving, false); await settle()
    assert.equal(h.page.dirty.value, true)
    assert.equal(h.page.steps.value[0].params.limit, '77')
    assert.equal(h.page.steps.value[0].params.active, false)
    assert.equal(h.page.steps.value[0].id, 1)
  } finally { stop(); editor.unmount(); h.cleanup() }
})

test('retry1: actual readiness template and Element Plus buttons render named links for the browser response shape', async () => {
  const text = await readFile(new URL('./ScenarioEditor.vue', import.meta.url), 'utf8')
  const start = text.indexOf('<ul v-if="!readinessStale"')
  const fragment = text.slice(start, text.indexOf('</ul>', start) + 5)
  const compiled = compileTemplate({ source: fragment, id: 'readiness-labels' })
  assert.deepEqual(compiled.errors, [])
  const render = new Function('__vue', compiled.code
    .replace(/import\s+\{([\s\S]*?)\}\s+from\s+['"]vue['"]/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = __vue`)
    .replace('export function render', 'return function render'))(Vue)
  const steps = [{ id: 49, name: '获取当前用户资料和能力' }, { id: 50, name: '获取我的通知未读汇总' }]
  const scenarioReadiness = steps.map(step => ({ step_id: step.id, ready: false, gaps: [{ code: 'authentication', field: 'auth', message: '请配置契约要求的认证请求头或参数' }] }))
  const html = await renderToString(Vue.createSSRApp({ components: { ElButton }, setup: () => ({ steps, scenarioReadiness, readinessStale: false, selectStep() {} }), render }))
  assert.match(html, /<button[^>]*>[\s\S]*?获取当前用户资料和能力[\s\S]*?<\/button>/)
  assert.match(html, /<button[^>]*>[\s\S]*?获取我的通知未读汇总[\s\S]*?<\/button>/)
  assert.equal((html.match(/auth: 请配置契约要求的认证请求头或参数/g) || []).length, 2)
})
