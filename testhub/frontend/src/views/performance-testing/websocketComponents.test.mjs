import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as form from './websocketStepForm.mjs'
import * as metrics from './websocketMetrics.mjs'
import * as catalog from './apiCatalogForm.mjs'
import * as sseForm from './sseStepForm.mjs'
import * as sseMetrics from './sseMetrics.mjs'
import zh from '../../locales/lang/zh-cn/performance-testing.js'
import en from '../../locales/lang/en/performance-testing.js'
import { baseCompile } from '@intlify/message-compiler'

const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
async function compile(file, extra = {}) {
  const descriptor = parse(await readFile(new URL(file, import.meta.url), 'utf8')).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: file }).errors, [])
  const modules = [], deps = { vue: Vue, '../websocketStepForm.mjs': form, '../websocketMetrics.mjs': metrics, '../apiCatalogForm.mjs': catalog, '../sseStepForm.mjs': sseForm, '../sseMetrics.mjs': sseMetrics,
    'vue-i18n': { useI18n: () => ({ t: key => key }) }, 'element-plus': { ElMessageBox: { confirm: async () => true } }, ...extra }
  const source = compileScript(descriptor, { id: file }).content.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(deps[name] || {}) - 1
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
  }).replace('export default', 'return')
  return new Function('__modules', source)(modules)
}
function mount(component, initialProps) {
  let state
  const wrapped = { ...component, setup(props, context) { state = component.setup(props, context); return state }, render: () => null }
  const container = {}
  const update = props => renderer.render(Vue.h(wrapped, props), container)
  update(initialProps)
  return { get state() { return state }, update, unmount: () => renderer.render(null, container) }
}
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }

test('WebSocket editor is inert while disabled and drops a pending template after unmount', async () => {
  let confirm, updates = 0
  const pending = new Promise(resolve => { confirm = resolve })
  const component = await compile('./components/WebSocketStepEditor.vue', { 'element-plus': { ElMessageBox: { confirm: () => pending } } })
  const config = form.websocketPushTemplate('home', 'pool_token')
  const props = { modelValue: config, accessVariable: 'pool_token', disabled: true, 'onUpdate:modelValue': () => { updates++ }, 'onUpdate:draft': () => { updates++ } }
  const h = mount(component, props)
  h.state.updateDraft(JSON.stringify(form.websocketPushTemplate('quotes', 'pool_token')))
  h.state.updateTimer('hold_open_ms', 1000)
  assert.equal(updates, 0)
  h.update({ ...props, disabled: false })
  const applying = h.state.applyPushTemplate('quotes'); h.unmount(); confirm(); await applying
  assert.equal(updates, 0)
})

test('delayed SSE template confirmation cannot write into another keyed step or project after remount', async () => {
  let confirm, updates = 0
  const pending = new Promise(resolve => { confirm = resolve })
  const component = await compile('./components/SseStepEditor.vue', { 'element-plus': { ElMessageBox: { confirm: () => pending } } })
  const next = sseForm.sseTemplate('MESSAGE_SPEECH')
  const h = mount(component, { key: 'step-one', modelValue: sseForm.sseTemplate('CHAT'), 'onUpdate:modelValue': () => { updates++ }, 'onUpdate:draft': () => { updates++ } })
  try {
    const applying = h.state.applyTemplate('CONTENT_SPEECH')
    h.update({ key: 'step-two', modelValue: next, 'onUpdate:modelValue': () => { updates++ }, 'onUpdate:draft': () => { updates++ } })
    await settle(); confirm(); await applying; await settle()
    assert.equal(updates, 0)
    assert.deepEqual(h.state.parsed.value.config, next)
  } finally { h.unmount() }
})

test('SSE editor keeps malformed draft without replacing the last valid model and supports typed preset reload', async () => {
  let config = sseForm.sseTemplate(), draft, h
  const props = () => ({ modelValue: config, draft, 'onUpdate:modelValue': value => { config = value; h?.update(props()) }, 'onUpdate:draft': value => { draft = value; h?.update(props()) } })
  const component = await compile('./components/SseStepEditor.vue')
  h = mount(component, props())
  try {
    h.state.updateDraft('{'); await settle()
    assert.equal(draft, '{'); assert.equal(h.state.parsed.value.config, null)
    assert.equal(config.rules[0].name, 'progress')
    await h.state.applyTemplate('CONTENT_SPEECH'); await settle()
    assert.equal(config.rules[1].match[0].expected, true)
    h.state.updateBound('idle_ms', 20000); await settle()
    assert.equal(config.idle_ms, 20000)
    h.unmount(); h = mount(component, props()); await settle()
    assert.deepEqual(h.state.parsed.value.errors, [])
    assert.equal(h.state.parsed.value.config.rules[2].match[0].expected, false)
  } finally { h.unmount() }
})

test('StepEditor permits only declared catalog SSE conversion and keeps editable request while saving/reloading', async () => {
  let model = { name: 'Speech', protocol: 'HTTP', method: 'POST', url: '/speech', body_type: 'JSON', body: '{"voice":"test"}', headers: { Accept: 'text/event-stream' }, params: {}, assertions: [{ type: 'STATUS_CODE', expected: 200 }], extractors: [], files: [], source_request: 8, source_metadata: { raw: { responses: { 200: { content: { 'text/event-stream': {} } } } } } }, h
  const props = () => ({ isK6: true, modelValue: model, 'onUpdate:modelValue': value => { model = value; h?.update(props()) } })
  const component = await compile('./components/StepEditor.vue')
  h = mount(component, props())
  try {
    await h.state.changeProtocol('WEBSOCKET'); await settle()
    assert.equal(model.protocol, 'HTTP')
    await h.state.changeProtocol('SSE'); await settle()
    assert.equal(model.protocol, 'SSE'); assert.equal(model.method, 'POST'); assert.equal(model.body, '{"voice":"test"}')
    assert.equal(h.state.isSse.value, true); assert.deepEqual(model.assertions, [])
    model = { ...form.stepForSave(model, 1), source_metadata: model.source_metadata }; h.unmount(); h = mount(component, props()); await settle()
    assert.equal(h.state.form.sse_config.version, 1)
    assert.equal(h.state.isSse.value, true)
  } finally { h.unmount() }
})

test('SSE report component renders unknown history and fixed failure stages without upstream messages', async () => {
  const h = mount(await compile('./components/SseMetrics.vue'), { metrics: { stream_metrics: [{ step_id: 5, streams: { started: 1 }, errors: [{ phase: 'business', reason: 'assertion_failed', count: 1 }, { phase: 'private detail', reason: 'token-secret', count: 1 }] }] }, steps: [{ id: 5, protocol: 'SSE', name: 'Frozen speech' }] })
  try {
    assert.equal(h.state.rows.value[0].name, 'Frozen speech')
    assert.equal(h.state.rows.value[0].completion.avg_ms, null)
    assert.equal(h.state.display(null), 'performanceTesting.sse.unknown')
    assert.equal(h.state.rows.value[0].errors[1].reason, 'unknown')
  } finally { h.unmount() }
})

test('SSE report exposes only safe one-based failure locations and the bounded-sample warning', async () => {
  const location = { reason: 'assertion_failed', scope: 'global_assertion', event_index: 2, condition_index: 1, count: 1 }
  const h = mount(await compile('./components/SseMetrics.vue'), { metrics: { stream_metrics: [{ step_id: 5, diagnostics: [location, { ...location, data: 'PRIVATE' }], diagnostics_truncated: true }] }, steps: [{ id: 5, protocol: 'SSE', name: 'Frozen stream' }] })
  try {
    assert.deepEqual(h.state.rows.value[0].diagnostics, [location])
    assert.equal(h.state.rows.value[0].diagnosticsTruncated, true)
    assert.ok(h.state.locationLabel(location).includes('2'))
    assert.ok(!h.state.locationLabel(location).includes('undefined'))
  } finally { h.unmount() }
})

test('actual WS editor retains malformed draft, keeps valid config, and preserves typed values when repaired/reordered', async () => {
  let config = form.websocketTemplate('pool_token'), draft, h
  const props = () => ({ modelValue: config, draft, accessVariable: 'pool_token',
    'onUpdate:modelValue': value => { config = value; h?.update(props()) }, 'onUpdate:draft': value => { draft = value; h?.update(props()) } })
  h = mount(await compile('./components/WebSocketStepEditor.vue'), props())
  try {
    h.state.updateDraft('{'); await settle()
    assert.equal(draft, '{'); assert.equal(h.state.parsed.value.config, null)
    assert.equal(config.commands[0].request.action, 'timeline.list')
    h.state.updateDraft(JSON.stringify(config)); await settle()
    h.state.moveCommand(1, -1); await settle()
    assert.equal(config.commands[0].request.action, 'timeline.unread')
    assert.equal(config.commands[0].assertions[0].expected, true)
    h.state.updateTimer('hold_open_ms', 2000); await settle()
    assert.equal(config.hold_open_ms, 2000)
    assert.equal(JSON.parse(draft).auth.request.payload.token, '{{pool_token}}')
  } finally { h.unmount() }
})

test('actual StepEditor rejects controlled conversion and preserves WS URL query without HTTP parameter extraction', async () => {
  let confirmations = 0, model = { name: 'Manual', method: 'POST', body_type: 'JSON', body: '{"value":1}', headers: {}, params: {}, assertions: [], extractors: [], files: [] }, h
  const props = () => ({ isK6: true, modelValue: model, accessVariable: 'pool_token', 'onUpdate:modelValue': value => { model = value; h?.update(props()) } })
  h = mount(await compile('./components/StepEditor.vue', { 'element-plus': { ElMessageBox: { confirm: async () => { confirmations++ } } } }), props())
  try {
    await h.state.changeProtocol('WEBSOCKET'); await settle()
    assert.equal(confirmations, 1); assert.equal(model.protocol, 'WEBSOCKET'); assert.equal(model.body, '')
    h.state.form.url = '/socket?locale=en'; h.state.syncUrlQueryParams(); await settle()
    assert.equal(model.url, '/socket?locale=en'); assert.deepEqual(model.params, {})
    await h.state.changeProtocol('HTTP'); await settle()
    assert.equal(model.body, '{"value":1}'); assert.equal(model.method, 'POST')
    model = { ...model, source_request: 7 }; h.update(props()); await settle()
    await h.state.changeProtocol('WEBSOCKET'); await settle()
    assert.equal(confirmations, 2); assert.equal(model.protocol, 'HTTP')
  } finally { h.unmount() }
})

test('StepEditor can switch to a file body and retain the selected project file on reload', async () => {
  let model = { name: 'upload', method: 'PUT', url: '/upload', body_type: 'JSON', body: '{}', files: [], assertions: [], extractors: [] }, h
  const props = () => ({ isK6: true, modelValue: model, uploadFiles: [{ id: 25, name: 'sample.wav' }],
    'onUpdate:modelValue': value => { model = value; h?.update(props()) } })
  const component = await compile('./components/StepEditor.vue')
  h = mount(component, props())
  try {
    h.state.form.body_type = 'BINARY'; h.state.bodyTypeChanged('BINARY'); await settle()
    assert.equal(model.body, ''); assert.equal(model.files.length, 1)
    h.state.form.files[0].file_id = 25; await settle()
    h.unmount(); h = mount(component, props()); await settle()
    assert.equal(h.state.form.body_type, 'BINARY'); assert.equal(h.state.form.files[0].file_id, 25)
  } finally { h.unmount() }
})

test('actual WS metrics shows unknown connections and restores command labels only from frozen metadata', async () => {
  const h = mount(await compile('./components/WebSocketMetrics.vue'), { metrics: { connections: { observed: true, current: null, peak: 2, unclosed: 1 }, commands_truncated: true,
    command_metrics: [{ step_id: 3, command_index: 0, total: 1, success: 1, failed: 0, name: 'Wrong mutable name' }] },
  steps: [{ id: 3, protocol: 'WEBSOCKET', websocket_commands: [{ index: 0, name: 'Frozen read', action: 'timeline.list' }] }] })
  try {
    assert.equal(h.state.connections.value.current, null)
    assert.equal(h.state.display(h.state.connections.value.current), 'performanceTesting.websocket.unknown')
    assert.equal(h.state.commands.value[0].name, 'Frozen read')
    assert.equal(h.state.commands.value[0].action, 'timeline.list')
  } finally { h.unmount() }
})

test('actual WS editor applies push templates and renders event wait metadata separately', async () => {
  let config = form.websocketTemplate('pool_token'), draft, h
  const props = () => ({ modelValue: config, draft, accessVariable: 'pool_token',
    'onUpdate:modelValue': value => { config = value; h?.update(props()) }, 'onUpdate:draft': value => { draft = value; h?.update(props()) } })
  h = mount(await compile('./components/WebSocketStepEditor.vue'), props())
  try {
    await h.state.applyPushTemplate('reminder'); await settle()
    assert.equal(config.mode, 'PUSH')
    assert.equal(config.commands[0].event_path, '$.event_type')
    assert.equal(config.auth.token, '{{pool_token}}')
    assert.deepEqual(h.state.parsed.value.errors, [])
  } finally { h.unmount() }
  const rendered = mount(await compile('./components/WebSocketMetrics.vue'), {
    metrics: { events: { success: 1 }, command_metrics: [{ step_id: 2, command_index: 0, total: 1 }] },
    steps: [{ id: 2, protocol: 'WEBSOCKET', websocket_commands: [{ index: 0, name: 'Reminder', action: 'portfolio.reminder.triggered', kind: 'event' }] }] })
  try {
    assert.equal(rendered.state.commands.value[0].latencyKind, 'event_wait')
    assert.equal(rendered.state.stages.value.find(row => row.label.endsWith('.events')).success, 1)
  } finally { rendered.unmount() }
})

test('WS and SSE locale messages compile in both supported locale catalogs', () => {
  const keys = object => Object.entries(object).flatMap(([key, value]) => typeof value === 'string' ? [key] : keys(value).map(child => `${key}.${child}`))
  assert.deepEqual(keys(zh.websocket).sort(), keys(en.websocket).sort())
  const visit = object => Object.values(object).forEach(value => {
    if (typeof value !== 'string') return visit(value)
    const errors = []; baseCompile(value, { onError: error => errors.push(error) }); assert.deepEqual(errors, [])
  })
  visit(zh.websocket); visit(en.websocket)
  assert.deepEqual(keys(zh.sse).sort(), keys(en.sse).sort())
  visit(zh.sse); visit(en.sse)
})
