import test from 'node:test'
import assert from 'node:assert/strict'

const api = await import('./reminderRecovery.mjs').catch(() => null)
const config = () => ({ version: 1, kind: 'portfolio_reminder', group_id: 'reminders', put_step_id: 11, receipt_step_id: 12, get_step_id: 13, delete_step_id: 14, stock_code: 'sh600000', max_resources: 1000 })
const steps = () => ['PUT', 'GET', 'GET', 'DELETE'].map((method, index) => ({ id: 11 + index, name: `step ${index}`, method, protocol: 'HTTP', enabled: true,
  url: index === 1 ? '/api/v1/portfolio/reminder-commands/{{rr_put_digest}}' : '/api/v1/portfolio/reminders/sh600000',
  execution_policy: { group_id: 'reminders', vu_start: 1, vu_end: 1000, max_runs_per_vu: 1, min_interval_ms: 0 } }))
const evidence = () => ({ version: 1, kind: 'portfolio_reminder', observed: true, state: 'RECOVERED', planned: 2, pending: 0, cleaned: 1, cancelled: 1, conflict: 0, requests: 5, reads: 3, writes: 2, completed_requests: 4, unknown_requests: 1 })

test('recovery binds four saved steps and keeps absence distinct from malformed configuration', () => {
  assert.ok(api, 'recovery form and observation contract must exist')
  assert.deepEqual(api.recoveryFormErrors({}, [], 'K6', 1000), [])
  assert.deepEqual(api.recoveryFormErrors(config(), steps(), 'K6', 1000), [])
  for (const value of [[], 'enabled', { ...config(), ready: true }, { ...config(), max_resources: 1001 }, { ...config(), stock_code: 'SH600000' }, { ...config(), receipt_step_id: 11 }]) {
    assert.ok(api.recoveryFormErrors(value, steps(), 'K6', 1000).length)
  }
  assert.ok(api.recoveryFormErrors(config(), steps(), 'JMETER', 1000).includes('engine'))
  const booleanGroupSteps = steps().map(step => ({ ...step, execution_policy: { ...step.execution_policy, group_id: true } }))
  assert.ok(api.recoveryFormErrors({ ...config(), group_id: true }, booleanGroupSteps, 'K6', 1000).includes('group'))
})

test('removed, reordered, copied-unmapped, unbounded, or noncontiguous steps invalidate binding', () => {
  assert.ok(api)
  const variants = [steps().slice(1), steps().reverse(), steps().map(step => ({ ...step, id: step.id + 20 })),
    [steps()[0], { id: 90, enabled: true }, ...steps().slice(1)],
    steps().map(step => ({ ...step, execution_policy: { ...step.execution_policy, max_runs_per_vu: 2 } })),
    steps().map(step => ({ ...step, protocol: 'SSE' })),
    steps().map(step => ({ ...step, is_setup: true })),
    steps().map(step => ({ ...step, url: 'https://other.example/api/v1/portfolio/reminders/sh600000' }))]
  for (const variant of variants) assert.ok(api.recoveryFormErrors(config(), variant, 'K6', 1000).length)
  assert.ok(api.recoveryFormErrors(config(), steps(), 'K6', 999).length)
  assert.ok(api.recoveryFormErrors({ ...config(), max_resources: 999 }, steps(), 'K6', 1000).length)
  assert.deepEqual(api.recoveryFormErrors(config(), [{ id: 90, enabled: false }, ...steps()], 'K6', 1000), [])
})

test('history is not enabled and absent observations never become zero or recovered', () => {
  assert.ok(api)
  assert.equal(api.recoveryObservation({}).state, 'DISABLED')
  assert.equal(api.recoveryObservation(undefined).state, 'UNKNOWN')
  for (const value of [null, [], { ...evidence(), observed: false }, { ...evidence(), version: 2 }, { ...evidence(), state: '<secret>' }]) {
    const result = api.recoveryObservation(value)
    assert.equal(result.state, 'UNKNOWN'); assert.equal(result.counts.requests, null)
  }
  const missing = evidence(); delete missing.pending
  const result = api.recoveryObservation(missing)
  assert.equal(result.state, 'UNKNOWN'); assert.equal(result.counts.pending, null)
})

test('recovery observations preserve attempts, received responses and unknown requests separately', () => {
  assert.ok(api)
  const result = api.recoveryObservation(evidence())
  assert.equal(result.state, 'RECOVERED')
  assert.equal(result.counts.requests, 5); assert.equal(result.counts.completed_requests, 4); assert.equal(result.counts.unknown_requests, 1)
  for (const value of [{ ...evidence(), pending: 1 }, { ...evidence(), writes: 3 }, { ...evidence(), unknown_requests: 0 }, { ...evidence(), planned: true }]) assert.equal(api.recoveryObservation(value).state, 'UNKNOWN')
  assert.equal(api.recoveryObservation({ ...evidence(), state: 'CORRUPT', observed: false }).state, 'CORRUPT')
})
