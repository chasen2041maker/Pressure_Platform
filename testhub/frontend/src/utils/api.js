import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const api = axios.create({ baseURL: '/api', timeout: 30000, headers: { 'Content-Type': 'application/json' } })
const authEndpoint = config => /\/auth\/(?:login|sms-login|test-register|logout|token\/refresh)\/?(?:\?|$)/.test(config.url || '')
api.interceptors.request.use(async config => {
  if (config.skipAuth || authEndpoint(config)) return config
  const store = useUserStore()
  const session = store.syncSession()
  config._authSessionId ??= session.id
  if (!config._retry && store.refreshToken && store.isTokenExpiringSoon) await store.refreshAccessToken()
  if (store.syncSession().id !== config._authSessionId) throw new Error('登录身份已变更，请重新操作')
  if (store.accessToken) config.headers.Authorization = `Bearer ${store.accessToken}`
  return config
})
api.interceptors.response.use(response => response, async error => {
  const config = error.config
  if (error.response?.status === 401 && config && !config._retry && !config.skipAuth && !authEndpoint(config)) {
    const store = useUserStore()
    const session = store.syncSession()
    if (config._authSessionId !== session.id) return Promise.reject(error)
    if (store.refreshToken) {
      config._retry = true
      const sent = config.headers?.Authorization
      const token = store.accessToken && sent !== `Bearer ${store.accessToken}`
        ? store.accessToken : await store.refreshAccessToken()
      config.headers.Authorization = `Bearer ${token}`
      return api(config)
    }
  }
  if (error.response?.status === 401 && !authEndpoint(config || {})) ElMessage.error('登录已过期，请重新登录')
  else if (error.response?.status >= 500) ElMessage.error('服务器错误，请稍后重试')
  return Promise.reject(error)
})
export default api
