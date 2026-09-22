import { defineStore } from 'pinia'
import { ref, computed, customRef, onScopeDispose } from 'vue'
import api from '@/utils/api'
import { track } from '@/utils/tracker'

const SESSION_KEY = 'auth_session_v1'
const LOCK_KEY = 'auth_refresh_lock_v1'
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
const nonce = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
function expiry(token) {
  try {
    const claim = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return Number.isFinite(claim.exp) && claim.exp > 0 ? claim.exp * 1000 : 0
  } catch { return 0 }
}
function readSession() {
  try {
    const saved = localStorage.getItem(SESSION_KEY)
    if (saved) return JSON.parse(saved)
    return { id: localStorage.getItem('refresh_token') || '', access: localStorage.getItem('access_token') || '',
      refresh: localStorage.getItem('refresh_token') || '', user: JSON.parse(localStorage.getItem('user') || 'null') }
  } catch { return { id: '', access: '', refresh: '', user: null } }
}

export const useUserStore = defineStore('user', () => {
  const user = ref(null), accessToken = ref(''), refreshToken = ref(''), tokenExpiresAt = ref(0)
  let refreshTimer = null, refreshPromise = null, loginAttempt = 0
  const rejectedRefresh = ref('')
  const isAuthenticated = computed(() => !!accessToken.value && !!user.value && refreshToken.value !== rejectedRefresh.value)
  // Read time at consumption: a computed value with only Date.now() stays cached.
  const clockFlag = predicate => customRef(track => ({ get() { track(); return predicate() }, set() {} }))
  const isTokenExpiringSoon = clockFlag(() => !!accessToken.value && (!tokenExpiresAt.value || tokenExpiresAt.value - Date.now() < 300000))
  const isTokenExpired = clockFlag(() => !!accessToken.value && (!tokenExpiresAt.value || Date.now() >= tokenExpiresAt.value))
  function syncSession() {
    const saved = readSession()
    accessToken.value = saved.access || ''; refreshToken.value = saved.refresh || ''
    user.value = saved.user || null; tokenExpiresAt.value = expiry(accessToken.value)
    if (rejectedRefresh.value && rejectedRefresh.value !== refreshToken.value) rejectedRefresh.value = ''
    return saved
  }
  function saveSession(saved) {
    localStorage.setItem(SESSION_KEY, JSON.stringify(saved))
    localStorage.setItem('access_token', saved.access)
    localStorage.setItem('refresh_token', saved.refresh)
    localStorage.setItem('user', JSON.stringify(saved.user))
    localStorage.setItem('token_expires_at', String(expiry(saved.access)))
    syncSession()
  }
  function stopAutoRefresh() { if (refreshTimer !== null) clearInterval(refreshTimer); refreshTimer = null }
  function clearSession(expected) {
    const current = readSession()
    if (expected && (expected.id !== current.id || expected.refresh !== current.refresh)) { syncSession(); return false }
    for (const key of [SESSION_KEY, 'access_token', 'refresh_token', 'user', 'token_expires_at']) localStorage.removeItem(key)
    syncSession(); stopAutoRefresh()
    return true
  }
  function startAutoRefresh() {
    stopAutoRefresh()
    refreshTimer = setInterval(async () => {
      syncSession()
      if (refreshToken.value && isTokenExpiringSoon.value) {
        try { await refreshAccessToken() } catch { /* Retry transient failures on the next bounded timer tick. */ }
      }
    }, 15000)
  }
  async function authenticate(url, data, event, type) {
    const attempt = ++loginAttempt
    const response = await api.post(url, data)
    if (attempt !== loginAttempt) throw new Error('登录请求已被替换')
    saveSession({ id: nonce(), access: response.data.access, refresh: response.data.refresh, user: response.data.user })
    startAutoRefresh()
    track(event, { event_type: 'business', module: 'auth', page_path: type === 'register' ? '/register' : '/login', success: true, metadata: { login_type: type } })
    return response.data
  }
  const login = data => authenticate('/auth/login/', data, 'login_success', 'password')
  const smsLogin = data => authenticate('/auth/sms-login/', data, 'login_success', 'sms')
  const register = data => authenticate('/auth/test-register/', data, 'register_success', 'register')
  async function logout() {
    ++loginAttempt
    const previous = syncSession()
    clearSession()
    try {
      if (previous.refresh && previous.access) {
        await api.post('/auth/logout/', { refresh: previous.refresh }, { timeout: 10000, skipAuth: true, headers: { Authorization: `Bearer ${previous.access}` } })
      }
    } catch { /* Local logout remains effective if blacklist delivery fails. */ }
    finally { if (!readSession().access) window.location.href = '/login' }
  }
  async function withRefreshLock(work) {
    if (globalThis.navigator?.locks?.request) {
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 40000)
      try { return await navigator.locks.request('testhub-auth-refresh', { signal: controller.signal }, work) }
      finally { clearTimeout(timeout) }
    }
    // Fallback for browsers without Web Locks: expiring ownership plus session compare-and-set.
    const owner = nonce(), deadline = Date.now() + 40000
    for (let attempts = 0; attempts < 400 && Date.now() <= deadline; attempts++) {
      let lock
      try { lock = JSON.parse(localStorage.getItem(LOCK_KEY) || 'null') } catch { lock = null }
      if (!lock || lock.until <= Date.now()) {
        localStorage.setItem(LOCK_KEY, JSON.stringify({ owner, until: Date.now() + 35000 }))
        await pause(0)
        if (JSON.parse(localStorage.getItem(LOCK_KEY) || '{}').owner === owner) {
          try { return await work() } finally {
            if (JSON.parse(localStorage.getItem(LOCK_KEY) || '{}').owner === owner) localStorage.removeItem(LOCK_KEY)
          }
        }
      }
      await pause(100)
    }
    throw new Error('登录刷新繁忙，请稍后重试')
  }
  function refreshAccessToken() {
    if (refreshPromise) return refreshPromise
    const requested = syncSession()
    refreshPromise = withRefreshLock(async () => {
      const before = syncSession()
      if (!before.refresh || before.refresh === rejectedRefresh.value) throw new Error('请重新登录')
      if (before.id !== requested.id || before.refresh !== requested.refresh) return before.access
      try {
        const { data } = await api.post('/auth/token/refresh/', { refresh: before.refresh })
        const current = readSession()
        if (current.id !== before.id || current.refresh !== before.refresh) {
          syncSession()
          if (current.access) return current.access
          throw new Error('登录已退出')
        }
        saveSession({ ...before, access: data.access, refresh: data.refresh || before.refresh })
        return data.access
      } catch (error) {
        const current = syncSession()
        if (current.id !== before.id || current.refresh !== before.refresh) {
          if (current.access) return current.access
          throw error
        }
        if ([400, 401].includes(error.response?.status)) {
          if (globalThis.navigator?.locks?.request) clearSession(before)
          else {
            // A suspended tab can outlive a storage lease. Do not delete shared credentials
            // while another owner may still be completing a successful rotation.
            rejectedRefresh.value = before.refresh
            stopAutoRefresh()
          }
          window.location.href = '/login'
        }
        throw error
      }
    }).finally(() => { refreshPromise = null })
    return refreshPromise
  }
  async function fetchProfile() {
    const before = syncSession()
    const response = await api.get('/auth/profile/')
    const current = syncSession()
    if (current.id === before.id && current.access) saveSession({ ...current, user: response.data })
    return response.data
  }
  async function initAuth() {
    syncSession()
    if (!accessToken.value) return
    startAutoRefresh()
    if (isTokenExpired.value && refreshToken.value) { try { await refreshAccessToken() } catch { return } }
    if (!user.value) { try { await fetchProfile() } catch { /* Preserve session on temporary profile failures. */ } }
  }
  const onStorage = event => {
    if (event.key === null || [SESSION_KEY, 'access_token', 'refresh_token', 'user'].includes(event.key)) {
      syncSession()
      if (accessToken.value && refreshToken.value !== rejectedRefresh.value) startAutoRefresh()
      else { stopAutoRefresh(); window.location.href = '/login' }
    }
  }
  window.addEventListener('storage', onStorage)
  onScopeDispose(() => { stopAutoRefresh(); window.removeEventListener('storage', onStorage) })
  syncSession()
  return { user, accessToken, refreshToken, tokenExpiresAt, isAuthenticated, isTokenExpiringSoon, isTokenExpired,
    login, smsLogin, register, logout, refreshAccessToken, fetchProfile, initAuth, startAutoRefresh, stopAutoRefresh, syncSession }
})
