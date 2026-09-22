import gzip
import csv
import json
import tempfile
import time
import unittest
from pathlib import Path

from apps.perf_testing.engines.k6_engine import K6Engine
from apps.perf_testing.tests.test_k6_engine import snapshot, TEST_ROOT


class WebSocketMetricsTests(unittest.TestCase):
    def engine(self, work):
        data = snapshot(1, 1)
        data['steps'] = [{'id': 1, 'name': 'HTTP', 'method': 'GET'},
                         {'id': 2, 'name': 'Socket', 'method': 'GET', 'protocol': 'WEBSOCKET',
                          'websocket_config': {'commands': [{'name': 'Read', 'request': {'action': 'timeline.list'}}]}}]
        engine = K6Engine(data, work_dir=work, raw_csv_path=str(Path(work) / 'raw.csv.gz'))
        engine._start_ts = time.monotonic() - 1
        return engine

    def test_http_and_session_counters_are_separate(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            engine = self.engine(work)
            engine._open_raw_writer()
            for step in (0, 1):
                engine._consume_event({'kind': 'request_started', 'step': step, 'vu': 1})
                engine._consume_event({'kind': 'request', 'step': step, 'vu': 1, 'ok': True,
                                       'elapsed_ms': 20, 'timestamp_ms': 1000, 'status': 101 if step else 200})
            engine._close_raw_writer()
            summary = engine.collect()['summary']
            self.assertEqual(summary['http_total'], 1)
            self.assertEqual(summary['http_started'], 1)
            self.assertEqual(summary['business_total'], 2)
            self.assertEqual(summary['websocket']['sessions']['completed'], 1)
            self.assertEqual(summary['step_metrics'][1]['latency_kind'], 'session')
            with gzip.open(engine.raw_csv_path, 'rt', encoding='utf8') as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(rows[1]['protocol'], 'WEBSOCKET')
            self.assertEqual(rows[1]['latency_kind'], 'session')

    def test_actual_connections_and_incomplete_commands_survive_abort(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            engine = self.engine(work)
            for state in ('started', 'completed'):
                engine._consume_event({'kind': 'ws_stage', 'stage': 'connect', 'state': state,
                                       'step': 1, 'vu': 1, 'ok': True, 'elapsed_ms': 3})
            engine._consume_event({'kind': 'ws_connection', 'state': 'opened', 'step': 1, 'vu': 1})
            engine._consume_event({'kind': 'ws_stage', 'stage': 'command', 'state': 'started', 'command': 0, 'step': 1, 'vu': 1})
            engine._end_ts = time.monotonic()
            summary = engine.collect()['summary']['websocket']
            self.assertEqual(summary['connections']['peak'], 1)
            self.assertIsNone(summary['connections']['current'])
            self.assertEqual(summary['connections']['unclosed'], 1)
            self.assertEqual(summary['commands']['incomplete'], 1)
            self.assertNotIn('SECRET', json.dumps(summary))

    def test_persisted_ws_samples_are_bounded_secret_free_and_idempotent(self):
        from apps.perf_testing.services.k6_samples import sample_payload, sanitized_payload, MAX_PAYLOAD_BYTES
        value = {'sample_seq': 1, 'elapsed_seconds': 1,
                 'websocket': {'version': 1, 'connections': {'observed': True, 'current': None, 'peak': 2, 'unclosed': 2},
                               'commands': {'started': 1, 'completed': 0, 'incomplete': 1},
                               'command_metrics': [dict(step_id=i // 64 + 1, command_index=i % 64, total=1,
                                    name='SECRET_NAME', action='SECRET_ACTION', avg_rt=5, payload='SECRET_FRAME') for i in range(10000)]}}
        result = sample_payload(value)
        self.assertIn('websocket', result)
        self.assertEqual(result['websocket']['connections']['current'], None)
        self.assertTrue(result['websocket']['commands_truncated'])
        self.assertEqual(result['websocket']['commands_total'], 10000)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True).encode()), MAX_PAYLOAD_BYTES)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(sanitized_payload(result), result)

    def test_report_preserves_session_latency_and_protocol_from_frozen_steps(self):
        from types import SimpleNamespace
        from apps.perf_testing.services.reporter import interface_rows
        execution = SimpleNamespace(load_snapshot={'_engine': 'K6'}, summary={'step_metrics': [{'step_id': 2, 'total': 1}]},
            steps_snapshot=[{'id': 2, 'name': 'WS', 'method': 'GET', 'protocol': 'WEBSOCKET', 'request_path': '/socket',
                             'websocket_commands': []}])
        row = interface_rows(execution, [])[0]
        self.assertEqual(row['protocol'], 'WEBSOCKET')
        self.assertEqual(row['latency_kind'], 'session')
