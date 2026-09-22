"""k6 adapter contract tests; optional binary tests use a local controlled server."""
import csv
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
TEST_ROOT = Path(os.environ.get('K6_ADAPTER_TEST_ROOT', ROOT.parent / 'runtime' / 'adapter-tests'))
TEST_ROOT.mkdir(parents=True, exist_ok=True)


class TestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            # k6 closes idle keep-alive sockets when its process exits.
            pass


def snapshot(users=10, rounds=10):
    return {
        'load_config': {'model': 'CONCURRENCY', 'concurrency': users,
                        'duration': 30, 'iterations_per_vu': rounds},
        'env_config': {'base_url': 'http://127.0.0.1:1'},
        'runtime_config': {'timeout': 3, 'follow_redirects': False},
        'variables': [
            {'name': 'username', 'type': 'CSV', 'data_file_id': 1, 'column': 'username'},
            {'name': 'password', 'type': 'CSV', 'data_file_id': 1, 'column': 'password'},
        ],
        'csv_data': {'1': {'rows': [
            {'username': f'user-{i}', 'password': 'quote"slash\\中文'} for i in range(10)
        ]}},
        'steps': [
            {'id': 1, 'name': '登录', 'is_setup': True, 'method': 'POST', 'url': '/login',
             'body_type': 'JSON', 'body': '{"username":"{{username}}","password":"${password}"}',
             'assertions': [{'type': 'STATUS_CODE', 'expected': '200'}],
             'extractors': [{'name': 'token', 'type': 'JSON_PATH', 'expr': '$.data.token'}]},
            {'id': 2, 'name': '业务一', 'method': 'GET', 'url': '/one',
             'headers': {'Authorization': 'Bearer {{token}}'},
             'assertions': [{'type': 'STATUS_CODE', 'expected': '200'},
                            {'type': 'JSON_PATH', 'json_path': '$.code', 'expected': '0'}]},
            {'id': 3, 'name': '业务二', 'method': 'GET', 'url': '/two',
             'headers': {'Authorization': 'Bearer ${token}'},
             'assertions': [{'type': 'JSON_PATH', 'expr': '$.code', 'expected': 0}]},
        ],
    }


class AdapterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('apps.perf_testing.engines.k6_engine'):
            from apps.perf_testing.engines import k6_engine
            cls.module = k6_engine
        else:
            cls.module = None

    def adapter(self):
        self.assertIsNotNone(self.module, 'k6 adapter must exist')
        return self.module

    def test_valid_login_snapshot(self):
        self.assertEqual(self.adapter().validate_snapshot(snapshot()), [])

    def test_runtime_payload_omits_catalog_provenance_without_changing_frozen_evidence(self):
        from copy import deepcopy
        m = self.adapter()
        original = snapshot(users=1, rounds=1)
        for step in original['steps']:
            step['source_metadata'] = {'schema': 'large-contract-' * 10000}
            step['preparation'] = {'confirmed_fields': ['body'], 'body_reviewed': True}
        before = deepcopy(original)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}), \
                mock.patch.object(m, 'is_available', return_value=True):
            engine = m.K6Engine(original, work_dir=work)
            engine.prepare()
            payload = json.loads((Path(work) / 'scenario.private.json').read_text(encoding='utf-8'))
            self.assertEqual(original, before)
            self.assertEqual(engine.steps, before['steps'])
            self.assertEqual(payload['steps'], [
                {key: value for key, value in step.items() if key not in ('source_metadata', 'preparation')}
                for step in before['steps']])
            self.assertLess((Path(work) / 'scenario.private.json').stat().st_size, 10000)

    def test_account_exhaustion_and_duplicate_are_blocked(self):
        m = self.adapter()
        self.assertTrue(any('账号' in x for x in m.validate_snapshot(snapshot(users=11))))
        s = snapshot()
        s['csv_data']['1']['rows'][1]['username'] = 'user-0'
        self.assertTrue(any('重复' in x for x in m.validate_snapshot(s)))

    def test_unsupported_rules_load_and_weights_are_blocked(self):
        m = self.adapter()
        variants = []
        for field, value in [('model', 'RPS'), ('ramp_up', 3), ('max_requests', 1)]:
            s = snapshot(); s['load_config'][field] = value; variants.append(s)
        s = snapshot(); s['steps'][1]['weight'] = 3; variants.append(s)
        s = snapshot(); s['steps'][1]['assertions'] = [{'type': 'REGEX', 'expected': '.*'}]; variants.append(s)
        s = snapshot(); s['steps'][0]['extractors'][0]['expr'] = '$.data[*].token'; variants.append(s)
        s = snapshot(); s['steps'][1]['assertions'][1]['operator'] = 'contains'; variants.append(s)
        s = snapshot(); s['variables'][0]['type'] = 'RANDOM_INT'; variants.append(s)
        for s in variants:
            with self.subTest(config=s['load_config']):
                self.assertTrue(m.validate_snapshot(s))

    def test_unknown_variable_and_invalid_json_blocked(self):
        m = self.adapter()
        s = snapshot(); s['steps'][1]['headers']['X'] = '{{missing}}'
        self.assertTrue(m.validate_snapshot(s))

    def test_json_path_expected_objects_and_arrays_are_rejected(self):
        for value in ({}, {'code': 'OK'}, [], ['OK']):
            for path_field in ('expr', 'json_path'):
                with self.subTest(value=value, path_field=path_field):
                    s = snapshot()
                    s['steps'][1]['assertions'] = [{'type': 'JSON_PATH', path_field: '$.data', 'expected': value}]
                    self.assertTrue(any('标量' in error for error in self.adapter().validate_snapshot(s)))

    def test_contains_accepts_only_nonempty_text_expected(self):
        for value in ('<html', '标题', ' text with spaces '):
            with self.subTest(value=value):
                s = snapshot()
                s['steps'][1]['assertions'] = [{'type': 'CONTAINS', 'expected': value}]
                self.assertEqual(self.adapter().validate_snapshot(s), [])
        for value in (None, '', ' \n\t', False, 0, [], {}):
            with self.subTest(value=value):
                s = snapshot()
                s['steps'][1]['assertions'] = [{'type': 'CONTAINS', 'expected': value}]
                self.assertTrue(any('非空字符串' in error for error in self.adapter().validate_snapshot(s)))

    def test_json_path_scalar_and_missing_expected_keep_existing_contract(self):
        for expected in ({}, *({'expected': value} for value in (None, False, True, 0, 1.5, '', 'OK'))):
            with self.subTest(expected=expected):
                s = snapshot()
                s['steps'][1]['assertions'] = [{'type': 'JSON_PATH', 'expr': '$.code', **expected}]
                self.assertEqual(self.adapter().validate_snapshot(s), [])

    def test_step_headers_override_environment_case_insensitively(self):
        s = snapshot()
        s['env_config']['headers'] = {'AUTHORIZATION': 'Bearer {{unused_global_token}}'}
        s['steps'][0]['headers'] = {'authorization': ''}
        self.assertEqual(self.adapter().validate_snapshot(s), [])

    def test_invalid_runtime_values_are_validation_errors(self):
        m = self.adapter()
        s = snapshot(); s['runtime_config']['worker_processes'] = 'bad'
        self.assertTrue(m.validate_snapshot(s))
        s = snapshot(); s['steps'][1]['think_time'] = {'type': 'FIXED', 'min': 'bad'}
        self.assertTrue(m.validate_snapshot(s))

    def test_extractors_cannot_overwrite_identity_or_builtin_variables(self):
        m = self.adapter()
        for name in ('username', 'vu_id', 'iteration'):
            s = snapshot(); s['steps'][0]['extractors'].append({'name': name, 'type': 'JSON_PATH', 'expr': '$.data.token'})
            self.assertTrue(m.validate_snapshot(s))
        s = snapshot(); s['steps'][0]['body'] = '{"username": {{username}}}'
        self.assertTrue(m.validate_snapshot(s))

    def test_request_count_and_histogram_semantics(self):
        m = self.adapter()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            e = m.K6Engine(snapshot(), work_dir=work, raw_csv_path=str(Path(work) / 'raw.csv.gz'))
            e._start_ts = time.monotonic() - 10
            e._open_raw_writer()
            for i in range(210):
                e._consume_event({'kind': 'request_started', 'step': 0 if i < 10 else 1, 'vu': i % 10 + 1})
                e._consume_event({'kind': 'request', 'step': 0 if i < 10 else 1,
                    'elapsed_ms': 1 if i < 200 else 1000, 'status': 200,
                    'ok': i < 200, 'error': '' if i < 200 else 'AssertionFailed',
                    'timestamp_ms': 1000 + i, 'vu': i % 10 + 1})
            for _ in range(100):
                e._consume_event({'kind': 'iteration', 'vu': 1})
            e._close_raw_writer()
            result = e.collect(); summary = result['summary']
            self.assertEqual(summary['http_total'], 210)
            self.assertEqual(summary['business_total'], 200)
            self.assertEqual(summary['http_started'], 210)
            self.assertEqual(summary['business_started'], 200)
            self.assertEqual(summary['http_incomplete'], 0)
            self.assertEqual(summary['business_incomplete'], 0)
            self.assertEqual(summary['failed_requests'], 10)
            self.assertEqual(summary['error_rate'], 5)
            self.assertEqual(summary['completed_iterations'], 100)
            self.assertGreater(summary['p99_rt'], 500)
            with gzip.open(e.raw_csv_path, 'rt', encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 210)
            self.assertEqual(sum(int(r['is_setup']) for r in rows), 10)
            self.assertNotIn('quote', json.dumps(result))

    def test_stop_terminates_process(self):
        m = self.adapter()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            e = m.K6Engine(snapshot(), work_dir=work)
            process = mock.Mock(); process.poll.return_value = None
            e.process = process
            e.stop()
            process.terminate.assert_called_once()
            self.assertTrue(e._stopping)

    def test_cpu_sampling_distinguishes_unknown_from_measured_peak(self):
        m = self.adapter()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            samples = []
            e = m.K6Engine(snapshot(), work_dir=work, on_sample=samples.append)
            initial = e.collect()['summary']
            self.assertIsNone(initial['peak_load_gen_cpu'])
            self.assertEqual(initial['cpu_sample_count'], 0)
            e._start_ts = time.monotonic() - 2
            probe = mock.Mock()
            probe.cpu_percent.side_effect = [4.7, 2.0, OSError('process exited')]
            probe.memory_info.return_value = mock.Mock(rss=1024 * 1024)
            e._proc_probe = probe
            for _ in range(3): e._emit_sample(force=True)
            summary = e.collect()['summary']
            self.assertEqual(summary['peak_load_gen_cpu'], 4.7)
            self.assertEqual(summary['cpu_sample_count'], 2)
            self.assertEqual([s['cpu_sampled'] for s in samples], [True, True, False])
            self.assertIsNone(samples[-1]['cpu_percent'])
            self.assertFalse(summary['load_generator_capacity_verified'])
            self.assertIsNone(summary['data_trustworthy'])

    def test_transport_codes_are_safe_and_aggregated_without_changing_old_csv_columns(self):
        m = self.adapter()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            e = m.K6Engine(snapshot(), work_dir=work, raw_csv_path=str(Path(work) / 'raw.csv.gz'))
            e._open_raw_writer()
            codes = [1212, 1212, 1220, 1211, 1050, 1202]
            for code in codes:
                e._consume_event({'kind': 'request', 'step': 1, 'vu': 1, 'status': 0,
                    'ok': False, 'elapsed_ms': 2, 'error': 'TransportError', 'error_code': code,
                    'error_category': 'untrusted-secret-token-injected', 'message': 'untrusted-secret-token'})
            e._consume_event({'kind': 'request', 'step': 1, 'vu': 1, 'status': 0,
                'ok': False, 'elapsed_ms': 2, 'error': 'TransportError',
                'error_code': 'untrusted-secret-token'})
            e._close_raw_writer()
            summary = e.collect()['summary']
            groups = summary['request_error_groups']
            refused = [g for g in groups if g['error_code'] == 1212]
            self.assertEqual(refused, [{'error_code': 1212, 'category': 'connection_refused',
                'status_code': 0, 'count': 2, 'business_count': 2, 'setup_count': 0}])
            self.assertEqual({g['category'] for g in groups},
                             {'connection_refused', 'connection_reset', 'connect_timeout',
                              'request_timeout', 'transport_error'})
            self.assertEqual(summary['failed_requests'], 7)
            self.assertTrue(any(g['type'] == 'ConnectionRefused' and g['count'] == 2 for g in summary['error_top']))
            with gzip.open(e.raw_csv_path, 'rt', encoding='utf-8') as f:
                reader = csv.DictReader(f); fields = reader.fieldnames; rows = list(reader)
            self.assertEqual(fields[:12], ['timestamp_ms', 'elapsed_ms', 'step', 'method', 'url',
                'status_code', 'success', 'sent_bytes', 'recv_bytes', 'error', 'is_setup', 'vu_id'])
            self.assertEqual(fields[12:], ['error_code', 'error_category'])
            self.assertEqual(rows[0]['error'], 'TransportError')
            self.assertEqual(rows[0]['error_code'], '1212')
            self.assertEqual(rows[0]['error_category'], 'connection_refused')
            self.assertNotIn('untrusted-secret', json.dumps(summary) + json.dumps(rows))


class RealK6Tests(unittest.TestCase):
    """Enable with K6_BIN; target is generated in this test, never a business server."""
    setUpClass = classmethod(AdapterContractTests.setUpClass.__func__)
    adapter = AdapterContractTests.adapter
    def setUp(self):
        if not self.module or not self.module.is_available():
            self.skipTest('K6_BIN is not available')

    def make_server(self, login_status=200, require_auth=False):
        calls = []
        class Handler(TestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_): pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                calls.append('login'); self.reply(login_status)
            def do_GET(self):
                calls.append(self.path)
                auth = self.headers.get('Authorization')
                authorized = auth == require_auth if isinstance(require_auth, str) else bool(auth)
                self.reply(401 if require_auth and not authorized else 200)
            def reply(self, status):
                data = b'{"code":0,"data":{"token":"private-test-token"}}'
                self.send_response(status); self.send_header('Content-Length', len(data))
                self.end_headers(); self.wfile.write(data)
        class TestServer(ThreadingHTTPServer): request_queue_size = 128
        server = TestServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}', calls

    def test_prepared_account_pools_are_frozen_separate_private_files(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            s = snapshot(users=10)
            original = json.loads(json.dumps(s))
            e = self.module.K6Engine(s, work_dir=work); e.prepare()
            config = json.loads((Path(work) / 'scenario.private.json').read_text(encoding='utf-8'))
            self.assertNotIn('csv_data', config)
            self.assertEqual(set(config['csv_files']), {'1'})
            pool = Path(config['csv_files']['1'])
            self.assertEqual(pool.parent.resolve(), Path(work).resolve())
            self.assertEqual(json.loads(pool.read_text(encoding='utf-8')), original['csv_data']['1']['rows'])
            self.assertEqual(s, original)
            s['csv_data']['1']['rows'][0]['username'] = 'changed-after-prepare'
            self.assertEqual(json.loads(pool.read_text(encoding='utf-8'))[0]['username'], 'user-0')
            self.assertNotIn('quote', (Path(work) / 'scenario.private.json').read_text(encoding='utf-8'))

    @unittest.skipUnless(shutil.which('node'), 'Node needed for no-network row-access audit')
    def test_thousand_vus_read_only_their_own_row_once_per_pool(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            s = snapshot(users=1000, rounds=1)
            s['csv_data'] = {
                '1': {'rows': [{'username': f'user-{i}', 'password': f'password-{i}'} for i in range(1000)]},
                '2': {'rows': [{'resource': i + 100} for i in range(1000)]},
            }
            s['variables'].append({'name': 'resource', 'type': 'CSV', 'data_file_id': 2, 'column': 'resource'})
            s['steps'] = [{'name': '验证行', 'method': 'POST', 'url': '/row', 'body_type': 'JSON',
                'body': '{"username":"{{username}}","password":"{{password}}","resource":"{{resource}}"}'}]
            e = self.module.K6Engine(s, work_dir=work); e.prepare()
            harness = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8')
  .replace(/^import .+;\r?\n/gm, '')
  .replace('export const options', 'const options')
  .replace('export default function ()', 'function runIteration()')
  .replace('export function handleSummary()', 'function handleSummary()');
const compiled = new vm.Script(source + '\nrunIteration(); runIteration();');
const shared = new Map(); let rowReads = 0, requests = 0;
for (let id = 1; id <= 1000; id++) {
  const accessed = new Map();
  function SharedArray(name, build) {
    if (!shared.has(name)) shared.set(name, build());
    return new Proxy(shared.get(name), {get(target, property) {
      if (/^\d+$/.test(String(property))) {
        assert.equal(Number(property), id - 1, 'VU touched another account row');
        accessed.set(name, (accessed.get(name) || 0) + 1); rowReads++;
      }
      return Reflect.get(target, property);
    }});
  }
  const context = vm.createContext({ SharedArray, __ENV: {K6_TESTHUB_CONFIG: process.argv[2]},
    open: path => fs.readFileSync(path, 'utf8'), exec: {vu: {idInTest:id, iterationInScenario:0}},
    sleep(){}, console:{log(){}}, http:{request(method,url,body) {
      const parsed = JSON.parse(body);
      assert.equal(parsed.username, 'user-' + (id - 1));
      assert.equal(parsed.password, 'password-' + (id - 1));
      assert.equal(parsed.resource, id + 99); requests++;
      return {status:200, error_code:0, json(){return {code:0};}};
    }} });
  compiled.runInContext(context);
  assert.equal(accessed.size, 2, 'Both pools must be referenced');
  for (const count of accessed.values()) assert.equal(count, 1, 'Multiple columns must share one fetched row');
}
assert.equal(shared.size, 2); assert.equal(rowReads, 2000); assert.equal(requests, 2000);
process.stdout.write(JSON.stringify({vus:1000,pools:shared.size,rowReads,requests}));
'''
            result = subprocess.run(['node', '-e', harness, str(Path(work) / 'scenario.js'),
                str(Path(work) / 'scenario.private.json')], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'vus': 1000, 'pools': 2, 'rowReads': 2000, 'requests': 2000})

    def test_failed_login_never_sends_business_requests(self):
        url, calls = self.make_server(login_status=401)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            s = snapshot(users=1, rounds=3); s['env_config']['base_url'] = url
            e = self.module.K6Engine(s, work_dir=work)
            e.prepare()
            with self.assertRaises(self.module.EngineError): e.run()
            self.assertEqual(calls, ['login'])
            self.assertEqual(e.collect()['summary']['http_total'], 1)
            self.assertEqual(e.collect()['summary']['http_started'], 1)
            self.assertEqual(e.collect()['summary']['business_total'], 0)
            self.assertEqual(e.collect()['summary']['business_started'], 0)
            self.assertEqual(e.collect()['summary']['setup_failed_vus'], 1)

    def test_preset_token_and_real_stop_preserve_completed_requests(self):
        url, calls = self.make_server()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            s = snapshot(users=1, rounds=0); s['env_config']['base_url'] = url
            s['steps'] = s['steps'][1:]
            s['steps'][0]['think_time'] = {'type': 'FIXED', 'min': 10}
            s['variables'] = [{'name': 'token', 'type': 'CSV', 'data_file_id': 1, 'column': 'token'}]
            s['csv_data'] = {'1': {'rows': [{'token': 'private-preset-token'}]}}
            samples = []; errors = []
            e = self.module.K6Engine(s, work_dir=work, on_sample=samples.append)
            e.prepare()
            def run():
                try: e.run()
                except Exception as exc: errors.append(type(exc).__name__)
            thread = threading.Thread(target=run); thread.start()
            deadline = time.monotonic() + 8
            while e._http_total < 4 and time.monotonic() < deadline: time.sleep(0.02)
            e.stop(); thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertIsNotNone(e.process.poll())
            self.assertFalse(errors)
            self.assertNotIn('login', calls)
            self.assertGreater(e.collect()['summary']['business_total'], 0)
            self.assertTrue(samples)

    def test_global_headers_and_web_millisecond_think_time(self):
        url, calls = self.make_server(require_auth=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            s = snapshot(users=1, rounds=1)
            s['load_config']['duration'] = 2
            s['env_config'] = {'base_url': url, 'headers': {'Authorization': 'Bearer {{token}}'}}
            s['steps'] = s['steps'][1:]
            for step in s['steps']:
                step['headers'] = {}
                step['think_time'] = {'type': 'FIXED', 'min': 30}
            s['variables'] = [{'name': 'token', 'type': 'CSV', 'data_file_id': 1, 'column': 'token'}]
            s['csv_data'] = {'1': {'rows': [{'token': 'private-preset-token'}]}}
            e = self.module.K6Engine(s, work_dir=work); e.prepare(); e.run()
            self.assertEqual(e.collect()['summary']['failed_requests'], 0)
            self.assertEqual(len(calls), 2)
            self.assertLess(e.collect()['duration'], 2)
            self.assertGreater(e.collect()['duration'], 0.05)

    def test_lowercase_step_auth_replaces_global_identity(self):
        url, _ = self.make_server(require_auth='Bearer private-preset-token')
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            s = snapshot(users=1, rounds=1)
            s['env_config'] = {'base_url': url, 'headers': {'Authorization': 'Bearer wrong-identity'}}
            s['steps'] = [{'name': '验证身份', 'url': '/identity', 'method': 'GET',
                           'headers': {'authorization': 'Bearer {{token}}'}}]
            s['variables'] = [{'name': 'token', 'type': 'CSV', 'data_file_id': 1, 'column': 'token'}]
            s['csv_data'] = {'1': {'rows': [{'token': 'private-preset-token'}]}}
            e = self.module.K6Engine(s, work_dir=work); e.prepare(); e.run()
            self.assertEqual(e.collect()['summary']['failed_requests'], 0)

    def test_child_abnormal_exit_preserves_partial_report(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            e = self.module.K6Engine(snapshot(users=1), work_dir=work)
            e.prepare()
            popen = subprocess.Popen
            emitted = {'kind': 'request', 'step': 1, 'vu': 1, 'status': 200,
                       'ok': True, 'elapsed_ms': 12, 'timestamp_ms': 100}
            code = 'import sys; print(' + repr(self.module.EVENT_PREFIX + json.dumps(emitted)) + ', flush=True); sys.exit(7)'
            def child(*args, **kwargs):
                return popen([sys.executable, '-c', code], **kwargs)
            probe = mock.Mock()
            probe.cpu_percent.side_effect = [99.9, 4.7]
            probe.memory_info.return_value = mock.Mock(rss=1024 * 1024)
            with mock.patch.object(self.module.subprocess, 'Popen', side_effect=child), \
                    mock.patch('psutil.Process', return_value=probe):
                with self.assertRaises(self.module.EngineError): e.run()
            summary = e.collect()['summary']
            self.assertEqual(summary['business_total'], 1)
            self.assertEqual(summary['k6_exit_code'], 7)
            self.assertEqual(summary['cpu_sample_count'], 1)
            self.assertEqual(summary['peak_load_gen_cpu'], 4.7)

    def test_no_business_samples_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            e = self.module.K6Engine(snapshot(users=1, rounds=0), work_dir=work)
            e.prepare(); popen = subprocess.Popen
            def child(*args, **kwargs): return popen([sys.executable, '-c', 'pass'], **kwargs)
            with mock.patch.object(self.module.subprocess, 'Popen', side_effect=child):
                with self.assertRaises(self.module.EngineError): e.run()
            self.assertEqual(e.collect()['summary']['business_total'], 0)

    def test_round_max_duration_cutoff_distinguishes_started_from_completed(self):
        calls = []
        class Handler(TestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                calls.append('started')
                time.sleep(2)
                data = b'{"code":0}'
                self.send_response(200); self.send_header('Content-Length', len(data))
                self.end_headers(); self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            s = snapshot(users=1, rounds=1)
            s['load_config']['duration'] = 1
            s['env_config']['base_url'] = f'http://127.0.0.1:{server.server_port}'
            s['steps'] = [{'name': '慢请求', 'url': '/slow', 'method': 'GET'}]
            e = self.module.K6Engine(s, work_dir=work); e.prepare()
            with self.assertRaises(self.module.EngineError): e.run()
            summary = e.collect()['summary']
            self.assertEqual(calls, ['started'], summary)
            self.assertEqual(summary['http_started'], 1)
            self.assertEqual(summary['business_started'], 1)
            self.assertEqual(summary['http_total'], 0)
            self.assertEqual(summary['business_total'], 0)
            self.assertEqual(summary['http_incomplete'], 1)
            self.assertEqual(summary['business_incomplete'], 1)

    def test_failed_extractor_request_cannot_reuse_previous_resource(self):
        calls = []; count = [0]
        class Handler(TestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_): pass
            def do_GET(self):
                calls.append(self.path)
                if self.path == '/pick':
                    count[0] += 1
                    data = {'code': 0 if count[0] == 1 else 1, 'data': {'items': [{'id': 123}]}}
                else: data = {'code': 0}
                raw = json.dumps(data).encode(); self.send_response(200)
                self.send_header('Content-Length', len(raw)); self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            s = snapshot(users=1, rounds=2)
            s['env_config']['base_url'] = f'http://127.0.0.1:{server.server_port}'
            s['steps'] = [
                {'name': '选择资源', 'url': '/pick', 'method': 'GET',
                 'assertions': [{'type': 'JSON_PATH', 'expr': '$.code', 'expected': 0}],
                 'extractors': [{'name': 'resource_id', 'type': 'JSON_PATH', 'expr': '$.data.items[0].id'}]},
                {'name': '打开资源', 'url': '/detail/{{resource_id}}', 'method': 'GET'},
            ]
            e = self.module.K6Engine(s, work_dir=work); e.prepare()
            with self.assertRaises(self.module.EngineError): e.run()
            self.assertEqual(calls, ['/pick', '/detail/123', '/pick'])
            self.assertEqual(e.collect()['summary']['business_total'], 3)
            self.assertEqual(e.collect()['summary']['business_started'], 3)
            self.assertEqual(e.collect()['summary']['business_incomplete'], 0)

    def test_real_http_counts_identity_json_escaping_and_safe_artifacts(self):
        m = self.adapter()
        calls = []; lock = threading.Lock(); seen = {}

        class Handler(TestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with lock: calls.append(('login', body['username'], body['password']))
                self.reply({'data': {'token': 'private-' + body['username']}})

            def do_GET(self):
                identity = self.headers.get('Authorization', '')
                with lock:
                    calls.append((self.path, identity))
                    first = self.path == '/one' and not seen.get(identity)
                    if self.path == '/one': seen[identity] = True
                self.reply({'code': 1 if first else 0})

            def reply(self, body):
                data = json.dumps(body).encode(); self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(data)); self.end_headers(); self.wfile.write(data)

        class TestServer(ThreadingHTTPServer):
            request_queue_size = 128
        server = TestServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
                s = snapshot(); s['env_config']['base_url'] = f'http://127.0.0.1:{server.server_port}'
                samples = []; logs = []
                e = m.K6Engine(s, work_dir=work, raw_csv_path=str(Path(work) / 'raw.csv.gz'),
                               on_sample=samples.append, on_log=lambda *x: logs.append(x))
                events = []; consume = e._consume_event
                def capture(event): events.append(event); consume(event)
                e._consume_event = capture
                e.prepare()
                try: e.run()
                except m.EngineError:
                    self.fail(json.dumps([x for x in events if x.get('kind') == 'request' and not x.get('ok')]))
                result = e.collect(); summary = result['summary']
                self.assertEqual(summary['http_total'], 210)
                self.assertEqual(summary['business_total'], 200)
                self.assertEqual(summary['http_started'], 210)
                self.assertEqual(summary['business_started'], 200)
                self.assertEqual(summary['http_incomplete'], 0)
                self.assertEqual(summary['business_incomplete'], 0)
                self.assertEqual(summary['error_rate'], 5, result['request_stats'])
                self.assertEqual(summary['completed_iterations'], 100)
                self.assertEqual(summary['max_concurrency'], 10)
                self.assertTrue(samples)
                self.assertEqual(len(calls), 210)
                self.assertEqual(len([x for x in calls if x[0] == 'login']), 10)
                for i in range(10):
                    self.assertEqual(sum(x[1] == f'Bearer private-user-{i}' for x in calls), 20)
                self.assertTrue(all(x[2] == 'quote"slash\\中文' for x in calls if x[0] == 'login'))
                with gzip.open(e.raw_csv_path, 'rt', encoding='utf-8') as f: raw = f.read()
                self.assertNotIn('private-user', raw + json.dumps(result) + json.dumps(logs))
        finally:
            server.shutdown(); server.server_close(); worker.join(3)


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object test')
class WindowsJobTests(unittest.TestCase):
    def test_worker_termination_kills_real_k6_child(self):
        self.assertIsNotNone(importlib.util.find_spec('apps.perf_testing.engines.k6_process'),
                             'Windows process ownership guard must exist')
        from apps.perf_testing.engines.k6_engine import _binary, is_available
        if not is_available(): self.skipTest('K6_BIN is not available')
        import psutil
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            work = Path(work)
            (work / 'idle.js').write_text("import {sleep} from 'k6'; export default function(){sleep(60);}", encoding='utf-8')
            worker_code = '''
import os, subprocess, sys, time
from pathlib import Path
from apps.perf_testing.engines.k6_process import WindowsJob
guard = WindowsJob()
env = {k:v for k,v in os.environ.items() if not k.upper().startswith('K6_')}
env['K6_NO_USAGE_REPORT'] = 'true'
child = subprocess.Popen([sys.argv[1], 'run', '--quiet', sys.argv[2]],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    creationflags=guard.creation_flags)
guard.attach_and_resume(child)
Path(sys.argv[3]).write_text(str(child.pid), encoding='ascii')
time.sleep(60)
'''
            worker = subprocess.Popen([sys.executable, '-c', worker_code, _binary(),
                                       str(work / 'idle.js'), str(work / 'pid')],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            child = None
            try:
                deadline = time.monotonic() + 10
                while not (work / 'pid').exists() and worker.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((work / 'pid').exists(), 'Worker could not establish child ownership')
                child = psutil.Process(int((work / 'pid').read_text(encoding='ascii')))
                self.assertTrue(child.is_running())
                worker.kill(); worker.wait(timeout=5)
                child.wait(timeout=5)
                self.assertFalse(child.is_running())
            finally:
                if worker.poll() is None: worker.kill(); worker.wait(timeout=5)
                if child and child.is_running(): child.kill(); child.wait(timeout=5)
                worker.stdout.close(); worker.stderr.close()


if __name__ == '__main__':
    unittest.main()
