const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const integer = value => Number.isSafeInteger(value) && value >= 0
export const recoveryRoles = ['put_step_id', 'receipt_step_id', 'get_step_id', 'delete_step_id']
export const recoveryCountFields = ['planned', 'pending', 'cleaned', 'cancelled', 'conflict', 'requests', 'reads', 'writes', 'completed_requests', 'unknown_requests']
const fields = ['version', 'kind', 'group_id', ...recoveryRoles, 'stock_code', 'max_resources']
const policyFields = ['group_id', 'vu_start', 'vu_end', 'max_runs_per_vu', 'min_interval_ms']
export const recoveryEnabled = value => value != null && (!object(value) || Object.keys(value).length > 0)
export const newRecoveryConfig = () => ({ version: 1, kind: 'portfolio_reminder', group_id: '', put_step_id: null, receipt_step_id: null, get_step_id: null, delete_step_id: null, stock_code: '', max_resources: 1 })

export function recoveryStepMatches(step, role, stock) {
  const index = recoveryRoles.indexOf(role)
  const path = index === 1 ? '/api/v1/portfolio/reminder-commands/{{rr_put_digest}}' : `/api/v1/portfolio/reminders/${stock}`
  return index >= 0 && integer(step.id) && step.id > 0 && step.enabled !== false && !step.is_setup
    && (step.protocol || 'HTTP') === 'HTTP' && step.method === ['PUT', 'GET', 'GET', 'DELETE'][index] && step.url === path
    && !Object.keys(step.params || {}).length && !(step.files || []).length
}

export function recoveryFormErrors(value, steps = [], engine = 'K6', concurrency = 1) {
  if (!recoveryEnabled(value)) return []
  if (!object(value) || Object.keys(value).length !== fields.length || fields.some(key => !Object.hasOwn(value, key)) || value.version !== 1 || value.kind !== 'portfolio_reminder') return ['config']
  const errors = []
  if (engine !== 'K6') errors.push('engine')
  if (typeof value.group_id !== 'string' || !/^[A-Za-z][A-Za-z0-9_]{0,47}$/.test(value.group_id)) errors.push('group')
  if (typeof value.stock_code !== 'string' || !/^(sh|sz|bj)[0-9]{6}$/.test(value.stock_code)) errors.push('stock')
  if (!integer(value.max_resources) || value.max_resources < 1 || value.max_resources > 1000) errors.push('limit')
  const ids = recoveryRoles.map(key => value[key])
  if (ids.some(id => !integer(id) || id < 1) || new Set(ids).size !== 4) return [...errors, 'savedSteps']
  const active = steps.filter(step => step.enabled !== false)
  const selected = ids.map(id => active.find(step => step.id === id))
  if (selected.some((step, index) => !step || !recoveryStepMatches(step, recoveryRoles[index], value.stock_code))) return [...errors, 'stepContract']
  const start = active.findIndex(step => step.id === ids[0])
  if (ids.some((id, index) => active[start + index]?.id !== id)) errors.push('order')
  const policy = selected[0].execution_policy || {}
  if (policy.group_id !== value.group_id || policy.max_runs_per_vu !== 1 || !integer(policy.vu_start) || policy.vu_start < 1 || !integer(policy.vu_end)
    || policy.vu_end < policy.vu_start || policy.vu_end > concurrency || policy.vu_end - policy.vu_start + 1 > value.max_resources
    || selected.some(step => policyFields.some(key => step.execution_policy?.[key] !== policy[key]))
    || active.some(step => !ids.includes(step.id) && step.execution_policy?.group_id === value.group_id)) errors.push('policy')
  if (active.some(step => !ids.includes(step.id) && ['PUT', 'DELETE'].includes(step.method) && String(step.url || '').includes('/portfolio/reminders/'))) errors.push('unboundWrite')
  return errors
}

export function recoveryObservation(value) {
  const counts = Object.fromEntries(recoveryCountFields.map(field => [field, null]))
  if (object(value) && !Object.keys(value).length) return { state: 'DISABLED', counts }
  const valid = object(value) && value.version === 1 && value.kind === 'portfolio_reminder'
  const states = ['PREPARING', 'PENDING', 'RECOVERED', 'CONFLICT', 'CORRUPT']
  if (!valid || !states.includes(value.state)) return { state: 'UNKNOWN', counts }
  if (value.observed !== true) return { state: value.state === 'CORRUPT' ? 'CORRUPT' : 'UNKNOWN', counts }
  for (const field of recoveryCountFields) if (integer(value[field])) counts[field] = value[field]
  const complete = Object.values(counts).every(integer)
  const consistent = complete && counts.planned === counts.pending + counts.cleaned + counts.cancelled + counts.conflict
    && counts.requests === counts.reads + counts.writes && counts.requests === counts.completed_requests + counts.unknown_requests
  const stateValid = value.state !== 'RECOVERED' || (counts.pending === 0 && counts.conflict === 0)
  return { state: consistent && stateValid ? value.state : 'UNKNOWN', counts }
}
