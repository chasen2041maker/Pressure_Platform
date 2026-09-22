import assert from 'node:assert/strict';
import test from 'node:test';
import { createPolicyRuntime } from '../../testhub/apps/perf_testing/engines/k6_execution_policy.js';

const group = { index: 1, policy: { group_id: 'private-name', vu_start: 2, vu_end: 2, max_runs_per_vu: 2, min_interval_ms: 1000 }, outputs: ['owned'] };
test('failure consumes attempt and next permitted attempt clears earlier outputs', () => {
  let time = 0;
  const events = [], context = { owned: 'old', token: 'SECRET' };
  const runtime = createPolicyRuntime([group], 2, event => events.push(event), () => time);
  assert.equal(runtime.begin(group, context), true); assert.equal('owned' in context, false);
  runtime.complete(group, true, 1, 1);
  time = 999; assert.equal(runtime.begin(group, context), false);
  time = 1000; context.owned = 'old'; assert.equal(runtime.begin(group, context), true);
  assert.equal('owned' in context, false);
  runtime.complete(group, false, 0, 2);
  time = 2000; assert.equal(runtime.begin(group, context), false);
  runtime.flush(true);
  assert.equal(events.filter(event => event.kind === 'group_started').length, 2);
  assert.deepEqual(events.at(-1).groups, [{ group: 1, not_participant: 0, quota: 1, interval: 1 }]);
  assert.equal(JSON.stringify(events).includes('SECRET'), false);
  assert.equal(JSON.stringify(events).includes('private-name'), false);
});
test('many skipped rounds produce bounded cumulative summaries rather than per-step events', () => {
  const events = [];
  const runtime = createPolicyRuntime([group], 1, event => events.push(event), () => 10000);
  for (let i = 0; i < 10000; i++) { assert.equal(runtime.begin(group, {}), false); runtime.flush(); }
  runtime.flush(true);
  assert.equal(events.length, 2);
  assert.equal(events.at(-1).groups[0].not_participant, 10000);
});
