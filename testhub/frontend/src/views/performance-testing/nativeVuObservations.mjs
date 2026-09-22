export const NATIVE_VU_DISPLAY_LIMIT = 2000
const VERSION = 'native_vu_v1'
const SOURCE = 'k6_rest_status_v1'
const RESULTS = new Set(['ok', 'not_ready', 'timeout', 'transport_error', 'malformed',
  'oversize', 'ownership_mismatch', 'collector_failed', 'missed_deadline'])
const integer = value => Number.isSafeInteger(value) && value >= 0
const identity = (value, executionId) => value?.version === VERSION && value.source === SOURCE
  && integer(executionId) && executionId > 0 && value.execution_id === executionId

function observation(value, executionId) {
  if (!identity(value, executionId) || !/^[a-f0-9]{32}$/.test(value.runner_instance || '')
    || !RESULTS.has(value.result) || !integer(value.observation_seq) || value.observation_seq < 1) return null
  const start = value.request_start_offset_ms, end = value.request_end_offset_ms
  if (!integer(start) || !integer(end) || end < start || value.rtt_ms !== end - start) return null
  if (value.result === 'ok' && (!['active_vus', 'initialized_vus', 'status'].every(key => integer(value[key]))
    || !['running', 'paused', 'stopped'].every(key => typeof value[key] === 'boolean'))) return null
  return {
    version: VERSION, source: SOURCE, execution_id: executionId, runner_instance: value.runner_instance,
    observation_seq: value.observation_seq, request_start_offset_ms: start, request_end_offset_ms: end,
    rtt_ms: value.rtt_ms, result: value.result,
    active_vus: value.result === 'ok' ? value.active_vus : null,
    initialized_vus: value.result === 'ok' ? value.initialized_vus : null,
    status: value.result === 'ok' ? value.status : null,
    running: value.result === 'ok' ? value.running : null,
    paused: value.result === 'ok' ? value.paused : null,
    stopped: value.result === 'ok' ? value.stopped : null,
    ...(value._displayMixedRunners === true ? { _displayMixedRunners: true } : {})
  }
}

export function appendNativeVu(existing, incoming, executionId) {
  const seen = new Set(), runners = new Map(), existingCounts = new Map()
  let mixedRunners = false
  for (const [items, wasDisplayed] of [[existing, true], [incoming, false]]) {
    for (const item of Array.isArray(items) ? items : []) {
      const row = observation(item, executionId)
      if (!row) continue
      mixedRunners ||= row._displayMixedRunners === true
      const key = `${row.runner_instance}:${row.observation_seq}`
      if (seen.has(key)) continue
      seen.add(key)
      if (wasDisplayed) existingCounts.set(row.runner_instance, (existingCounts.get(row.runner_instance) || 0) + 1)
      if (!runners.has(row.runner_instance)) runners.set(row.runner_instance, [])
      runners.get(row.runner_instance).push(row)
    }
  }
  // Sequence orders one collector only. Another runner's offset is not a shared clock.
  for (const rows of runners.values()) rows.sort((a, b) => a.observation_seq - b.observation_seq)
  let remaining = NATIVE_VU_DISPLAY_LIMIT
  const allocation = new Map()
  for (const [runner, count] of existingCounts) {
    const retained = Math.min(count, remaining)
    allocation.set(runner, retained)
    remaining -= retained
  }
  // Each displayed instance keeps its slots; only unused slots can admit another instance.
  for (const [runner, count] of allocation) {
    const growth = Math.min(runners.get(runner).length - count, remaining)
    allocation.set(runner, count + growth)
    remaining -= growth
  }
  const established = [...allocation].flatMap(([runner, count]) => count > 0 ? runners.get(runner).slice(-count) : [])
  const unseen = [...runners].filter(([runner]) => !existingCounts.has(runner)).flatMap(([, rows]) => rows)
  const rows = [...established, ...(remaining > 0 ? unseen.slice(-remaining) : [])]
  mixedRunners ||= runners.size > 1
  // Keep uncertainty after trimming a foreign instance; this is local display metadata only.
  return mixedRunners ? rows.map(row => ({ ...row, _displayMixedRunners: true })) : rows
}

export function nativeVuFromSamples(samples, executionId) {
  return appendNativeVu([], (Array.isArray(samples) ? samples : []).flatMap(sample =>
    Array.isArray(sample?.native_vu_observations) ? sample.native_vu_observations : []), executionId)
}

const canLink = row => row.result === 'ok' && row.rtt_ms <= 750 && row.status === 7
  && row.running && !row.paused && !row.stopped && row.active_vus <= row.initialized_vus

export function projectNativeVu(observations, executionId) {
  const rows = appendNativeVu([], observations, executionId), points = []
  const mixedRunners = rows.some(row => row._displayMixedRunners)
  let previous = null
  for (const row of rows) {
    const start = row.request_start_offset_ms, end = row.request_end_offset_ms
    const midpoint = start / 2000 + end / 2000
    if (previous && previous.result === 'ok' && row.result === 'ok'
      && (!canLink(previous) || !canLink(row) || previous.runner_instance !== row.runner_instance
        || row.observation_seq !== previous.observation_seq + 1
        || start < previous.request_end_offset_ms || start - previous.request_start_offset_ms > 2000)) {
      points.push({ value: [midpoint, null], interval: null })
    }
    points.push({ value: [midpoint, row.active_vus], interval: [start, end], result: row.result,
      rtt: row.rtt_ms, sequence: row.observation_seq, runner: row.runner_instance, uncertain: !canLink(row) })
    previous = row
  }
  return { points, latest: mixedRunners ? null : rows.at(-1)?.active_vus ?? null, count: rows.length, mixedRunners }
}

export function nativeVuEvidence(summary, executionId) {
  const accepted = identity(summary, executionId) && summary.evidence_scope === 'collector_received'
  const count = key => accepted && integer(summary[key]) ? summary[key] : null
  const reason = summary?.version === VERSION && summary.source === SOURCE
    && (summary.execution_id == null || summary.execution_id === executionId)
    && ['unsupported_runner', 'not_started'].includes(summary.reason) ? summary.reason : ''
  return { received: count('attempt_count'), valid: count('valid_count'), missing: count('missing_count'),
    below: count('below_target_count'), late: count('late_count'), reason,
    // This protocol's summary describes collector receipts, not a storage audit or continuous proof.
    persistence: '未验证', sustained: '未验证' }
}
