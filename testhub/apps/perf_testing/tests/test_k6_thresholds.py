"""Deterministic request events and isolated Django workers; no target HTTP or k6 process."""
import csv
import gzip
from pathlib import Path
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.perf_testing.engines.k6_engine import K6Engine, validate_snapshot
from apps.perf_testing.services import executor, k6_thresholds as sla
from apps.perf_testing.services.sla import evaluate as legacy_evaluate
from apps.perf_testing.models import PerfProject, PerfScenario, PerfScenarioStep

STEPS = [{'id': 11, 'name': 'same', 'url': '/one'}, {'id': 12, 'name': 'same', 'url': '/two'}]


def metrics() -> dict:
    return {'business_total': 20, 'total_requests': 20, 'failed_requests': 1, 'success_requests': 19,
            'error_rate': 5, 'avg_rt': 30, 'p95_rt': 100, 'tps': 2, 'metric_duration_seconds': 10,
            'http_incomplete': 0, 'business_incomplete': 0, 'completed_iterations': 10,
            'step_metrics': [
                {'step_id': 11, 'total': 10, 'success': 9, 'failed': 1, 'error_rate': 10, 'p95_rt': 200},
                {'step_id': 12, 'total': 10, 'success': 10, 'failed': 0, 'error_rate': 0, 'p95_rt': 50}]}


def config() -> dict:
    return {'enabled': True, 'thresholds': {'error_rate': 6}, 'step_thresholds': [
        {'step_id': 11, 'thresholds': {'error_rate': 9}},
        {'step_id': 12, 'thresholds': {'p95_response_time': 80}}]}


class K6ThresholdTests(SimpleTestCase):
    def test_report_no_samples_keeps_unknown_metric_neutral(self) -> None:
        from apps.perf_testing.tests.test_k6_report import K6ReportTests
        helper = K6ReportTests()
        execution = helper.execution()
        execution.summary.update(business_total=0, p95_rt=0, error_rate=0)
        execution.sla_result, execution.sla_detail = sla.evaluate(
            {'enabled': True, 'thresholds': {'p95_response_time': 1}}, execution.summary, steps=STEPS)
        report = helper.render(execution)
        cards = helper.cards(report)
        self.assertEqual(cards['累计 P95（估算）'], '未采集')
        self.assertEqual(cards['业务失败率'], '未采集')
        self.assertIsNone(execution.sla_detail[0]['actual'])
        self.assertIn('<td class="">未评估</td>', report)
        self.assertNotIn('<td class="fail">未通过</td>', report)

    def test_global_and_duplicate_name_steps_have_independent_results(self) -> None:
        result, rows = sla.evaluate(config(), metrics(), steps=STEPS)
        self.assertEqual(result, 'FAILED')
        self.assertEqual([(row['step_id'], row['status']) for row in rows],
                         [(None, 'PASSED'), (11, 'FAILED'), (12, 'PASSED')])
        self.assertEqual(rows[2]['source'], sla.SOURCE)
        self.assertEqual(rows[2]['percentile_method'], 'fixed_bucket_interpolation')

    def test_zero_requests_unknown_steps_missing_and_nonfinite_are_not_passed(self) -> None:
        cfg = {'enabled': True, 'thresholds': {'p95_response_time': 1000}}
        for value in (None, float('inf'), float('-inf'), float('nan'), True):
            summary = metrics()
            summary['p95_rt'] = value
            result, rows = sla.evaluate(cfg, summary, steps=STEPS)
            self.assertEqual(result, 'NOT_EVALUATED')
            self.assertIsNone(rows[0]['actual'])
        for summary in ({}, dict(metrics(), business_total=0)):
            result, rows = sla.evaluate(cfg, summary, steps=STEPS)
            self.assertEqual(result, 'NOT_EVALUATED')
            self.assertEqual(rows[0]['reason'], 'no_business_requests')
        summary = metrics()
        summary.pop('p95_rt')
        self.assertEqual(sla.evaluate(cfg, summary, steps=STEPS)[0], 'NOT_EVALUATED')
        summary = metrics()
        summary['step_metrics'] = []
        result, rows = sla.evaluate(config(), summary, steps=STEPS)
        self.assertEqual(result, 'NOT_EVALUATED')
        self.assertEqual(rows[1]['reason'], 'no_step_requests')
        result, rows = sla.evaluate(config(), metrics(), steps=STEPS[:1])
        self.assertEqual(result, 'NOT_EVALUATED')
        self.assertEqual(rows[-1]['reason'], 'unknown_step')

    def test_stopped_failed_incomplete_rounds_and_inflight_requests_are_not_passed(self) -> None:
        for status, load, changes, reason in [
            ('STOPPED', {}, {}, 'execution_incomplete'),
            ('FAILED', {}, {}, 'execution_incomplete'),
            ('COMPLETED', {'concurrency': 2, 'iterations_per_vu': 10}, {}, 'iterations_incomplete'),
            ('COMPLETED', {}, {'http_incomplete': 1}, 'requests_incomplete'),
            ('COMPLETED', {}, {'business_incomplete': None}, 'missing_metric')]:
            with self.subTest(reason=reason):
                result, rows = sla.evaluate(config(), dict(metrics(), **changes), steps=STEPS,
                                             execution_status=status, load=load)
                self.assertEqual(result, 'NOT_EVALUATED')
                self.assertEqual(rows[0]['reason'], reason)
                self.assertIsNone(rows[0]['passed'])

    def test_error_rate_uses_exact_counts_not_rounded_display_rate(self) -> None:
        cfg = {'enabled': True, 'thresholds': {'error_rate': 0}}
        summary = dict(metrics(), business_total=100000, failed_requests=1, success_requests=99999, error_rate=0)
        result, rows = sla.evaluate(cfg, summary, steps=STEPS)
        self.assertEqual(result, 'FAILED')
        self.assertEqual(rows[0]['actual'], 0.001)
        summary['success_requests'] = 100000
        self.assertEqual(sla.evaluate(cfg, summary, steps=STEPS)[0], 'NOT_EVALUATED')

    def test_rps_uses_completed_count_and_engine_duration(self) -> None:
        cfg = {'enabled': True, 'thresholds': {'min_tps': 2.001}}
        result, rows = sla.evaluate(cfg, dict(metrics(), tps=999), steps=STEPS)
        self.assertEqual(result, 'FAILED')
        self.assertEqual(rows[0]['actual'], 2)
        self.assertEqual(rows[0]['unit'], 'requests/s')
        self.assertEqual(sla.evaluate(cfg, dict(metrics(), metric_duration_seconds=0), steps=STEPS)[0], 'NOT_EVALUATED')

    def test_config_rejects_unknown_scope_nonfinite_units_and_duplicate_or_deleted_ids(self) -> None:
        variants = [dict(config(), unsupported=True), dict(config(), step_thresholds={}),
                    dict(config(), enabled='false'), dict(config(), abort_on_breach='true')]
        for value in (float('nan'), float('inf'), True, -1):
            variants += [dict(config(), abort_delay=value),
                         dict(config(), thresholds={'error_rate': value})]
        variants += [dict(config(), breach_window=0), dict(config(), thresholds={'error_rate': 101}),
                     dict(config(), thresholds={'p95_response_time': {'value': 1, 'unit': 's'}})]
        for step_id in (99, True, '11', 0):
            variants.append(dict(config(), step_thresholds=[{'step_id': step_id, 'thresholds': {'error_rate': 1}}]))
        variants.append(dict(config(), step_thresholds=config()['step_thresholds'] * 2))
        for cfg in variants:
            with self.subTest(config=cfg), self.assertRaises(ValidationError):
                sla.validate_config(cfg, STEPS)
        for invalid_step in (dict(STEPS[0], enabled=False), dict(STEPS[0], is_setup=True)):
            with self.assertRaises(ValidationError):
                sla.validate_config(config(), [invalid_step, STEPS[1]])
        self.assertEqual(sla.validate_config({'enabled': True, 'step_thresholds': config()['step_thresholds']}, STEPS)['thresholds'], {})

    def test_delay_sustained_breach_reset_and_distinct_rules(self) -> None:
        cfg = dict(config(), abort_on_breach=True, abort_delay=3, breach_window=2)
        detector = sla.BreachDetector(cfg, STEPS)
        for elapsed in (0, 1, 2, 3, 4):
            self.assertFalse(detector.check({'elapsed_seconds': elapsed, 'sla_metrics': metrics()}))
        self.assertTrue(detector.check({'elapsed_seconds': 5, 'sla_metrics': metrics()}))
        self.assertEqual(detector.evidence['details'][0]['step_id'], 11)
        self.assertEqual(detector.evidence['elapsed_seconds'], 5)
        self.assertFalse(detector.check({'elapsed_seconds': 6, 'sla_metrics': metrics()}))
        detector = sla.BreachDetector(dict(cfg, abort_delay=0), STEPS)
        self.assertFalse(detector.check({'elapsed_seconds': 1, 'sla_metrics': metrics()}))
        passing = metrics()
        passing['step_metrics'][0].update(failed=0, success=10)
        passing['step_metrics'][1]['p95_rt'] = 90
        self.assertFalse(detector.check({'elapsed_seconds': 2, 'sla_metrics': passing}))
        self.assertFalse(detector.check({'elapsed_seconds': 3, 'sla_metrics': metrics()}))
        self.assertFalse(detector.check({'elapsed_seconds': 4, 'sla_metrics': {}}))
        self.assertFalse(detector.check({'elapsed_seconds': 5, 'sla_metrics': metrics()}))
        self.assertFalse(detector.check({'elapsed_seconds': 10, 'sla_metrics': metrics()}), 'observation gap resets continuity')
        self.assertFalse(detector.check({'elapsed_seconds': 11, 'sla_metrics': metrics(), 'engine_finished': True}))

    def test_legacy_evaluator_keeps_valid_behavior_but_missing_is_unknown(self) -> None:
        cfg = {'enabled': True, 'thresholds': {'p95_response_time': 100}}
        self.assertEqual(legacy_evaluate(cfg, {'p95_rt': 50})[0], 'PASSED')
        self.assertEqual(legacy_evaluate(cfg, {'p95_rt': 150})[0], 'FAILED')
        for summary in ({}, {'p95_rt': 0, 'total_requests': 0}, {'p95_rt': float('nan')}):
            self.assertEqual(legacy_evaluate(cfg, summary)[0], 'NOT_EVALUATED')

    def test_same_name_step_counts_csv_and_cumulative_quantiles(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            snapshot = {'steps': STEPS, 'env_config': {'base_url': 'http://unused.invalid'},
                        'load_config': {}, 'sla_config': config()}
            self.assertEqual(validate_snapshot(snapshot), [])
            engine = K6Engine(snapshot, work_dir=work, raw_csv_path=str(Path(work) / 'raw.csv.gz'))
            engine._start_ts, engine._end_ts = 1, 11
            engine._open_raw_writer()
            window_p95 = []
            for index, count, elapsed, ok in ((0, 99, 1, True), (1, 1, 1000, False)):
                for _ in range(count):
                    engine._consume_event({'kind': 'request_started', 'step': index, 'vu': 1})
                    engine._consume_event({'kind': 'request', 'step': index, 'vu': 1, 'status': 200,
                                           'elapsed_ms': elapsed, 'ok': ok, 'error': 'AssertionFailed' if not ok else ''})
                window_p95.append(engine.collector.take_window(1)['p95_rt'])
            engine._close_raw_writer()
            summary = engine.collect()['summary']
            self.assertEqual(summary['business_total'], 100)
            self.assertEqual(summary['failed_requests'], 1)
            self.assertEqual(summary['p95_rt'], 1)
            self.assertNotEqual(summary['p95_rt'], sum(window_p95) / 2)
            rows = summary['step_metrics']
            self.assertEqual([(row['step_id'], row['total']) for row in rows], [(11, 99), (12, 1)])
            self.assertEqual([row['step_name'] for row in rows], ['same', 'same'])
            for row in rows:
                self.assertEqual(row['success'] + row['failed'], row['total'])
            with gzip.open(engine.raw_csv_path, 'rt', encoding='utf-8') as fh:
                raw = list(csv.DictReader(fh))
            self.assertEqual(len(raw), 100)
            self.assertEqual(sum(row['success'] == '0' for row in raw), 1)
            self.assertEqual({row['url'] for row in raw}, {'step:11', 'step:12'})


class K6SLAWorkerTests(TransactionTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        override = override_settings(PERF_PRIVATE_ROOT=self.root / 'private', MEDIA_ROOT=self.root / 'media')
        override.enable()
        self.addCleanup(override.disable)
        self.user = get_user_model().objects.create_user(username='c11-isolated')
        self.project = PerfProject.objects.create(name='c11', owner=self.user)
        self.scenario = PerfScenario.objects.create(project=self.project, created_by=self.user, engine='K6', name='c11',
            env_config={'base_url': 'http://unused.invalid'},
            load_config={'model': 'CONCURRENCY', 'concurrency': 1, 'duration': 30, 'iterations_per_vu': 10},
            sla_config={'enabled': True, 'thresholds': {'error_rate': 0},
                        'abort_on_breach': True, 'abort_delay': 2, 'breach_window': 2})
        self.step = PerfScenarioStep.objects.create(scenario=self.scenario, name='business', method='GET', url='/unused')
        for patcher in (mock.patch('apps.perf_testing.engines.k6_version', return_value='test-fixed'),
                        mock.patch.object(executor, 'push_update'), mock.patch.object(executor, 'flush_push_updates'),
                        mock.patch.object(executor, '_notify_if_needed')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_fake_engine(self, *, rounds: int = 10, failed: bool = True) -> tuple:
        created = []

        class EventEngine(K6Engine):
            def prepare(self) -> None:
                self._open_raw_writer()

            def run(self) -> None:
                created.append(self)
                self._start_ts = 100
                for elapsed in range(1, rounds + 1):
                    self._consume_event({'kind': 'request_started', 'step': 0, 'vu': 1})
                    self._consume_event({'kind': 'request', 'step': 0, 'vu': 1, 'status': 200,
                                         'elapsed_ms': 15, 'ok': not failed, 'error': 'AssertionFailed' if failed else ''})
                    self._consume_event({'kind': 'iteration', 'vu': 1})
                    with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic', return_value=100 + elapsed):
                        self._emit_sample()
                    if self._stopping:
                        break
                self._end_ts = 100 + elapsed
                self._close_raw_writer()

        execution = executor.create_execution(self.scenario, user=self.user)
        with mock.patch('apps.perf_testing.engines.get_engine_class', return_value=EventEngine):
            result = executor.run_execution(execution.id)
        return result, created[0]

    def test_sla_dispatches_real_engine_stop_and_preserves_partial_report(self) -> None:
        result, engine = self.run_fake_engine()
        self.assertTrue(engine._stopping)
        self.assertEqual(result.status, 'STOPPED')
        self.assertEqual(result.sla_result, 'NOT_EVALUATED')
        self.assertEqual(result.summary['business_total'], 4)
        self.assertEqual(result.summary['raw_rows'], 4)
        self.assertEqual(result.request_stats.get().failed, 4)
        self.assertIn('SLA', result.summary['stop_reason'])
        self.assertEqual(result.summary['sla_abort']['elapsed_seconds'], 4)
        self.assertEqual(result.summary['sla_abort']['details'][0]['status'], 'FAILED')
        self.assertTrue(result.report_url)
        report = (self.root / 'media' / result.report_url).read_text(encoding='utf-8')
        self.assertIn('整体 SLA 未评估', report)
        self.assertIn('中止依据', report)
        self.assertIn('非原生 k6 thresholds', report)

    def test_completed_execution_can_fail_sla_without_execution_failure(self) -> None:
        self.scenario.sla_config['abort_on_breach'] = False
        self.scenario.save()
        result, engine = self.run_fake_engine()
        self.assertFalse(engine._stopping)
        self.assertEqual(result.status, 'COMPLETED')
        self.assertEqual(result.sla_result, 'FAILED')
        self.assertTrue(result.summary['execution_complete'])

    def test_incomplete_rounds_do_not_pass_even_if_collected_requests_pass(self) -> None:
        result, _ = self.run_fake_engine(rounds=2, failed=False)
        self.assertEqual(result.sla_result, 'NOT_EVALUATED')
        self.assertEqual(result.summary['completion_reason'], 'iterations_incomplete')
        self.assertFalse(result.summary['execution_complete'])

    def test_serializer_checks_current_scene_ids_and_engine_switch(self) -> None:
        from apps.perf_testing.serializers import PerfScenarioSerializer
        from types import SimpleNamespace
        context = {'request': SimpleNamespace(user=self.user)}
        for step_id in (self.step.pk + 99,):
            serializer = PerfScenarioSerializer(self.scenario, data={'sla_config': {
                'enabled': True, 'step_thresholds': [{'step_id': step_id, 'thresholds': {'error_rate': 0}}]}},
                partial=True, context=context)
            self.assertFalse(serializer.is_valid())
            self.assertIn('sla_config', serializer.errors)
        self.scenario.sla_config['step_thresholds'] = [{'step_id': self.step.pk, 'thresholds': {'error_rate': 0}}]
        self.scenario.save()
        serializer = PerfScenarioSerializer(self.scenario, data={'engine': 'LOCUST'}, partial=True, context=context)
        self.assertFalse(serializer.is_valid())
        self.assertIn('sla_config', serializer.errors)
