const copy = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value))
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const equal = (first, second) => JSON.stringify(first) === JSON.stringify(second)

export const newScenarioForm = () => ({
  project: null, name: '', description: '', engine: 'K6', enabled: true,
  environment: null, global_environment: null, account_pool_version: null, account_pool_group: '',
  load_config: { model: 'CONCURRENCY', concurrency: 1, iterations_per_vu: 1, duration: 30, ramp_up: 0, max_requests: 0 },
  sla_config: { enabled: true, thresholds: { p95_response_time: 2000, error_rate: 0 }, step_thresholds: [], abort_delay: 0, abort_on_breach: false, breach_window: 10 },
  perf_targets: { max_p95_rt: 2000, max_avg_rt: null, min_tps: null, max_error_rate: 0 },
  variables: [], env_config: { base_url: '', headers: {} },
  runtime_config: { timeout: 30, sample_interval: 1, keep_alive: true, proxy: '' }
})

export const manualStepDefaults = () => ({
  protocol: 'HTTP', websocket_config: {},
  enabled: true, is_setup: false, method: 'GET', url: '', headers: {}, params: {},
  body_type: 'NONE', body: '', files: [], extractors: [],
  assertions: [{ type: 'STATUS_CODE', expected: 200 }], think_time: 0, weight: 1
})

export function mergeProjectDefaults(form, response, { project, baseline }) {
  const result = copy(form)
  if (form.project !== project || response.project !== project || baseline.project !== project || form.engine !== 'K6') return result
  const defaults = copy(response.defaults)
  const binding = ['environment', 'global_environment', 'account_pool_version', 'account_pool_group']
  if (binding.some(key => !equal(form[key], baseline[key]))) {
    for (const key of binding) delete defaults[key]
    delete defaults.runtime_config?.auth_profile
    delete defaults.runtime_config?.account_identity_variable
  }
  for (const key of ['auth_profile', 'account_identity_variable']) {
    if (!equal(form.runtime_config?.[key], baseline.runtime_config?.[key])) delete defaults.runtime_config?.[key]
  }
  const merge = (current, before, defaults) => {
    const merged = copy(current) || {}
    for (const [key, value] of Object.entries(defaults || {})) {
      if (['__proto__', 'prototype', 'constructor'].includes(key)) continue
      if (object(value) && object(current?.[key])) merged[key] = merge(current[key], before?.[key], value)
      else if (equal(current?.[key], before?.[key])) merged[key] = copy(value)
    }
    return merged
  }
  return merge(result, baseline, defaults)
}

export function clearProjectBindings(form) {
  const result = copy(form)
  Object.assign(result, { environment: null, global_environment: null, account_pool_version: null, account_pool_group: '' })
  delete result.runtime_config?.auth_profile
  delete result.runtime_config?.account_identity_variable
  return result
}
