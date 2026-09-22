export function tlsModeOf(config) {
  return typeof config?.verify_ssl === 'boolean' ? (config.verify_ssl ? 'on' : 'off') : 'inherit'
}

export function effectiveTlsVerified(config) {
  return config?.verify_ssl !== false
}

export function withTlsMode(config, mode) {
  const result = { ...config }
  if (mode === 'inherit') delete result.verify_ssl
  else result.verify_ssl = mode === 'on'
  return result
}

export function selectionState(form, options, loaded) {
  const has = id => id !== null && id !== undefined && id !== ''
  const matches = (id, scope) => !has(id) || options.some(env => env.id === id && env.scope === scope
    && (scope === 'GLOBAL' ? env.project === null : env.project === form.project))
  return {
    legacyBlocked: form.engine !== 'K6' && (has(form.environment) || has(form.global_environment)),
    invalid: Boolean(loaded) && (!matches(form.environment, 'PROJECT') || !matches(form.global_environment, 'GLOBAL'))
  }
}

export function canWriteEnvironment(environment, permissions) {
  if (!permissions) return false
  return environment.scope === 'GLOBAL' ? permissions.can_manage_global === true
    : permissions.project_ids?.includes(environment.project) === true
}

export function publicVariable(variable) {
  const result = { ...variable }
  if (variable.secret) {
    for (const field of ['value', 'values', 'options']) {
      if (Object.hasOwn(result, field)) result[field] = '******'
    }
  }
  return result
}

const containsMask = value => value === '******' || (value && typeof value === 'object'
  && Object.values(value).some(containsMask))
const clearMasks = value => value === '******' ? '' : Array.isArray(value)
  ? value.map(clearMasks) : value && typeof value === 'object'
    ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clearMasks(item)])) : value

export function copyEnvironment(source) {
  const headers = clearMasks(source.headers || {})
  const headerRows = Object.entries(source.headers || {}).map(([key, value]) => ({
    key, value: headers[key], required: Boolean(containsMask(value))
  }))
  const variableRows = (source.variables || []).map(variable => {
    const result = clearMasks(variable)
    const requiredFields = []
    for (const key of Object.keys(variable)) {
      if (containsMask(variable[key])) {
        requiredFields.push(key)
        if (key === 'values') result[key] = []
      }
    }
    return { value: result, requiredFields }
  })
  return { form: { name: source.name, scope: source.scope, project: source.project ?? null,
    base_url: source.base_url || '', headers, variables: variableRows.map(row => row.value),
    verify_ssl: source.verify_ssl !== false, is_active: false },
  secrets: { headers: headerRows, variables: variableRows } }
}

export function missingCopySecrets(rows) {
  if (!rows) return ['unavailable']
  const missing = value => (typeof value === 'string' && !value.trim())
      || value === null || value === undefined || containsMask(value)
      || (Array.isArray(value) && value.length === 0)
  return [
    ...rows.headers.filter(row => row.required && missing(row.value)),
    ...rows.variables.flatMap(row => row.requiredFields.filter(key => missing(row.value[key])))
  ]
}

export function latestRequestGate() {
  let version = 0
  return { begin: () => ++version, isCurrent: request => request === version }
}
