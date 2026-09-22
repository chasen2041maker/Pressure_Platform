import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { randomBytes } from 'node:crypto'
import vm from 'node:vm'

const policySource = readFileSync(new URL('../../../../apps/perf_testing/engines/k6_execution_policy.js', import.meta.url), 'utf8').replace(/export /g, '')
const recoverySource = readFileSync(new URL('../../../../apps/perf_testing/engines/k6_reminder_recovery.js', import.meta.url), 'utf8').replace(/export /g, '')
const source = policySource + '\n' + recoverySource + '\n' + readFileSync(new URL('../../../../apps/perf_testing/engines/k6_script.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/mg, '')
  .replace('export default function ()', 'function iteration()').replace(/export /g, '')
const uuid4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

function runFixture(bypassPreflight) {
  const outputs = [['access_token', 'token'], ['refresh_token', 'refresh_token'], ['expires_in', 'expires_in']]
    .map(([name, path]) => ({ name, type: 'JSON_PATH', expr: `$.data.${path}` }))
  const config = {
    load_config: { model: 'CONCURRENCY', concurrency: 2, duration: 30, iterations_per_vu: 2 },
    runtime_config: { auth_profile: { mode: 'LOGIN', transport: 'BEARER', max_attempts: 2,
      access_token_variable: 'access_token', refresh_token_variable: 'refresh_token',
      expires_in_variable: 'expires_in', cookie_name: 'sid' } },
    env_config: { base_url: 'http://fixture', headers: { 'Idempotency-Key': '{{request_id}}', 'X-Same-ID': '${request_id}' } },
    variables: ['user_id', 'password'].map(name => ({ name, type: 'CSV', data_file_id: 1, column: name })),
    csv_files: { 1: 'rows' },
    steps: [
      { id: 1, name: 'list', url: '/auth/items', extractors: [{ name: 'item_id', expr: '$.data.items[0].id' }] },
      { id: 2, name: 'detail', url: '/auth/items/{{item_id}}' },
      { id: 'auth:login', name: 'login', method: 'POST', url: '/auth/login', is_setup: true, auth_phase: 'login',
        auth_input_variables: ['user_id', 'password'], body_type: 'JSON', body: '{"user_id":"{{user_id}}","password":"{{password}}"}',
        assertions: [{ type: 'JSON_PATH', expr: '$.code', expected: 0 }], extractors: outputs },
      { id: 'auth:refresh', name: 'refresh', method: 'POST', url: '/auth/refresh', is_setup: true, auth_phase: 'refresh',
        auth_input_variables: ['refresh_token'], body_type: 'JSON', body: '{"refresh_token":"{{refresh_token}}"}', extractors: outputs }
    ]
  }
  if (bypassPreflight) {
    config.variables.push({ name: 'request_id', value: 'user-fixed' })
    config.steps[0].extractors.push({ name: 'request_id', expr: '$.data.items[0].id' })
  }
  const rows = [1, 2].map(vu => ({ user_id: `u${vu}`, password: `synthetic-password-${vu}` }))
  const events = [], requests = [], sessions = new Map(), refreshed = new Set(), vus = []
  let serial = 0
  class Jar {
    constructor() { this.cookies = {} }
    set(_url, name, value) { this.cookies[name] = [value] }
    cookiesForURL() { return this.cookies }
  }
  const response = (status, data) => ({ status, error_code: 0, json: () => data })
  for (let vu = 1; vu <= 2; vu++) {
    const execution = { vu: { idInTest: vu, iterationInScenario: 0 } }
    const sandbox = {
      __ENV: { K6_TESTHUB_CONFIG: 'config' }, exec: execution,
      open: name => JSON.stringify(name === 'config' ? config : rows),
      SharedArray: function (_name, fn) { return fn() }, sleep: () => {}, Date: { now: () => 100000 },
      randomBytes: n => Uint8Array.from(randomBytes(n)).buffer,
      console: { log: line => events.push(JSON.parse(line.split('TESTHUB_K6_EVENT ')[1])) },
      http: { CookieJar: Jar, request: (_method, url, body, params) => {
        const path = new URL(url).pathname, data = body ? JSON.parse(body) : {}
        requests.push({ vu, path, headers: params.headers })
        if (path === '/auth/login' || path === '/auth/refresh') {
          const user = path.endsWith('login') ? data.user_id : sessions.get(data.refresh_token)
          assert.equal(user, `u${vu}`, 'authentication identity')
          if (path.endsWith('refresh')) refreshed.add(vu)
          const token = `synthetic-token-${vu}-${++serial}`, refresh = `synthetic-refresh-${vu}-${serial}`
          sessions.set(token, user); sessions.set(refresh, user)
          params.jar.set(url, 'sid', user)
          return response(200, { code: 0, data: { token, refresh_token: refresh, expires_in: 3600 } })
        }
        assert.equal(sessions.get(params.headers.authorization?.replace('Bearer ', '')), `u${vu}`, 'business identity')
        if (path !== '/auth/items' && !refreshed.has(vu)) return response(401, {})
        if (path === '/auth/items') return response(200, { code: 0, data: { items: [{ id: `item-u${vu}-${execution.vu.iterationInScenario}` }] } })
        assert.equal(path, `/auth/items/item-u${vu}-${execution.vu.iterationInScenario}`, 'current iteration resource')
        return response(200, { code: 0 })
      } }
    }
    vm.createContext(sandbox)
    vm.runInContext(source, sandbox, { timeout: 5000 })
    vus.push({ sandbox, execution })
  }
  for (let round = 0; round < 2; round++) {
    for (const { sandbox, execution } of vus) {
      execution.vu.iterationInScenario = round
      vm.runInContext('iteration()', sandbox, { timeout: 5000 })
    }
  }
  return { requests, events }
}

test('shipped k6 script keeps UUIDv4 unique per logical request and restores business ID after refresh', () => {
  const allLogical = []
  for (const bypassPreflight of [false, true]) {
    const output = runFixture(bypassPreflight)
    assert.equal(output.requests.length, 14) // 8 business + 2 retries + 2 login + 2 refresh
    for (const row of output.requests) {
      const id = row.headers['idempotency-key']
      assert.match(id, uuid4)
      assert.equal(row.headers['x-same-id'], id)
    }
    for (const vu of [1, 2]) {
      const rows = output.requests.filter(row => row.vu === vu)
      const retried = rows.filter(row => row.path === `/auth/items/item-u${vu}-0`)
      assert.equal(retried.length, 2)
      assert.equal(retried[0].headers['idempotency-key'], retried[1].headers['idempotency-key'])
      const logical = rows.filter(row => row !== retried[1]).map(row => row.headers['idempotency-key'])
      assert.equal(new Set(logical).size, 6)
      allLogical.push(...logical)
    }
    assert.equal(JSON.stringify(output.events).includes('idempotency-key'), false)
  }
  assert.equal(allLogical.length, 24)
  assert.equal(new Set(allLogical).size, 24)
})
