"""Built-in logical request IDs; optional native tests target loopback fixtures only."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.perf_testing.engines.k6_engine import K6Engine, validate_snapshot
from apps.perf_testing.services import auth_profiles, account_pools
from . import test_k6_auth_refresh as fixtures

UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')


def request_id_snapshot():
    snap = fixtures.auth_snapshot()
    snap['load_config']['iterations_per_vu'] = 2
    snap['env_config']['headers'].update({'Idempotency-Key': '{{request_id}}', 'X-Same-ID': '${request_id}'})
    return snap


class RequestIDTests(SimpleTestCase):
    def test_builtin_is_available_in_requests_auth_templates_and_catalog(self):
        snap = request_id_snapshot()
        self.assertEqual(validate_snapshot(snap), [])
        from apps.perf_testing.services.api_catalog import request_readiness
        request = {'url': '/one', 'headers': {'Idempotency-Key': '{{request_id}}'}, 'params': {}, 'body_type': 'NONE'}
        self.assertTrue(request_readiness(request, {'requirements': []})['ready'])
        request['headers']['Idempotency-Key'] = '{{unknown_id}}'
        self.assertFalse(request_readiness(request, {'requirements': []})['ready'])

    def test_request_id_cannot_be_a_user_variable_or_extractor(self):
        for kind in ('CONSTANT', 'CSV'):
            snap = fixtures.auth_snapshot()
            variable = {'name': 'request_id', 'type': kind, 'value': 'fixed', 'data_file_id': 1, 'column': 'user_id'}
            snap['variables'].append(variable)
            with self.subTest(kind=kind):
                self.assertTrue(validate_snapshot(snap))
        snap = fixtures.auth_snapshot()
        snap['steps'][0]['extractors'].append({'name': 'request_id', 'type': 'JSON_PATH', 'expr': '$.id'})
        self.assertTrue(validate_snapshot(snap))

    def test_auth_outputs_and_account_mapping_cannot_own_request_id(self):
        profile = fixtures.auth_snapshot()['runtime_config']['auth_profile']
        profile['login']['extractors'].append({'name': 'request_id', 'type': 'JSON_PATH', 'expr': '$.id'})
        with self.assertRaises(ValidationError):
            auth_profiles.normalize_profile(profile)
        with self.assertRaises(ValidationError):
            auth_profiles.normalize_profile({'mode': 'STATIC', 'access_token_variable': 'request_id'})
        with self.assertRaises(ValidationError):
            account_pools.parse_accounts(b'user\nu1\n', 'csv', 'user', {'request_id': 'user'})

@unittest.skipUnless(os.environ.get('RUN_K6_INTEGRATION') == '1' and os.environ.get('K6_BIN'),
                     'explicit local native fixture opt-in required')
class NativeRequestIDTests(SimpleTestCase):
    def test_two_vus_two_rounds_two_steps_and_forced_refresh_retry(self):
        from unittest import mock
        rows = []
        lock = threading.Lock()
        refreshed = set()
        tokens = {f'fake-token-u{vu}-{version}': vu for vu in (1, 2) for version in (1, 2)}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, body, vu):
                with lock:
                    rows.append({'path': self.path, 'vu': vu, 'status': status,
                        'id': self.headers.get('Idempotency-Key'), 'same_id': self.headers.get('X-Same-ID')})
                content = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self):
                vu = tokens.get(self.headers.get('Authorization', '').removeprefix('Bearer '))
                if self.path not in ('/one', '/two') or vu is None:
                    self.reply(404, {}, vu)
                    return
                with lock:
                    reject = self.path == '/two' and vu not in refreshed
                self.reply(401 if reject else 200, {'code': 'OK'}, vu)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                vu = next((vu for vu in (1, 2) if body.get('refresh_token') == f'fake-refresh-u{vu}'), None)
                if self.path != '/refresh' or vu is None:
                    self.reply(404, {}, vu)
                    return
                with lock:
                    refreshed.add(vu)
                self.reply(200, {'token': f'fake-token-u{vu}-2', 'refresh_token': f'fake-refresh-u{vu}'}, vu)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        all_logical = []
        summaries = []
        try:
            for _ in range(2):
                rows.clear(); refreshed.clear()
                snap = {'load_config': {'model': 'CONCURRENCY', 'concurrency': 2,
                    'duration': 10, 'iterations_per_vu': 2},
                    'env_config': {'base_url': f'http://127.0.0.1:{server.server_port}',
                        'headers': {'Idempotency-Key': '{{request_id}}', 'X-Same-ID': '${request_id}'}},
                    'runtime_config': {'timeout': 2, 'auth_profile': {'mode': 'STATIC', 'transport': 'BEARER',
                        'access_token_variable': 'token', 'refresh_token_variable': 'refresh_token',
                        'refresh': {'url': '/refresh', 'body_type': 'JSON',
                            'body': '{"refresh_token":"{{refresh_token}}"}',
                            'extractors': [{'name': name, 'expr': '$.' + name} for name in ('token', 'refresh_token')]}}},
                    'variables': [{'name': name, 'type': 'CSV', 'data_file_id': 1, 'column': name}
                                  for name in ('username', 'token', 'refresh_token')],
                    'csv_data': {'1': {'rows': [{'username': f'u{vu}', 'token': f'fake-token-u{vu}-1',
                        'refresh_token': f'fake-refresh-u{vu}'} for vu in (1, 2)]}},
                    'steps': [{'id': i, 'name': path, 'method': 'GET', 'url': path,
                        'assertions': [{'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'}]}
                        for i, path in enumerate(('/one', '/two'), 1)]}
                self.assertEqual(validate_snapshot(snap), [])
                with tempfile.TemporaryDirectory(prefix='request-id-native-') as work, \
                        mock.patch.dict(os.environ, {'K6_RUNNER': 'NATIVE'}):
                    engine = K6Engine(snap, work_dir=work)
                    try:
                        engine.prepare(); engine.run()
                    finally:
                        engine.stop()
                    summary = engine.collect()['summary']
                    self.assertIsNotNone(engine.process.poll())
                self.assertEqual(len(rows), 12)
                self.assertEqual((summary['business_total'], summary['success_requests'], summary['failed_requests']), (10, 8, 2))
                self.assertEqual(summary['http_total'], 12)
                self.assertEqual(summary['completed_iterations'], 4)
                for row in rows:
                    self.assertRegex(row['id'], UUID4)
                    self.assertEqual(row['id'], row['same_id'])
                for vu in (1, 2):
                    own = [row for row in rows if row['vu'] == vu]
                    two = [row for row in own if row['path'] == '/two']
                    self.assertEqual([row['status'] for row in two], [401, 200, 200])
                    self.assertEqual(two[0]['id'], two[1]['id'])
                    logical = [row['id'] for row in own if row is not two[1]]
                    self.assertEqual(len(set(logical)), 5)
                    all_logical.extend(logical)
                summaries.append({key: summary[key] for key in ('business_total', 'success_requests',
                    'failed_requests', 'http_total', 'completed_iterations', 'distinct_vus', 'k6_exit_code')})
            self.assertEqual(len(all_logical), len(set(all_logical)))
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        if os.environ.get('REQUEST_ID_EVIDENCE'):
            Path(os.environ['REQUEST_ID_EVIDENCE']).write_text(json.dumps({'status': 'PASS',
                'scope': 'native_loopback_only', 'runs': summaries, 'logical_uuid_count': len(all_logical),
                'uuid_v4_unique': True, 'refresh_retry_same_id': True,
                'no_auth_scope_clobber': True, 'fixture_thread_stopped': True}, indent=2) + '\n', encoding='utf-8')
