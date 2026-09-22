// Per-VU state: attempts are consumed before the first request, including failures.
export function createPolicyRuntime(groups, vu, emit, now = Date.now) {
  const states = new Map();
  let lastFlush = 0;
  for (const group of groups) if (group.policy.group_id) {
    states.set(group.index, { attempts: 0, lastStarted: null, not_participant: 0, quota: 0, interval: 0, dirty: false });
  }
  return {
    begin(group, context) {
      const policy = group.policy;
      if (!policy.group_id) return true;
      const state = states.get(group.index);
      const time = now();
      const reason = vu < policy.vu_start || vu > policy.vu_end ? 'not_participant'
        : state.attempts >= policy.max_runs_per_vu ? 'quota'
          : state.lastStarted !== null && time - state.lastStarted < policy.min_interval_ms ? 'interval' : null;
      if (reason) { state[reason]++; state.dirty = true; return false; }
      state.attempts++;
      state.lastStarted = time;
      for (const name of group.outputs) delete context[name];
      emit({ kind: 'group_started', group: group.index, attempt: state.attempts });
      return true;
    },
    complete(group, failed, blocked, executed) {
      if (group.policy.group_id) emit({ kind: 'group_completed', group: group.index,
        attempt: states.get(group.index).attempts, ok: !failed && !blocked,
        blocked_steps: blocked, executed_steps: executed });
    },
    waitMs() {
      let wait = Infinity;
      for (const group of groups) {
        const p = group.policy;
        if (!p.group_id) return 0;
        const state = states.get(group.index);
        if (vu < p.vu_start || vu > p.vu_end || state.attempts >= p.max_runs_per_vu) continue;
        wait = Math.min(wait, state.lastStarted === null ? 0 : Math.max(0, state.lastStarted + p.min_interval_ms - now()));
      }
      return wait;
    },
    flush(force = false) {
      const time = now();
      if (!force && time - lastFlush < 5000) return;
      const rows = [];
      for (const [group, state] of states) if (state.dirty) {
        rows.push({ group, not_participant: state.not_participant, quota: state.quota, interval: state.interval });
        state.dirty = false;
      }
      if (rows.length) emit({ kind: 'policy_skips', groups: rows });
      lastFlush = time;
    },
  };
}
