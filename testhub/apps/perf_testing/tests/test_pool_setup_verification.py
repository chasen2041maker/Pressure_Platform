from copy import deepcopy
import importlib
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase


existing = importlib.import_module('apps.perf_testing.tests.test_pool_verification')


class SetupEvidenceTests(SimpleTestCase):
    def setUp(self):
        existing.EvidenceTests.setUp(self)
        setup = {'id': 30, 'name': 'open own fixture', 'method': 'POST', 'url': '/open',
                 'enabled': True, 'is_setup': True,
                 'assertions': [{'type': 'STATUS_CODE', 'expected': 200},
                                {'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'}],
                 'extractors': [{'type': 'JSON_PATH', 'name': 'fixture_id', 'expr': '$.data.id'}]}
        self.snapshot['steps'].insert(0, setup)
        self.setup_metric = {'step_id': 30, 'step_name': setup['name'], 'method': 'POST',
                             'url': 'step:30', 'phase': 'setup', 'total': 1, 'success': 1, 'failed': 0}
        self.setup_stat = {k: v for k, v in self.setup_metric.items() if k not in ('step_id', 'phase')}
        self.stats.insert(0, self.setup_stat)
        self.execution.summary['step_metrics'].insert(0, self.setup_metric)
        self.execution.summary.update(http_total=2, http_started=2)
        self.batch.entries[0]['setup_dependencies'] = [{
            'step_id': 30, 'key': 'setup-key', 'source_key': 'POST /open',
            'revision': 1, 'definition_hash': 'setup-definition'}]
        self.sign()

    def sign(self):
        proof = self.service._snapshot_hash(self.snapshot, self.batch.entries)
        for entry in self.batch.entries:
            entry['snapshot_hash'] = proof

    result = existing.EvidenceTests.result

    def test_setup_and_consumer_require_exact_separate_counters(self):
        result = self.result()
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual((result['total'], result['success'], result['failed']), (1, 1, 0))

    def test_missing_setup_metric_or_stat_never_passes(self):
        self.execution.summary['step_metrics'].remove(self.setup_metric)
        self.assertNotEqual(self.result()['status'], 'passed')
        self.execution.summary['step_metrics'].insert(0, self.setup_metric)
        self.stats.remove(self.setup_stat)
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_setup_failure_keeps_unstarted_consumer_not_run(self):
        self.setup_metric.update(success=0, failed=1)
        self.setup_stat.update(success=0, failed=1)
        self.execution.summary['step_metrics'].remove(self.metric)
        self.stats.pop()
        self.execution.status = 'FAILED'
        self.execution.summary.update(completed_iterations=0, setup_failed_vus=1,
                                      business_total=0, business_started=0, total_requests=0,
                                      success_requests=0, failed_requests=0, http_total=1, http_started=1)
        self.assertEqual((self.result()['status'], self.result()['verdict']), ('not_run', 'not_run'))
        dependency = self.result()['dependency_results'][0]
        self.assertEqual(dependency, {'step_id': 30, 'source_key': 'POST /open', 'revision': 1,
            'method': 'POST', 'status': 'failed', 'verdict': 'failed', 'total': 1, 'success': 0, 'failed': 1})

    def test_unexecuted_setup_is_identified_without_claiming_business_failure(self):
        self.execution.summary['step_metrics'].clear()
        self.stats.clear()
        self.execution.status = 'FAILED'
        result = self.result()
        self.assertEqual(result['status'], 'not_run')
        self.assertEqual(result['dependency_results'][0]['status'], 'not_run')

    def test_dependency_summary_never_exposes_runtime_url_headers_or_body(self):
        setup = self.snapshot['steps'][0]
        setup.update(url='/open?token=private-query', headers={'Authorization': 'Bearer private-header'},
                     body={'private': 'request-secret'})
        self.setup_metric['error'] = 'response-secret'
        self.sign()
        result = self.result()
        self.assertEqual(result['dependency_results'][0]['status'], 'passed')
        for private in ('private-query', 'private-header', 'request-secret', 'response-secret'):
            self.assertNotIn(private, str(result))

    def test_setup_failure_does_not_promote_a_successful_consumer(self):
        self.setup_metric.update(success=0, failed=1)
        self.setup_stat.update(success=0, failed=1)
        self.assertNotEqual(self.result()['status'], 'passed')

    def test_failed_consumer_stays_failed_after_successful_setup(self):
        self.metric.update(success=0, failed=1)
        self.stats[-1].update(success=0, failed=1)
        self.execution.summary.update(success_requests=0, failed_requests=1)
        self.assertEqual(self.result()['status'], 'failed')

    def test_http_only_setup_is_not_business_proof(self):
        self.snapshot['steps'][0]['assertions'] = [{'type': 'STATUS_CODE', 'expected': 200}]
        self.sign()
        self.assertNotEqual(self.result()['status'], 'passed')
        self.snapshot['steps'][0]['assertions'] = [{'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'}]
        self.sign()
        self.assertNotEqual(self.result()['status'], 'passed')

    def test_setup_phase_and_single_execution_are_required(self):
        self.setup_metric['phase'] = 'business'
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.setup_metric['phase'] = 'setup'
        self.setup_metric.update(total=2, success=2)
        self.setup_stat.update(total=2, success=2)
        self.execution.summary.update(http_total=3, http_started=3)
        self.assertNotEqual(self.result()['status'], 'passed')

    def test_http_totals_include_setup_without_changing_business_totals(self):
        self.execution.summary.update(http_total=1, http_started=1)
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.execution.summary.update(http_total=2, http_started=2, business_total=2,
                                      business_started=2, total_requests=2, success_requests=2)
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_frozen_setup_request_and_dependency_association_are_signed(self):
        self.snapshot['steps'][0]['url'] = '/different'
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.snapshot['steps'][0]['url'] = '/open'
        self.batch.entries[0]['setup_dependencies'][0]['definition_hash'] = 'different'
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_shared_setup_is_counted_once_and_each_consumer_binding_is_signed(self):
        self.snapshot['steps'].append({**deepcopy(self.snapshot['steps'][-1]), 'id': 32, 'url': '/two'})
        self.batch.entries.append({**deepcopy(self.batch.entries[0]), 'prepared_id': 10, 'step_id': 32})
        metric = {**deepcopy(self.metric), 'step_id': 32, 'url': 'step:32'}
        self.execution.summary['step_metrics'].append(metric)
        self.stats.append({k: v for k, v in metric.items() if k not in ('step_id', 'phase')})
        self.execution.summary.update(business_total=2, business_started=2, total_requests=2,
                                      success_requests=2, http_total=3, http_started=3)
        self.sign()
        results = self.service.evaluate_evidence(self.batch, self.execution, self.snapshot, self.stats)
        self.assertEqual([r['status'] for r in results], ['passed', 'passed'])
        self.batch.entries[1]['setup_dependencies'] = []
        results = self.service.evaluate_evidence(self.batch, self.execution, self.snapshot, self.stats)
        self.assertTrue(all(r['verdict'] == 'evidence_invalid' for r in results))

    def test_duplicate_or_unreferenced_setup_is_invalid_even_with_new_proof(self):
        self.batch.entries[0]['setup_dependencies'].append(deepcopy(self.batch.entries[0]['setup_dependencies'][0]))
        self.sign()
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.batch.entries[0]['setup_dependencies'].pop()
        self.snapshot['steps'].insert(0, {**deepcopy(self.snapshot['steps'][0]), 'id': 29})
        self.sign()
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_unknown_setup_metric_and_snapshot_flag_are_invalid(self):
        self.snapshot['steps'][0]['is_setup'] = False
        self.sign()
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.snapshot['steps'][0]['is_setup'] = True
        self.sign()
        self.execution.summary['step_metrics'].append({**self.setup_metric, 'step_id': 999, 'url': 'step:999'})
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')


class SetupLaunchTests(TestCase):
    setUp = existing.VerificationLaunchTests.setUp
    launch = existing.VerificationLaunchTests.launch

    def setup_descriptor(self):
        kwargs = self.core.step_kwargs(self.row)
        kwargs.update(name='open own fixture', method='POST', url='/open', is_setup=True,
                      assertions=[{'type': 'STATUS_CODE', 'expected': 200},
                                  {'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'}])
        return {'key': 'setup-key', 'row': SimpleNamespace(pk=999), 'kwargs': kwargs,
                'reference': {'source_key': 'POST /open', 'revision': 1,
                              'definition_hash': 'setup-definition', 'extractors': []}}

    def start_with_setup(self, confirm=False):
        return self.service.start_verification(self.project.pk, [{'id': self.row.pk, 'revision': self.row.revision}],
                                               1, self.key, self.user, confirm_writes=confirm)

    def test_write_setup_requires_confirmation_before_creating_any_batch(self):
        from apps.perf_testing.services import api_catalog, executor
        dependency = self.setup_descriptor()
        with mock.patch.object(self.core, 'expanded_setup_steps', return_value=[dependency], create=True), \
                mock.patch.object(self.core, 'resolve_setup_steps', return_value=[dependency], create=True), \
                mock.patch.object(executor, 'debug_run') as launch:
            with self.assertRaises(api_catalog.CatalogInputError):
                self.start_with_setup()
        launch.assert_not_called()
        self.assertFalse(self.models.PerfPreparationBatch.objects.exists())

    def test_launch_freezes_setup_steps_and_consumer_association(self):
        from apps.perf_testing.services import executor, k6_execution
        dependency = self.setup_descriptor()
        with mock.patch.object(self.core, 'expanded_setup_steps', return_value=[dependency], create=True), \
                mock.patch.object(self.core, 'resolve_setup_steps', return_value=[dependency], create=True), \
                mock.patch.object(executor, 'debug_run', side_effect=self.launch):
            result = self.start_with_setup(confirm=True)
        batch = self.models.PerfPreparationBatch.objects.get(pk=result['id'])
        steps = list(batch.scenario.steps.order_by('order'))
        self.assertEqual([(s.method, s.is_setup) for s in steps], [('POST', True), ('GET', False)])
        self.assertEqual(batch.entries[0]['setup_dependencies'], [{
            'step_id': steps[0].pk, 'key': 'setup-key', 'source_key': 'POST /open',
            'revision': 1, 'definition_hash': 'setup-definition'}])
        snapshot = k6_execution.load_snapshot(self.override.options['PERF_PRIVATE_ROOT'], batch.execution_id)
        self.assertEqual(batch.entries[0]['snapshot_hash'], self.service._snapshot_hash(snapshot, batch.entries))

    def test_changed_setup_after_freezing_rejects_before_launch(self):
        from apps.perf_testing.services import executor
        dependency = self.setup_descriptor()
        changed = deepcopy(dependency)
        changed['key'] = 'changed-key'
        current = [dependency]
        build_snapshot = executor.build_snapshot
        def freeze_then_change(*args, **kwargs):
            result = build_snapshot(*args, **kwargs)
            current[:] = [changed]
            return result
        with mock.patch.object(self.core, 'expanded_setup_steps', return_value=[dependency], create=True), \
                mock.patch.object(self.core, 'resolve_setup_steps', side_effect=lambda row: list(current), create=True), \
                mock.patch.object(executor, 'build_snapshot', side_effect=freeze_then_change), \
                mock.patch.object(executor, 'debug_run') as launch:
            result = self.start_with_setup(confirm=True)
        launch.assert_not_called()
        self.assertEqual(result['status'], 'rejected')
        self.assertFalse(self.models.PerfExecution.objects.exists())
