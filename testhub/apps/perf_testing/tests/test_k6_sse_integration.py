"""Native candidate tests use only loopback synthetic HTTP, WS and SSE services."""
from copy import deepcopy
import json
import os
import tempfile
import time
import unittest
from unittest import mock
from apps.perf_testing.engines import k6_engine
from .fixtures.support_websocket_server import SupportWebSocketServer
from .test_k6_engine import TEST_ROOT
from .test_k6_websocket_integration import ws_snapshot
from .test_sse_steps import sse_config


@unittest.skipUnless(os.environ.get('K6_SSE_TEST_BIN'), 'bounded SSE candidate required')
class NativeSSETests(unittest.TestCase):
    def fixture(self, server, mode):
        owner = self
        self.sse_auth = []
        base = server.server.RequestHandlerClass
        class Handler(base):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.sse_auth.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers(); self.close_connection = True
                if mode == 'first_error':
                    self.wfile.write(b'event: error\ndata: PRIVATE_EXCEPTION\n\n'); self.wfile.flush()
                    return
                self.wfile.write(b'data: {"ok":false,"value":"PRIVATE_VALUE"}\n\n' if mode == 'assertion' else b'data: {"ok":true,"value":"PRIVATE_VALUE"}\n\n'); self.wfile.flush()
                time.sleep(.03)
                if mode == 'success':
                    self.wfile.write(b'data: [DONE]\n\n')
                elif mode == 'error':
                    self.wfile.write(b'event: error\ndata: PRIVATE_EXCEPTION\n\n')
                self.wfile.flush()
        server.server.RequestHandlerClass = Handler

    def test_platform_mixed_protocol_and_single_stream_http_count(self):
        with SupportWebSocketServer() as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, mock.patch.dict(os.environ,
                {'K6_BIN': os.environ['K6_SSE_TEST_BIN'], 'K6_RUNNER': 'NATIVE'}):
            self.fixture(server, 'success')
            data = ws_snapshot(server.url)
            data['steps'].insert(1, {'id': 4, 'name': 'Stream', 'protocol': 'SSE', 'method': 'POST', 'url': '/stream', 'sse_config': sse_config()})
            self.assertEqual(k6_engine.validate_snapshot(data), [])
            engine = k6_engine.K6Engine(data, work_dir=work)
            engine.prepare(); engine.run()
            summary = engine.collect()['summary']
            self.assertEqual(summary['http_total'], 3)
            self.assertEqual(summary['total_requests'], 4)
            self.assertEqual(summary['failed_requests'], 0)
            stream = summary['sse']
            self.assertEqual(stream['streams'], dict(started=1, completed=1, success=1, failed=0, incomplete=0))
            self.assertEqual(stream['events'], 2)
            self.assertEqual(stream['first_event']['count'], 1)
            self.assertGreater(stream['completion']['avg_ms'], stream['first_event']['avg_ms'])
            self.assertEqual(self.sse_auth, ['Bearer FAKE_TOKEN_0'])
            self.assertNotIn('PRIVATE_VALUE', json.dumps(engine.collect()))
            self.assertNotIn('FAKE_TOKEN', json.dumps(engine.collect()))

    def test_failed_stream_clears_outputs_and_blocks_its_bounded_group(self):
        for mode, reason in [('error', 'event_error'), ('eof', 'unexpected_eof'), ('first_error', 'event_error'), ('assertion', 'assertion_failed')]:
            with self.subTest(mode=mode), SupportWebSocketServer() as server, tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, mock.patch.dict(os.environ,
                    {'K6_BIN': os.environ['K6_SSE_TEST_BIN'], 'K6_RUNNER': 'NATIVE'}):
                self.fixture(server, mode)
                data = ws_snapshot(server.url, rounds=2)
                policy = dict(group_id='stream', vu_start=1, vu_end=1, max_runs_per_vu=1, min_interval_ms=0)
                data['steps'] = [data['steps'][0], {'id': 4, 'name': 'Stream', 'protocol': 'SSE', 'method': 'POST',
                    'url': '/stream', 'sse_config': sse_config(), 'execution_policy': policy},
                    {'id': 5, 'name': 'Dependent', 'method': 'GET', 'url': '/after/{{stream_value}}', 'execution_policy': policy}]
                self.assertEqual(k6_engine.validate_snapshot(data), [])
                engine = k6_engine.K6Engine(data, work_dir=work)
                engine.prepare(); engine.run()
                summary = engine.collect()['summary']
                self.assertEqual(summary['sse']['streams']['failed'], 1)
                self.assertEqual(summary['sse']['errors'][0]['reason'], reason)
                self.assertEqual(summary['sse']['first_event']['count'], 1)
                if mode == 'assertion':
                    self.assertEqual(summary['sse']['stream_metrics'][0]['diagnostics'], [dict(reason='assertion_failed',
                        scope='rule_assertion', event_index=1, rule_index=1, condition_index=1, count=1)])
                self.assertEqual(summary['http_total'], 3)
                self.assertFalse(any(path.startswith('/after/') for path in server.http_calls))
                self.assertEqual(summary['execution_policy']['groups'][0]['blocked_steps'], 1)
