"""Run the shipped K6 JavaScript in isolated Node VMs without network access."""
import json
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless

from django.test import SimpleTestCase, tag

from apps.perf_testing.engines.k6_engine import K6Engine, validate_snapshot

NODE = shutil.which('node') or ('C:/Program Files/nodejs/node.exe'
                              if Path('C:/Program Files/nodejs/node.exe').is_file() else None)


def dependency_snapshot(*, setup=True):
    rules = [{'type': 'STATUS_CODE', 'expected': 200},
             {'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK', 'operator': 'eq'}]
    return {
        'load_config': {'model': 'CONCURRENCY', 'concurrency': 2, 'duration': 30,
                        'iterations_per_vu': 2},
        'env_config': {'base_url': 'http://fixture'},
        'runtime_config': {'auth_profile': {'mode': 'STATIC', 'transport': 'BEARER',
                                          'access_token_variable': 'token'}},
        'variables': [{'name': name, 'type': 'CSV', 'data_file_id': 1, 'column': name}
                      for name in ('user_id', 'token')],
        'csv_data': {'1': {'rows': [{'user_id': f'u{vu}', 'token': f'FAKE_SECRET_{vu}'}
                                  for vu in (1, 2)]}},
        'steps': [
            {'id': 216, 'name': 'Open own conversation', 'enabled': True, 'is_setup': setup,
             'method': 'POST', 'url': '/api/v1/support/conversations/demo',
             'headers': {'Authorization': 'Bearer {{token}}', 'Idempotency-Key': '{{request_id}}'},
             'body_type': 'NONE', 'body': '', 'params': {},
             'assertions': rules + [{'type': 'JSON_PATH', 'expr': '$.data.channel',
                                     'expected': 'demo', 'operator': 'eq'}],
             'extractors': [{'name': 'support_conversation_id', 'type': 'JSON_PATH',
                             'expr': '$.data.conversation_id'}]},
            {'id': 220, 'name': 'Read own conversation', 'enabled': True, 'is_setup': False,
             'method': 'GET', 'url': '/api/v1/support/conversations/{{support_conversation_id}}/messages',
             'headers': {'Authorization': 'Bearer {{token}}'}, 'body_type': 'NONE',
             'body': '', 'params': {}, 'assertions': rules, 'extractors': []},
        ],
    }


HARNESS = r"""
const fs = require('fs'), vm = require('vm'), crypto = require('crypto');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.script, 'utf8').replace(/^import .*;$/mg, '')
  .replace('export default function ()', 'function iteration()').replace(/export /g, '');
const requests = [], events = [], vus = [];
const reply = (status, data) => ({status, error_code: 0, json: () => data});
class Jar { set() {} cookiesForURL() { return {}; } }
for (let vu = 1; vu <= 2; vu++) {
  const execution = {vu: {idInTest: vu, iterationInScenario: 0}};
  const sandbox = {
    __ENV: {K6_TESTHUB_CONFIG: 'config'}, exec: execution,
    open: name => JSON.stringify(name === 'config' ? input.config : input.rows),
    SharedArray: function(name, fn) { return fn(); }, sleep: () => {},
    randomBytes: size => Uint8Array.from(crypto.randomBytes(size)).buffer,
    Date: {now: () => 100000},
    console: {log: line => events.push(JSON.parse(line.split('TESTHUB_K6_EVENT ')[1]))},
    http: {CookieJar: Jar, request: (method, url, body, params) => {
      const path = new URL(url).pathname, round = execution.vu.iterationInScenario;
      requests.push({vu, round, method, path, headers: params.headers, body});
      if (params.headers.authorization !== 'Bearer FAKE_SECRET_' + vu) throw Error('crossed identity');
      if (method === 'POST') {
        if (path !== '/api/v1/support/conversations/demo') throw Error('unexpected producer');
        const fail = vu === 1 && (!input.laterFailure || round === 1);
        const data = {code: 'OK', data: {channel: 'demo', conversation_id: 'owned-vu' + vu + '-round' + round}};
        if (fail) {
          if (input.case === 'transport') throw Error('synthetic timeout');
          if (input.case === 'http') return reply(503, data);
          if (input.case === 'assertion') data.code = 'NOT_OK';
          if (input.case === 'missing') delete data.data.conversation_id;
          if (input.case === 'empty') data.data.conversation_id = '';
          if (input.case === 'null') data.data.conversation_id = null;
        }
        return reply(200, data);
      }
      const expectedRound = input.laterFailure ? round : 0;
      if (path !== '/api/v1/support/conversations/owned-vu' + vu + '-round' + expectedRound + '/messages') {
        throw Error('crossed or stale conversation');
      }
      return reply(200, {code: 'OK', data: {items: []}});
    }},
  };
  vm.createContext(sandbox); vm.runInContext(source, sandbox); vus.push({sandbox, execution});
}
for (let round = 0; round < 2; round++) {
  for (const {sandbox, execution} of vus) {
    execution.vu.iterationInScenario = round; vm.runInContext('iteration()', sandbox);
  }
}
process.stdout.write(JSON.stringify({requests, events}));
"""


@tag('k6', 'setup_dependencies', 'offline')
@skipUnless(NODE, 'Node is required for the offline JavaScript sandbox')
class SetupDependencyRuntimeTests(SimpleTestCase):
    def harness(self, case='success', *, later_failure=False):
        snapshot = dependency_snapshot(setup=not later_failure)
        self.assertEqual(validate_snapshot(snapshot), [])
        engine = K6Engine(snapshot)
        config = {**snapshot, 'steps': engine.steps, 'csv_files': {'1': 'rows'},
                  'runtime_config': {'auth_profile': engine.auth_profile}}
        script = Path(__file__).resolve().parents[1] / 'engines' / 'k6_script.js'
        process = subprocess.run([NODE, '-e', HARNESS], input=json.dumps({
            'case': case, 'laterFailure': later_failure, 'config': config,
            'rows': snapshot['csv_data']['1']['rows'], 'script': str(script),
        }), capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        output = json.loads(process.stdout)
        for event in output['events']:
            engine._consume_event(event)
        self.assertNotIn('SECRET', json.dumps(output['events']))
        # A later failed producer removes its output; rendering its consumer fails closed.
        self.assertEqual(engine._runtime_failures, 1 if later_failure else 0)
        return engine, output

    def test_setup_once_per_vu_retains_own_conversation_for_two_rounds(self):
        engine, output = self.harness()
        setup = [row for row in output['requests'] if row['method'] == 'POST']
        business = [row for row in output['requests'] if row['method'] == 'GET']
        self.assertEqual([(row['vu'], row['round']) for row in setup], [(1, 0), (2, 0)])
        self.assertEqual([(row['vu'], row['round']) for row in business],
                         [(1, 0), (2, 0), (1, 1), (2, 1)])
        self.assertEqual(len({row['headers']['idempotency-key'] for row in setup}), 2)
        for row in setup:
            self.assertRegex(row['headers']['idempotency-key'],
                             r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
        for row in business:
            self.assertIn(f"/owned-vu{row['vu']}-round0/", row['path'])
            self.assertEqual(row['headers']['authorization'], f"Bearer FAKE_SECRET_{row['vu']}")
        self.assertEqual(engine._setup_failed_vus, set())
        self.assertEqual(engine.collector.total, 4)
        self.assertTrue(all(event['ok'] for event in output['events'] if event['kind'] == 'request'))

    def test_failed_setup_never_sends_consumers_or_borrows_another_vu_output(self):
        for case, error in [('http', 'HTTPFailed'), ('assertion', 'AssertionFailed'),
                            ('transport', 'TransportError'),
                            ('missing', 'ExtractionFailed'), ('empty', 'ExtractionFailed'),
                            ('null', 'ExtractionFailed')]:
            with self.subTest(case=case):
                engine, output = self.harness(case)
                own = [row for row in output['requests'] if row['vu'] == 1]
                self.assertEqual([(row['method'], row['round']) for row in own], [('POST', 0)])
                self.assertEqual([row['round'] for row in output['requests']
                                  if row['vu'] == 2 and row['method'] == 'GET'], [0, 1])
                failed = [event for event in output['events']
                          if event['kind'] == 'request' and event['vu'] == 1]
                self.assertEqual([(event['ok'], event['error']) for event in failed], [(False, error)])
                self.assertEqual(engine._setup_failed_vus, {1})
                self.assertEqual(engine.collector.total, 2)

    def test_failed_later_producer_clears_previous_iteration_output(self):
        for case in ('http', 'assertion', 'transport', 'missing', 'empty', 'null'):
            with self.subTest(case=case):
                _, output = self.harness(case, later_failure=True)
                business = [row for row in output['requests'] if row['method'] == 'GET']
                self.assertEqual([(row['vu'], row['round']) for row in business],
                                 [(1, 0), (2, 0), (2, 1)])
                self.assertFalse(any(row['vu'] == 1 and row['round'] == 1 for row in business))
                self.assertTrue(all(event['ok'] for event in output['events']
                                    if event['kind'] == 'request' and event['vu'] == 2))
