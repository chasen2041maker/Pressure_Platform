import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as catalog from './apiCatalogForm.mjs'
import zh from '../../locales/lang/zh-cn/performance-testing.js'

const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const config = (revision = 2) => ({ revision, config: { environment: 3, global_environment: null, account_pool_version: 9, account_pool_group: '', token_variable: 'token', identity_variable: 'user_id' },
  choices: { environments: [{ id: 3, name: 'Test', scope: 'PROJECT' }], account_pool_versions: [{ id: 9, version: 1, pool_name: 'Accounts', field_names: ['token', 'user_id'], groups: [] }] } })
const row = (id, status = 'unverified', method = 'GET') => ({ id, name: `API ${id}`, method, path: `/items/${id}`, tags: [], readiness: { ready: false },
  source_key: `${method} /items/${id}`, prepared: { id: id + 100, request_id: id, source_key: `${method} /items/${id}`, revision: 4, config_revision: 2, status, gaps: [], last_evidence: null } })
const listing = rows => ({ data: { results: rows, count: rows.length, next: null, version: { version: 7 }, tags: [] } })
const t = (key, values = {}) => (key.split('.').slice(1).reduce((value, part) => value?.[part], zh) || key).replace(/\{(\w+)\}/g, (_, name) => values[name] ?? `{${name}}`)
const text = node => [node?.text || '', ...(node?.children || []).map(text)].join('')
const nodes = node => [node, ...(node.children || []).flatMap(nodes)]
const renderer = Vue.createRenderer({ createElement: tag => ({ tag, props: {}, children: [] }), createText: value => ({ text: value }), createComment: () => ({ text: '' }),
  insert(child, parent, anchor) { if (child.parent) { const i = child.parent.children.indexOf(child); if (i >= 0) child.parent.children.splice(i, 1) } child.parent = parent; const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, child) },
  remove(child) { const i = child.parent?.children.indexOf(child) ?? -1; if (i >= 0) child.parent.children.splice(i, 1) },
  setText(node, value) { node.text = value }, setElementText(node, value) { node.children = [{ text: value }] }, patchProp(node, key, _, value) { node.props[key] = value },
  parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] })

async function mount(file, api, initial, extra = {}) {
  const events = [], confirmations = [], singleEvents = []
  const deps = { vue: Vue, '../apiCatalogForm.mjs': catalog, '@/api/performance-testing': api,
    'vue-i18n': { useI18n: () => ({ t }) }, 'element-plus': { ElMessage: { success() {}, error() {} }, ElMessageBox: { confirm: async message => { confirmations.push(message); return true } } }, ...extra }
  const descriptor = parse(await readFile(new URL(file, import.meta.url), 'utf8')).descriptor
  const script = compileScript(descriptor, { id: file })
  const template = compileTemplate({ source: descriptor.template.content, id: file, compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  const modules = []
  const imports = code => code.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const i = modules.push(deps[name] || { default: { render: () => null } }) - 1
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${i}];\n` : `const ${binding} = __modules[${i}].default;\n`
  })
  const component = new Function('__modules', imports(script.content).replace('export default', 'return'))(modules)
  const render = new Function('__modules', imports(template.code).replace('export function render', 'return function render'))(modules)
  let state
  const rendered = { ...component, setup(props, context) { state = component.setup(props, context); return state }, render }
  const props = Vue.reactive({ ...initial })
  const app = renderer.createApp({ render: () => Vue.h(rendered, { ...props,
    onChanged: value => events.push(['changed', value]), onImported: value => events.push(['imported', value]), onSingleVerification: value => singleEvents.push(value),
    'onUpdate:selectedIds': value => { props.selectedIds = value }, 'onUpdate:modelValue': value => { props.modelValue = value } }) })
  const wrap = tag => ({ inheritAttrs: false, setup: (_, { attrs, slots }) => () => Vue.h(tag, { ...attrs, disabled: attrs.disabled || attrs.loading }, [tag === 'button' ? null : attrs.title, slots.default?.(), slots.footer?.()]) })
  for (const name of ['ElForm', 'ElFormItem', 'ElSelect', 'ElOption', 'ElCheckbox', 'ElTag', 'ElEmpty', 'ElPagination', 'ElAlert', 'ElCollapse', 'ElCollapseItem', 'ElDrawer', 'ElRadioGroup', 'ElRadioButton']) app.component(name, wrap('div'))
  app.component('ElButton', wrap('button')); app.component('ElInput', wrap('input')); app.directive('loading', {})
  const root = { children: [] }; app.mount(root); await settle()
  return { props, state, root, events, confirmations, singleEvents, async click(label) { const button = nodes(root).find(node => node.tag === 'button' && text(node).trim() === label); assert.ok(button, label); if (!button.props.disabled) await button.props.onClick(); await settle() },
    async project(id) { props.projectId = id; await settle() }, unmount: () => app.unmount() }
}
async function panel(overrides = {}, props = {}, extra) {
  const calls = []
  const api = { getPerfApiPoolConfig: async id => { calls.push(['get', id]); return { data: config() } },
    updatePerfApiPoolConfig: async (id, data) => { calls.push(['save', id, data]); return { data: { ...config(3), config: data.config } } },
    preparePerfApiPool: async (id, data) => { calls.push(['prepare', id, data]); return { data: { prepared_count: 1, blocked_count: 2 } } },
    verifyPerfApiPool: async (id, data) => { calls.push(['verify', id, data]); return { data: { id: 1, status: 'completed', results: [] } } }, ...overrides }
  return { ...await mount('./components/ApiPoolPreparation.vue', api, { projectId: 7, catalogVersion: 7, selectedRows: [row(1)], ...props }, extra), calls }
}

test('testing one API ignores the bulk selection and uses only its saved revision', async () => {
  const h = await panel({}, { selectedRows: [row(2), row(3)] })
  try {
    await h.state.verifyOne(row(1)); await settle()
    const request = h.calls.find(call => call[0] === 'verify')[2]
    assert.deepEqual(request.selection, [{ id: 101, revision: 4 }])
    assert.equal(request.expected_catalog_version, 7)
    assert.equal(request.confirm_writes, false)
    assert.equal(h.confirmations.length, 0)
    assert.equal(h.singleEvents.at(-1).requestId, 1)
  } finally { h.unmount() }
})

test('testing one API rejects missing configuration and unsaved shared changes', async () => {
  const h = await panel()
  try {
    assert.equal(h.state.canVerifyOne(row(1, 'blocked')), false)
    await h.state.verifyOne(row(1, 'blocked'))
    h.state.form.environment = 20
    assert.equal(h.state.canVerifyOne(row(1)), false)
    await h.state.verifyOne(row(1))
    assert.equal(h.calls.filter(call => call[0] === 'verify').length, 0)
  } finally { h.unmount() }
})

test('a GET with a write dependency discloses the setup and requires confirmation', async () => {
  const item = row(1)
  item.prepared.setup_steps = [{ source_key: 'POST /conversations', method: 'POST', path: '/conversations', revision: 6, outputs: ['conversation_id'] }]
  item.prepared.verification_methods = ['POST', 'GET']
  item.prepared.has_writes = true
  const h = await panel()
  try {
    await h.state.verifyOne(item)
    assert.equal(h.confirmations.length, 1)
    assert.match(h.confirmations[0], /POST \/conversations/)
    assert.match(h.confirmations[0], /前置/)
    assert.match(h.confirmations[0], /GET/)
    assert.equal(h.calls.find(call => call[0] === 'verify')[2].confirm_writes, true)
  } finally { h.unmount() }
  const cancelled = await panel({}, {}, { 'element-plus': { ElMessageBox: { confirm: async () => { throw 'cancel' } } } })
  try {
    await cancelled.state.verifyOne(item)
    assert.equal(cancelled.calls.filter(call => call[0] === 'verify').length, 0)
  } finally { cancelled.unmount() }
})

test('confirmation counts different setup edges to the same API separately and deduplicates shared edges', async () => {
  const one = row(1), two = row(2)
  const setup = { source_key: 'POST /conversations', method: 'POST', path: '/conversations', revision: 6, definition_hash: 'a'.repeat(64) }
  one.prepared.setup_steps = [{ ...setup, key: 'first-edge', outputs: ['first_id'] }, { ...setup, key: 'second-edge', outputs: ['second_id'] }]
  two.prepared.setup_steps = [one.prepared.setup_steps[0]]
  const h = await panel({}, { selectedRows: [one, two] })
  try {
    await h.click('一键验证')
    const message = h.confirmations[0]
    assert.equal(message.split('POST /conversations').length - 1, 2)
    assert.match(message, /2 个前置请求/)
    assert.match(message, /first_id/)
    assert.match(message, /second_id/)
    assert.equal(h.calls.find(call => call[0] === 'verify')[2].confirm_writes, true)
  } finally { h.unmount() }
})

test('single write confirmation names only the target and respects edits made during confirmation', async () => {
  const h = await panel({}, { selectedRows: [row(2), row(3)] })
  try {
    await h.state.verifyOne(row(1, 'unverified', 'PUT'))
    assert.match(h.confirmations[0], /1/); assert.match(h.confirmations[0], /PUT/)
    assert.deepEqual(h.calls.find(call => call[0] === 'verify')[2].selection, [{ id: 101, revision: 4 }])
    assert.equal(h.calls.find(call => call[0] === 'verify')[2].confirm_writes, true)
  } finally { h.unmount() }
  const confirmation = deferred()
  const changed = await panel({}, {}, { 'element-plus': { ElMessageBox: { confirm: () => confirmation.promise } } })
  try {
    const running = changed.state.verifyOne(row(1, 'unverified', 'PUT')); await settle()
    changed.props.disabled = true; confirmation.resolve(true); await running
    assert.equal(changed.calls.filter(call => call[0] === 'verify').length, 0)
  } finally { changed.unmount() }
})

test('uncertain single API execution resumes the same request and cannot start another', async () => {
  const requests = []
  const h = await panel({ verifyPerfApiPool: async (_, payload) => {
    requests.push(JSON.parse(JSON.stringify(payload)))
    if (requests.length === 1) throw new Error('timeout')
    return { data: { id: 8, status: 'completed', results: [{ prepared_id: 101, verdict: 'http_only' }] } }
  } })
  try {
    await h.state.verifyOne(row(1)); await h.state.verifyOne(row(2))
    assert.equal(requests.length, 1)
    await h.state.resumeVerification(); await settle()
    assert.deepEqual(requests[0], requests[1])
    assert.match(text(h.root), /仅 HTTP 通过/)
    assert.doesNotMatch(text(h.root), /验证通过/)
  } finally { h.unmount() }
})

test('catalog notes stay in details and the row test button uses the clicked request', async () => {
  const item = row(1); item.prepared.preparation_note = '仅供专用测试账号使用'
  const seen = []
  const h = await mount('./components/ProjectApiCatalog.vue', {
    getPerfApiCatalog: async () => listing([item]),
    getPerfApiCatalogRequest: async () => ({ data: { operation: {}, readiness: {}, prepared: item.prepared, version: { version: 7 } } })
  }, { projectId: 7, selectable: false }, { './ApiPoolPreparation.vue': { default: {
    setup(_, { expose }) { expose({ canVerifyOne: () => true, verifyOne: value => seen.push(value.id) }); return () => null },
  } } })
  try {
    await settle()
    assert.doesNotMatch(text(h.root), /仅供专用测试账号使用/)
    await h.click('测试一次'); assert.deepEqual(seen, [1])
    h.state.showDetail(1); await settle()
    assert.match(text(h.root), /仅供专用测试账号使用/)
    h.state.showDetail(1); await settle()
    assert.doesNotMatch(text(h.root), /仅供专用测试账号使用/)
  } finally { h.unmount() }
})

test('real preparation UI is read-only on mount and saves references before preparing the whole catalog', async () => {
  const h = await panel({}, { selectedRows: [] })
  try {
    assert.deepEqual(h.calls, [['get', 7]])
    assert.match(text(h.root), /一键补齐配置/); assert.match(text(h.root), /一键验证/)
    h.state.form.account_pool_group = 'group-a'; await settle()
    await h.click('一键补齐配置')
    assert.deepEqual(h.calls[1], ['save', 7, { expected_config_revision: 2, config: { ...config().config, account_pool_group: 'group-a' } }])
    assert.deepEqual(h.calls[2], ['prepare', 7, { expected_catalog_version: 7, expected_config_revision: 3 }])
    assert.match(text(h.root), /1.*2/)
    assert.equal(h.calls.filter(call => call[0] === 'verify').length, 0)
  } finally { h.unmount() }
})

test('all-catalog selection includes blocked rows across pages while filtered selection has its own scope', async () => {
  const all = [row(1, 'blocked'), row(2, 'unverified'), row(3, 'unprepared', 'POST')], queries = []
  const h = await mount('./components/ProjectApiCatalog.vue', { getPerfApiCatalog: async (_, query) => {
    queries.push(query)
    if (query.method) return listing([all[2]])
    return { data: { ...listing(query.page === 2 ? all.slice(2) : all.slice(0, 2)).data, count: 3, next: query.page === 1 ? 'next' : null } }
  } }, { projectId: 7, selectable: false })
  try {
    h.state.filters.method = 'POST'; await settle()
    await h.click('全选全部接口'); assert.deepEqual(h.state.verificationRows.value.map(item => item.id), [1, 2, 3])
    assert.ok(queries.slice(-2).every(query => !query.method && !query.ready && !query.prepared_status))
    await h.click('全选当前筛选'); assert.deepEqual(h.state.verificationRows.value.map(item => item.id), [3])
    h.state.toggleVerification(all[0], true); assert.deepEqual(h.state.verificationRows.value.map(item => item.id), [3, 1])
  } finally { h.unmount() }
})

test('mixed selection verifies only eligible rows and explicitly discloses skipped names and reasons', async () => {
  const blocked = row(2, 'blocked', 'POST'); blocked.prepared.gaps = [{ field: 'body/id', message: '缺少真实订单ID' }]
  const h = await panel({}, { selectedRows: [row(1), blocked] })
  try {
    assert.match(text(h.root), /已选 2/); assert.match(text(h.root), /可验证 1/); assert.match(text(h.root), /跳过 1/)
    await h.click('一键验证')
    assert.match(h.confirmations[0], /POST \/items\/2/); assert.match(h.confirmations[0], /缺少真实订单ID/)
    const body = h.calls.find(call => call[0] === 'verify')[2]
    assert.deepEqual(body.selection, [{ id: 101, revision: 4 }]); assert.equal(body.confirm_writes, false)
  } finally { h.unmount() }
})

test('refresh retains selection and a dirty editor, but clearing while refresh is pending cannot resurrect selection', async () => {
  let revision = 4, pending
  const h = await mount('./components/ProjectApiCatalog.vue', { getPerfApiCatalog: async () => pending ? pending.promise : listing([{ ...row(1), prepared: { ...row(1).prepared, revision } }]) }, { projectId: 7 })
  try {
    h.state.toggleVerification(h.state.rows.value[0], true); await h.state.openEditor(1); h.state.editorDirty.value = true
    revision = 5; await h.state.poolChanged()
    assert.equal(h.state.verificationRows.value[0].prepared.revision, 5)
    assert.equal(h.state.editorId.value, 1); assert.equal(h.state.editorDirty.value, true)
    pending = deferred(); const task = h.state.poolChanged(); await settle()
    h.state.clearVerification(); pending.resolve(listing([row(1)])); await task
    assert.deepEqual(h.state.verificationRows.value, [])
  } finally { h.unmount() }
})

test('prepare captures the selected request IDs instead of silently preparing the full project', async () => {
  const h = await panel({}, { selectedRows: [row(2, 'blocked'), row(3, 'unprepared')] })
  try {
    await h.click('一键补齐配置')
    assert.deepEqual(h.calls.at(-1), ['prepare', 7, { expected_catalog_version: 7, expected_config_revision: 2, request_ids: [2, 3] }])
  } finally { h.unmount() }
})

test('write verification requires explicit confirmation and captures revisions; cancellation sends nothing', async () => {
  const h = await panel({}, { selectedRows: [row(1, 'unverified', 'POST'), row(2)] })
  try {
    await h.click('一键验证')
    assert.match(h.confirmations[0], /2/); assert.match(h.confirmations[0], /POST/); assert.match(h.confirmations[0], /写/)
    const request = h.calls.find(call => call[0] === 'verify')[2]
    assert.deepEqual(request.selection, [{ id: 101, revision: 4 }, { id: 102, revision: 4 }])
    assert.equal(request.expected_catalog_version, 7); assert.equal(request.confirm_writes, true)
    assert.ok(request.request_key)
  } finally { h.unmount() }
  const cancelled = await panel({}, {}, { 'element-plus': { ElMessageBox: { confirm: async () => { throw 'cancel' } } } })
  try { await cancelled.click('一键验证'); assert.equal(cancelled.calls.filter(call => call[0] === 'verify').length, 0) }
  finally { cancelled.unmount() }
})

test('a timed-out verification is retried only on click with the same request key and selection', async () => {
  const bodies = []
  const h = await panel({ verifyPerfApiPool: async (_, data) => { bodies.push(JSON.parse(JSON.stringify(data))); if (bodies.length === 1) throw new Error('timeout'); return { data: { id: 4, status: 'completed', results: [] } } } })
  try {
    await h.click('一键验证'); assert.equal(bodies.length, 1)
    assert.match(text(h.root), /未确认/)
    await h.click('查询本次验证'); assert.deepEqual(bodies[0], bodies[1])
  } finally { h.unmount() }
})

test('late project config and prepare results never replace a new project or announce old results', async () => {
  const old = deferred(), prepare = deferred()
  const h = await panel({ getPerfApiPoolConfig: id => id === 7 ? old.promise : Promise.resolve({ data: { ...config(), config: { ...config().config, environment: 8 } } }) })
  try { await h.project(8); old.resolve({ data: config() }); await settle(); assert.equal(h.state.form.environment, 8) }
  finally { h.unmount() }
  const p = await panel({ preparePerfApiPool: () => prepare.promise })
  try {
    const task = p.click('一键补齐配置'); await settle(); await p.project(8)
    prepare.resolve({ data: { prepared_count: 196, blocked_count: 0 } }); await task
    assert.equal(p.state.preparation.value, null); assert.equal(p.events.length, 0)
  } finally { p.unmount() }
})

test('selection or project changes during confirmation cancel the outgoing verification', async () => {
  for (const change of ['project', 'selection']) {
    const dialog = deferred()
    const h = await panel({}, {}, { 'element-plus': { ElMessageBox: { confirm: () => dialog.promise } } })
    try {
      const task = h.click('一键验证'); await settle()
      if (change === 'project') await h.project(8); else { h.props.selectedRows = [row(2)]; await settle() }
      dialog.resolve(true); await task
      assert.equal(h.calls.filter(call => call[0] === 'verify').length, 0)
    } finally { h.unmount() }
  }
})

test('catalog separates passed evidence from source readiness and only bulk-selects passed for reuse', async () => {
  const values = [row(1, 'passed'), row(2, 'unverified'), row(3, 'stale'), row(4, 'blocked')]
  const calls = []
  const h = await mount('./components/ProjectApiCatalog.vue', { getPerfApiCatalog: async (_, query) => { calls.push(query); return listing(query.prepared_status === 'passed' ? [values[0]] : values) } },
    { projectId: 7, selectable: true, usePrepared: true, selectedIds: [] })
  try {
    assert.match(text(h.root), /验证通过/); assert.match(text(h.root), /待验证/); assert.match(text(h.root), /需重新验证/)
    await h.state.selectAll(); await settle(); assert.deepEqual(h.props.selectedIds, [1])
    assert.equal(calls.at(-1).prepared_status, 'passed'); assert.equal(calls.at(-1).ready, undefined)
    h.state.toggle(3, true); await settle(); assert.deepEqual(h.props.selectedIds, [1])
  } finally { h.unmount() }
})

test('drawer imports only current passed revisions and preserves raw mode payload', async () => {
  const requests = [], values = [row(1, 'passed'), row(2, 'passed')]
  const h = await mount('./components/ImportFromApiDrawer.vue', {
    getPerfApiCatalog: async () => listing(values),
    importStepsFromApi: async (id, data) => { requests.push([id, data]); return { data: { imported: 2, steps: [] } } }
  }, { modelValue: true, projectId: 7, scenarioId: 13 })
  try {
    assert.equal(text(h.root).includes(t('performanceTesting.importApi.asSetup')), false)
    h.state.selectedIds.value = [1, 2]; h.state.selectionState.value = { version: 7, rows: values.map(row => ({ ...row, _catalogVersion: 7 })) }; await h.state.confirm()
    assert.deepEqual(requests[0], [13, { request_ids: [1, 2], as_setup: false, use_prepared: true, expected_catalog_version: 7,
      expected_prepared_revisions: { 'GET /items/1': 4, 'GET /items/2': 4 } }])
    h.props.modelValue = true; await settle(); h.state.usePrepared.value = false; await settle(); h.state.selectedIds.value = [1]
    await h.state.confirm(); assert.deepEqual(requests[1], [13, { request_ids: [1], as_setup: false }])
    h.props.modelValue = true; await settle(); h.state.asSetup.value = true; h.state.usePrepared.value = true; await settle()
    assert.equal(h.state.asSetup.value, false, 'verified imports are business steps and cannot retain raw setup mode')
  } finally { h.unmount() }
})

test('drawer rejects a stale prepared selection and discards old errors after close or project switch', async () => {
  let imported = 0
  const h = await mount('./components/ImportFromApiDrawer.vue', { getPerfApiCatalog: async () => listing([row(1, 'stale')]), importStepsFromApi: async () => { imported++ } },
    { modelValue: true, projectId: 7, scenarioId: 13 })
  try { h.state.selectedIds.value = [1]; await h.state.confirm(); assert.equal(imported, 0); assert.ok(h.state.error.value) }
  finally { h.unmount() }
  const pending = deferred()
  const p = await mount('./components/ImportFromApiDrawer.vue', { getPerfApiCatalog: async () => listing([row(1, 'passed')]), importStepsFromApi: () => pending.promise },
    { modelValue: true, projectId: 7, scenarioId: 13 })
  try {
    p.state.selectedIds.value = [1]; p.state.selectionState.value = { version: 7, rows: [{ ...row(1, 'passed'), _catalogVersion: 7 }] }; const task = p.state.confirm(); await settle(); p.props.modelValue = false; await settle(); p.props.modelValue = true; await settle()
    pending.reject({ response: { status: 409 } }); await task
    assert.equal(p.state.error.value, ''); assert.equal(p.events.length, 0)
  } finally { p.unmount() }
})

test('only a sole environment and pool are suggested locally; ambiguous choices remain unset', async () => {
  for (const ambiguous of [false, true]) {
    const data = config(0)
    data.config = { ...data.config, environment: null, account_pool_version: null }
    if (ambiguous) { data.choices.environments.push({ id: 4, name: 'Other', scope: 'PROJECT' }); data.choices.account_pool_versions.push({ ...data.choices.account_pool_versions[0], id: 10 }) }
    const h = await panel({ getPerfApiPoolConfig: async () => ({ data }) })
    try {
      assert.equal(h.state.form.environment, ambiguous ? null : 3)
      assert.equal(h.state.form.account_pool_version, ambiguous ? null : 9)
      assert.equal(h.calls.length, 0, 'suggestions are not persisted or executed on load')
      if (!ambiguous) { await h.click('一键补齐配置'); assert.equal(h.calls[0][2].expected_config_revision, 0) }
    } finally { h.unmount() }
  }
})

test('late verification polls and failures after project change or unmount cannot affect the new owner', async () => {
  for (const transition of ['project', 'unmount']) for (const fails of [false, true]) {
    const original = globalThis.setTimeout, old = deferred(); let callback
    globalThis.setTimeout = fn => { callback = fn; return 0 }
    const h = await panel({ verifyPerfApiPool: async () => ({ data: { id: 8, status: 'running', results: [] } }), getPerfApiPoolVerification: () => old.promise })
    try {
      await h.click('一键验证'); assert.ok(callback)
      const task = callback(); await settle()
      if (transition === 'project') await h.project(8); else h.unmount()
      if (fails) old.reject(new Error('old offline')); else old.resolve({ data: { id: 8, status: 'completed', results: [{ prepared_id: 101, status: 'passed', verdict: 'passed' }] } })
      await task; await settle()
      assert.equal(h.events.length, 0)
      if (transition === 'project') { assert.equal(h.state.batch.value, null); assert.equal(h.state.error.value, '') }
    } finally { globalThis.setTimeout = original; if (transition !== 'unmount') h.unmount() }
  }
})

test('HTTP-only verdict stays unverified in the rendered batch result and does not claim business success', async () => {
  const h = await panel({ verifyPerfApiPool: async () => ({ data: { id: 8, status: 'completed', results: [{ prepared_id: 101, revision: 4, status: 'unverified', verdict: 'http_only', total: 1, success: 1, failed: 0 }] } }) })
  try { await h.click('一键验证'); assert.match(text(h.root), /仅 HTTP 通过，业务结果待确认/); assert.match(text(h.root), /GET \/items\/1/); assert.doesNotMatch(text(h.root), /验证通过/) }
  finally { h.unmount() }
})

test('a failed setup explains an unstarted target in collapsed dependency results', async () => {
  const dependency = { step_id: 30, source_key: 'POST /conversations', revision: 6, status: 'failed', verdict: 'failed', total: 1, success: 0, failed: 1 }
  const h = await panel({ verifyPerfApiPool: async () => ({ data: { id: 8, status: 'failed', results: [{ prepared_id: 101, revision: 4, status: 'not_run', verdict: 'not_run', total: 0, success: 0, failed: 0, dependency_results: [dependency] }] } }) })
  try {
    await h.click('一键验证')
    const detail = nodes(h.root).find(node => node.tag === 'details' && text(node).includes('POST /conversations'))
    assert.ok(detail)
    assert.equal(!!detail.props.open, false)
    assert.match(text(detail), /前置执行结果/)
    assert.match(text(detail), /成功 0，失败 1/)
    assert.match(text(h.root), /未执行/)
    assert.doesNotMatch(text(h.root), /验证通过/)
  } finally { h.unmount() }
})

test('a successful request from an incomplete batch asks for separate verification without calling that request failed', async () => {
  const h = await panel({ verifyPerfApiPool: async () => ({ data: { id: 8, status: 'failed', results: [{ prepared_id: 101, revision: 4, status: 'unverified', verdict: 'incomplete', total: 1, success: 1, failed: 0 }] } }) })
  try {
    await h.click('一键验证')
    const result = nodes(h.root).find(node => node.tag === 'li')
    assert.match(text(result), /该请求已成功，但本批次未完成，请单独验证/)
    assert.match(text(result), /成功 1，失败 0/)
    assert.doesNotMatch(text(result), /验证失败|验证通过/)
  } finally { h.unmount() }
})

test('catalog selection carries the shown revision, not a silently refreshed template revision', async () => {
  let version = 4, snapshot
  const h = await mount('./components/ProjectApiCatalog.vue', { getPerfApiCatalog: async () => listing([{ ...row(1, 'passed'), prepared: { ...row(1, 'passed').prepared, revision: version } }]) },
    { projectId: 7, selectable: true, usePrepared: true, selectedIds: [], onSelectionState: value => { snapshot = value } })
  try {
    h.state.toggle(1, true); await settle(); assert.equal(snapshot.rows[0].prepared.revision, 4)
    version = 5; await h.state.load(); assert.equal(snapshot.rows[0].prepared.revision, 4)
    h.state.clearSelection(); await settle(); h.state.toggle(1, true); await settle(); assert.equal(snapshot.rows[0].prepared.revision, 5)
  } finally { h.unmount() }
})

test('409 saving shared settings never continues into prepare or verification', async () => {
  const h = await panel({ updatePerfApiPoolConfig: async () => { throw { response: { status: 409 } } } })
  try { h.state.form.environment = 20; await h.click('一键补齐配置'); assert.equal(h.calls.filter(call => ['prepare', 'verify'].includes(call[0])).length, 0); assert.match(text(h.root), /已变化/) }
  finally { h.unmount() }
})

test('cleared Element Plus selections keep a complete config with null IDs and empty variable names', async () => {
  const h = await panel()
  try {
    h.state.form.environment = undefined; h.state.form.account_pool_version = ''; h.state.form.token_variable = undefined; h.state.form.identity_variable = null
    await settle()
    await h.click('保存公共配置')
    assert.deepEqual(h.calls[1][2].config, { ...config().config, environment: null, account_pool_version: null, token_variable: '', identity_variable: '' })
  } finally { h.unmount() }
})
