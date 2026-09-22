// Bound portfolio steps consume immutable wire bytes; ordinary HTTP remains unchanged.
const uint32 = value => Number.isInteger(value) && value > 0 && value <= 4294967295;
const identifier = value => typeof value === 'string' && /^[1-9][0-9]{0,18}$/.test(value)
  && (value.length < 19 || value <= '9223372036854775807');
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    const result = {};
    for (const key of Object.keys(value).sort()) result[key] = canonical(value[key]);
    return result;
  }
  return value;
}
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const conditionNames = ['price_above', 'price_below', 'daily_pct_up', 'daily_pct_down', 'five_min_pct_up', 'five_min_pct_down'];
function conditions(value, input) {
  if (!value || typeof value !== 'object' || Object.keys(value).sort().join(',') !== [...conditionNames].sort().join(',')) throw new Error('ReminderConditions');
  const result = {};
  for (const name of conditionNames) {
    const row = value[name];
    if (!row || Object.keys(row).sort().join(',') !== 'enabled,value' || typeof row.enabled !== 'boolean' || typeof row.value !== 'string') throw new Error('ReminderCondition');
    if (!row.enabled && (row.value === '' || (input && row.value === '0'))) {
      result[name] = {enabled: false, value: ''}; continue;
    }
    const scale = name.startsWith('price_') ? 6 : 4;
    if (!new RegExp(`^(?:0|[1-9][0-9]{0,6})(?:\\.[0-9]{1,${scale}})?$`).test(row.value)) throw new Error('ReminderDecimal');
    const [whole, rawFraction = ''] = row.value.split('.');
    const fraction = rawFraction.replace(/0+$/, '');
    const max = scale === 6 ? 1000000 : 100;
    if ((whole === '0' && !fraction) || Number(whole) > max || (Number(whole) === max && fraction)) throw new Error('ReminderDecimal');
    result[name] = {enabled: row.enabled, value: whole + (fraction ? '.' + fraction : '')};
  }
  return result;
}
function sameConditions(actual, requested) { return equal(conditions(actual, false), conditions(requested, true)); }

export function createReminderGuard(binding, context, hash) {
  if (!binding) return {handles: () => false, request: () => null, response: () => true};
  const ids = binding.config;
  let verifiedOwner = false;
  let receipt = null;
  const role = step => step.id === 'reminder:identity' ? 'identity'
    : ['put', 'receipt', 'get', 'delete'].find(name => ids[name + '_step_id'] === step.id);
  function tokenMatches() { return hash(context[binding.token_variable]) === context.rr_token_hash; }
  return {
    handles: step => Boolean(role(step)),
    request(step, url) {
      const kind = role(step);
      if (!kind) return null;
      const path = kind === 'identity' ? '/api/v1/me' : kind === 'receipt'
        ? '/api/v1/portfolio/reminder-commands/' + context.rr_put_digest : binding.path;
      const method = kind === 'put' ? 'PUT' : kind === 'delete' ? 'DELETE' : 'GET';
      const shortOrigin = binding.origin.startsWith('https:') ? binding.origin.replace(/:443$/, '') : binding.origin.replace(/:80$/, '');
      if (binding.version !== 1 || (url !== binding.origin + path && url !== shortOrigin + path) || step.method !== method || !tokenMatches()
          || (kind !== 'identity' && !verifiedOwner)) throw new Error('ReminderFrozenRequest');
      if (kind !== 'put' && kind !== 'delete') return {body: null};
      const body = context['rr_' + kind + '_body'];
      const key = context['rr_' + kind + '_key'];
      if (typeof body !== 'string' || hash(`${method}\n${path}\n\n${body}`) !== context['rr_' + kind + '_fingerprint']
          || hash(`${method}\n${path}\nuser:${context.rr_owner}\n${key}`) !== context['rr_' + kind + '_digest']) {
        throw new Error('ReminderFrozenRequest');
      }
      return {body, key};
    },
    response(step, json) {
      const kind = role(step);
      if (!kind) return true;
      try {
        if (!json || json.code !== 'OK' || !json.data || typeof json.data !== 'object' || !tokenMatches()) return false;
        const data = json.data;
        if (kind === 'identity') {
          verifiedOwner = identifier(data.user_id) && data.user_id === context.rr_owner;
          return verifiedOwner;
        }
        if (!verifiedOwner) return false;
        const expected = JSON.parse(context.rr_put_body);
        if (kind === 'receipt') {
          receipt = null;
          const rule = data.rule;
          if (data.request_key !== context.rr_put_digest || data.request_fingerprint !== context.rr_put_fingerprint
              || data.operation !== 'put' || data.state !== 'committed' || data.stock_code !== ids.stock_code
              || !rule || !identifier(rule.id) || !uint32(rule.revision)
              || !sameConditions(rule.conditions, expected.conditions) || !equal(rule.policy, expected.policy)) return false;
          receipt = rule;
          return true;
        }
        if (kind === 'put' || kind === 'get') {
          return data.stock_code === ids.stock_code && uint32(data.revision)
            && sameConditions(data.conditions, expected.conditions) && equal(data.policy, expected.policy)
            && (kind === 'put' || Boolean(receipt && data.revision === receipt.revision));
        }
        return Boolean(receipt && data.outcome === 'deleted' && data.deleted === true
          && identifier(data.rule_id) && data.rule_id === receipt.id && data.before_revision === receipt.revision
          && uint32(data.after_revision) && data.after_revision === receipt.revision + 1);
      } catch (_) { return false; }
    },
  };
}
