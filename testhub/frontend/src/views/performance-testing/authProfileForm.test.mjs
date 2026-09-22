import test from 'node:test'
import assert from 'node:assert/strict'
import { authSources, newAuthProfile, setRefresh, authFormErrors, MASK } from './authProfileForm.mjs'

test('safe variable sources expose mapping metadata only and identify fixed pool identity', () => {
  const sources = authSources({ fieldMapping: { user_id: 'principal', password: 'password' }, identityColumn: 'principal', rows: [{ password: 'SECRET' }] }, [{ type: 'CSV', name: 'token', column: 'token', value: 'SECRET' }])
  assert.deepEqual(sources.map(row => [row.name, row.identity]), [['user_id', true], ['password', false], ['token', false]])
  assert.doesNotMatch(JSON.stringify(sources), /SECRET/)
})
test('login and refresh defaults bind own inputs and required outputs without altering the original', () => {
  const original = newAuthProfile('principal')
  assert.match(original.login.body, /principal/)
  const refreshed = setRefresh(original, true, 'principal')
  assert.equal(original.refresh, undefined)
  assert.ok(refreshed.login.extractors.some(row => row.name === refreshed.refresh_token_variable))
  assert.ok(refreshed.refresh.extractors.some(row => row.name === refreshed.refresh_token_variable))
  assert.deepEqual(authFormErrors(refreshed), [])
  refreshed.expires_in_variable = 'expiry'
  assert.ok(authFormErrors(refreshed).includes('login:requiredOutputs'))
  const disabled = setRefresh(refreshed, false)
  assert.equal(disabled.refresh, undefined)
  assert.equal(disabled.expires_in_variable, undefined)
})
test('masked stored body remains valid; malformed JSON and dynamic auth keys cannot save', () => {
  const profile = newAuthProfile('user_id')
  profile.login.body = MASK
  assert.deepEqual(authFormErrors(profile), [])
  profile.login.body = '{broken'
  assert.deepEqual(authFormErrors(profile), ['login:json'])
  profile.login.body = '{"nested":{"{{key}}":"{{user_id}}"}}'
  assert.deepEqual(authFormErrors(profile), ['login:keys'])
})
