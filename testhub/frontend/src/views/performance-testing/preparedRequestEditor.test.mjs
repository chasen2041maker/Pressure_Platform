import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as catalog from './apiCatalogForm.mjs'
import * as fields from './preparedRequestForm.mjs'
import * as sseForm from './sseStepForm.mjs'
import * as wsForm from './websocketStepForm.mjs'
import zh from '../../locales/lang/zh-cn/performance-testing.js'

const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const t = (key, values = {}) => (key.split('.').slice(1).reduce((value, part) => value?.[part], zh) || key).replace(/\{(\w+)\}/g, (_, name) => values[name] ?? `{${name}}`)
const text = node => [node?.text || '', ...(node?.children || []).map(text)].join('')
const nodes = node => [node, ...(node.children || []).flatMap(nodes)]
const renderer = Vue.createRenderer({ createElement: tag => ({ tag, props: {}, children: [] }), createText: value => ({ text: value }), createComment: () => ({ text: '' }),
  insert(child, parent, anchor) { if (child.parent) { const i = child.parent.children.indexOf(child); if (i >= 0) child.parent.children.splice(i, 1) } child.parent = parent; const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, child) },
  remove(child) { const i = child.parent?.children.indexOf(child) ?? -1; if (i >= 0) child.parent.children.splice(i, 1) },
  setText(node, value) { node.text = value }, setElementText(node, value) { node.children = [{ text: value }] }, patchProp(node, key, _, value) { node.props[key] = value },
  parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] })

async function mount(file, api, initial, extra = {}) {
  const events = [], confirmations = []
  const deps = { vue: Vue, '../apiCatalogForm.mjs': catalog, '../preparedRequestForm.mjs': fields, '../sseStepForm.mjs': sseForm, '../websocketStepForm.mjs': wsForm, '@/api/performance-testing': api,
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
    onSaved: value => events.push(['saved', value]), onChanged: value => events.push(['changed', value]), onImported: value => events.push(['imported', value]),
    'onUpdate:selectedIds': value => { props.selectedIds = value }, 'onUpdate:modelValue': value => { props.modelValue = value } }) })
  const wrap = tag => ({ inheritAttrs: false, setup: (_, { attrs, slots }) => () => Vue.h(tag, { ...attrs, disabled: attrs.disabled || attrs.loading }, [attrs.title, slots.default?.(), slots.footer?.()]) })
  for (const name of ['ElForm', 'ElFormItem', 'ElSelect', 'ElOption', 'ElCheckbox', 'ElTag', 'ElEmpty', 'ElPagination', 'ElAlert', 'ElCollapse', 'ElCollapseItem', 'ElDrawer', 'ElRadioGroup', 'ElRadioButton']) app.component(name, wrap('div'))
  app.component('ElButton', wrap('button')); app.component('ElInput', { inheritAttrs: false, setup: (_, { attrs }) => () => Vue.h('input', { ...attrs, value: attrs.modelValue, onInput: event => attrs['onUpdate:modelValue']?.(event.target.value) }) }); app.directive('loading', {})
  const root = { children: [] }; app.mount(root); await settle()
  return { props, state, root, events, confirmations, async click(label) { const button = nodes(root).find(node => node.tag === 'button' && text(node).trim() === label); assert.ok(button, label); if (!button.props.disabled) await button.props.onClick(); await settle() },
    async project(id) { props.projectId = id; await settle() }, unmount: () => app.unmount() }
}

const document = (id = 12, revision = 0) => ({ catalog_version: 3, prepared: { id: revision ? 100 + id : null, request_id: id, revision, status: 'blocked', gaps: [{ field: 'path/id', code: 'missing', message: '需要真实业务 ID' }] },
  request: { name: 'Item', method: 'POST', url: '/api/items/{{path_id}}', headers: { Authorization: 'Bearer {{token}}', 'X-Secret': '******' }, params: { page: 0, active: false }, body_type: 'JSON', body: '{"name":"","enabled":false}', files: [], extractors: [], assertions: [{ type: 'STATUS_CODE', expected: 200 }], think_time: 0, weight: 1 },
  preparation: { confirmed_fields: [], body_reviewed: false }, known_variable_names: ['token', 'user_id'],
  operation: { id, source_key: 'POST /items/{id}', request_path: '/api/items/{id}', method: 'POST',
    parameters: [{ name: 'id', in: 'path', required: true, schema: { type: 'integer' } }, { name: 'page', in: 'query', schema: { type: 'integer' } }, { name: 'active', in: 'query', schema: { type: 'boolean' } }],
    requirements: [{ field: 'path/id', required: true, resource: true, provenance: 'missing' }, { field: 'body/name', required: true, provenance: 'missing' }],
    request_body: { content: { 'application/json': { schema: { type: 'object', required: ['name'], properties: { name: { type: 'string' }, enabled: { type: 'boolean' } } } } } } }
})
const copy = value => JSON.parse(JSON.stringify(value))

function websocketDocument() {
  const value = document(12, 4)
  value.operation = { id: 12, method: 'GET', request_path: '/home/ws', parameters: [], requirements: [], raw: { 'x-websocket': true, responses: { 101: {} } } }
  value.request = { ...value.request, method: 'GET', url: '/home/ws', protocol: 'HTTP', websocket_config: {}, sse_config: {}, headers: { Authorization: 'Bearer {{token}}', cookie: '******', 'X-Client': 'pressure' } }
  value.prepared.protocol = 'HTTP'; value.prepared.status = 'unverified'; value.prepared.gaps = []
  value.auth_access_token_variable = 'pool_token'
  return value
}

test('declared WebSocket preparation converts, saves and reloads PUSH rules using the configured token variable', async () => {
  let saved = websocketDocument(), writes = 0
  const h = await editor({ getPerfPreparedRequest: async () => ({ data: copy(saved) }), updatePerfPreparedRequest: async (_, id, payload) => {
    writes++; assert.equal(payload.expected_revision, 4)
    saved = { ...saved, request: copy(payload.request), preparation: copy(payload.preparation), prepared: { ...saved.prepared, protocol: payload.request.protocol } }
    return { data: copy(saved) }
  } })
  try {
    await h.click('配置为 WebSocket 会话')
    assert.match(h.confirmations[0], /重新验证/)
    assert.equal(h.state.request.value.protocol, 'WEBSOCKET')
    assert.equal(h.state.request.value.url, '/home/ws')
    assert.deepEqual(h.state.request.value.headers, { 'X-Client': 'pressure' })
    for (const key of ['assertions', 'extractors', 'files']) assert.deepEqual(h.state.request.value[key], [])
    assert.equal(h.state.request.value.body_type, 'NONE'); assert.equal(h.state.request.value.body, '')
    assert.deepEqual(h.state.request.value.params, {}); assert.deepEqual(h.state.request.value.sse_config, {})
    assert.equal(h.state.request.value.websocket_config.auth.request.payload.token, '{{pool_token}}')
    h.state.updateWebsocketDraft('{'); await h.click('保存并重新检查')
    assert.equal(writes, 0); assert.match(h.state.error.value, /WebSocket/)
    const config = wsForm.websocketPushTemplate('home', 'pool_token')
    h.state.updateWebsocketDraft(JSON.stringify(config)); await h.click('保存并重新检查')
    assert.equal(writes, 1); assert.deepEqual(saved.request.websocket_config, config)
    assert.equal(Object.keys(saved.request).some(key => key.startsWith('_')), false)
    await h.state.load(); await settle()
    assert.deepEqual(JSON.parse(h.state.websocketDraft.value), config); assert.equal(h.state.dirty.value, false)
  } finally { h.unmount() }
})

test('WebSocket preparation accepts only GET plus literal declaration and an object 101 response', async () => {
  for (const change of [v => { v.operation.method = 'POST' }, v => { v.operation.raw['x-websocket'] = 'true' }, v => { delete v.operation.raw.responses[101] }, v => { v.operation.raw.responses[101] = null }, v => { v.operation.raw.responses[101] = [] }]) {
    const value = websocketDocument(); change(value)
    const h = await editor({ getPerfPreparedRequest: async () => ({ data: copy(value) }) })
    try {
      assert.equal(h.state.canEnableWebsocket.value, false)
      await h.state.enableWebsocket(); assert.equal(h.state.request.value.protocol, 'HTTP')
      h.state.rawText.value = JSON.stringify({ ...value.request, protocol: 'WEBSOCKET', websocket_config: wsForm.websocketTemplate('pool_token') })
      assert.equal(h.state.applyRaw(), false)
      assert.equal(h.writes.length, 0)
    } finally { h.unmount() }
  }
})

test('WebSocket conversion and pending save cannot replace a different selection or a newer draft', async () => {
  const pending = deferred(), value = websocketDocument()
  const h = await mount('./components/PreparedRequestEditor.vue', { getPerfPreparedRequest: async (_, id) => ({ data: id === 12 ? copy(value) : document(id) }) }, { projectId: 7, requestId: 12 }, { 'element-plus': { ElMessageBox: { confirm: () => pending.promise } } })
  try {
    const converting = h.state.enableWebsocket(); h.props.requestId = 13; await settle(); pending.resolve(); await converting
    assert.notEqual(h.state.request.value.protocol, 'WEBSOCKET')
  } finally { h.unmount() }
  const response = deferred(); Object.assign(value.request, { protocol: 'WEBSOCKET', body_type: 'NONE', body: '', params: {}, headers: {}, websocket_config: wsForm.websocketPushTemplate('home', 'pool_token') })
  const edit = await editor({ getPerfPreparedRequest: async () => ({ data: copy(value) }), updatePerfPreparedRequest: () => response.promise })
  try {
    const saving = edit.state.save()
    edit.state.websocketDraft.value = '{new draft'
    response.resolve({ data: { ...copy(value), prepared: { ...value.prepared, revision: 5 } } }); await saving
    assert.equal(edit.state.websocketDraft.value, '{new draft'); assert.match(edit.state.error.value, /修改/)
    assert.equal(edit.state.data.value.prepared.revision, 5)
  } finally { edit.unmount() }
})

test('catalog shows saved and evidence protocols separately and never infers WS evidence from GET', async () => {
  const prepared = { ...websocketDocument().prepared, protocol: 'WEBSOCKET', status: 'unverified', last_evidence: { protocol: 'HTTP', verdict: 'failed', execution_id: 8 } }
  const h = await mount('./components/ProjectApiCatalog.vue', {
    getPerfApiCatalog: async () => ({ data: { results: [{ id: 12, name: 'Home', method: 'GET', path: '/home/ws', tags: [], prepared }], count: 1, tags: [] } })
  }, { projectId: 7, selectable: true, selectedIds: [] })
  try {
    assert.match(text(h.root), /配置协议：WebSocket/); assert.match(text(h.root), /验证协议：HTTP/)
    assert.doesNotMatch(text(nodes(h.root).find(node => node.props?.class === 'request-meta')), /验证通过/)
    h.state.rows.value[0].prepared.last_evidence.protocol = undefined; await settle()
    assert.match(text(h.root), /验证协议：未记录/)
  } finally { h.unmount() }
})

test('WebSocket preparation never guesses an unconfigured authentication variable', async () => {
  const value = websocketDocument(); value.auth_access_token_variable = null
  const h = await editor({ getPerfPreparedRequest: async () => ({ data: copy(value) }) })
  try {
    await h.state.enableWebsocket()
    assert.equal(h.state.request.value.protocol, 'HTTP')
    assert.match(text(h.root), /公共认证/)
  } finally { h.unmount() }
})

for (const protocol of ['HTTP', 'WEBSOCKET']) test(`prepared ${protocol} to SSE editor saves and reloads a declared stream and blocks invalid drafts`, async () => {
  let saved = document(12, 1), writes = 0
  saved.operation.raw = { responses: { 200: { content: { 'text/event-stream': {} } } } }
  Object.assign(saved.request, { protocol, websocket_config: protocol === 'WEBSOCKET' ? wsForm.websocketTemplate() : {}, sse_config: {} })
  const h = await editor({ getPerfPreparedRequest: async () => ({ data: copy(saved) }), updatePerfPreparedRequest: async (_, id, payload) => {
    writes++; saved = { ...saved, request: copy(payload.request), preparation: copy(payload.preparation) }; return { data: copy(saved) }
  } })
  try {
    await h.click('配置为 SSE 业务流')
    assert.equal(h.state.request.value.protocol, 'SSE')
    assert.equal(h.state.request.value.body, saved.request.body)
    assert.deepEqual(h.state.request.value.assertions, [])
    assert.deepEqual(h.state.request.value.websocket_config, {})
    assert.equal(h.state.websocketDraft.value, undefined)
    h.state.updateSseDraft('{'); await h.click('保存并重新检查')
    assert.equal(writes, 0); assert.match(h.state.error.value, /SSE/)
    const config = sseForm.sseTemplate('CONTENT_SPEECH')
    h.state.updateSseDraft(JSON.stringify(config)); await h.click('保存并重新检查')
    assert.equal(writes, 1); assert.deepEqual(saved.request.sse_config, config)
    await h.state.load(); await settle()
    assert.deepEqual(JSON.parse(h.state.sseDraft.value), config)
    assert.equal(h.state.dirty.value, false)
  } finally { h.unmount() }
})

test('prepared SSE conversion cannot cross a changed catalog selection during confirmation', async () => {
  const pending = deferred()
  const source = document(); source.operation.raw = { produces: ['text/event-stream'] }
  const h = await mount('./components/PreparedRequestEditor.vue', { getPerfPreparedRequest: async (_, id) => ({ data: id === 12 ? copy(source) : document(id) }) }, { projectId: 7, requestId: 12 }, { 'element-plus': { ElMessageBox: { confirm: () => pending.promise } } })
  try {
    const conversion = h.state.enableSse()
    h.props.requestId = 13; await settle(); pending.resolve(); await conversion
    assert.notEqual(h.state.request.value.protocol, 'SSE')
    assert.equal(h.state.canEnableSse.value, false)
  } finally { h.unmount() }
})

test('whole union body saves and reopens JSON with variables without double encoding', async () => {
  const saved = document()
  saved.operation.request_body.content['application/json'].schema = { oneOf: [
    { type: 'object', properties: { grant_type: { const: 'sms_code' } } },
    { type: 'object', properties: { grant_type: { const: 'password' } } }
  ] }
  saved.request.body = JSON.stringify(JSON.stringify({ grant_type: 'sms_code' }))
  const h = await editor({ getPerfPreparedRequest: async () => ({ data: copy(saved) }),
    updatePerfPreparedRequest: async (_, id, payload) => {
      saved.request = copy(payload.request)
      return { data: copy(saved) }
    } })
  try {
    const body = { grant_type: 'sms_code', phone: '{{login_phone}}', verification_code: '{{login_code}}', device_id: 'test-device' }
    await h.fill('body', JSON.stringify(body))
    await h.click('保存并重新检查')
    assert.deepEqual(JSON.parse(saved.request.body), body)
    await h.state.load(); await settle()
    const input = nodes(h.root).find(node => node.tag === 'input' && node.props['aria-label'] === 'body')
    assert.deepEqual(JSON.parse(input.props['model-value']), body)
  } finally { h.unmount() }
})

test('whole JSON body retains incomplete draft and blocks save until corrected', async () => {
  const saved = document()
  saved.operation.request_body.content['application/json'].schema = { oneOf: [{ type: 'object' }] }
  saved.request.body = '{broken old body'
  const writes = []
  const h = await editor({ getPerfPreparedRequest: async () => ({ data: copy(saved) }),
    updatePerfPreparedRequest: async (_, id, payload) => { writes.push(copy(payload)); return { data: copy(saved) } } })
  try {
    await h.fill('body', '{"value":')
    assert.equal(h.state.request.value.body, '{"value":')
    await h.click('保存并重新检查')
    assert.equal(writes.length, 0)
    await h.fill('body', '{"value":false}')
    await h.click('保存并重新检查')
    assert.deepEqual(JSON.parse(writes[0].request.body), { value: false })
  } finally { h.unmount() }
})

test('whole JSON union body preserves explicit strings and typed scalars', () => {
  const operation = { request_body: { content: { 'application/json': { schema: { oneOf: [{ type: 'object' }, { type: 'string' }] } } } } }
  const request = { body_type: 'JSON', body: '{}' }
  const field = fields.scalarFields(operation, request).find(item => item.field === 'body')
  assert.equal(field.type, 'json')
  for (const value of ['{"nested":true}', '{{payload}}', 0, false, null, [1, 2]]) {
    const updated = fields.writeField(request, operation, field, JSON.stringify(value))
    fields.checkScalarTypes(updated, operation)
    assert.deepEqual(JSON.parse(updated.body), value)
  }
  assert.equal(JSON.parse(fields.writeField(request, operation, { field: 'body', type: 'string' }, '{"literal":true}').body), '{"literal":true}')
})

test('optional union body may stay omitted while required JSON must be present', () => {
  const operation = { request_body: { required: false, content: { 'application/json': { schema: { oneOf: [{ type: 'object' }] } } } } }
  const request = { body_type: 'NONE', body: '', files: [] }
  assert.doesNotThrow(() => fields.checkScalarTypes(request, operation))
  operation.request_body.required = true
  assert.throws(() => fields.checkScalarTypes(request, operation), /body/)
})

test('editing scalar parameters preserves setup references and displays their source and outputs', async () => {
  const saved = document(12, 1)
  const dependency = { source_key: 'POST /conversations', revision: 6, definition_hash: 'a'.repeat(64), extractors: [{ name: 'conversation_id', type: 'JSON_PATH', expr: '$.data.id' }] }
  saved.preparation.setup_steps = [dependency]
  const recovery = { version: 1, kind: 'portfolio_reminder', group_id: 'owned_reminder', stock_code: 'sz000001', max_resources: 1000 }
  saved.preparation.resource_recovery = recovery
  saved.prepared.setup_steps = [{ ...dependency, method: 'POST', path: '/conversations', outputs: ['conversation_id'] }]
  let payload
  const h = await mount('./components/PreparedRequestEditor.vue', {
    getPerfPreparedRequest: async () => ({ data: copy(saved) }),
    updatePerfPreparedRequest: async (_, id, value) => { payload = copy(value); return { data: { ...copy(saved), request: value.request, preparation: value.preparation } } },
  }, { projectId: 7, requestId: 12 })
  try {
    assert.match(text(h.root), /POST \/conversations/)
    assert.match(text(h.root), /conversation_id/)
    h.state.updateField(h.state.fields.value.find(field => field.field === 'path/id'), '42')
    await h.click('保存并重新检查')
    assert.deepEqual(payload.preparation.setup_steps, [dependency])
    assert.deepEqual(payload.preparation.resource_recovery, recovery)
    assert.equal(saved.preparation.setup_steps[0].revision, 6)
  } finally { h.unmount() }
})

test('manual preparation notes render as plain text in the catalog and editor without changing verification status', async () => {
  const note = '功能未实现：仓储缺少能力。\n真实执行 #49：HTTP 503。<script>doNotRun()</script>'
  const saved = document(12, 1)
  saved.prepared.preparation_note = note
  saved.prepared.setup_steps = [{ source_key: 'POST /conversations', method: 'POST', path: '/conversations', revision: 6, definition_hash: 'a'.repeat(64), outputs: ['conversation_id'] }]
  const h = await mount('./components/PreparedRequestEditor.vue', { getPerfPreparedRequest: async () => ({ data: saved }) }, { projectId: 7, requestId: 12 })
  const c = await mount('./components/ProjectApiCatalog.vue', {
    getPerfApiCatalog: async () => ({ data: { results: [{ id: 12, name: 'Item', method: 'GET', path: '/items', tags: [], readiness: { ready: true }, prepared: saved.prepared }], count: 1, tags: [], version: { version: 3 } } }),
    getPerfApiCatalogRequest: async () => ({ data: { version: { version: 3 }, operation: saved.operation, readiness: {}, prepared: saved.prepared } }),
  }, { projectId: 7, selectable: true, selectedIds: [], usePrepared: true })
  try {
    assert.equal(text(c.root).includes(note), false)
    assert.equal(text(c.root).includes('POST /conversations'), false)
    c.state.showDetail(12); await settle()
    for (const instance of [h, c]) {
      assert.ok(text(instance.root).includes(note))
      assert.match(text(instance.root), /POST \/conversations/)
      assert.match(text(instance.root), /conversation_id/)
      assert.equal(nodes(instance.root).some(node => node.tag === 'script' || node.props?.innerHTML), false)
      const status = nodes(instance.root).find(node => ['editor-heading', 'request-meta'].includes(node.props?.class))
      assert.match(text(status), /未补齐/)
      assert.doesNotMatch(text(status), /验证通过/)
    }
    assert.equal(h.events.length, 0)
    assert.equal(c.props.selectedIds.length, 0)
  } finally { h.unmount(); c.unmount() }
})

async function editor(overrides = {}) {
  let saved = document()
  const writes = []
  const h = await mount('./components/PreparedRequestEditor.vue', {
    getPerfPreparedRequest: async () => ({ data: copy(saved) }),
    updatePerfPreparedRequest: async (project, id, payload) => {
      writes.push({ project, id, payload: copy(payload) })
      saved = { ...copy(saved), request: copy(payload.request), preparation: copy(payload.preparation), prepared: { ...saved.prepared, revision: saved.prepared.revision + 1, status: 'unverified', gaps: [] } }
      return { data: copy(saved) }
    }, ...overrides
  }, { projectId: 7, requestId: 12 })
  return { ...h, writes, async fill(field, value) {
    const input = nodes(h.root).find(node => node.tag === 'input' && node.props['aria-label'] === field)
    assert.ok(input, `Editable field ${field}`); input.props.onInput({ target: { value } }); await settle()
  } }
}

test('FORM scalar edits preserve encoding and binary fields are selected as files', () => {
  const operation = { request_body: { content: { 'multipart/form-data': { schema: {
    type: 'object', required: ['file', 'language'], properties: {
      file: { type: 'string', format: 'binary' }, language: { type: 'string' } }
  } } } } }
  const request = { body_type: 'FORM', body: '{"language":"zh"}', files: [{ field: 'file', file_id: 9 }] }
  assert.equal(fields.scalarFields(operation, request).some(field => field.field === 'body/file'), false)
  const changed = fields.writeField(request, operation, { field: 'body/language', type: 'string' }, 'en')
  assert.equal(changed.body_type, 'FORM'); assert.deepEqual(changed.files, request.files)
})

test('project file picker rejects stale project results and selects only listed upload files', async () => {
  const late = deferred(), requests = []
  const h = await mount('./components/UploadFileFields.vue', {
    getPerfDataFiles: async params => { requests.push(params); return params.project === 7 ? late.promise : { data: [{ id: 21, name: 'Current.wav' }] } }
  }, { projectId: 7, modelValue: [{ field: 'file', file_id: null }] })
  try {
    await h.project(8); late.resolve({ data: [{ id: 11, name: 'Old.wav' }] }); await settle()
    assert.equal(h.state.files.value[0].id, 21)
    h.state.selectFile(0, 11); await settle(); assert.equal(h.props.modelValue[0].file_id, null)
    h.state.selectFile(0, 21); await settle(); assert.equal(h.props.modelValue[0].file_id, 21)
    assert.ok(requests.every(params => params.file_type === 'UPLOAD'))
  } finally { h.unmount() }
})

test('prepared multipart selection survives normal save and reopening', async () => {
  let saved = document(), writes = 0
  saved.operation = { ...saved.operation, request_body: { content: { 'multipart/form-data': { schema: {
    type: 'object', properties: { file: { type: 'string', format: 'binary' }, language: { type: 'string' } }
  } } } }, requirements: [] }
  saved.request = { ...saved.request, body_type: 'FORM', body: '{"language":"zh","file":null}', files: [{ field: 'file', file_id: null }] }
  const h = await mount('./components/PreparedRequestEditor.vue', {
    getPerfPreparedRequest: async () => ({ data: copy(saved) }),
    updatePerfPreparedRequest: async (_, id, payload) => { writes++; saved = { ...saved, request: payload.request, preparation: payload.preparation }; return { data: copy(saved) } }
  }, { projectId: 7, requestId: 12 })
  try {
    h.state.updateFiles([{ field: 'file', file_id: 21, filename: '', content_type: '' }]); await settle()
    await h.click('保存并重新检查'); assert.equal(writes, 1)
    await h.state.load(); assert.equal(h.state.request.value.body_type, 'FORM')
    assert.equal(h.state.request.value.files[0].file_id, 21)
    assert.deepEqual(JSON.parse(h.state.request.value.body), { language: 'zh' })
  } finally { h.unmount() }
})

test('upload waits for the project file list and cannot strand its loading state', async () => {
  const late = deferred(); let uploads = 0
  const h = await mount('./components/UploadFileFields.vue', {
    getPerfDataFiles: () => late.promise,
    uploadPerfUploadFile: async () => { uploads++; return { data: { id: 31, name: 'sample.wav' } } }
  }, { projectId: 7, modelValue: [{ field: 'file', file_id: null }] })
  try {
    assert.equal(h.state.loading.value, true)
    await h.state.upload({ target: { files: [{ name: 'sample.wav', size: 16 }], value: 'sample.wav' } })
    assert.equal(uploads, 0)
    late.resolve({ data: [] }); await settle()
    assert.equal(h.state.loading.value, false)
    await h.state.upload({ target: { files: [{ name: 'sample.wav', size: 16 }], value: 'sample.wav' } })
    assert.equal(uploads, 1); assert.equal(h.state.loading.value, false); assert.equal(h.state.uploading.value, false)
    assert.equal(h.props.modelValue[0].file_id, 31)
  } finally { h.unmount() }
})

test('scalar path/query/body values can be filled, saved with CAS, and reopened without losing typed defaults or masks', async () => {
  const h = await editor()
  try {
    assert.match(text(h.root), /需要真实业务 ID/)
    await h.fill('path/id', ''); await h.fill('path/id', '42'); await h.fill('body/name', 'Example')
    await h.click('保存并重新检查')
    assert.equal(h.writes.length, 1)
    const body = h.writes[0].payload
    assert.equal(body.expected_catalog_version, 3); assert.equal(body.expected_revision, 0)
    assert.equal(body.request.url, '/api/items/42'); assert.deepEqual(body.request.params, { page: 0, active: false })
    assert.equal(JSON.parse(body.request.body).name, 'Example'); assert.equal(JSON.parse(body.request.body).enabled, false)
    assert.equal(body.request.headers['X-Secret'], '******'); assert.equal(body.request.headers.Authorization, 'Bearer {{token}}')
    await h.state.load(); assert.equal(h.state.request.value.url, '/api/items/42'); assert.equal(h.state.data.value.prepared.revision, 1)
    const heading = nodes(h.root).find(node => node.props?.class === 'editor-heading')
    assert.match(text(heading), /待验证/); assert.doesNotMatch(text(heading), /验证通过/)
  } finally { h.unmount() }
})

test('known variable references and explicit resource confirmation remain editable without exposing account values', async () => {
  const h = await editor()
  try {
    h.state.useVariable(h.state.fields.value.find(field => field.field === 'path/id'), 'user_id'); await settle()
    h.state.confirmField('path/id', true); await h.click('保存并重新检查')
    assert.equal(h.writes[0].payload.request.url, '/api/items/{{user_id}}')
    assert.deepEqual(h.writes[0].payload.preparation.confirmed_fields, ['path/id'])
    assert.deepEqual(h.state.data.value.known_variable_names, ['token', 'user_id'])
  } finally { h.unmount() }
})

test('409 retains user edits, advanced JSON edits are usable, and malformed drafts cannot be saved', async () => {
  let fail = true
  const h = await editor({ updatePerfPreparedRequest: async (_, id, payload) => { if (fail) throw { response: { status: 409 } }; return { data: { ...document(id, 1), request: payload.request } } } })
  try {
    await h.fill('path/id', '99'); await h.click('保存并重新检查')
    assert.equal(h.state.request.value.url, '/api/items/99'); assert.match(text(h.root), /已变化/)
    h.state.rawText.value = '{invalid'; await h.click('保存并重新检查'); assert.match(text(h.root), /JSON/)
    const next = copy(h.state.request.value); next.assertions.push({ type: 'JSON_PATH', json_path: '$.code', expected: 200 })
    h.state.rawText.value = JSON.stringify(next); fail = false; await h.click('保存并重新检查')
    assert.equal(h.state.request.value.assertions.length, 2)
  } finally { h.unmount() }
})

test('late loads and saves never overwrite a different project or interface', async () => {
  const old = deferred()
  const h = await editor({ getPerfPreparedRequest: async (_, id) => id === 12 ? old.promise : { data: document(id) } })
  try { h.props.requestId = 13; await settle(); old.resolve({ data: document(12) }); await settle(); assert.equal(h.state.data.value.operation.id, 13) }
  finally { h.unmount() }
  const saving = deferred()
  const p = await editor({ updatePerfPreparedRequest: () => saving.promise })
  try {
    await p.fill('path/id', '42'); const task = p.click('保存并重新检查'); await settle()
    p.props.projectId = 8; p.props.requestId = 13; await settle()
    saving.resolve({ data: document(12, 1) }); await task
    assert.equal(p.events.length, 0); assert.equal(p.state.request.value.url, '/api/items/{{path_id}}')
  } finally { p.unmount() }
})

test('field projection preserves encoded path segments and nested typed body values', () => {
  const data = document()
  const next = fields.writeField({ ...data.request, url: '/api/items/old?keep=1' }, data.operation, { field: 'path/id', type: 'string' }, 'a/b')
  assert.equal(next.url, '/api/items/a%2Fb?keep=1'); assert.equal(fields.readField(next, data.operation, 'path/id'), 'a/b')
  assert.equal(fields.writeField(data.request, data.operation, { field: 'body/enabled', type: 'boolean' }, 'false').params.active, false)
  assert.throws(() => fields.writeField(data.request, data.operation, { field: 'body/__proto__/polluted', type: 'string' }, 'yes'))
})
