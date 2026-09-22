import api from '@/utils/api'

function parseEnvBoolean(value) {
  if (typeof value === 'boolean') {
    return value
  }

  if (value == null) {
    return false
  }

  return ['true', '1', 'yes', 'on'].includes(String(value).trim().toLowerCase())
}

const ANALYTICS_ENABLED = parseEnvBoolean(import.meta.env.VITE_ANALYTICS_ENABLED)
const SESSION_STORAGE_KEY = 'testhub_analytics_session_id'

function getSessionId() {
  if (typeof window === 'undefined') {
    return ''
  }

  const existingId = window.sessionStorage.getItem(SESSION_STORAGE_KEY)
  if (existingId) {
    return existingId
  }

  const sessionId =
    typeof window.crypto?.randomUUID === 'function'
      ? window.crypto.randomUUID()
      : `session_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`

  window.sessionStorage.setItem(SESSION_STORAGE_KEY, sessionId)
  return sessionId
}

function sanitizeSimpleObject(source) {
  if (!source || typeof source !== 'object') {
    return {}
  }

  return Object.entries(source).reduce((acc, [key, value]) => {
    if (value == null) {
      return acc
    }

    if (['string', 'number', 'boolean'].includes(typeof value)) {
      acc[key] = value
    } else {
      acc[key] = String(value)
    }
    return acc
  }, {})
}

function resolveModule(path = '') {
  if (!path) return 'unknown'
  if (path === '/home') return 'home'
  if (path.startsWith('/ai-generation')) return 'ai_generation'
  if (path.startsWith('/api-testing')) return 'api_testing'
  if (path.startsWith('/ui-automation')) return 'ui_automation'
  if (path.startsWith('/app-automation')) return 'app_automation'
  if (path.startsWith('/ai-intelligent-mode')) return 'ai_intelligent_mode'
  if (path.startsWith('/data-factory')) return 'data_factory'
  if (path.startsWith('/configuration')) return 'configuration'
  return 'other'
}

export function isAnalyticsEnabled() {
  return ANALYTICS_ENABLED
}

export function track(eventName, payload = {}) {
  if (!ANALYTICS_ENABLED || !eventName) {
    return Promise.resolve(false)
  }

  const data = {
    event_name: eventName,
    event_type: payload.event_type || 'custom',
    module: payload.module || resolveModule(payload.page_path || window.location.pathname),
    page_path: payload.page_path || window.location.pathname,
    route_name: payload.route_name || '',
    referrer_path: payload.referrer_path || '',
    target_path: payload.target_path || '',
    session_id: payload.session_id || getSessionId(),
    metadata: sanitizeSimpleObject(payload.metadata),
  }

  if (typeof payload.success === 'boolean') {
    data.success = payload.success
  }

  if (Number.isFinite(payload.duration_ms) && payload.duration_ms >= 0) {
    data.duration_ms = Math.round(payload.duration_ms)
  }

  return api.post('/analytics/events/', data).catch(() => false)
}

export function trackPageView(to, from) {
  if (!ANALYTICS_ENABLED || !to) {
    return Promise.resolve(false)
  }

  return track('page_view', {
    event_type: 'page_view',
    module: resolveModule(to.path),
    page_path: to.fullPath || to.path,
    route_name: typeof to.name === 'string' ? to.name : '',
    referrer_path: from?.fullPath || '',
    metadata: {
      path: to.path || '',
      params: JSON.stringify(sanitizeSimpleObject(to.params)),
      query: JSON.stringify(sanitizeSimpleObject(to.query)),
    },
  })
}
