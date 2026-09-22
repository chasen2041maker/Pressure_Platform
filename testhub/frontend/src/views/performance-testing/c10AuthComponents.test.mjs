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
import zh from '../../locales/lang/zh-cn/performance-testing.js'
import en from '../../locales/lang/en/performance-testing.js'
import { baseCompile } from '@intlify/message-compiler'

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
async function authHarness(initial) {
  let model = initial || {}, valid = true, mounted, disabled = false, edits = 0, savedRevision = 0
  const props = () => ({ modelValue: model, disabled, savedRevision, sources: [{ name: 'user_id', column: 'user_id', source: 'pool', identity: true }, { name: 'password', column: 'password', source: 'pool' }],
    'onUpdate:modelValue': value => { model = value; if (mounted) mounted.update(props()) }, onValidity: value => { valid = value }, onEdit: () => { edits++ } })
  mounted = mount(await compile('./components/AuthProfileEditor.vue', deps()), props())
  await settle()
  return { page: mounted.state, get model() { return model }, get valid() { return valid }, get edits() { return edits },
    saved(value) { model = value; savedRevision++; mounted.update(props()) }, disable() { disabled = true; mounted.update(props()) }, cleanup: mounted.unmount }
}

test('actual auth component enables LOGIN/STATIC and BEARER/COOKIE with refresh config, preserving independent identity', async () => {
  const h = await authHarness()
  try {
    h.page.toggle(true); await settle()
    assert.equal(h.model.mode, 'LOGIN'); assert.match(h.model.login.body, /user_id/)
    h.page.refresh(true); await settle()
    assert.ok(h.model.refresh); assert.equal(h.valid, true)
    h.page.setMode('STATIC'); await settle(); assert.equal(h.model.login, undefined)
    h.page.setTransport('COOKIE'); await settle(); assert.equal(h.model.cookie_name, 'sid')
    h.page.field('cookie_variable', 'account_cookie'); await settle(); assert.equal(h.model.cookie_variable, 'account_cookie')
    h.disable(); h.page.field('cookie_variable', 'wrong'); await settle(); assert.equal(h.model.cookie_variable, 'account_cookie')
  } finally { h.cleanup() }
})
test('actual auth component fully replaces or cancels masked body/headers/expectations without revealing saved values', async () => {
  const initial = auth.newAuthProfile('user_id')
  initial.login.body = auth.MASK; initial.login.headers = { 'X-Principal': auth.MASK }
  initial.login.assertions.push({ type: 'JSON_PATH', expr: '$.code', expected: auth.MASK })
  const h = await authHarness(initial)
  try {
    h.page.replace('login', 'body'); await settle(); assert.equal(h.model.login.body, '{}')
    h.page.stepField('login', 'body', '{"principal":"{{user_id}}"}'); await settle()
    h.page.cancel('login', 'body'); await settle(); assert.equal(h.model.login.body, auth.MASK)
    h.page.replace('login', 'headers'); await settle(); assert.deepEqual(h.model.login.headers, {})
    h.page.headers('login', '{broken'); await settle(); assert.equal(h.valid, false)
    h.page.cancel('login', 'headers'); await settle(); assert.deepEqual(h.model.login.headers, initial.login.headers); assert.equal(h.valid, true)
    h.page.replaceExpected('login', 1); await settle(); assert.equal(h.model.login.assertions[1].expected, '')
    h.page.cancelExpected('login', 1); await settle(); assert.equal(h.model.login.assertions[1].expected, auth.MASK)
    h.page.replace('login', 'body'); await settle(); h.page.stepField('login', 'body', '{"principal":"{{user_id}}"}'); await settle()
    h.saved(initial); await settle(); assert.equal(h.model.login.body, auth.MASK); assert.equal(h.page.replacing['login.body'], undefined)
    h.page.ruleField('login', 'assertions', 1, 'expr', '$.result'); await settle(); assert.equal(h.model.login.assertions[1].expected, '', 'changing masked rule requires replacement')
  } finally { h.cleanup() }
})
test('actual auth component blocks malformed drafts and dynamic JSON keys; disabling clears draft errors', async () => {
  const h = await authHarness(auth.newAuthProfile('user_id'))
  try {
    h.page.stepField('login', 'body', '{"{{key}}":"{{user_id}}"}'); await settle(); assert.equal(h.valid, false)
    h.page.headers('login', '{bad'); await settle(); assert.equal(h.valid, false); assert.equal(h.edits, 1, 'invalid draft still marks the parent dirty')
    h.page.toggle(false); await settle(); assert.equal(h.valid, true)
    h.page.toggle(true); await settle(); assert.equal(h.valid, true)
  } finally { h.cleanup() }
})
test('successful save revision ends empty header/body/expected replacements and stale cancel events cannot restore masks', async () => {
  const initial = auth.newAuthProfile('user_id')
  initial.login.body = auth.MASK
  initial.login.headers = { 'X-Principal': auth.MASK }
  initial.login.assertions.push({ type: 'JSON_PATH', expr: '$.code', expected: auth.MASK })
  const h = await authHarness(initial)
  try {
    h.page.replace('login', 'headers'); await settle(); h.page.headers('login', '{}'); await settle()
    h.page.replace('login', 'body'); await settle(); h.page.bodyType('login', 'NONE'); await settle()
    h.page.replaceExpected('login', 1); await settle()
    assert.ok(Object.keys(h.page.replacing).length)
    const saved = auth.cloneAuth(h.model)
    h.saved(saved); await settle()
    assert.deepEqual({ ...h.page.replacing }, {})
    assert.deepEqual({ ...h.page.headerDrafts }, {})
    assert.deepEqual({ ...h.page.headerErrors }, {})
    h.page.cancel('login', 'headers'); h.page.cancel('login', 'body'); h.page.cancelExpected('login', 1); await settle()
    assert.deepEqual(h.model, saved)
    h.page.stepField('login', 'url', '/auth/login-again'); await settle()
    assert.deepEqual(h.model.login.headers, {})
    assert.equal(h.model.login.body, '')
    assert.equal(h.model.login.assertions[1].expected, '')
    assert.equal(h.valid, true)
  } finally { h.cleanup() }
})
test('cancelling a body replacement preserves an independent invalid header draft until that draft is cancelled', async () => {
  const profile = auth.newAuthProfile('user_id')
  profile.login.body = auth.MASK; profile.login.headers = { 'X-Principal': auth.MASK }
  const h = await authHarness(profile)
  try {
    h.page.replace('login', 'body'); await settle()
    h.page.replace('login', 'headers'); await settle(); h.page.headers('login', '{broken'); await settle()
    h.page.cancel('login', 'body'); await settle()
    assert.equal(h.model.login.body, auth.MASK)
    assert.equal(h.page.headerDrafts.login, '{broken'); assert.equal(h.valid, false)
    h.page.cancel('login', 'headers'); await settle()
    assert.deepEqual(h.model.login.headers, profile.login.headers); assert.equal(h.valid, true)
  } finally { h.cleanup() }
})
test('actual ScenarioEditor sends auth config and replaces secrets with server masks without false dirty state', async () => {
  const profile = auth.newAuthProfile('user_id')
  let saved = { id: 13, project: 7, name: 'Auth scenario', engine: 'K6', env_config: {}, variables: [], runtime_config: { auth_profile: profile }, steps: [{ id: 1, name: 'business', url: '/x', enabled: true }] }
  const sent = []
  const api = { getEngineStatus: async () => ({ data: { engines: [] } }), getPerfProjects: async () => ({ data: [{ id: 7 }] }), getPerfDataFiles: async () => ({ data: [] }),
    getPerfEnvironmentPermissions: async () => ({ data: { project_ids: [7] } }), getPerfEnvironments: async () => ({ data: [] }), getPerfScenario: async () => ({ data: auth.cloneAuth(saved) }),
    updatePerfScenario: async (_, payload) => { sent.push(auth.cloneAuth(payload)); saved = auth.cloneAuth({ ...saved, ...payload }); saved.runtime_config.auth_profile.login.body = auth.MASK; return { data: auth.cloneAuth(saved) } },
    savePerfScenarioSteps: async (_, steps) => ({ data: { steps: auth.cloneAuth(steps) } }), getPerfScenarioReadiness: async () => ({ data: { results: [] } }) }
  const oldWindow = globalThis.window; globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const h = mount(await compile('./ScenarioEditor.vue', deps(api)))
  try {
    await settle(); assert.equal(await h.state.handleSave(), true)
    assert.equal(sent[0].runtime_config.auth_profile.login.body, profile.login.body)
    assert.equal(h.state.form.runtime_config.auth_profile.login.body, auth.MASK)
    assert.equal(h.state.dirty.value, false)
    assert.equal(await h.state.handleSave(), true)
    assert.equal(sent[1].runtime_config.auth_profile.login.body, auth.MASK)
  } finally { h.unmount(); globalThis.window = oldWindow }
})
test('actual parent and auth component end replacement only on committed save, including empty fields and later step-save failure', async () => {
  const profile = auth.newAuthProfile('user_id')
  profile.login.body = auth.MASK
  profile.login.headers = { 'X-Principal': auth.MASK }
  profile.login.assertions = [{ type: 'JSON_PATH', expr: '$.code', expected: auth.MASK }]
  let saved = { id: 13, project: 7, name: 'Lifecycle', engine: 'K6', env_config: {}, variables: [], runtime_config: { auth_profile: profile }, steps: [{ id: 1, name: 'business', url: '/x', enabled: true }] }
  let rejectProfile = false, rejectSteps = false
  const submitted = []
  const api = { getEngineStatus: async () => ({ data: { engines: [] } }), getPerfProjects: async () => ({ data: [{ id: 7 }] }), getPerfDataFiles: async () => ({ data: [] }),
    getPerfEnvironmentPermissions: async () => ({ data: { project_ids: [7] } }), getPerfEnvironments: async () => ({ data: [] }), getPerfScenario: async () => ({ data: auth.cloneAuth(saved) }),
    updatePerfScenario: async (_, payload) => {
      if (rejectProfile) throw new Error('Profile save rejected')
      submitted.push(auth.cloneAuth(payload))
      saved = auth.cloneAuth({ ...saved, ...payload })
      const step = saved.runtime_config.auth_profile.login
      if (step.body) step.body = auth.MASK
      step.headers = Object.fromEntries(Object.keys(step.headers).map(key => [key, auth.MASK]))
      step.assertions.forEach(rule => { if (rule.type !== 'STATUS_CODE') rule.expected = auth.MASK })
      return { data: auth.cloneAuth(saved) }
    },
    savePerfScenarioSteps: async (_, steps) => { if (rejectSteps) throw new Error('Step save rejected'); return { data: { steps: auth.cloneAuth(steps) } } },
    getPerfScenarioReadiness: async () => ({ data: { results: [] } }) }
  const oldWindow = globalThis.window; globalThis.window = { addEventListener() {}, removeEventListener() {} }
  const parent = mount(await compile('./ScenarioEditor.vue', deps(api)))
  let child, stop
  try {
    await settle()
    const props = () => ({ modelValue: parent.state.form.runtime_config.auth_profile, savedRevision: parent.state.authSavedRevision.value,
      disabled: parent.state.saving.value, 'onUpdate:modelValue': value => { parent.state.form.runtime_config.auth_profile = value; parent.state.markDirty() },
      onEdit: parent.state.markDirty, onValidity: value => { parent.state.authFormValid.value = value } })
    child = mount(await compile('./components/AuthProfileEditor.vue', deps()), props())
    stop = Vue.watch(() => [parent.state.form.runtime_config.auth_profile, parent.state.authSavedRevision.value, parent.state.saving.value], () => child.update(props()), { deep: true, flush: 'sync' })
    child.state.replace('login', 'headers'); await settle(); child.state.headers('login', '{}'); await settle()
    rejectProfile = true
    const oldRevision = parent.state.authSavedRevision.value
    assert.equal(await parent.state.handleSave(), false); await settle()
    assert.equal(parent.state.authSavedRevision.value, oldRevision)
    assert.ok(child.state.replacing['login.headers'], 'unsuccessful profile save still permits cancellation')
    child.state.cancel('login', 'headers'); await settle()
    assert.deepEqual(parent.state.form.runtime_config.auth_profile.login.headers, { 'X-Principal': auth.MASK })
    child.state.replace('login', 'headers'); await settle(); child.state.headers('login', '{}'); await settle()
    child.state.replace('login', 'body'); await settle(); child.state.bodyType('login', 'NONE'); await settle()
    child.state.replaceExpected('login', 0); await settle(); child.state.ruleField('login', 'assertions', 0, 'type', 'STATUS_CODE'); await settle(); child.state.ruleField('login', 'assertions', 0, 'expected', '200'); await settle()
    rejectProfile = false; rejectSteps = true
    assert.equal(await parent.state.handleSave(), false); await settle()
    assert.equal(parent.state.authSavedRevision.value, oldRevision + 1, 'profile already committed even when separate steps save fails')
    assert.deepEqual({ ...child.state.replacing }, {})
    child.state.cancel('login', 'headers'); child.state.cancel('login', 'body'); child.state.cancelExpected('login', 0); await settle()
    rejectSteps = false; child.state.stepField('login', 'url', '/auth/relogin'); await settle()
    assert.equal(await parent.state.handleSave(), true); await settle()
    const last = submitted.at(-1).runtime_config.auth_profile.login
    assert.deepEqual(last.headers, {}); assert.equal(last.body, ''); assert.equal(last.assertions[0].expected, '200')
    assert.equal(parent.state.dirty.value, false)
  } finally { stop?.(); child?.unmount(); parent.unmount(); globalThis.window = oldWindow }
})
test('all auth locale entries compile and locale keys match', () => {
  assert.deepEqual(Object.keys(zh.auth).sort(), Object.keys(en.auth).sort())
  for (const locale of [zh.auth, en.auth]) for (const [key, value] of Object.entries(locale)) {
    const errors = []; baseCompile(value, { onError: error => errors.push(error) })
    assert.deepEqual(errors, [], key)
  }
})
