import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import zh from '../../locales/lang/zh-cn/performance-testing.js'
import en from '../../locales/lang/en/performance-testing.js'
import { baseCompile } from '@intlify/message-compiler'

const clone = value => JSON.parse(JSON.stringify(value))
const config = () => ({ version: 1, kind: 'portfolio_reminder', group_id: 'reminders', put_step_id: 11, receipt_step_id: 12, get_step_id: 13, delete_step_id: 14, stock_code: 'sh600000', max_resources: 1000 })
const steps = () => ['PUT', 'GET', 'GET', 'DELETE'].map((method, index) => ({ id: 11 + index, name: `step ${index}`, method, protocol: 'HTTP', enabled: true,
  url: index === 1 ? '/api/v1/portfolio/reminder-commands/{{rr_put_digest}}' : '/api/v1/portfolio/reminders/sh600000',
  execution_policy: { group_id: 'reminders', vu_start: 1, vu_end: 1000, max_runs_per_vu: 1, min_interval_ms: 0 } }))
const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }), insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
async function compile(file, dependencies = {}) {
  const url = new URL(file, import.meta.url)
  const source = await readFile(url, 'utf8').catch(() => '')
  assert.ok(source, `${file} must exist`)
  const descriptor = parse(source).descriptor
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: file }).errors, [])
  const script = compileScript(descriptor, { id: file }).content
  for (const match of script.matchAll(/from ['"]([^'"]+\.mjs)['"]/g)) dependencies[match[1]] = await import(new URL(match[1], url))
  const deps = { vue: Vue, 'vue-i18n': { useI18n: () => ({ t: key => key }) }, ...dependencies }, modules = []
  const code = script.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(deps[name] || {}) - 1
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
  }).replace('export default', 'return')
  return new Function('__modules', code)(modules)
}
function mount(component, props = {}) {
  let state
  const wrapped = { ...component, setup(p, context) { state = component.setup(p, context); return state }, render: () => null }, container = {}
  const update = props => renderer.render(Vue.h(wrapped, props), container)
  update(props)
  return { state, update, close: () => renderer.render(null, container) }
}

test('actual editor binds saved once steps, reloads server value and respects disabled state', async () => {
  let model = {}, valid = true, disabled = false, h
  const props = () => ({ modelValue: model, steps: steps(), concurrency: 1000, disabled,
    'onUpdate:modelValue': value => { model = value; h?.update(props()) }, onValidity: value => { valid = value } })
  h = mount(await compile('./components/ReminderRecoveryEditor.vue'), props())
  try {
    h.state.toggle(true); await settle(); assert.equal(valid, false)
    for (const [key, value] of Object.entries(config())) h.state.field(key, value)
    await settle(); assert.equal(valid, true); assert.deepEqual(model, config())
    assert.deepEqual(h.state.options('receipt_step_id').map(step => step.id), [12])
    const saved = clone(model); h.state.field('max_resources', 1); await settle(); assert.equal(valid, false)
    model = saved; h.update(props()); await settle(); assert.equal(valid, true)
    disabled = true; h.update(props()); await settle(); h.state.field('stock_code', 'sz000001'); h.state.toggle(false); assert.deepEqual(model, saved)
    disabled = false; h.update(props()); await settle(); h.state.toggle(false); assert.deepEqual(model, {})
  } finally { h.close() }
})

async function scenarioHarness(initial, onSave) {
  let saved = clone(initial), sent = []
  const api = { getEngineStatus: async () => ({ data: { engines: [] } }), getPerfProjects: async () => ({ data: [{ id: 7 }] }), getPerfDataFiles: async () => ({ data: [] }),
    getPerfEnvironmentPermissions: async () => ({ data: { project_ids: [7] } }), getPerfEnvironments: async () => ({ data: [] }), getPerfScenario: async () => ({ data: clone(saved) }),
    updatePerfScenario: async (_, payload) => { sent.push(clone(payload)); await onSave?.(); saved = clone({ ...saved, ...payload }); return { data: clone(saved) } },
    savePerfScenarioSteps: async (_, value) => { saved.steps = clone(value); return { data: { steps: clone(value) } } }, getPerfScenarioReadiness: async () => ({ data: { results: [] } }) }
  const oldWindow = globalThis.window; globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const h = mount(await compile('./ScenarioEditor.vue', { '@/api/performance-testing': api,
    'element-plus': { ElMessage: { success() {}, error() {}, warning() {}, info() {} } },
    'vue-router': { useRoute: () => ({ params: { id: '13' }, query: {} }), useRouter: () => ({ push() {}, replace() {} }), onBeforeRouteLeave() {} },
    './shared': { apiError: (error, fallback) => error.message || fallback, formatDuration: String } }))
  await settle()
  return { ...h, sent, saved: () => saved, close: () => { h.close(); globalThis.window = oldWindow } }
}
const scenario = () => ({ id: 13, project: 7, name: 'Recovery', engine: 'K6', env_config: {}, variables: [], load_config: { concurrency: 1000 }, runtime_config: { resource_recovery: config(), timeout: 19 }, steps: steps() })

test('actual scenario saves and reloads recovery without losing other runtime configuration', async () => {
  const h = await scenarioHarness(scenario())
  let saved
  try {
    assert.equal(await h.state.handleSave(), true)
    assert.deepEqual(h.sent[0].runtime_config.resource_recovery, config()); assert.equal(h.sent[0].runtime_config.timeout, 19)
    saved = h.saved(); assert.equal(h.state.dirty.value, false)
    h.state.steps.value.reverse()
    assert.equal(await h.state.handleSave(), false); assert.equal(h.sent.length, 1)
  } finally { h.close() }
  const reload = await scenarioHarness(saved)
  try { assert.deepEqual({ ...reload.state.form.runtime_config.resource_recovery }, config()); assert.equal(await reload.state.handleSave(), true) } finally { reload.close() }
})

test('actual scenario keeps recovery edits made during save and blocks incompatible engine', async () => {
  let release
  const delayed = new Promise(resolve => { release = resolve })
  const h = await scenarioHarness(scenario(), () => delayed)
  try {
    const saving = h.state.handleSave(); await settle()
    h.state.form.runtime_config.resource_recovery = {}; h.state.markDirty(); release()
    assert.equal(await saving, false); assert.deepEqual({ ...h.state.form.runtime_config.resource_recovery }, {}); assert.equal(h.state.dirty.value, true)
    h.state.form.runtime_config.resource_recovery = config(); h.state.form.engine = 'JMETER'
    assert.equal(await h.state.handleSave(), false)
  } finally { release(); h.close() }
})

test('actual recovery card distinguishes historical disabled, pending and unobserved and only emits read refresh', async () => {
  let refreshed = 0
  const h = mount(await compile('./components/ReminderRecoveryStatus.vue'), { value: {}, onRefresh: () => refreshed++ })
  try {
    assert.equal(h.state.observation.value.state, 'DISABLED')
    h.update({ value: { version: 1, kind: 'portfolio_reminder', observed: false, state: 'PREPARING' }, onRefresh: () => refreshed++ }); await settle()
    assert.equal(h.state.observation.value.state, 'UNKNOWN'); assert.equal(h.state.display(null), 'performanceTesting.recovery.unknown')
    h.state.refresh(); assert.equal(refreshed, 1)
  } finally { h.close() }
})

test('recovery locale keys match and every message compiles', () => {
  assert.ok(zh.recovery); assert.deepEqual(Object.keys(zh.recovery).sort(), Object.keys(en.recovery).sort())
  for (const locale of [zh.recovery, en.recovery]) for (const [key, value] of Object.entries(locale)) {
    const errors = []; baseCompile(value, { onError: error => errors.push(error) }); assert.deepEqual(errors, [], key)
  }
})
