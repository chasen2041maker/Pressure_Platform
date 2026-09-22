from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from apps.perf_testing.services.k6_sse_metrics import SSEMetrics
from apps.perf_testing.services.sse_steps import normalize_sse_config
from .test_sse_steps import sse_snapshot, sse_config


class SSEDiagnosticTests(SimpleTestCase):
    def metrics(self, max_events=64):
        steps = sse_snapshot()['steps']
        steps[0]['sse_config'] = normalize_sse_config(dict(sse_config(), max_events=max_events))
        return SSEMetrics(steps)

    def consume(self, metrics, diagnostic, vu=1):
        metrics.start(0, vu)
        metrics.consume(dict(kind='sse_result', started=True, closed=True, ok=False, phase='business',
            reason='assertion_failed', events=1, bytes=20, elapsed_ms=3, diagnostic=diagnostic), 0, vu)

    def test_blank_operator_normalizes_without_expected_or_coercion(self):
        config = sse_config()
        config['rules'][0]['assertions'] = [dict(type='JSON_PATH', expr='$.value', operator='blank')]
        self.assertEqual(normalize_sse_config(config)['rules'][0]['assertions'][0]['operator'], 'blank')
        config['rules'][0]['assertions'][0]['expected'] = ''
        with self.assertRaises(ValidationError):
            normalize_sse_config(config)

    def test_locations_are_bounded_by_frozen_contract_and_never_copy_extra_data(self):
        location = dict(scope='rule_assertion', event_index=1, rule_index=1, condition_index=1)
        metrics = self.metrics()
        self.consume(metrics, location)
        self.consume(metrics, location, 2)
        public = metrics.snapshot()['stream_metrics'][0]
        self.assertEqual(public['diagnostics'], [dict(location, reason='assertion_failed', count=2)])
        self.assertFalse(public['diagnostics_truncated'])
        bad = [None, [], 'PRIVATE', dict(location, data='PRIVATE'), dict(location, scope='PRIVATE'),
            dict(location, event_index=True), dict(location, event_index=65), dict(location, event_index=0),
            dict(location, rule_index=3), dict(location, condition_index=2), dict(location, rule_index=2),
            dict(location, scope='global_assertion'), dict(location, condition_index=False)]
        for item in bad:
            with self.subTest(item=item):
                metrics = self.metrics()
                self.consume(metrics, item)
                public = metrics.snapshot()
                self.assertEqual(public['stream_metrics'][0]['diagnostics'], [])
                self.assertEqual(public['streams']['failed'], 1)
                self.assertEqual(public['streams']['success'], 0)
                self.assertNotIn('PRIVATE', str(public))

    def test_diagnostics_aggregate_32_distinct_positions_and_mark_overflow(self):
        metrics = self.metrics()
        for index in range(1, 36):
            self.consume(metrics, dict(scope='rule_assertion', event_index=index, rule_index=1, condition_index=1), index)
        self.consume(metrics, dict(scope='rule_assertion', event_index=1, rule_index=1, condition_index=1), 100)
        row = metrics.snapshot()['stream_metrics'][0]
        self.assertEqual(len(row['diagnostics']), 32)
        self.assertTrue(row['diagnostics_truncated'])
        self.assertEqual(row['diagnostics'][0]['count'], 2)
        self.assertEqual(row['streams']['failed'], 36)

    def test_html_renders_safe_location_without_changing_existing_metric_rows(self):
        from .test_k6_report import K6ReportTests
        helper = K6ReportTests(); execution = helper.execution()
        metrics = self.metrics()
        execution.steps_snapshot = metrics.steps
        self.consume(metrics, dict(scope='rule_assertion', event_index=1, rule_index=1, condition_index=1))
        execution.summary['sse'] = metrics.snapshot()
        execution.summary['sse']['stream_metrics'][0]['diagnostics'].append(dict(scope='PRIVATE', value='PRIVATE'))
        html = helper.render(execution)
        self.assertIn('SSE 失败定位', html)
        self.assertIn('rule_assertion', html)
        self.assertNotIn('PRIVATE', html)
