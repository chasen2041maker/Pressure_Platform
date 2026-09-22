export const MASK = '******'
export const cloneAuth = value => JSON.parse(JSON.stringify(value))

export function authRequest(phase, identity = 'user_id') {
  return { method: 'POST', url: phase === 'login' ? '/auth/login' : '/auth/refresh', body_type: 'JSON',
    body: JSON.stringify(phase === 'login' ? { [identity]: `{{${identity}}}`, password: '{{password}}' } : { refresh_token: '{{refresh_token}}' }, null, 2),
    headers: {}, assertions: [{ type: 'STATUS_CODE', expected: '200' }],
    extractors: [{ type: 'JSON_PATH', name: 'access_token', expr: '$.data.token' }] }
}

export function newAuthProfile(identity) {
  return { mode: 'LOGIN', transport: 'BEARER', access_token_variable: 'access_token', max_attempts: 1,
    retry_delay_ms: 0, expiry_skew_seconds: 0, refresh_on_status: [401], login: authRequest('login', identity) }
}

export function authSources(pool = {}, variables = []) {
  return [
    ...Object.entries(pool.fieldMapping || {}).map(([name, column]) => ({ name, column, source: 'pool', identity: column === pool.identityColumn })),
    ...variables.filter(v => v.type === 'CSV' && v.name).map(v => ({ name: v.name, column: v.column, source: 'csv', identity: false }))
  ].filter((item, index, rows) => rows.findIndex(other => other.name === item.name) === index)
}

export function setRefresh(profile, enabled, identity) {
  const next = cloneAuth(profile)
  if (enabled) {
    next.refresh_token_variable ||= 'refresh_token'
    next.refresh ||= authRequest('refresh', identity)
    if (next.transport === 'COOKIE') next.refresh.extractors = next.refresh.extractors.filter(rule => rule.name !== 'access_token' || rule.expr !== '$.data.token')
    for (const phase of ['login', 'refresh']) {
      const step = next[phase]
      if (step && !step.extractors.some(rule => rule.name === next.refresh_token_variable)) {
        step.extractors.push({ type: 'JSON_PATH', name: next.refresh_token_variable, expr: '$.data.refresh_token' })
      }
    }
  } else {
    delete next.refresh
    delete next.refresh_token_variable
    delete next.expires_in_variable
  }
  return next
}

export function authFormErrors(profile) {
  if (!profile?.mode) return []
  const errors = []
  const name = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/
  if (profile.transport === 'BEARER' && !name.test(profile.access_token_variable || '')) errors.push('profile:variableName')
  if (profile.transport === 'COOKIE' && (!name.test(profile.cookie_name || '') || (profile.mode === 'STATIC' && !name.test(profile.cookie_variable || '')))) errors.push('profile:variableName')
  for (const key of ['access_token_variable', 'cookie_variable', 'refresh_token_variable', 'expires_in_variable']) {
    if (profile[key] && !name.test(profile[key])) errors.push('profile:variableName')
  }
  for (const phase of ['login', 'refresh']) {
    const step = profile[phase]
    if (!step) continue
    const required = [profile.transport === 'BEARER' ? profile.access_token_variable : null, profile.refresh_token_variable, profile.expires_in_variable].filter(Boolean)
    if (required.some(value => !step.extractors.some(rule => rule.name === value))) errors.push(`${phase}:requiredOutputs`)
    if (!/^\/(?!\/)[^\s?#{}\\]*$/.test(step.url || '')) errors.push(`${phase}:path`)
    if (step.body_type === 'JSON' && step.body !== MASK) {
      try {
        const body = JSON.parse(step.body)
        const dynamicKeys = value => value && typeof value === 'object' && Object.entries(value).some(([key, item]) => /\{\{|\$\{/.test(key) || dynamicKeys(item))
        if (dynamicKeys(body)) errors.push(`${phase}:keys`)
      } catch { errors.push(`${phase}:json`) }
    }
  }
  return errors
}
