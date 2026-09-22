"""Real k6 deadline regressions against an isolated local HTTP server."""
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from . import test_k6_engine as fixtures


class TimedDrainTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AdapterContractTests.setUpClass.__func__)
    setUp = fixtures.RealK6Tests.setUp

    def server(self, delay=1.6, status=200, started=None):
        calls = []

        class Handler(fixtures.TestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                calls.append(self.path)
                if started:
                    started.set()
                if self.path in ('/slow', '/slow/1'):
                    time.sleep(delay)
                data = b'{"code":0}'
                self.send_response(status)
                self.send_header('Content-Length', len(data))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}', calls

    def run_snapshot(self, value):
        with tempfile.TemporaryDirectory(dir=fixtures.TEST_ROOT) as work:
            engine = self.module.K6Engine(value, work_dir=work)
            engine.prepare()
            engine.run()
            return engine.collect()['summary']

    def test_inflight_request_finishes_but_next_step_is_not_started(self):
        base, calls = self.server()
        value = fixtures.snapshot(users=1, rounds=0)
        value['load_config']['duration'] = 1
        value['env_config']['base_url'] = base
        value['steps'] = [
            {'name': 'in flight', 'method': 'GET', 'url': '/slow'},
            {'name': 'must not start', 'method': 'GET', 'url': '/next'},
        ]
        summary = self.run_snapshot(value)
        self.assertEqual(calls, ['/slow'])
        self.assertEqual(summary['http_started'], 1)
        self.assertEqual(summary['http_total'], 1)
        self.assertEqual(summary['business_incomplete'], 0)
        self.assertEqual(summary['failed_requests'], 0)
        self.assertEqual(summary['completed_iterations'], 0)
        self.assertEqual(summary['timed_load']['configured_seconds'], 1)
        self.assertEqual(summary['timed_load']['drain_limit_seconds'], 8)
        self.assertGreater(summary['timed_load']['observed_drain_seconds'], 0)
        self.assertGreater(summary['timed_load']['engine_seconds'], 1.6)
        self.assertIsNone(summary['native_vu']['sustained_concurrency_verified'])

    def test_real_request_timeout_remains_a_failure(self):
        base, calls = self.server(delay=2.5)
        value = fixtures.snapshot(users=1, rounds=0)
        value['load_config']['duration'] = 1
        value['runtime_config']['timeout'] = 1
        value['env_config']['base_url'] = base
        value['steps'] = [{'name': 'timeout', 'method': 'GET', 'url': '/slow'}]
        summary = self.run_snapshot(value)
        self.assertEqual(calls, ['/slow'])
        self.assertEqual(summary['failed_requests'], 1)
        self.assertEqual(summary['business_incomplete'], 0)
        self.assertEqual(summary['request_error_groups'][0]['category'], 'request_timeout')

    def test_expired_setup_is_incomplete_without_fictitious_auth_failure(self):
        base, calls = self.server()
        value = fixtures.snapshot(users=1, rounds=0)
        value['load_config']['duration'] = 1
        value['env_config']['base_url'] = base
        value['steps'] = [
            {'name': 'setup', 'method': 'GET', 'url': '/slow', 'is_setup': True},
            {'name': 'business', 'method': 'GET', 'url': '/business'},
        ]
        with tempfile.TemporaryDirectory(dir=fixtures.TEST_ROOT) as work:
            engine = self.module.K6Engine(value, work_dir=work)
            engine.prepare()
            with self.assertRaisesRegex(self.module.EngineError, '尚未完成全部前置'):
                engine.run()
            summary = engine.collect()['summary']
        self.assertEqual(calls, ['/slow'])
        self.assertEqual(summary['setup_incomplete_vus'], 1)
        self.assertEqual(summary['setup_failed_vus'], 0)
        self.assertEqual(summary['auth_failed_vus'], 0)
        self.assertEqual(summary['business_started'], 0)

    def test_explicit_stop_during_drain_keeps_incomplete_evidence(self):
        started = threading.Event()
        base, calls = self.server(delay=2.6, started=started)
        value = fixtures.snapshot(users=1, rounds=0)
        value['load_config']['duration'] = 1
        value['env_config']['base_url'] = base
        value['steps'] = [{'name': 'in flight', 'method': 'GET', 'url': '/slow'}]
        with tempfile.TemporaryDirectory(dir=fixtures.TEST_ROOT) as work:
            engine = self.module.K6Engine(value, work_dir=work)
            engine.prepare()

            def stop_after_deadline():
                if started.wait(timeout=5):
                    time.sleep(1.2)
                    engine.stop()

            stopper = threading.Thread(target=stop_after_deadline, daemon=True)
            stopper.start()
            engine.run()
            stopper.join(timeout=5)
            result = engine.collect()
        self.assertEqual(calls, ['/slow'])
        self.assertTrue(result['stop_reason'])
        self.assertEqual(result['summary']['business_incomplete'], 1)
        self.assertEqual(result['summary']['business_total'], 0)

    def test_one_successful_vu_cannot_hide_another_vus_expired_setup(self):
        base, calls = self.server()
        value = fixtures.snapshot(users=2, rounds=0)
        value['load_config']['duration'] = 1
        value['env_config']['base_url'] = base
        value['steps'] = [
            {'name': 'setup', 'method': 'GET', 'url': '/slow/{{vu_id}}', 'is_setup': True},
            {'name': 'business', 'method': 'GET', 'url': '/business', 'think_time': {'type': 'FIXED', 'min': 50}},
        ]
        with tempfile.TemporaryDirectory(dir=fixtures.TEST_ROOT) as work:
            engine = self.module.K6Engine(value, work_dir=work)
            engine.prepare()
            with self.assertRaisesRegex(self.module.EngineError, '1 个用户在发压到期'):
                engine.run()
            summary = engine.collect()['summary']
        self.assertEqual(summary['setup_incomplete_vus'], 1)
        self.assertGreater(summary['business_total'], 0)
        self.assertEqual(summary['failed_requests'], 0)
        self.assertEqual(summary['business_incomplete'], 0)

    def test_round_limit_keeps_its_hard_max_duration(self):
        base, calls = self.server()
        value = fixtures.snapshot(users=1, rounds=1)
        value['load_config']['duration'] = 1
        value['env_config']['base_url'] = base
        value['steps'] = [{'name': 'slow', 'method': 'GET', 'url': '/slow'}]
        with tempfile.TemporaryDirectory(dir=fixtures.TEST_ROOT) as work:
            engine = self.module.K6Engine(value, work_dir=work)
            engine.prepare()
            with self.assertRaises(self.module.EngineError):
                engine.run()
            self.assertEqual(calls, ['/slow'])
            self.assertEqual(engine.collect()['summary']['business_incomplete'], 1)
