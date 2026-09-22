import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import axios from 'axios'
import { createPinia, defineStore } from 'pinia'

const userSource = readFileSync(new URL('./user.js', import.meta.url), 'utf8').replace(/^import .*\r?\n/gm, '').replace('export const useUserStore', 'globalThis.useUserStore')
const apiSource = readFileSync(new URL('../utils/api.js', import.meta.url), 'utf8').replace(/^import .*\r?\n/gm, '').replace('const api =', 'globalThis.api =').replace('export default api', '')
const jwt = exp => `eyJhbGciOiJub25lIn0.${Buffer.from(JSON.stringify({ exp })).toString('base64url')}.fake`
function storage() {
  const map = new Map([['access_token', jwt(3600)], ['refresh_token', 'fake-refresh'], ['user', '{"id":1}']])
  return { getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, String(value)), removeItem: key => map.delete(key) }
}
function harness({ shared = storage(), adapter, post, clock = { now: 0 }, locks } = {}) {
  const timers = new Map(), listeners = new Map()
  let timerId = 0
  const pinia = createPinia()
  const context = vm.createContext({ ...Vue, axios, localStorage: shared, atob, crypto: { randomUUID: () => Math.random().toString() },
    navigator: { locks }, AbortController, Date: class extends Date { static now() { return clock.now } }, setTimeout, clearTimeout,
    setInterval: fn => { timers.set(++timerId, fn); return timerId }, clearInterval: id => timers.delete(id),
    window: { location: { href: '' }, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) },
    ElMessage: { error() {} }, console: { log() {}, error() {}, warn() {} }, track() {},
    defineStore: (name, setup) => { const use = defineStore(name, setup); return () => use(pinia) } })
  vm.runInContext(apiSource, context)
  if (adapter) context.api.defaults.adapter = adapter
  if (post) context.api = { post }
  vm.runInContext(userSource, context)
  return { store: context.useUserStore(), api: context.api, shared, clock, timers, listeners, context, cleanup: () => context.useUserStore().$dispose() }
}
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const tick = () => new Promise(resolve => setTimeout(resolve, 10))
const unauthorized = config => Object.assign(new Error('fake unauthorized'), { config, response: { status: 401, config } })

test('JWT exp is used for every login method and expiry changes with time', async () => {
  for (const method of ['login', 'smsLogin', 'register']) {
    const h = harness({ post: async () => ({ data: { access: jwt(60), refresh: 'fake-r2', user: { id: 2 } } }) })
    await h.store[method]({}); assert.equal(h.store.tokenExpiresAt, 60000)
    assert.equal(h.store.isTokenExpired, false); h.clock.now = 60001
    assert.equal(h.store.isTokenExpired, true)
    h.store.startAutoRefresh(); h.store.startAutoRefresh(); assert.equal(h.timers.size, 1)
    h.store.stopAutoRefresh(); assert.equal(h.timers.size, 0)
  }
})
test('parallel export 401s refresh once and replay blob GET once with new bearer', async () => {
  let refreshes = 0; const seen = []
  const h = harness({ adapter: async config => {
    if (config.url.includes('refresh')) { refreshes++; await tick(); return { status: 200, config, data: { access: jwt(7200), refresh: 'fake-r2' } } }
    seen.push({ token: config.headers.Authorization, type: config.responseType, method: config.method })
    if (config.headers.Authorization === `Bearer ${jwt(3600)}`) throw unauthorized(config)
    return { status: 200, config, data: new Blob(['fake report']) }
  } })
  const results = await Promise.all([h.api.get('/reports/1/export/', { responseType: 'blob' }), h.api.get('/reports/2/export/', { responseType: 'blob' })])
  assert.equal(refreshes, 1); assert.equal(seen.length, 4)
  assert.ok(seen.every(item => item.type === 'blob' && item.method === 'get'))
  assert.ok(seen.slice(2).every(item => item.token === `Bearer ${jwt(7200)}`))
  assert.equal(await results[0].data.text(), 'fake report')
})
test('401 replay is bounded and login/logout/refresh 401s never recurse', async () => {
  let calls = 0
  const h = harness({ adapter: async config => { calls++; if (config.url.includes('refresh')) return { status: 200, config, data: { access: jwt(7200), refresh: 'fake-r2' } }; throw unauthorized(config) } })
  await assert.rejects(h.api.get('/protected/')); assert.equal(calls, 3)
  for (const url of ['/auth/login/', '/auth/sms-login/', '/auth/test-register/', '/auth/logout/', '/auth/token/refresh/']) {
    let count = 0; const a = harness({ adapter: async config => { count++; throw unauthorized(config) } })
    await assert.rejects(a.api.post(url, {})); assert.equal(count, 1)
  }
})
test('stale tab reads latest rotated refresh and transient failure retains session', async () => {
  const shared = storage(); const used = []
  const backend = async (_url, data) => { used.push(data.refresh); return { data: { access: jwt(7200 + used.length), refresh: `fake-r${used.length}` } } }
  const a = harness({ shared, post: backend }), b = harness({ shared, post: backend })
  await a.store.refreshAccessToken(); await b.store.refreshAccessToken()
  assert.deepEqual(used, ['fake-refresh', 'fake-r1'])
  const c = harness({ shared, post: async () => { throw { response: { status: 503 } } } })
  await assert.rejects(c.store.refreshAccessToken()); assert.equal(shared.getItem('refresh_token'), 'fake-r2')
})
test('logout during refresh prevents old response resurrection; logout response cannot delete new login', async () => {
  const pending = deferred(), out = deferred()
  const h = harness({ post: async url => url.includes('refresh') ? pending.promise : url.includes('logout') ? out.promise : { data: { access: jwt(9000), refresh: 'fake-new', user: { id: 2 } } } })
  const refresh = h.store.refreshAccessToken(); await tick()
  const logout = h.store.logout(); await tick()
  assert.equal(h.shared.getItem('access_token'), null)
  assert.equal(h.context.window.location.href, '', 'blacklist attempt settles before full navigation')
  pending.resolve({ data: { access: jwt(7200), refresh: 'fake-obsolete' } }); await assert.rejects(refresh)
  await h.store.login({}); out.resolve({ data: {} }); await logout
  assert.equal(h.shared.getItem('refresh_token'), 'fake-new')
  assert.equal(h.context.window.location.href, '', 'old logout completion cannot navigate away from replacement login')
})
test('old rejected refresh cannot erase replacement login and ordinary errors stay rejected', async () => {
  const pending = deferred(), shared = storage()
  const a = harness({ shared, post: () => pending.promise })
  const b = harness({ shared, post: async () => ({ data: { access: jwt(9000), refresh: 'fake-new', user: { id: 2 } } }) })
  const refresh = a.store.refreshAccessToken(); await tick(); await b.store.login({})
  pending.reject({ response: { status: 401 } }); await refresh
  assert.equal(shared.getItem('refresh_token'), 'fake-new')
  const h = harness({ adapter: async config => { throw Object.assign(new Error('fake validation'), { config, response: { status: 400, data: { detail: 'fake field error' } } }) } })
  await assert.rejects(h.api.get('/ordinary/'), error => error.response.data.detail === 'fake field error')
})


function webLocks() {
  let tail = Promise.resolve()
  return { request(_name, options, work) { const callback = work || options; const next = tail.then(callback); tail = next.catch(() => {}); return next } }
}
test('two tabs concurrently refresh once under Web Locks and fallback, sharing the rotated session', async () => {
  for (const locks of [undefined, webLocks()]) {
    const shared = storage(), pending = deferred(); let calls = 0
    const post = async (_url, data) => { calls++; assert.equal(data.refresh, 'fake-refresh'); return pending.promise }
    const a = harness({ shared, post, locks }), b = harness({ shared, post, locks })
    const first = a.store.refreshAccessToken(), second = b.store.refreshAccessToken()
    await tick(); assert.equal(calls, 1)
    pending.resolve({ data: { access: jwt(7200), refresh: 'fake-rotated' } })
    const result = await Promise.all([first, second]); assert.deepEqual(result, [jwt(7200), jwt(7200)])
    assert.equal(calls, 1); assert.equal(shared.getItem('refresh_token'), 'fake-rotated')
    a.cleanup(); b.cleanup()
  }
})
test('older successful refresh cannot overwrite replacement identity; logout propagates and disposal cleans listeners', async () => {
  const shared = storage(), pending = deferred()
  const a = harness({ shared, post: async () => pending.promise })
  const b = harness({ shared, post: async () => ({ data: { access: jwt(9000), refresh: 'fake-new', user: { id: 2 } } }) })
  const refresh = a.store.refreshAccessToken(); await tick(); await b.store.login({})
  pending.resolve({ data: { access: jwt(7200), refresh: 'fake-old' } }); assert.equal(await refresh, jwt(9000))
  assert.equal(a.store.user.id, 2); assert.equal(shared.getItem('refresh_token'), 'fake-new')
  await b.store.logout(); a.listeners.get('storage')({ key: 'auth_session_v1' })
  assert.equal(a.store.accessToken, ''); assert.equal(a.timers.size, 0)
  a.cleanup(); b.cleanup(); assert.equal(a.listeners.size, 0); assert.equal(b.listeners.size, 0)
})
test('network failure settles all refresh callers and retains credentials; rejected credentials clear only unchanged session', async () => {
  for (const status of [undefined, 503, 401]) {
    let calls = 0; const h = harness({ locks: webLocks(), post: async () => { calls++; await tick(); throw { response: status ? { status } : undefined } } })
    const results = await Promise.allSettled([h.store.refreshAccessToken(), h.store.refreshAccessToken()])
    assert.ok(results.every(value => value.status === 'rejected')); assert.equal(calls, 1)
    assert.equal(h.shared.getItem('refresh_token'), status === 401 ? null : 'fake-refresh'); h.cleanup()
  }
})
test('fallback lease overlap: early rejected refresh cannot delete another tab pending successful rotation', async () => {
  const shared = storage(), success = deferred(), failure = deferred()
  const a = harness({ shared, post: () => success.promise }), b = harness({ shared, post: () => failure.promise })
  const first = a.store.refreshAccessToken(); await tick()
  // A suspended beyond lease expiry can overlap a new owner; storage is not a mutex.
  shared.removeItem('auth_refresh_lock_v1')
  const second = b.store.refreshAccessToken(); const settled = second.catch(() => 'rejected'); await tick()
  failure.reject({ response: { status: 401 } }); assert.equal(await settled, 'rejected')
  assert.equal(shared.getItem('refresh_token'), 'fake-refresh')
  assert.equal(b.store.isAuthenticated, false)
  success.resolve({ data: { access: jwt(7200), refresh: 'fake-winner' } }); await first
  b.listeners.get('storage')({ key: 'auth_session_v1' })
  assert.equal(b.store.refreshToken, 'fake-winner'); assert.equal(b.store.isAuthenticated, true)
  a.cleanup(); b.cleanup()
})
test('an old protected request is not replayed under a replacement login identity', async () => {
  const pending = deferred(); let protectedCalls = 0
  const h = harness({ adapter: async config => {
    if (config.url === '/auth/login/') return { status: 200, config, data: { access: jwt(9000), refresh: 'fake-other-person', user: { id: 2 } } }
    protectedCalls++; if (protectedCalls === 1) { await pending.promise; throw unauthorized(config) }
    return { status: 200, config, data: 'should not replay' }
  } })
  const request = h.api.post('/protected/mutation/', { harmless: 'fake' }); const settled = request.then(() => 'success', () => 'rejected')
  await tick(); await h.store.login({}); pending.resolve()
  assert.equal(await settled, 'rejected'); assert.equal(protectedCalls, 1); h.cleanup()
})

test('existing router SSO exchange legacy writes keep identity, JWT expiry and init timer', async () => {
  const shared = storage(); for (const key of ['access_token', 'refresh_token', 'user']) shared.removeItem(key)
  const seen = []
  const h = harness({ shared, adapter: async config => {
    seen.push(config)
    if (config.url === '/auth/exchange-token/') return { status: 200, config, data: { access: jwt(7200), refresh: 'fake-sso-refresh', user: { id: 44 } } }
    return { status: 200, config, data: 'fake protected result' }
  } })
  const router = readFileSync(new URL('../router/index.js', import.meta.url), 'utf8')
  const branch = router.slice(router.indexOf('    // SSO'), router.indexOf('    // 有 token'))
    .replace("(await import('@/utils/api')).default", 'apiClient')
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  let navigation
  await new AsyncFunction('to', 'userStore', 'localStorage', 'next', 'apiClient', branch)(
    { query: { code: 'fake-sso-code' }, path: '/home' }, h.store, shared, value => { navigation = value }, h.api)
  assert.equal(navigation.replace, true); assert.equal(navigation.query.code, undefined)
  await h.store.initAuth(); assert.equal(h.store.user.id, 44); assert.equal(h.store.tokenExpiresAt, 7200000); assert.equal(h.timers.size, 1)
  await h.api.get('/ordinary/'); assert.equal(seen.at(-1).headers.Authorization, `Bearer ${jwt(7200)}`)
  assert.equal(h.store.user.id, 44); h.cleanup()
})
test('explicit logout attempts server blacklist with captured bearer and bounded timeout before navigating', async () => {
  const pending = deferred(); let sent
  const h = harness({ adapter: config => { sent = config; return pending.promise } })
  const out = h.store.logout(); await tick()
  assert.equal(h.store.accessToken, ''); assert.equal(h.context.window.location.href, '')
  assert.equal(sent.url, '/auth/logout/'); assert.equal(sent.timeout, 10000); assert.equal(sent.headers.Authorization, `Bearer ${jwt(3600)}`)
  pending.reject(Object.assign(new Error('fake timeout'), { config: sent, code: 'ECONNABORTED' })); await out
  assert.equal(h.context.window.location.href, '/login'); assert.equal(h.shared.getItem('refresh_token'), null); h.cleanup()
})
