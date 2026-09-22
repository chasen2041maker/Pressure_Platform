import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const policySource = readFileSync(new URL('../../testhub/apps/perf_testing/engines/k6_execution_policy.js', import.meta.url), 'utf8').replace(/export /g, '');
const source = policySource + '\n' + readFileSync(new URL('../../testhub/apps/perf_testing/engines/k6_script.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/mg, '').replace('export default function ()', 'function iteration()').replace(/export /g, '');
const step = (url, extra = {}) => ({ method: 'GET', url, ...extra });
const extractors = [{ name: 'token', expr: '$.token' }, { name: 'refresh', expr: '$.refresh' }];

function execute({ steps, groups, auth, responses = [], duration = 1, now = 100000, rounds = 0, extraIterations = 0 }) {
  let clock = now;
  const calls = [], events = [], sleeps = [];
  const config = {
    load_config: { concurrency: 1, duration, iterations_per_vu: rounds },
    runtime_config: { timeout: 3, auth_profile: auth },
    env_config: { base_url: 'http://fixture.invalid' }, steps, execution_groups: groups,
    variables: ['token', 'refresh', 'user'].map(name => ({ name, value: 'private-fixture' })),
  };
  class Jar { set() {} cookiesForURL() { return {}; } }
  const sandbox = {
    __ENV: { K6_TESTHUB_CONFIG: 'config' },
    exec: { vu: { idInTest: 1, iterationInScenario: 0 }, scenario: { startTime: 100000 } },
    open: () => JSON.stringify(config), Date: { now: () => clock },
    sleep: seconds => { sleeps.push(seconds); clock += seconds * 1000; },
    SharedArray: function (_name, load) { return load(); },
    console: { log: line => events.push(JSON.parse(line.slice('TESTHUB_K6_EVENT '.length))) },
    http: { CookieJar: Jar, request: (_method, url) => {
      assert.ok(rounds > 0 || clock < 101000, 'no HTTP attempt admitted after the shared deadline');
      const reply = responses[calls.length] || {};
      calls.push({ path: new URL(url).pathname, start: clock });
      clock += reply.delay || 0;
      return { status: reply.status ?? 200, error_code: reply.error_code || 0,
        json: () => reply.body || { code: 0, token: 'private-fixture', refresh: 'private-fixture' } };
    } },
  };
  vm.createContext(sandbox);
  vm.runInContext(source + '\niteration();', sandbox);
  for (let i = 0; i < extraIterations; i++) vm.runInContext('iteration()', sandbox);
  const options = vm.runInContext('options.scenarios.business', sandbox);
  assert.equal(events.some(e => e.kind === 'runtime_error'), false);
  return { calls, events, sleeps, options };
}

test('bounded drain completes assertions but admits no next business step or iteration', () => {
  const result = execute({ steps: [step('/one', { assertions: [{ type: 'JSON_PATH', expr: '$.code', expected: 0 }] }), step('/two')],
    responses: [{ delay: 1600, body: { code: 4 } }], extraIterations: 2 });
  assert.deepEqual(result.calls.map(c => c.path), ['/one']);
  assert.equal(result.events.find(e => e.kind === 'request').ok, false);
  assert.equal(result.events.filter(e => e.kind === 'iteration').length, 0);
  assert.equal(result.events.filter(e => e.kind === 'request_started').length, 1);
  assert.equal(result.options.gracefulStop, '8s');
});

test('late VU shares the scenario deadline and does not restart its own timer', () => {
  const result = execute({ steps: [step('/one')], now: 101000 });
  assert.equal(result.calls.length, 0);
  assert.equal(result.events.find(e => e.kind === 'load_expired').setup_complete, false);
});

test('incomplete setup does not pass or send later setup/business requests', () => {
  const result = execute({ steps: [step('/setup', { is_setup: true }), step('/setup2', { is_setup: true }), step('/one')], responses: [{ delay: 1600 }] });
  assert.deepEqual(result.calls.map(c => c.path), ['/setup']);
  assert.equal(result.events.find(e => e.kind === 'load_expired').setup_complete, false);
  assert.equal(result.events.some(e => e.kind === 'auth_failed'), false);
});

test('last setup or login completing after deadline cannot count as a prepared VU', () => {
  const setup = execute({ steps: [step('/setup', { is_setup: true }), step('/one')], responses: [{ delay: 1600 }] });
  const login = execute({ auth: { mode: 'LOGIN', transport: 'BEARER', access_token_variable: 'token' },
    steps: [step('/login', { is_setup: true, auth_phase: 'login', auth_input_variables: ['user'], extractors }), step('/one')],
    responses: [{ delay: 1600 }] });
  for (const result of [setup, login]) {
    assert.equal(result.calls.length, 1);
    assert.equal(result.events.find(e => e.kind === 'load_expired').setup_complete, false);
    assert.equal(result.events.some(e => e.kind === 'auth_failed'), false);
    assert.equal(result.events.some(e => e.kind === 'iteration'), false);
  }
});

test('think time is bounded by remaining load time', () => {
  const result = execute({ steps: [step('/one', { think_time: { type: 'FIXED', min: 60000 } }), step('/two')], responses: [{ delay: 250 }] });
  assert.deepEqual(result.sleeps, [0.75]);
  assert.deepEqual(result.calls.map(c => c.path), ['/one']);
  assert.equal(result.events.some(e => e.kind === 'iteration'), false);
});

test('login retry delay stops at deadline without reporting a fictitious auth failure', () => {
  const result = execute({ auth: { mode: 'LOGIN', transport: 'BEARER', access_token_variable: 'token', max_attempts: 2, retry_delay_ms: 60000 },
    steps: [step('/login', { is_setup: true, auth_phase: 'login', auth_input_variables: ['user'], extractors }), step('/one')], responses: [{ delay: 500, status: 401 }] });
  assert.deepEqual(result.calls.map(c => c.path), ['/login']);
  assert.deepEqual(result.sleeps, [0.5]);
  assert.equal(result.events.some(e => e.kind === 'auth_failed'), false);
  assert.equal(result.events.find(e => e.kind === 'load_expired').setup_complete, false);
});

test('401 after deadline cannot start refresh; refresh crossing deadline cannot retry business', () => {
  const auth = { mode: 'STATIC', transport: 'BEARER', access_token_variable: 'token', refresh_token_variable: 'refresh' };
  const steps = [step('/refresh', { is_setup: true, auth_phase: 'refresh', auth_input_variables: ['refresh'], extractors }), step('/one')];
  const expired = execute({ auth, steps, responses: [{ status: 401, delay: 1600 }] });
  assert.deepEqual(expired.calls.map(c => c.path), ['/one']);
  assert.equal(expired.events.some(e => e.kind === 'auth_failed'), false);
  const refreshed = execute({ auth, steps, responses: [{ status: 401, delay: 100 }, { delay: 1500 }] });
  assert.deepEqual(refreshed.calls.map(c => c.path), ['/one', '/refresh']);
  assert.equal(refreshed.events.some(e => e.kind === 'auth_failed'), false);
  assert.equal(refreshed.events.some(e => e.kind === 'iteration'), false);
});

test('per-vu iterations retains its hard max duration', () => {
  const result = execute({ steps: [step('/one'), step('/two')], rounds: 1 });
  assert.equal(result.options.gracefulStop, '0s');
  assert.equal(result.options.executor, 'per-vu-iterations');
  assert.equal(result.events.filter(e => e.kind === 'iteration').length, 1);
});

const twoStepGroup = {
  index: 1,
  policy: { group_id: 'two-steps', vu_start: 1, vu_end: 1, max_runs_per_vu: 100, min_interval_ms: 0 },
  steps: [{ index: 0, requires: [] }, { index: 1, requires: [] }], outputs: [],
};
const nextGroup = { policy: {}, steps: [{ index: 2, requires: [] }], outputs: [] };

test('completed group keeps its terminal when final think time reaches the deadline', () => {
  const result = execute({
    steps: [step('/one'), step('/two', { think_time: { type: 'FIXED', min: 600 } }), step('/next')],
    groups: [twoStepGroup, nextGroup], responses: [{ delay: 200 }, { delay: 300 }], extraIterations: 1,
  });
  assert.deepEqual(result.calls.map(c => c.path), ['/one', '/two']);
  assert.deepEqual(result.sleeps, [0.5]);
  const terminals = result.events.filter(e => e.kind === 'group_completed');
  assert.equal(terminals.length, 1);
  assert.equal(terminals[0].ok, true);
  assert.equal(terminals[0].executed_steps, 2);
  assert.equal(terminals[0].blocked_steps, 0);
  assert.equal(result.events.filter(e => e.kind === 'request' && e.ok).length, 2);
  assert.equal(result.events.some(e => e.kind === 'iteration'), false);
});

test('first group request crossing deadline cannot send or complete its second step', () => {
  const result = execute({ steps: [step('/one'), step('/two')], groups: [twoStepGroup],
    responses: [{ delay: 1600 }], extraIterations: 1 });
  assert.deepEqual(result.calls.map(c => c.path), ['/one']);
  assert.equal(result.events.filter(e => e.kind === 'group_started').length, 1);
  assert.equal(result.events.some(e => e.kind === 'group_completed'), false);
  assert.equal(result.events.find(e => e.kind === 'request').ok, true);
});

test('failed HTTP group keeps its failed terminal before final think crosses deadline', () => {
  const result = execute({
    steps: [step('/one'), step('/two', { think_time: { type: 'FIXED', min: 600 } })],
    groups: [twoStepGroup], responses: [{ delay: 200 }, { delay: 300, status: 500 }],
  });
  assert.deepEqual(result.calls.map(c => c.path), ['/one', '/two']);
  const terminals = result.events.filter(e => e.kind === 'group_completed');
  assert.equal(terminals.length, 1);
  assert.equal(terminals[0].ok, false);
  assert.equal(terminals[0].executed_steps, 2);
  assert.equal(result.events.filter(e => e.kind === 'request' && !e.ok).length, 1);
});

test('final group think time still delays admission of the next group', () => {
  const result = execute({
    steps: [step('/one'), step('/two', { think_time: { type: 'FIXED', min: 200 } }), step('/next')],
    groups: [twoStepGroup, nextGroup], responses: [{ delay: 200 }, { delay: 100 }],
  });
  assert.deepEqual(result.calls.map(c => [c.path, c.start]), [['/one', 100000], ['/two', 100200], ['/next', 100500]]);
  assert.deepEqual(result.sleeps, [0.2]);
  assert.equal(result.events.filter(e => e.kind === 'group_completed').length, 1);
  assert.equal(result.events.filter(e => e.kind === 'iteration').length, 1);
});

async function mixedDeadline({ preparation, rounds = 0 } = {}) {
  let clock = 100900;
  const events = [], httpCalls = [], admissions = [];
  const socket = step('/socket', { protocol: 'WEBSOCKET', headers: { 'X-Fixture': '{{slow}}' },
    websocket_config: { auth: {}, commands: [] } });
  const config = {
    load_config: { concurrency: 1, duration: 1, iterations_per_vu: rounds },
    runtime_config: { timeout: 3, auth_profile: { mode: 'STATIC', transport: 'BEARER',
      access_token_variable: 'token', refresh_token_variable: 'refresh' } },
    env_config: { base_url: 'http://fixture.invalid' },
    steps: [step('/refresh', { is_setup: true, auth_phase: 'refresh', auth_input_variables: ['refresh'], extractors }), socket, step('/after')],
    variables: [{ name: 'token', value: 'fixture-token' }, { name: 'refresh', value: 'fixture-refresh' }, { name: 'slow', value: 'ok' }],
  };
  class Jar { set() {} cookiesForURL() { return {}; } }
  const sandbox = {
    __ENV: { K6_TESTHUB_CONFIG: 'config' }, exec: { vu: { idInTest: 1, iterationInScenario: 0 }, scenario: { startTime: 100000 } },
    open: () => JSON.stringify(config), Date: { now: () => clock }, sleep: seconds => { clock += seconds * 1000; },
    WebSocket: class {}, SharedArray: function (_name, load) { return load(); },
    console: { log: line => events.push(JSON.parse(line.slice('TESTHUB_K6_EVENT '.length))) },
    slow: { toString: () => { clock += 500; return 'ok'; } },
    http: { CookieJar: Jar, request: (_method, url) => {
      httpCalls.push(new URL(url).pathname); clock += 500;
      return { status: 200, json: () => ({ token: 'fixture-token', refresh: 'fixture-refresh' }) };
    } },
    runWebSocketSession: async (_config, deps) => {
      admissions.push({ load: deps.remaining(), drain: deps.drainRemaining?.() });
      clock += 500;
      return true;
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(source + '\ninitialize(true); setupPassed = true;', sandbox);
  if (preparation === 'refresh') vm.runInContext('expiresAt = Date.now();', sandbox);
  if (preparation === 'render') vm.runInContext('context.slow = slow;', sandbox);
  await vm.runInContext('mixedIteration()', sandbox);
  assert.equal(events.some(event => event.kind === 'runtime_error' || event.kind === 'auth_failed'), false);
  return { httpCalls, events, admissions };
}

test('shipped mixed caller supplies global drain only to timed sessions and never to later HTTP', async () => {
  const timed = await mixedDeadline();
  assert.deepEqual(timed.admissions, [{ load: 100, drain: 8100 }]);
  assert.deepEqual(timed.httpCalls, []);
  assert.equal(timed.events.filter(event => event.kind === 'load_expired').length, 1);
  const fixed = await mixedDeadline({ rounds: 1 });
  assert.deepEqual(fixed.admissions, [{ load: 100, drain: undefined }]);
  assert.deepEqual(fixed.httpCalls, []);
});

test('refresh or header rendering crossing cutoff creates no attempted WebSocket session', async () => {
  for (const preparation of ['refresh', 'render']) {
    const result = await mixedDeadline({ preparation });
    assert.equal(result.admissions.length, 0);
    assert.deepEqual(result.httpCalls, preparation === 'refresh' ? ['/refresh'] : []);
    assert.equal(result.events.filter(event => event.kind === 'load_expired').length, 1);
    assert.equal(result.events.filter(event => event.kind === 'request_started' && event.step === 1).length, 0);
  }
});
