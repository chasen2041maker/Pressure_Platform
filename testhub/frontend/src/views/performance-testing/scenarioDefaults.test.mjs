import test from 'node:test'
import assert from 'node:assert/strict'
import {
  newScenarioForm, manualStepDefaults, mergeProjectDefaults, clearProjectBindings
} from './scenarioDefaults.mjs'

const clone = value => structuredClone(value)

function projectForm(project = 11) {
  return { ...newScenarioForm(), project }
}

function responseFor(project, defaults = {}) {
  return {
    project,
    defaults: {
      environment: 101,
      global_environment: 102,
      account_pool_version: 201,
      account_pool_group: 'readers',
      runtime_config: {
        timeout: 45,
        auth_profile: { type: 'BEARER', access_token_variable: 'token' },
        account_identity_variable: 'user_id'
      },
      ...defaults
    }
  }
}

test('a new scenario starts at one VU, one round and thirty seconds', () => {
  const form = newScenarioForm()
  assert.equal(form.engine, 'K6')
  assert.equal(form.load_config.concurrency, 1)
  assert.equal(form.load_config.iterations_per_vu, 1)
  assert.equal(form.load_config.duration, 30)
  assert.equal(form.sla_config.enabled, true)
  assert.deepEqual(form.sla_config.thresholds, { p95_response_time: 2000, error_rate: 0 })
  assert.equal(form.sla_config.abort_on_breach, false)
})

test('new scenarios do not share mutable nested defaults', () => {
  const first = newScenarioForm()
  const second = newScenarioForm()
  const before = clone(second)
  first.load_config.concurrency = 17
  first.runtime_config.timeout = 89
  first.env_config.headers.Authorization = 'synthetic-header'
  first.variables.push({ key: 'name', value: 'first-only' })
  first.sla_config.thresholds.custom = 99
  first.sla_config.step_thresholds.push({ step: 'first-only' })
  first.perf_targets.max_p95_rt = 789
  assert.deepEqual(second, before)
  assert.deepEqual(newScenarioForm(), before)
})

test('manual steps use GET without a body or headers and assert status 200', () => {
  const step = manualStepDefaults()
  assert.equal(step.method, 'GET')
  assert.equal(step.body_type, 'NONE')
  assert.deepEqual(step.headers, {})
  assert.deepEqual(step.assertions, [{ type: 'STATUS_CODE', expected: 200 }])
})

test('each manual step owns its header object and assertion records', () => {
  const first = manualStepDefaults()
  const second = manualStepDefaults()
  const before = clone(second)
  first.headers.Authorization = 'synthetic-header'
  first.assertions[0].expected = 201
  first.assertions.push({ type: 'JSON_PATH', expr: '$.ok', expected: true })
  assert.deepEqual(second, before)
  assert.deepEqual(manualStepDefaults(), before)
})

test('current project defaults merge into unchanged fields without mutating or aliasing inputs', () => {
  const form = projectForm()
  const baseline = clone(form)
  const response = responseFor(form.project)
  const originals = clone({ form, baseline, response })
  const merged = mergeProjectDefaults(form, response, { project: 11, baseline })
  assert.equal(merged.environment, 101)
  assert.equal(merged.account_pool_version, 201)
  assert.equal(merged.runtime_config.timeout, 45)
  assert.equal(merged.runtime_config.auth_profile.access_token_variable, 'token')
  assert.notStrictEqual(merged, form)
  merged.runtime_config.auth_profile.access_token_variable = 'changed-after-merge'
  merged.env_config.headers.local = 'changed-after-merge'
  merged.variables.push({ key: 'local', value: 'changed-after-merge' })
  assert.deepEqual({ form, baseline, response }, originals)
})

test('defaults arriving after user edits preserve changed leaves and still fill untouched siblings', () => {
  const baseline = projectForm()
  const form = clone(baseline)
  form.name = 'Typed while waiting'
  form.runtime_config.timeout = 67
  form.load_config.concurrency = 9
  form.perf_targets.max_p95_rt = 750
  const response = responseFor(11, {
    load_config: { concurrency: 3, duration: 60 },
    perf_targets: { max_p95_rt: 2000, max_error_rate: 0 }
  })
  const merged = mergeProjectDefaults(form, response, { project: 11, baseline })
  assert.equal(merged.name, 'Typed while waiting')
  assert.equal(merged.runtime_config.timeout, 67)
  assert.equal(merged.runtime_config.auth_profile.access_token_variable, 'token')
  assert.equal(merged.load_config.concurrency, 9)
  assert.equal(merged.load_config.duration, 60)
  assert.equal(merged.perf_targets.max_p95_rt, 750)
  assert.equal(merged.perf_targets.max_error_rate, 0)
})

test('explicit false, zero, empty text and null edits are not treated as missing values', () => {
  const baseline = projectForm()
  baseline.environment = 91
  baseline.account_pool_version = 92
  baseline.account_pool_group = 'old-group'
  const form = clone(baseline)
  form.environment = null
  form.account_pool_version = null
  form.account_pool_group = ''
  form.enabled = false
  form.runtime_config.keep_alive = false
  form.load_config.iterations_per_vu = 0
  const response = responseFor(11, {
    enabled: true,
    load_config: { iterations_per_vu: 10 },
    runtime_config: { keep_alive: true, timeout: 45 }
  })
  const merged = mergeProjectDefaults(form, response, { project: 11, baseline })
  assert.equal(merged.environment, null)
  assert.equal(merged.account_pool_version, null)
  assert.equal(merged.account_pool_group, '')
  assert.equal(merged.enabled, false)
  assert.equal(merged.runtime_config.keep_alive, false)
  assert.equal(merged.load_config.iterations_per_vu, 0)
  assert.equal(merged.runtime_config.timeout, 45)
})

test('explicit false, zero and null from project defaults are applied to untouched fields', () => {
  const form = projectForm()
  form.environment = 91
  const baseline = clone(form)
  const merged = mergeProjectDefaults(form, responseFor(11, {
    environment: null,
    enabled: false,
    load_config: { iterations_per_vu: 0 },
    runtime_config: { keep_alive: false }
  }), { project: 11, baseline })
  assert.equal(merged.environment, null)
  assert.equal(merged.enabled, false)
  assert.equal(merged.load_config.iterations_per_vu, 0)
  assert.equal(merged.runtime_config.keep_alive, false)
})

test('a late project A response cannot pollute the current project B form', () => {
  const baselineA = projectForm(11)
  const formB = projectForm(22)
  formB.environment = 303
  formB.runtime_config.timeout = 71
  const before = clone(formB)
  const merged = mergeProjectDefaults(formB, responseFor(11), { project: 11, baseline: baselineA })
  assert.deepEqual(merged, before)
  assert.deepEqual(formB, before)
})

test('editing one binding while defaults load never mixes another environment and credentials', () => {
  for (const edit of [{ environment: 999 }, { account_pool_version: 888 }]) {
    const baseline = projectForm()
    const form = { ...clone(baseline), ...edit }
    const merged = mergeProjectDefaults(form, responseFor(11), { project: 11, baseline })
    for (const key of ['environment', 'global_environment', 'account_pool_version', 'account_pool_group']) {
      assert.equal(merged[key], form[key])
    }
    assert.equal(merged.runtime_config.auth_profile, undefined)
    assert.equal(merged.runtime_config.account_identity_variable, undefined)
  }
})

test('changing engine while defaults load cannot inherit K6 bindings', () => {
  const baseline = projectForm()
  const form = { ...clone(baseline), engine: 'JMETER' }
  assert.deepEqual(mergeProjectDefaults(form, responseFor(11), { project: 11, baseline }), form)
})

test('explicit cleared or custom authentication is never reenabled or mixed with defaults', () => {
  for (const profile of [{}, { mode: 'LOGIN', transport: 'COOKIE', cookie_name: 'session' }]) {
    const baseline = projectForm()
    const form = clone(baseline)
    form.runtime_config.auth_profile = profile
    const merged = mergeProjectDefaults(form, responseFor(11), { project: 11, baseline })
    assert.deepEqual(merged.runtime_config.auth_profile, profile)
  }
})

test('a response for a different project is ignored even when the request project is current', () => {
  const form = projectForm(22)
  const baseline = clone(form)
  assert.deepEqual(
    mergeProjectDefaults(form, responseFor(11), { project: 22, baseline }),
    form
  )
})

test('project switching clears project identities while retaining custom performance settings', () => {
  const form = projectForm()
  Object.assign(form, {
    environment: 101,
    global_environment: 102,
    account_pool_version: 201,
    account_pool_group: 'old-project-group'
  })
  form.runtime_config = {
    timeout: 77, sample_interval: 2, keep_alive: false, proxy: '',
    auth_profile: { type: 'BEARER', access_token_variable: 'old_project_token' },
    account_identity_variable: 'old_project_user_id'
  }
  form.load_config = { model: 'CONCURRENCY', concurrency: 8, iterations_per_vu: 6, duration: 90 }
  form.perf_targets = { max_p95_rt: 700, max_avg_rt: null, min_tps: 0, max_error_rate: 2 }
  const before = clone(form)
  const cleared = clearProjectBindings(form)
  assert.equal(cleared.environment, null)
  assert.equal(cleared.global_environment, null)
  assert.equal(cleared.account_pool_version, null)
  assert.equal(cleared.account_pool_group, '')
  assert.deepEqual(cleared.runtime_config.auth_profile ?? {}, {})
  assert.equal(cleared.runtime_config.account_identity_variable ?? '', '')
  assert.equal(cleared.runtime_config.timeout, 77)
  assert.equal(cleared.runtime_config.sample_interval, 2)
  assert.equal(cleared.runtime_config.keep_alive, false)
  assert.deepEqual(cleared.load_config, before.load_config)
  assert.deepEqual(cleared.perf_targets, before.perf_targets)
  assert.deepEqual(form, before)
})
