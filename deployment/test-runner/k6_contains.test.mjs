import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const policySource = readFileSync(new URL('../../testhub/apps/perf_testing/engines/k6_execution_policy.js', import.meta.url), 'utf8').replace(/export /g, '');
const source = policySource + '\n' + readFileSync(new URL('../../testhub/apps/perf_testing/engines/k6_script.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/mg, '')
  .replace('export default function ()', 'function iteration()').replace(/export /g, '');

function execute(body, expected, status = 200) {
  const events = [];
  let requests = 0;
  const config = {
    load_config: { concurrency: 1, iterations_per_vu: 1, _purpose: 'debug' },
    env_config: { base_url: 'http://fixture.invalid' },
    steps: [{ id: 1, method: 'GET', url: '/document', assertions: [
      { type: 'STATUS_CODE', expected: 200 }, { type: 'CONTAINS', expected },
    ] }],
  };
  const sandbox = {
    __ENV: { K6_TESTHUB_CONFIG: 'config' },
    exec: { vu: { idInTest: 1, iterationInScenario: 0 } },
    open: () => JSON.stringify(config), sleep: () => {},
    SharedArray: function (_name, load) { return load(); },
    console: { log: line => events.push(JSON.parse(line.slice('TESTHUB_K6_EVENT '.length))) },
    http: { request: () => {
      requests += 1;
      return { status, body, headers: { 'Content-Type': 'text/html' }, json: () => {
        throw new Error('HTML must not be parsed as JSON');
      } };
    } },
  };
  vm.runInNewContext(source + '\niteration();', sandbox);
  assert.equal(requests, 1);
  assert.equal(JSON.stringify(events).includes('PRIVATE_BODY_SENTINEL'), false);
  return { request: events.find(event => event.kind === 'request'),
    diagnostic: events.find(event => event.kind === 'diagnostic') };
}

test('CONTAINS checks HTML bytes and preserves failure accounting without emitting the body', () => {
  for (const [body, expected, passed] of [
    ['<html>PRIVATE_BODY_SENTINEL</html>', '<html', true],
    ['<html>PRIVATE_BODY_SENTINEL</html>', '<missing', false],
    ['<html>PRIVATE_BODY_SENTINEL</html>', ' <html', false],
    [null, '<html', false],
  ]) {
    const result = execute(body, expected);
    assert.equal(result.request.ok, passed);
    assert.equal(result.diagnostic.assertions[1].result, passed ? 'passed' : 'mismatch');
    assert.equal(result.diagnostic.response.state, 'non_json_omitted');
  }
});

test('CONTAINS rejects invalid expected values and cannot hide HTTP failure', () => {
  for (const expected of [null, '', ' \n', 0, false, [], {}]) {
    const result = execute('<html>PRIVATE_BODY_SENTINEL</html>', expected);
    assert.equal(result.request.ok, false);
    assert.equal(result.diagnostic.assertions[1].result, 'mismatch');
  }
  const result = execute('<html>PRIVATE_BODY_SENTINEL</html>', '<html', 503);
  assert.equal(result.request.ok, false);
  assert.equal(result.diagnostic.outcome, 'http_failed');
});
