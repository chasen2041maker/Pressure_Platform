import test from 'node:test'
import assert from 'node:assert/strict'
import { tlsModeOf, withTlsMode, selectionState, copyEnvironment, missingCopySecrets, canWriteEnvironment, latestRequestGate, publicVariable } from './environmentForm.mjs'

test('TLS inheritance omits the override, while a saved explicit false survives', () => {
  assert.equal(tlsModeOf({}), 'inherit')
  assert.equal(tlsModeOf({ verify_ssl: false }), 'off')
  assert.equal(tlsModeOf({ verify_ssl: true }), 'on')
  assert.deepEqual(withTlsMode({ base_url: '', verify_ssl: false }, 'inherit'), { base_url: '' })
  assert.deepEqual(withTlsMode({}, 'off'), { verify_ssl: false })
  assert.deepEqual(withTlsMode({}, 'on'), { verify_ssl: true })
})

test('selection checks scope and project and retains references for explicit legacy resolution', () => {
  const options = [{ id: 2, scope: 'PROJECT', project: 7 }, { id: 3, scope: 'GLOBAL', project: null }]
  const form = { engine: 'K6', project: 7, environment: 2, global_environment: 3 }
  assert.deepEqual(selectionState(form, options, true), { legacyBlocked: false, invalid: false })
  assert.equal(selectionState({ ...form, project: 8 }, options, true).invalid, true)
  assert.equal(selectionState({ ...form, environment: 3 }, options, true).invalid, true)
  assert.equal(selectionState({ ...form, global_environment: 2 }, options, true).invalid, true)
  assert.equal(selectionState({ ...form, engine: 'BUILTIN' }, options, true).legacyBlocked, true)
  assert.equal(form.environment, 2)
  assert.equal(selectionState(form, [], false).invalid, false)
  assert.equal(selectionState(form, [], true).invalid, true)
})

test('public copies clear nested masks and secrets without mutating the source or dropping other types', () => {
  const original = { id: 4, name: 'QA', scope: 'PROJECT', project: 7, version: 8,
    base_url: 'https://example.test', headers: { Authorization: '******', Accept: 'application/json' },
    variables: [{ name: 'password', type: 'CONSTANT', secret: true, value: '******' },
      { name: 'old', type: 'ENUM', secret: true, values: '******', strategy: 'ROUND_ROBIN' },
      { name: 'count', type: 'RANDOM_INT', min: 1, max: 9 }], verify_ssl: false, is_active: true }
  const { form, secrets } = copyEnvironment(original)
  assert.equal(form.id, undefined)
  assert.equal(form.version, undefined)
  assert.equal(form.headers.Authorization, '')
  assert.equal(form.headers.Accept, 'application/json')
  assert.equal(form.variables[0].value, '')
  assert.deepEqual(form.variables[1].values, [])
  assert.deepEqual(form.variables[2], original.variables[2])
  assert.equal(original.variables[0].value, '******')
  assert.equal(missingCopySecrets(secrets).length, 3)
  secrets.headers[0].value = 'new-secret'
  secrets.variables[0].value.value = 'new-password'
  secrets.variables.splice(1, 1)
  assert.deepEqual(missingCopySecrets(secrets), [])
})

test('write controls follow server permissions and fail closed without metadata', () => {
  assert.equal(canWriteEnvironment({ scope: 'GLOBAL' }, null), false)
  assert.equal(canWriteEnvironment({ scope: 'GLOBAL' }, { can_manage_global: false, project_ids: [7] }), false)
  assert.equal(canWriteEnvironment({ scope: 'GLOBAL' }, { can_manage_global: true, project_ids: [] }), true)
  assert.equal(canWriteEnvironment({ scope: 'PROJECT', project: 8 }, { project_ids: [7] }), false)
  assert.equal(canWriteEnvironment({ scope: 'PROJECT', project: 7 }, { project_ids: [7] }), true)
})

test('marking an existing variable secret hides its preview without changing its saved payload', () => {
  const variable = { name: 'legacy', type: 'ENUM', secret: true,
    value: 'new-secret', values: ['first', 'second'], options: { private: 'sensitive' }, strategy: 'RANDOM' }
  const preview = publicVariable(variable)
  assert.equal(preview.value, '******')
  assert.equal(preview.values, '******')
  assert.equal(preview.options, '******')
  assert.equal(preview.strategy, 'RANDOM')
  assert.deepEqual(variable.values, ['first', 'second'])
  assert.deepEqual(publicVariable({ name: 'public', value: 'hello' }), { name: 'public', value: 'hello' })
})

test('an earlier project response and completion cannot replace a newer request', async () => {
  const gate = latestRequestGate()
  const old = gate.begin()
  const next = gate.begin()
  assert.equal(gate.isCurrent(old), false)
  assert.equal(gate.isCurrent(next), true)
  gate.begin()
  assert.equal(gate.isCurrent(next), false)
})
