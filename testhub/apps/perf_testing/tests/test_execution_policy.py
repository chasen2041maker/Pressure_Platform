"""Bounded groups must limit real attempts, not merely successful requests."""
from copy import deepcopy
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from apps.perf_testing.engines import k6_engine
from .test_k6_engine import TEST_ROOT, TestHandler


def policy(**changes):
    return dict(dict(group_id='reminders', vu_start=2, vu_end=2,
                max_runs_per_vu=1, min_interval_ms=0), **changes)


def data(users=4, rounds=2):
    return {'engine': 'K6', 'load_config': {'model': 'CONCURRENCY', 'concurrency': users,
                'duration': 5, 'iterations_per_vu': rounds},
            'runtime_config': {'timeout': 1}, 'env_config': {'base_url': 'http://127.0.0.1:1',
                'headers': {'X-Fixture-User': '{{user_id}}'}},
            'variables': [{'name': 'user_id', 'type': 'CSV', 'data_file_id': 1, 'column': 'id'}],
            'csv_data': {'1': {'rows': [{'id': f'fixture-{i}'} for i in range(1, users + 1)]}}, 'steps': [
                {'id': 1, 'url': '/daily'},
                {'id': 2, 'url': '/produce/{{vu_id}}', 'execution_policy': policy(),
                 'extractors': [{'name': 'owned', 'type': 'JSON_PATH', 'expr': '$.id'}]},
                {'id': 3, 'url': '/consume/{{owned}}', 'execution_policy': policy()},
            ]}


class PolicyValidationTests(unittest.TestCase):
    def test_websocket_assertion_dependencies_cannot_cross_groups(self):
        from .test_k6_websocket_integration import ws_snapshot
        for phase in ('auth', 'command'):
            with self.subTest(phase=phase):
                snapshot = ws_snapshot('http://127.0.0.1:1')
                snapshot['steps'] = snapshot['steps'][:2]
                snapshot['steps'][0].update(execution_policy=policy(vu_start=1, vu_end=1),
                    extractors=[{'name': 'owned', 'type': 'JSON_PATH', 'expr': '$.ok'}])
                socket = snapshot['steps'][1]
                socket['execution_policy'] = policy(group_id='other', vu_start=1, vu_end=1)
                config = socket['websocket_config']
                frame = config['auth'] if phase == 'auth' else config['commands'][0]
                frame['assertions'] = [{'type': 'JSON_PATH', 'expr': '$.ok', 'expected': '{{owned}}'}]
                self.assertIn('执行组不能引用组外业务步骤的输出；请使用前置或本组前序提取值',
                              k6_engine.validate_snapshot(snapshot))

    def test_websocket_frame_local_assertions_still_require_prior_outputs(self):
        from .test_k6_websocket_integration import ws_snapshot
        snapshot = ws_snapshot('http://127.0.0.1:1')
        socket = snapshot['steps'][1]
        socket['execution_policy'] = policy(vu_start=1, vu_end=1)
        socket['websocket_config']['auth']['assertions'] = [
            {'type': 'JSON_PATH', 'expr': '$.ok', 'expected': '{{next_value}}'}]
        self.assertIn('WebSocket 帧引用了尚未定义或尚未提取的变量', k6_engine.validate_snapshot(snapshot))

    def test_http_assertion_templates_remain_literals(self):
        from apps.perf_testing.services.execution_policy import references
        snapshot = data()
        consumer = snapshot['steps'][2]
        consumer.update(url='/literal', execution_policy={}, assertions=[
            {'type': 'CONTAINS', 'expected': '{{owned}}'},
            {'type': 'JSON_PATH', 'expr': '$.id', 'expected': '{{owned}}'}])
        self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
        self.assertNotIn('owned', references(consumer))

    def test_only_effective_headers_contribute_group_dependencies(self):
        from apps.perf_testing.services.execution_policy import compile_groups
        for header in ('X-Owned', 'x-OWNED'):
            with self.subTest(header=header):
                snapshot = data()
                snapshot['env_config']['headers']['X-Owned'] = '{{owned}}'
                snapshot['steps'].append({'id': 4, 'url': '/after'})
                for step in snapshot['steps']:
                    step['headers'] = {header: 'fixed'}
                self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
                groups = compile_groups(snapshot['steps'], snapshot['env_config']['headers'])
                self.assertEqual(groups[1]['steps'][0]['requires'], ['user_id', 'vu_id'])
                self.assertEqual(groups[1]['steps'][1]['requires'], ['owned', 'user_id'])

    def test_estimate_counts_participants_and_http_separately(self):
        from apps.perf_testing.services.execution_policy import request_plan
        snapshot = data()
        snapshot['steps'].append({'id': 4, 'protocol': 'WEBSOCKET'})
        plan = request_plan(snapshot['steps'], 4, 2)
        self.assertEqual((plan['business'], plan['http'], plan['websocket']), (18, 10, 8))
        self.assertEqual(plan['groups'][0]['attempt_limit'], 1)

    def test_fixed_rounds_cap_the_reported_attempt_limit(self):
        from apps.perf_testing.services.execution_policy import PolicyMetrics
        snapshot = data()
        for step in snapshot['steps'][1:]:
            step['execution_policy'].update(vu_start=1, vu_end=4, max_runs_per_vu=10)
        row = PolicyMetrics(snapshot['steps'], snapshot['load_config']).snapshot()['groups'][0]
        self.assertEqual((row['planned_participants'], row['attempt_limit']), (4, 8))

    def test_policy_sample_whitelist_does_not_copy_secrets(self):
        from apps.perf_testing.services.k6_samples import sample_payload
        value = sample_payload({'sample_seq': 1, 'elapsed_seconds': 1,
            'execution_policy': {'version': 1, 'groups': [{'group_index': 1, 'participants': 1,
                'group_id': 'SECRET', 'user_id': 'SECRET', 'token': 'SECRET'}]}})
        self.assertIn('execution_policy', value)
        self.assertNotIn('SECRET', json.dumps(value))

    def test_rejects_invalid_policy_instead_of_ignoring_it(self):
        for key, value in [('vu_start', 0), ('vu_end', 5), ('max_runs_per_vu', 0),
                           ('min_interval_ms', -1), ('vu_start', True), ('vu_end', '2'),
                           ('group_id', 'secret?token=x'), ('unknown', 1)]:
            snapshot = data()
            for step in snapshot['steps'][1:]:
                step['execution_policy'][key] = value
            self.assertTrue(k6_engine.validate_snapshot(snapshot), key)

    def test_rejects_setup_noncontiguous_conflicting_and_cross_group_dependencies(self):
        variants = []
        snapshot = data(); snapshot['steps'][1]['is_setup'] = True; variants.append(snapshot)
        snapshot = data(); snapshot['steps'].insert(2, {'id': 4, 'url': '/gap'}); variants.append(snapshot)
        snapshot = data(); snapshot['steps'][2]['execution_policy']['vu_end'] = 3; variants.append(snapshot)
        snapshot = data(); snapshot['steps'][2]['execution_policy'] = {}; variants.append(snapshot)
        snapshot = data(); snapshot['steps'][2]['execution_policy']['group_id'] = 'other'; variants.append(snapshot)
        snapshot = data(); snapshot['engine'] = 'BUILTIN'; variants.append(snapshot)
        for snapshot in variants:
            self.assertTrue(k6_engine.validate_snapshot(snapshot))

    def test_group_and_setup_dependencies_are_valid(self):
        snapshot = data()
        self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
        snapshot['steps'][0].update(is_setup=True, extractors=[{'name': 'setup_id', 'type': 'JSON_PATH', 'expr': '$.id'}])
        snapshot['steps'][1]['url'] += '/{{setup_id}}'
        self.assertEqual(k6_engine.validate_snapshot(snapshot), [])


@unittest.skipUnless(k6_engine.is_available(), 'native fixed k6 required')
class NativePolicyTests(unittest.TestCase):
    def run_case(self, snapshot=None, fail=False):
        calls = []
        class Handler(TestHandler):
            def do_GET(self):
                calls.append(self.path)
                failed = self.path.startswith('/produce/') and (fail is True or fail == 'second' and calls.count(self.path) == 2)
                body = json.dumps({'id': 'owned-' + self.path.rsplit('/', 1)[-1]}).encode()
                self.send_response(500 if failed else 200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
                snapshot = deepcopy(snapshot or data())
                snapshot['env_config']['base_url'] = f'http://127.0.0.1:{server.server_port}'
                engine = k6_engine.K6Engine(snapshot, work_dir=work)
                engine.prepare(); engine.run()
                return engine.collect()['summary'], calls
        finally:
            server.shutdown(); server.server_close(); worker.join(2)

    def test_daily_four_users_two_rounds_and_special_one_user_once(self):
        summary, calls = self.run_case()
        self.assertEqual(calls.count('/daily'), 8)
        self.assertEqual(calls.count('/produce/2'), 1)
        self.assertEqual(calls.count('/consume/owned-2'), 1)
        self.assertEqual(len(calls), 10)
        self.assertEqual(summary['executor_iterations'], 8)
        self.assertEqual(summary['completed_iterations'], 8)
        group = summary['execution_policy']['groups'][0]
        self.assertEqual((group['participants'], group['started'], group['completed'], group['success']), (1, 1, 1, 1))

    def test_final_think_cutoff_keeps_completed_group_and_real_http_outcome(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                snapshot = data(users=1, rounds=0)
                snapshot['load_config']['duration'] = 1
                snapshot['steps'] = [
                    {'id': 1, 'url': '/first', 'execution_policy': policy(vu_start=1, vu_end=1)},
                    {'id': 2, 'url': '/produce/1', 'execution_policy': policy(vu_start=1, vu_end=1),
                     'think_time': {'type': 'FIXED', 'min': 2000}},
                    {'id': 3, 'url': '/must-not-start'},
                ]
                summary, calls = self.run_case(snapshot, fail=failed)
                self.assertEqual(calls, ['/first', '/produce/1'])
                group = summary['execution_policy']['groups'][0]
                self.assertEqual((group['started'], group['completed'], group['incomplete']), (1, 1, 0))
                self.assertEqual((group['executed_steps'], group['blocked_steps']), (2, 0))
                self.assertEqual((group['success'], group['failed']), (int(not failed), int(failed)))
                self.assertEqual(summary['failed_requests'], int(failed))
                self.assertEqual(summary['http_total'], 2)
                self.assertEqual(summary['completed_iterations'], 0)

    def test_failed_attempt_spends_quota_and_blocks_downstream(self):
        summary, calls = self.run_case(fail=True)
        self.assertEqual(calls.count('/produce/2'), 1)
        self.assertFalse(any(path.startswith('/consume/') for path in calls))
        self.assertEqual(summary['http_total'], 9)
        self.assertEqual(summary['failed_requests'], 1)
        group = summary['execution_policy']['groups'][0]
        self.assertEqual((group['started'], group['failed'], group['blocked_steps']), (1, 1, 1))
        self.assertEqual(summary['completed_iterations'], 7)

    def test_special_only_empty_rounds_do_not_inflate_business_iterations(self):
        snapshot = data(); snapshot['steps'].pop(0)
        summary, calls = self.run_case(snapshot)
        self.assertEqual(len(calls), 2)
        self.assertEqual(summary['executor_iterations'], 8)
        self.assertEqual(summary['completed_iterations'], 1)
        self.assertEqual(summary['idle_iterations'], 7)
        from apps.perf_testing.services.k6_thresholds import completion_reason
        self.assertEqual(completion_reason(summary, snapshot['load_config'], 'COMPLETED'), '')

    def test_built_in_request_id_is_available_inside_group(self):
        snapshot = data()
        snapshot['steps'][1]['headers'] = {'X-Request-Id': '{{request_id}}'}
        summary, calls = self.run_case(snapshot)
        self.assertEqual(len(calls), 10)
        self.assertEqual(summary['execution_policy']['groups'][0]['blocked_steps'], 0)

    def test_overridden_header_does_not_block_real_group_requests(self):
        for header in ('X-Owned', 'x-OWNED'):
            with self.subTest(header=header):
                snapshot = data()
                snapshot['env_config']['headers']['X-Owned'] = '{{owned}}'
                for step in snapshot['steps']:
                    step['headers'] = {header: 'fixed'}
                self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
                summary, calls = self.run_case(snapshot)
                self.assertEqual(len(calls), 10)
                self.assertEqual(calls.count('/produce/2'), 1)
                self.assertEqual(calls.count('/consume/owned-2'), 1)
                group = summary['execution_policy']['groups'][0]
                self.assertEqual((group['success'], group['blocked_steps']), (1, 0))

    def test_replaced_auth_headers_do_not_become_group_dependencies(self):
        snapshot = data()
        snapshot['runtime_config']['auth_profile'] = {
            'mode': 'STATIC', 'transport': 'BEARER', 'access_token_variable': 'user_id'}
        snapshot['env_config']['headers'].update(Authorization='Bearer {{owned}}', Cookie='old={{owned}}')
        for step in snapshot['steps']:
            step['headers'] = {'aUTHORIZATION': '{{owned}}'}
        self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
        summary, calls = self.run_case(snapshot)
        self.assertEqual(len(calls), 10)
        self.assertEqual(summary['execution_policy']['groups'][0]['success'], 1)

    def test_second_attempt_failure_cannot_reuse_first_attempt_output(self):
        snapshot = data()
        for step in snapshot['steps'][1:]:
            step['execution_policy']['max_runs_per_vu'] = 2
        summary, calls = self.run_case(snapshot, fail='second')
        self.assertEqual(calls.count('/produce/2'), 2)
        self.assertEqual(calls.count('/consume/owned-2'), 1)
        group = summary['execution_policy']['groups'][0]
        self.assertEqual((group['success'], group['failed'], group['blocked_steps']), (1, 1, 1))

    def test_interval_skip_does_not_spend_second_attempt(self):
        snapshot = data(rounds=2)
        for step in snapshot['steps'][1:]:
            step['execution_policy'].update(max_runs_per_vu=2, min_interval_ms=60000)
        summary, calls = self.run_case(snapshot)
        self.assertEqual(calls.count('/produce/2'), 1)
        group = summary['execution_policy']['groups'][0]
        self.assertEqual((group['started'], group['interval'], group['quota']), (1, 1, 0))

    def test_timed_exhausted_groups_wait_without_business_activity_or_extra_calls(self):
        snapshot = data(rounds=0); snapshot['load_config']['duration'] = 1
        snapshot['steps'].pop(0)
        summary, calls = self.run_case(snapshot)
        self.assertEqual(len(calls), 2)
        self.assertEqual(summary['completed_iterations'], 1)
        self.assertLessEqual(summary['executor_iterations'], 5)
        self.assertEqual(summary['max_concurrency'], 1)


@unittest.skipUnless(k6_engine.is_available(), 'native fixed k6 required')
class NativeMixedPolicyTests(unittest.TestCase):
    def test_websocket_assertion_dependencies_admit_success_and_block_failed_producer(self):
        from .test_k6_websocket_integration import ws_snapshot
        from .fixtures.support_websocket_server import SupportWebSocketServer
        for phase in ('auth', 'command'):
            for failed in (False, True):
                with self.subTest(phase=phase, failed=failed), SupportWebSocketServer('success') as server, \
                        tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                        mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
                    snapshot = ws_snapshot(server.url)
                    snapshot['steps'] = snapshot['steps'][:2]
                    for step in snapshot['steps']:
                        step['execution_policy'] = policy(vu_start=1, vu_end=1)
                    producer, socket = snapshot['steps']
                    producer['extractors'] = [{'name': 'owned', 'type': 'JSON_PATH', 'expr': '$.ok'}]
                    if failed:
                        producer['assertions'] = [{'type': 'STATUS_CODE', 'expected': 201}]
                    config = socket['websocket_config']
                    frame = config['auth'] if phase == 'auth' else config['commands'][0]
                    frame['assertions'] = [{'type': 'JSON_PATH', 'expr': '$.ok', 'expected': '{{owned}}'}]
                    self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
                    engine = k6_engine.K6Engine(snapshot, work_dir=work); engine.prepare(); engine.run()
                    summary = engine.collect()['summary']
                    group = summary['execution_policy']['groups'][0]
                    self.assertEqual(server.http_calls, ['/before'])
                    if failed:
                        self.assertEqual(server.frames, [])
                        self.assertFalse(any(headers.get('Upgrade', '').lower() == 'websocket' for headers in server.headers))
                        self.assertEqual((group['executed_steps'], group['blocked_steps'], group['failed']), (1, 1, 1))
                        self.assertEqual(summary['websocket']['sessions']['started'], 0)
                        self.assertEqual(summary['failed_requests'], 1)
                    else:
                        self.assertEqual([frame['action'] for frame in server.frames], ['auth', 'timeline.list', 'timeline.unread'])
                        self.assertEqual((group['executed_steps'], group['blocked_steps'], group['success']), (2, 0, 1))
                        self.assertEqual(summary['websocket']['sessions']['success'], 1)

    def test_websocket_prior_frame_assertion_outputs_do_not_block_session_entry(self):
        from .test_k6_websocket_integration import ws_snapshot
        from .fixtures.support_websocket_server import SupportWebSocketServer
        with SupportWebSocketServer('success') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            snapshot = ws_snapshot(server.url)
            snapshot['steps'] = snapshot['steps'][1:2]
            socket = snapshot['steps'][0]
            socket['execution_policy'] = policy(vu_start=1, vu_end=1)
            config = socket['websocket_config']
            config['auth']['extractors'] = [{'name': 'auth_ok', 'type': 'JSON_PATH', 'expr': '$.ok'}]
            config['commands'][0]['assertions'] = [{'type': 'JSON_PATH', 'expr': '$.ok', 'expected': '{{auth_ok}}'}]
            config['commands'][1]['assertions'] = [{'type': 'JSON_PATH', 'expr': '$.data.next', 'expected': '{{next_value}}'}]
            self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
            engine = k6_engine.K6Engine(snapshot, work_dir=work); engine.prepare(); engine.run()
            summary = engine.collect()['summary']
            self.assertEqual(summary['websocket']['sessions']['success'], 1)
            self.assertEqual(summary['websocket']['commands']['success'], 2)
            group = summary['execution_policy']['groups'][0]
            self.assertEqual((group['success'], group['blocked_steps'], group['executed_steps']), (1, 0, 1))

    def test_overridden_websocket_headers_match_http_dependency_scope(self):
        from .test_k6_websocket_integration import ws_snapshot
        from .fixtures.support_websocket_server import SupportWebSocketServer
        for header in ('X-Owned', 'x-OWNED'):
            with self.subTest(header=header), SupportWebSocketServer('success') as server, \
                    tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                    mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
                snapshot = ws_snapshot(server.url)
                snapshot['env_config']['headers'].update({'X-Owned': '{{never_defined}}',
                    'Authorization': '{{never_defined}}', 'Cookie': '{{never_defined}}'})
                for step in snapshot['steps']:
                    step.update(headers={header: 'fixed'}, execution_policy=policy(vu_start=1, vu_end=1))
                self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
                engine = k6_engine.K6Engine(snapshot, work_dir=work); engine.prepare(); engine.run()
                summary = engine.collect()['summary']
                self.assertEqual(summary['http_total'], 2)
                self.assertEqual(summary['websocket']['sessions']['success'], 1)
                self.assertEqual(summary['execution_policy']['groups'][0]['success'], 1)
                handshakes = [headers for headers in server.headers if headers.get('Upgrade', '').lower() == 'websocket']
                self.assertEqual(len(handshakes), 1)
                self.assertEqual({key.lower(): value for key, value in handshakes[0].items()}.get('x-owned'), 'fixed')

    def test_deadline_keeps_unfinished_group_and_admits_no_later_http(self):
        from .test_k6_websocket_integration import ws_snapshot
        from .fixtures.support_websocket_server import SupportWebSocketServer
        with SupportWebSocketServer('delayed_command') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            snapshot = ws_snapshot(server.url)
            snapshot['load_config'].update(duration=1, iterations_per_vu=0)
            for step in snapshot['steps']: step['execution_policy'] = policy(vu_start=1, vu_end=1)
            snapshot['steps'][0]['think_time'] = {'type': 'FIXED', 'min': 550}
            snapshot['steps'][1]['websocket_config'].update(max_session_ms=1000, command_timeout_ms=1000)
            engine = k6_engine.K6Engine(snapshot, work_dir=work); engine.prepare()
            with self.assertRaises(k6_engine.EngineError): engine.run()
            summary = engine.collect()['summary']
            self.assertEqual(server.http_calls, ['/before'])
            self.assertEqual(summary['execution_policy']['groups'][0]['incomplete'], 1)
            self.assertEqual(summary['completed_iterations'], 0)

    def test_http_ws_http_share_one_bounded_group_decision(self):
        from .test_k6_websocket_integration import ws_snapshot
        from .fixtures.support_websocket_server import SupportWebSocketServer
        with SupportWebSocketServer('success') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            snapshot = ws_snapshot(server.url, users=4, rounds=2)
            snapshot['steps'].insert(0, {'id': 4, 'url': '/daily'})
            for step in snapshot['steps'][1:]: step['execution_policy'] = policy()
            engine = k6_engine.K6Engine(snapshot, work_dir=work); engine.prepare(); engine.run()
            summary = engine.collect()['summary']
            self.assertEqual(summary['http_total'], 10)
            self.assertEqual(summary['websocket']['sessions']['completed'], 1)
            self.assertEqual(summary['websocket']['commands']['completed'], 2)
            self.assertEqual(summary['execution_policy']['groups'][0]['success'], 1)
            self.assertEqual(summary['executor_iterations'], 8)
            self.assertEqual(summary['completed_iterations'], 8)

    def test_stop_retains_incomplete_group_and_session(self):
        from .test_k6_websocket_integration import ws_snapshot
        from .fixtures.support_websocket_server import SupportWebSocketServer
        with SupportWebSocketServer('timeout') as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
            snapshot = ws_snapshot(server.url)
            for step in snapshot['steps']: step['execution_policy'] = policy(vu_start=1, vu_end=1)
            snapshot['steps'][1]['websocket_config']['command_timeout_ms'] = 2000
            engine = k6_engine.K6Engine(snapshot, work_dir=work); engine.prepare()
            failures = []
            def run():
                try: engine.run()
                except Exception as exc: failures.append(str(exc))
            worker = threading.Thread(target=run); worker.start()
            deadline = time.monotonic() + 4
            while not engine._websocket.stages['commands']['started'] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(engine._websocket.stages['commands']['started'])
            engine.stop(); worker.join(5)
            self.assertFalse(worker.is_alive()); self.assertEqual(failures, [])
            summary = engine.collect()['summary']
            self.assertEqual(summary['execution_policy']['groups'][0]['incomplete'], 1)
            self.assertEqual(summary['websocket']['sessions']['incomplete'], 1)
            self.assertEqual(server.http_calls, ['/before'])
