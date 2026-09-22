import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from apps.perf_testing.engines import k6_engine
from apps.perf_testing.tests.fixtures.support_websocket_server import SupportWebSocketServer
from apps.perf_testing.tests.test_k6_engine import TEST_ROOT
from apps.perf_testing.tests.test_websocket_steps import push_config


def ws_snapshot(url, users=1, rounds=1):
    return {'load_config': {'model': 'CONCURRENCY', 'concurrency': users, 'duration': 10,
                            'iterations_per_vu': rounds, '_purpose': 'debug' if users == rounds == 1 else 'load'},
            'env_config': {'base_url': url, 'headers': {'Authorization': 'DO_NOT_SEND', 'Cookie': 'DO_NOT_SEND'}},
            'runtime_config': {'timeout': 2, 'account_identity_variable': 'access_token', 'auth_profile': {'mode': 'STATIC', 'transport': 'BEARER', 'access_token_variable': 'access_token'}},
            'variables': [{'name': 'access_token', 'type': 'CSV', 'data_file_id': 1, 'column': 'token'}],
            'csv_data': {'1': {'rows': [{'token': f'FAKE_TOKEN_{i}'} for i in range(users)]}},
            'steps': [{'id': 1, 'name': 'HTTP before', 'method': 'GET', 'url': '/before'},
                      {'id': 2, 'name': 'Socket', 'protocol': 'WEBSOCKET', 'method': 'GET', 'url': '/socket',
                       'websocket_config': {'version': 1, 'connect_timeout_ms': 150, 'command_timeout_ms': 150,
                            'max_session_ms': 3000, 'heartbeat_interval_ms': 0, 'hold_open_ms': 0,
                            'auth': {'request': {'version': 1, 'action': 'auth', 'payload': {'token': '{{access_token}}'}}},
                            'commands': [
                                {'name': 'First', 'request': {'version': 1, 'action': 'timeline.list', 'payload': {'key': '{{request_id}}'}},
                                 'assertions': [{'type': 'JSON_PATH', 'expr': '$.ok', 'expected': True}],
                                 'extractors': [{'name': 'next_value', 'type': 'JSON_PATH', 'expr': '$.data.next'}]},
                                {'name': 'Second', 'request': {'version': 1, 'action': 'timeline.unread', 'payload': {'value': '{{next_value}}', 'key': '{{request_id}}'}}}]}},
                      {'id': 3, 'name': 'HTTP after', 'method': 'GET', 'url': '/after'}]}


@unittest.skipUnless(k6_engine.is_available(), 'native fixed k6 required')
class NativeWebSocketTests(unittest.TestCase):
    def test_push_hold_error_fails_session_while_later_snapshots_do_not_add_successes(self):
        for mode in ('push_hold_error', 'push_hold_more'):
            with self.subTest(mode=mode), SupportWebSocketServer(mode) as server, \
                    tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
                data = ws_snapshot(server.url)
                config = push_config()
                config.update(connect_timeout_ms=300, command_timeout_ms=300, max_session_ms=1000, hold_open_ms=250)
                data['steps'][1]['websocket_config'] = config
                engine = k6_engine.K6Engine(data, work_dir=work)
                engine.prepare(); engine.run()
                summary = engine.collect()['summary']
                ws = summary['websocket']
                self.assertEqual(ws['sessions']['failed'], int(mode == 'push_hold_error'))
                self.assertEqual(ws['sessions']['success'], int(mode == 'push_hold_more'))
                self.assertEqual(ws['events']['success'], 1)
                self.assertEqual(ws['events']['completed'], 1)
                self.assertEqual(ws['connections']['current'], 0)
                self.assertEqual(ws['connections']['unclosed'], 0)
                self.assertEqual(server.closed, 1)
                self.assertEqual(summary['failed_requests'], int(mode == 'push_hold_error'))

    def test_failed_push_blocks_same_group_output_and_does_not_refund_attempt(self):
        with SupportWebSocketServer('push_error') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            data = ws_snapshot(server.url, rounds=2)
            config = push_config()
            config.update(connect_timeout_ms=300, command_timeout_ms=300, max_session_ms=1000)
            data['steps'][1]['websocket_config'] = config
            data['steps'][2]['url'] = '/after/{{quote_cycle}}'
            policy = {'group_id': 'push_group', 'vu_start': 1, 'vu_end': 1, 'max_runs_per_vu': 1, 'min_interval_ms': 0}
            for step in data['steps'][1:]:
                step['execution_policy'] = policy
            self.assertEqual(k6_engine.validate_snapshot(data), [])
            engine = k6_engine.K6Engine(data, work_dir=work)
            engine.prepare(); engine.run()
            summary = engine.collect()['summary']
            self.assertEqual(summary['http_total'], 2)
            self.assertEqual(summary['websocket']['sessions']['failed'], 1)
            self.assertEqual(summary['websocket']['events']['failed'], 1)
            self.assertEqual(server.http_calls, ['/before', '/before'])
            group = summary['execution_policy']['groups'][0]
            self.assertEqual(group['started'], 1)
            self.assertEqual(group['blocked_steps'], 1)
            self.assertEqual(group['quota'], 1)

    def test_stopping_push_retains_incomplete_event_and_unknown_close(self):
        with SupportWebSocketServer('push_timeout') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            data = ws_snapshot(server.url)
            config = push_config()
            config.update(connect_timeout_ms=300, command_timeout_ms=2000, max_session_ms=3000)
            data['steps'][1]['websocket_config'] = config
            engine = k6_engine.K6Engine(data, work_dir=work)
            engine.prepare()
            errors = []
            def run():
                try:
                    engine.run()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.monotonic() + 4
            while not engine._websocket.stages['events']['started'] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(engine._websocket.stages['events']['started'], 1)
            engine.stop(); worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            stats = engine.collect()['summary']['websocket']
            self.assertEqual(stats['events']['incomplete'], 1)
            self.assertEqual(stats['sessions']['incomplete'], 1)
            self.assertIsNone(stats['connections']['current'])

    def test_push_bearer_two_identities_metrics_and_same_group_downstream(self):
        with SupportWebSocketServer('push_success') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            data = ws_snapshot(server.url, users=2, rounds=2)
            config = push_config()
            config.update(connect_timeout_ms=300, command_timeout_ms=300, max_session_ms=1000)
            data['steps'][1]['websocket_config'] = config
            data['steps'][2]['url'] = '/after/{{quote_cycle}}'
            policy = {'group_id': 'push_group', 'vu_start': 1, 'vu_end': 2, 'max_runs_per_vu': 1, 'min_interval_ms': 0}
            for step in data['steps'][1:]:
                step['execution_policy'] = policy
            self.assertEqual(k6_engine.validate_snapshot(data), [])
            engine = k6_engine.K6Engine(data, work_dir=work)
            engine.prepare(); engine.run()
            result = engine.collect()
            summary = result['summary']
            self.assertEqual(summary['websocket']['events']['success'], 2)
            self.assertEqual(summary['websocket']['commands']['started'], 0)
            self.assertEqual(summary['websocket']['auth']['success'], 2)
            self.assertEqual(summary['websocket']['command_metrics'][0]['latency_kind'], 'event_wait')
            self.assertEqual(summary['websocket']['connections']['current'], 0)
            self.assertEqual(summary['http_total'], 6)
            self.assertEqual(summary['business_total'], 8)
            self.assertEqual(summary['failed_requests'], 0)
            headers = [h for h in server.headers if h.get('Upgrade', '').lower() == 'websocket']
            self.assertEqual(sorted(h.get('Authorization') for h in headers), ['Bearer FAKE_TOKEN_0', 'Bearer FAKE_TOKEN_1'])
            self.assertTrue(all('Cookie' not in h for h in headers))
            self.assertTrue(all(frame == {'type': 'quote.subscribe', 'codes': ['sz000001']} for frame in server.frames))
            self.assertEqual(server.http_calls.count('/after/fixture-cycle'), 2)
            self.assertNotIn('FAKE_TOKEN_', json.dumps(result))
            self.assertNotIn('SECRET_', json.dumps(result))

    def test_push_initial_frame_and_failed_waits_are_not_handshake_success(self):
        for mode in ('push_initial', 'push_reminder', 'push_timeout', 'push_error', 'push_auth_failed', 'push_redirect'):
            with self.subTest(mode=mode), SupportWebSocketServer(mode) as server, \
                    tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
                data = ws_snapshot(server.url)
                config = push_config()
                config.update(connect_timeout_ms=300, command_timeout_ms=300, max_session_ms=1000)
                success = mode in ('push_initial', 'push_reminder')
                if success:
                    del config['commands'][0]['request']
                if mode == 'push_reminder':
                    config['commands'][0]['event_path'] = '$.event_type'
                data['steps'][1]['websocket_config'] = config
                engine = k6_engine.K6Engine(data, work_dir=work)
                engine.prepare(); engine.run()
                summary = engine.collect()['summary']
                self.assertEqual(summary['websocket']['sessions']['success'], int(success))
                self.assertEqual(summary['websocket']['sessions']['failed'], int(not success))
                self.assertEqual(summary['websocket']['events']['success'], int(success))
                self.assertNotIn('/redirect-must-not-follow', server.http_calls)
                if mode in ('push_auth_failed', 'push_redirect'):
                    self.assertEqual(summary['websocket']['auth']['failed'], 1)
                    self.assertEqual(summary['websocket']['events']['started'], 0)
                else:
                    self.assertEqual(summary['websocket']['connections']['current'], 0)

    def run_fixture(self, mode='success', users=1, rounds=1, mutate=None):
        with SupportWebSocketServer(mode) as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            snapshot = ws_snapshot(server.url, users, rounds)
            if mutate:
                mutate(snapshot)
            engine = k6_engine.K6Engine(snapshot, work_dir=work, raw_csv_path=str(Path(work) / 'raw.csv.gz'))
            engine.prepare()
            self.assertTrue((Path(work) / 'k6_websocket.js').exists())
            engine.run()
            result = engine.collect()
            self.assertNotIn('SECRET_', json.dumps(result))
            self.assertNotIn('FAKE_TOKEN_', json.dumps(result))
            self.assertFalse(any('Authorization' in headers or 'Cookie' in headers
                                 for headers in server.headers if headers.get('Upgrade', '').lower() == 'websocket'))
            return result['summary'], server.frames, server.http_calls, server.closed

    def test_mixed_native_socket_and_http_two_accounts_two_rounds(self):
        summary, frames, calls, closed = self.run_fixture(users=2, rounds=2)
        self.assertEqual(summary['http_total'], 8)
        self.assertEqual(summary['business_total'], 12)
        self.assertEqual(summary['websocket']['sessions']['success'], 4)
        self.assertEqual(summary['websocket']['commands']['success'], 8)
        self.assertEqual(summary['websocket']['connections']['current'], 0)
        self.assertEqual(closed, 4)
        self.assertEqual(len(calls), 8)
        tokens = [frame['payload']['token'] for frame in frames if frame['action'] == 'auth']
        self.assertEqual(sorted(tokens), ['FAKE_TOKEN_0'] * 2 + ['FAKE_TOKEN_1'] * 2)
        self.assertEqual(len({frame['id'] for frame in frames}), len(frames))

    def test_native_negative_protocol_paths_fail_business_with_one_terminal(self):
        for mode in ('auth_failed', 'wrong_id', 'malformed', 'close', 'no_handshake'):
            with self.subTest(mode=mode):
                summary, _, _, _ = self.run_fixture(mode)
                self.assertEqual(summary['websocket']['sessions']['failed'], 1)
                self.assertEqual(summary['websocket']['sessions']['completed'], 1)
                self.assertEqual(summary['http_total'], 2)
                self.assertEqual(summary['failed_requests'], 1)

    def test_native_hold_heartbeat(self):
        def mutate(snapshot):
            config = snapshot['steps'][1]['websocket_config']
            config.update(heartbeat_interval_ms=1000, hold_open_ms=1200)
        summary, frames, _, _ = self.run_fixture(mutate=mutate)
        self.assertGreaterEqual(summary['websocket']['heartbeat']['success'], 1)
        self.assertEqual(summary['websocket']['commands']['completed'], 2)
        self.assertTrue(any(frame['action'] == 'ping' for frame in frames))

    def test_native_setup_socket_runs_once_per_vu(self):
        summary, frames, _, _ = self.run_fixture(rounds=2, mutate=lambda data: data['steps'][1].update(is_setup=True))
        self.assertEqual(summary['websocket']['sessions']['completed'], 1)
        self.assertEqual(summary['business_total'], 4)
        self.assertEqual(len([frame for frame in frames if frame['action'] == 'auth']), 1)

    def test_native_admitted_session_drains_handshake_and_commands_but_not_following_http(self):
        for mode in ('delayed_handshake', 'delayed_first_command'):
            with self.subTest(mode=mode):
                def mutate(data):
                    data['load_config'].update(duration=1, iterations_per_vu=0)
                    data['steps'][0]['think_time'] = {'type': 'FIXED', 'min': 550}
                    data['steps'][1]['websocket_config'].update(
                        connect_timeout_ms=1000, max_session_ms=1000, command_timeout_ms=1000)
                summary, frames, calls, closed = self.run_fixture(mode, mutate=mutate)
                self.assertEqual(summary['websocket']['sessions']['success'], 1)
                self.assertEqual(summary['websocket']['sessions']['failed'], 0)
                self.assertEqual(summary['websocket']['commands']['success'], 2)
                self.assertEqual([frame['action'] for frame in frames], ['auth', 'timeline.list', 'timeline.unread'])
                self.assertEqual(calls, ['/before'])
                self.assertEqual(closed, 1)
                self.assertEqual(summary['websocket']['connections']['current'], 0)
                self.assertEqual(summary['websocket']['connections']['unclosed'], 0)
                self.assertEqual(summary['completed_iterations'], 0)

    def test_native_draining_session_keeps_original_session_maximum(self):
        def mutate(data):
            data['load_config'].update(duration=1, iterations_per_vu=0)
            data['steps'][0]['think_time'] = {'type': 'FIXED', 'min': 550}
            data['steps'][1]['websocket_config'].update(max_session_ms=1000, command_timeout_ms=1000)
        summary, frames, calls, _ = self.run_fixture('delayed_command', mutate=mutate)
        self.assertEqual(summary['websocket']['sessions']['failed'], 1)
        self.assertEqual([frame['action'] for frame in frames], ['auth', 'timeline.list', 'timeline.unread'])
        self.assertTrue(any(item['category'] == 'WSSessionTimeout' for item in summary['request_error_groups']))
        self.assertEqual(calls, ['/before'])
        self.assertEqual(summary['completed_iterations'], 0)

    def test_native_user_stop_keeps_unfinished_session_and_unknown_close(self):
        with SupportWebSocketServer('timeout') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            data = ws_snapshot(server.url)
            data['steps'][1]['websocket_config'].update(command_timeout_ms=2000)
            engine = k6_engine.K6Engine(data, work_dir=work)
            engine.prepare()
            errors = []
            def run():
                try:
                    engine.run()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.monotonic() + 4
            while not engine._websocket.stages['commands']['started'] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreater(engine._websocket.stages['commands']['started'], 0)
            engine.stop()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            websocket = engine.collect()['summary']['websocket']
            self.assertEqual(websocket['sessions']['incomplete'], 1)
            self.assertEqual(websocket['commands']['incomplete'], 1)
            self.assertIsNone(websocket['connections']['current'])
            self.assertEqual(websocket['connections']['unclosed'], 1)

    def test_peer_ignoring_close_aborts_owned_process_without_later_http(self):
        with SupportWebSocketServer('ignore_close') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            engine = k6_engine.K6Engine(ws_snapshot(server.url), work_dir=work)
            engine.prepare()
            errors = []
            def run():
                try:
                    engine.run()
                except Exception as exc:
                    errors.append(str(exc))
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(3)
            bounded = not worker.is_alive()
            if not bounded:
                engine.stop()
                worker.join(3)
            self.assertTrue(bounded, 'close timeout must abort the owned native process')
            self.assertEqual(server.http_calls, ['/before'])
            self.assertTrue(errors)
            self.assertEqual(engine.collect()['summary']['websocket']['sessions']['failed'], 1)
            self.assertIsNone(engine.collect()['summary']['websocket']['connections']['current'])

    def test_round_limit_deadline_retains_incomplete_without_admitting_another_command(self):
        with SupportWebSocketServer('delayed_command') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            data = ws_snapshot(server.url, rounds=10)
            data['load_config']['duration'] = 1
            data['steps'][0]['think_time'] = {'type': 'FIXED', 'min': 550}
            data['steps'][1]['websocket_config'].update(max_session_ms=1000, command_timeout_ms=1000)
            engine = k6_engine.K6Engine(data, work_dir=work)
            engine.prepare()
            errors = []
            def run():
                try:
                    engine.run()
                except Exception as exc:
                    errors.append(str(exc))
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(4)
            bounded = not worker.is_alive()
            if not bounded:
                engine.stop()
                worker.join(3)
            self.assertTrue(bounded)
            self.assertTrue(errors, 'unfinished requested rounds must fail')
            self.assertEqual([frame['action'] for frame in server.frames], ['auth', 'timeline.list'])
            summary = engine.collect()['summary']
            sessions = summary['websocket']['sessions']
            self.assertEqual(sessions['incomplete'] + sessions['failed'], 1)
            self.assertEqual(summary['http_total'], 1)

    def test_hard_round_cutoff_ignoring_close_is_bounded_by_engine_watchdog(self):
        with SupportWebSocketServer('ignore_close') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            data = ws_snapshot(server.url, rounds=10)
            data['load_config']['duration'] = 1
            data['steps'][1]['websocket_config'].update(max_session_ms=1000, hold_open_ms=500)
            engine = k6_engine.K6Engine(data, work_dir=work)
            engine.prepare()
            errors = []
            def run():
                try:
                    engine.run()
                except Exception as exc:
                    errors.append(str(exc))
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(5)
            bounded = not worker.is_alive()
            if not bounded:
                engine.stop()
                worker.join(3)
            self.assertTrue(bounded, 'Python watchdog must clean up after k6 cancels JS close timers')
            self.assertTrue(any('截止清理时间' in error for error in errors))
            self.assertEqual(server.http_calls, ['/before'])
            self.assertEqual(engine.collect()['summary']['websocket']['sessions']['incomplete'], 1)
