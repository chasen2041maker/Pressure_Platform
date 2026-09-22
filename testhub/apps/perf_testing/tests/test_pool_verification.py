"""Prepared-pool verification evidence; synthetic data, no target requests."""
from copy import deepcopy
import importlib
from types import SimpleNamespace
import tempfile
from pathlib import Path
import uuid
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext


class EvidenceTests(SimpleTestCase):
    def setUp(self):
        self.service = importlib.import_module('apps.perf_testing.services.pool_verification')
        self.snapshot = {
            'execution_id': 20, 'scenario_id': 11, 'engine': 'K6',
            'load_config': {'concurrency': 1, 'iterations_per_vu': 1, '_purpose': 'debug'},
            'env_config': {'base_url': 'http://fixture.invalid'}, 'variables': [],
            'environment_sources': [], 'account_pool': None, 'runtime_config': {},
            'csv_data': {}, 'sla_config': {}, 'perf_targets': {},
            'steps': [{'id': 31, 'name': 'same-name', 'method': 'GET', 'url': '/one',
                       'enabled': True, 'is_setup': False,
                       'assertions': [{'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'}]}],
        }
        self.batch = SimpleNamespace(id=3, project_id=5, scenario_id=11, created_by_id=7,
            entries=[{'prepared_id': 9, 'revision': 2, 'step_id': 31,
                      'definition_hash': 'definition',
                      'snapshot_hash': self.service._snapshot_hash(self.snapshot)}])
        self.metric = {'step_id': 31, 'step_name': 'same-name', 'method': 'GET', 'url': 'step:31',
                       'phase': 'business', 'total': 1, 'success': 1, 'failed': 0}
        self.stats = [{key: value for key, value in self.metric.items() if key not in ('step_id', 'phase')}]
        self.execution = SimpleNamespace(id=20, project_id=5, scenario_id=11, executed_by_id=7,
            status='COMPLETED', summary={'step_metrics': [self.metric], 'business_total': 1,
                'business_started': 1, 'http_total': 1, 'http_started': 1,
                'business_incomplete': 0, 'http_incomplete': 0, 'completed_iterations': 1,
                'total_requests': 1, 'success_requests': 1, 'failed_requests': 0,
                'distinct_vus': 1, 'setup_failed_vus': 0, 'auth_failed_vus': 0})

    def result(self):
        return self.service.evaluate_evidence(self.batch, self.execution, self.snapshot, self.stats)[0]

    def test_exact_snapshot_step_and_persisted_counts_pass(self):
        result = self.result()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual((result['total'], result['success'], result['failed']), (1, 1, 0))

    def test_http_200_with_business_assertion_failure_is_failed(self):
        self.metric.update(success=0, failed=1)
        self.stats[0].update(success=0, failed=1)
        self.execution.summary.update(success_requests=0, failed_requests=1)
        self.assertEqual(self.result()['status'], 'failed')

    def test_auth_failure_leaves_successful_request_unverified_not_failed(self):
        self.snapshot['steps'][0].update(name='healthz', url='/healthz')
        self.metric['step_name'] = self.stats[0]['step_name'] = 'healthz'
        self.snapshot['steps'].append({**deepcopy(self.snapshot['steps'][0]),
                                       'id': 32, 'name': 'me', 'url': '/me'})
        self.snapshot['runtime_config'] = {'auth_profile': {
            'mode': 'STATIC', 'transport': 'BEARER', 'access_token_variable': 'load_token'}}
        self.batch.entries.append({**self.batch.entries[0], 'prepared_id': 10, 'step_id': 32})
        for entry in self.batch.entries:
            entry['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        failed = {**deepcopy(self.metric), 'step_id': 32, 'step_name': 'me',
                  'url': 'step:32', 'success': 0, 'failed': 1}
        self.execution.summary['step_metrics'].append(failed)
        self.stats.append({key: value for key, value in failed.items() if key not in ('step_id', 'phase')})
        self.execution.summary.update(business_total=2, business_started=2, http_total=2,
            http_started=2, total_requests=2, success_requests=1, failed_requests=1,
            completed_iterations=0, auth_failed_vus=1)
        for status in ('FAILED', 'STOPPED', 'TIMEOUT', 'ABORTED'):
            with self.subTest(status=status):
                self.execution.status = status
                successful, rejected = self.service.evaluate_evidence(
                    self.batch, self.execution, self.snapshot, self.stats)
                self.assertEqual((successful['status'], successful['verdict']), ('unverified', 'incomplete'))
                self.assertEqual((successful['total'], successful['success'], successful['failed']), (1, 1, 0))
                self.assertEqual((rejected['status'], rejected['verdict']), ('failed', 'failed'))
                self.assertEqual((rejected['total'], rejected['success'], rejected['failed']), (1, 0, 1))

    def test_missing_step_is_not_run(self):
        self.execution.summary['step_metrics'] = []
        self.stats.clear()
        self.assertEqual(self.result()['status'], 'not_run')

    def test_http_only_is_explicit_and_never_business_passed(self):
        self.snapshot['steps'][0]['assertions'] = [{'type': 'STATUS_CODE', 'expected': '200'}]
        self.batch.entries[0]['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        self.assertEqual(self.result()['verdict'], 'http_only')
        self.assertEqual(self.result()['status'], 'unverified')

    def test_contains_proof_requires_actual_matching_counts_and_complete_execution(self):
        self.snapshot['steps'][0]['assertions'] = [
            {'type': 'STATUS_CODE', 'expected': 200}, {'type': 'CONTAINS', 'expected': '<html'}]
        self.batch.entries[0]['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        self.assertEqual(self.result()['status'], 'passed')
        self.metric.update(success=0, failed=1)
        self.stats[0].update(success=0, failed=1)
        self.execution.summary.update(success_requests=0, failed_requests=1)
        self.assertEqual(self.result()['status'], 'failed')
        self.metric.update(success=1, failed=0)
        self.stats[0].update(success=1, failed=0)
        self.execution.summary.update(success_requests=1, failed_requests=0)
        self.execution.status = 'STOPPED'
        self.assertEqual(self.result()['verdict'], 'incomplete')
        self.execution.status = 'COMPLETED'
        self.snapshot['steps'][0]['assertions'][1]['expected'] = '<changed'
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_empty_or_invalid_contains_is_not_business_evidence(self):
        for rule in ([{'type': 'CONTAINS', 'expected': value} for value in (None, '', ' \n', 0, False, [], {})]
                     + [{'type': 'CONTAINS', 'expected': '<html', 'operator': 'ne'}]):
            with self.subTest(rule=rule):
                self.snapshot['steps'][0]['assertions'] = [{'type': 'STATUS_CODE', 'expected': 200}, rule]
                self.batch.entries[0]['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
                self.assertEqual(self.result()['verdict'], 'http_only')

    def test_changed_snapshot_or_execution_owner_cannot_pass(self):
        original = deepcopy(self.snapshot)
        for field, value in [('env_config', {'base_url': 'http://other.invalid'}),
                             ('runtime_config', {'auth_profile': {'mode': 'STATIC'}}),
                             ('csv_data', {'changed': {'rows': [{'secret': 'changed'}]}})]:
            self.snapshot = {**deepcopy(original), field: value}
            self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.snapshot = original
        self.execution.project_id = 6
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_duplicate_or_wrong_step_association_cannot_pass(self):
        self.execution.summary['step_metrics'].append(deepcopy(self.metric))
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.execution.summary['step_metrics'] = [self.metric]
        self.stats[0]['url'] = 'step:32'
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_counts_are_strict_and_interrupted_completion_never_passes(self):
        for value in (True, -1, 1.5, '1', None):
            with self.subTest(value=value):
                self.metric['total'] = value
                self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.metric['total'] = 1
        self.execution.summary['http_incomplete'] = 1
        self.assertNotEqual(self.result()['status'], 'passed')
        self.execution.summary['http_incomplete'] = 0
        for status in ('STOPPED', 'FAILED', 'RUNNING'):
            self.execution.status = status
            self.assertNotEqual(self.result()['status'], 'passed')

    def test_missing_or_duplicate_persisted_stats_never_pass(self):
        self.stats.clear()
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')
        self.stats = [deepcopy(self.metric), deepcopy(self.metric)]
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_two_same_named_steps_are_proved_by_exact_ids(self):
        self.snapshot['steps'].append({**deepcopy(self.snapshot['steps'][0]), 'id': 32, 'url': '/two'})
        self.batch.entries.append({**self.batch.entries[0], 'prepared_id': 10, 'step_id': 32})
        for entry in self.batch.entries: entry['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        metric = {**deepcopy(self.metric), 'step_id': 32, 'url': 'step:32'}
        self.execution.summary['step_metrics'].append(metric)
        self.stats.append({key: value for key, value in metric.items() if key not in ('step_id', 'phase')})
        self.execution.summary.update(business_total=2, business_started=2, http_total=2,
            http_started=2, total_requests=2, success_requests=2)
        results = self.service.evaluate_evidence(self.batch, self.execution, self.snapshot, self.stats)
        self.assertEqual([item['status'] for item in results], ['passed', 'passed'])
        self.stats[0].update(success=0, failed=1)
        self.assertTrue(all(item['status'] != 'passed' for item in
            self.service.evaluate_evidence(self.batch, self.execution, self.snapshot, self.stats)))


class VerificationLaunchTests(TestCase):
    def setUp(self):
        from apps.perf_testing import models
        from apps.perf_testing.services import api_catalog, prepared_requests, pool_verification
        self.models = models; self.core = prepared_requests; self.service = pool_verification
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.override = override_settings(PERF_PRIVATE_ROOT=Path(self.temp.name) / 'private')
        self.override.enable(); self.addCleanup(self.override.disable)
        self.user = get_user_model().objects.create_user(username='verification-owner')
        self.project = models.PerfProject.objects.create(name='synthetic', owner=self.user)
        self.environment = models.PerfEnvironment.objects.create(name='synthetic', scope='PROJECT',
            project=self.project, created_by=self.user, base_url='http://fixture.invalid', variables=[])
        schema = {'type': 'object', 'required': ['code'],
                  'properties': {'code': {'type': 'string', 'const': 'OK'}}}
        response = {'description': 'ok', 'content': {getattr(self, 'response_media', 'application/json'): {'schema': schema}}}
        doc = {'openapi': '3.1.0', 'paths': {'/one': {'get': {'responses': {'200': response}}}}}
        api_catalog.import_document(self.project.pk, api_catalog.parse_document(doc), 0, self.user)
        config = {**prepared_requests.DEFAULT_CONFIG, 'environment': self.environment.pk}
        prepared_requests.save_config(self.project.pk, {'expected_config_revision': 0, 'config': config}, self.user)
        prepared_requests.prepare(self.project.pk, {'expected_catalog_version': 1, 'expected_config_revision': 1}, self.user)
        self.row = models.PerfPreparedRequest.objects.get(project=self.project)
        self.key = str(uuid.uuid4())

    def start(self):
        return self.service.start_verification(self.project.pk, [{'id': self.row.pk, 'revision': self.row.revision}],
                                               1, self.key, self.user)

    def launch(self, scene, user):
        from apps.perf_testing.services import executor, k6_execution
        execution = self.models.PerfExecution.objects.create(scenario=scene, project=self.project,
            execution_no=f'synthetic-{uuid.uuid4().hex}', executed_by=user, status='PENDING')
        snapshot = executor.build_snapshot(scene, user=user, load_config={'model': 'CONCURRENCY', 'concurrency': 1,
            'iterations_per_vu': 1, 'duration': 60, 'ramp_up': 0, 'max_requests': 0, '_purpose': 'debug'})
        snapshot['execution_id'] = execution.pk
        k6_execution.save_snapshot(Path(self.temp.name) / 'private', execution.pk, snapshot)
        return {'execution': execution, 'preflight': {'passed': True}}

    def finish(self, batch):
        step = batch.scenario.steps.get()
        metric = {'step_id': step.pk, 'step_name': step.name, 'method': step.method, 'url': f'step:{step.pk}',
                  'phase': 'business', 'total': 1, 'success': 1, 'failed': 0}
        execution = batch.execution
        execution.status = 'COMPLETED'
        execution.summary = {'step_metrics': [metric], 'business_total': 1, 'business_started': 1,
            'http_total': 1, 'http_started': 1, 'business_incomplete': 0, 'http_incomplete': 0,
            'completed_iterations': 1, 'total_requests': 1, 'success_requests': 1, 'failed_requests': 0,
            'distinct_vus': 1, 'setup_failed_vus': 0, 'auth_failed_vus': 0}
        execution.save(update_fields=['status', 'summary'])
        self.models.PerfRequestStat.objects.create(execution=execution,
            **{key: value for key, value in metric.items() if key not in ('step_id', 'phase')})

    def test_repeated_post_is_one_launch_and_poll_has_zero_writes(self):
        from apps.perf_testing.services import executor
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch) as launch:
            first = self.start(); second = self.start()
        launch.assert_called_once()
        self.assertEqual(first['id'], second['id'])
        batch = self.models.PerfPreparationBatch.objects.get(pk=first['id'])
        self.assertTrue(batch.scenario.is_preparation)
        self.assertIsNone(batch.scenario.environment_id)
        self.assertEqual(batch.scenario.env_config['base_url'], 'http://fixture.invalid')
        self.finish(batch)
        with CaptureQueriesContext(connection) as queries:
            result = self.service.verification_summary(self.row)
        self.assertEqual(result['status'], 'passed', result)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries))
        self.row.request = {**self.row.request, 'params': {'changed': '1'}}; self.row.save()
        self.assertEqual(self.service.verification_summary(self.row)['status'], 'unverified')

    def test_context_change_before_launch_rejects_without_business_start(self):
        from apps.perf_testing.services import executor
        original = executor.build_snapshot
        def snapshot_then_rotate(*args, **kwargs):
            result = original(*args, **kwargs)
            self.environment.base_url = 'http://changed.invalid'; self.environment.save()
            return result
        with mock.patch.object(executor, 'build_snapshot', side_effect=snapshot_then_rotate), \
                mock.patch.object(executor, 'debug_run') as launch:
            result = self.start()
        launch.assert_not_called()
        self.assertEqual(result['status'], 'rejected')
        self.assertFalse(self.models.PerfExecution.objects.exists())

    def test_environment_change_after_freezing_does_not_retarget_clone(self):
        from apps.perf_testing.services import executor
        def rotate_and_launch(scene, user):
            self.environment.base_url = 'http://changed.invalid'; self.environment.save()
            self.assertEqual(scene.env_config['base_url'], 'http://fixture.invalid')
            self.assertIsNone(scene.environment_id)
            return self.launch(scene, user)
        with mock.patch.object(executor, 'debug_run', side_effect=rotate_and_launch):
            result = self.start()
        self.finish(self.models.PerfPreparationBatch.objects.get(pk=result['id']))
        self.assertEqual(self.service.verification_summary(self.row)['status'], 'unverified')

    def test_uncertain_launch_is_not_repeated_and_missing_snapshot_never_passes(self):
        from apps.perf_testing.services import executor
        def uncertain(scene, user):
            self.launch(scene, user)
            raise OSError('synthetic-secret-must-not-escape')
        with mock.patch.object(executor, 'debug_run', side_effect=uncertain) as launch:
            result = self.start(); repeated = self.start()
        launch.assert_called_once()
        self.assertEqual(result['id'], repeated['id'])
        self.assertEqual(result['launch_state'], 'uncertain')
        self.assertNotIn('synthetic-secret', str(result))
        batch = self.models.PerfPreparationBatch.objects.get(pk=result['id'])
        self.finish(batch)
        (Path(self.temp.name) / 'private' / 'executions' / str(batch.execution_id) / 'snapshot.json').unlink()
        evidence = self.service.verification_summary(self.row)
        self.assertEqual(evidence['status'], 'unverified')
        self.assertEqual(evidence['last_evidence']['verdict'], 'evidence_invalid')

    def test_write_selection_requires_explicit_confirmation_before_any_batch(self):
        from apps.perf_testing.services import executor, api_catalog
        self.row.request = {**self.row.request, 'method': 'POST'}; self.row.save()
        with mock.patch.object(executor, 'debug_run') as launch:
            with self.assertRaises(api_catalog.CatalogInputError): self.start()
        launch.assert_not_called()
        self.assertFalse(self.models.PerfPreparationBatch.objects.exists())

    def test_preflight_rejection_is_idempotent_and_same_key_cannot_change_selection(self):
        from apps.perf_testing.services import executor, api_catalog
        with mock.patch.object(executor, 'debug_run', return_value={'execution': None}) as launch:
            result = self.start(); self.start()
            self.row.revision += 1
            with self.assertRaises(api_catalog.CatalogConflict): self.start()
        launch.assert_called_once()
        self.assertEqual(result['status'], 'rejected')
        self.assertFalse(self.models.PerfExecution.objects.exists())

    def test_request_scoped_cache_reads_evidence_once_and_does_not_outlive_request(self):
        from apps.perf_testing.services import executor
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch): result = self.start()
        self.finish(self.models.PerfPreparationBatch.objects.get(pk=result['id']))
        with mock.patch.object(self.service, 'load_snapshot', wraps=self.service.load_snapshot) as reads, \
                mock.patch.object(self.core, '_context', wraps=self.core._context) as context:
            with self.core.validation_scope(fresh=True):
                self.assertEqual(self.service.verification_summary(self.row)['status'], 'passed')
                self.assertEqual(self.service.verification_summary(self.row)['status'], 'passed')
            self.assertEqual(reads.call_count, 1)
            self.assertEqual(context.call_count, 1)
            self.environment.base_url = 'http://changed.invalid'; self.environment.save()
            self.assertEqual(self.service.verification_summary(self.row)['status'], 'unverified')
            self.assertEqual(context.call_count, 2)


class SSEEvidenceTests(SimpleTestCase):
    result = EvidenceTests.result

    def setUp(self):
        from .test_sse_steps import sse_config
        from apps.perf_testing.services.sse_steps import normalize_sse_config
        from apps.perf_testing.services.k6_sse_metrics import SSEMetrics
        EvidenceTests.setUp(self)
        self.snapshot['steps'][0].update(protocol='SSE', assertions=[], sse_config=normalize_sse_config(sse_config()))
        self.batch.entries[0]['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        streams = SSEMetrics(self.snapshot['steps']); streams.start(0, 1)
        streams.consume({'kind': 'sse_first_event', 'elapsed_ms': 2}, 0, 1)
        streams.consume({'kind': 'sse_result', 'started': True, 'closed': True, 'ok': True,
                         'reason': 'completed', 'phase': 'business', 'elapsed_ms': 4, 'events': 2, 'bytes': 60}, 0, 1)
        self.execution.summary['sse'] = streams.snapshot()

    def test_real_stream_contract_and_exact_persisted_evidence_pass(self):
        self.assertEqual(self.result()['status'], 'passed')
        self.assertTrue(self.service._business_assertions(self.snapshot['steps'][0], require_status=True))

    def test_http_success_without_stream_evidence_never_passes(self):
        for value in (None, {}, {'version': 1, 'stream_metrics': []}):
            with self.subTest(value=value):
                self.execution.summary['sse'] = value
                self.assertNotEqual(self.result()['status'], 'passed')

    def test_failed_incomplete_zero_or_boolean_stream_counts_never_pass(self):
        original = deepcopy(self.execution.summary['sse'])
        for field, value in [('started', 0), ('completed', 0), ('success', 0), ('failed', 1),
                             ('incomplete', 1), ('success', True)]:
            with self.subTest(field=field, value=value):
                self.execution.summary['sse'] = deepcopy(original)
                for item in (self.execution.summary['sse'], self.execution.summary['sse']['stream_metrics'][0]):
                    item['streams'][field] = value
                self.assertNotEqual(self.result()['status'], 'passed')

    def test_eof_error_or_no_terminal_contract_cannot_be_business_success(self):
        original = deepcopy(self.execution.summary['sse'])
        for reason in ('unexpected_eof', 'event_error', 'terminal_missing'):
            with self.subTest(reason=reason):
                self.execution.summary['sse'] = deepcopy(original)
                for item in (self.execution.summary['sse'], self.execution.summary['sse']['stream_metrics'][0]):
                    item['errors'] = [{'phase': 'business', 'reason': reason, 'count': 1}]
                self.assertNotEqual(self.result()['status'], 'passed')
        self.execution.summary['sse'] = original
        self.snapshot['steps'][0]['sse_config']['rules'] = [{'name': 'done', 'event': 'message', 'data': '[DONE]', 'terminal': True}]
        self.batch.entries[0]['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        self.assertNotEqual(self.result()['status'], 'passed')

    def test_missing_duplicate_wrong_step_and_aggregate_mismatch_never_pass(self):
        original = deepcopy(self.execution.summary['sse'])
        for change in ('missing', 'duplicate', 'wrong', 'aggregate', 'no_first_event', 'no_completion', 'no_events', 'no_bytes'):
            with self.subTest(change=change):
                value = self.execution.summary['sse'] = deepcopy(original)
                row = value['stream_metrics'][0]
                if change == 'missing': value['stream_metrics'] = []
                if change == 'duplicate': value['stream_metrics'].append(deepcopy(row))
                if change == 'wrong': row['step_id'] = 32
                if change == 'aggregate': value['events'] += 1
                if change == 'no_first_event': row['first_event']['count'] = 0
                if change == 'no_completion': row['completion']['count'] = 0
                if change == 'no_events': row['events'] = value['events'] = 0
                if change == 'no_bytes': row['bytes'] = value['bytes'] = 0
                self.assertNotEqual(self.result()['status'], 'passed')

    def test_stop_and_changed_event_contract_invalidate_previous_success(self):
        for status in ('FAILED', 'STOPPED', 'TIMEOUT', 'RUNNING'):
            self.execution.status = status
            self.assertNotEqual(self.result()['status'], 'passed')
        self.execution.status = 'COMPLETED'
        self.snapshot['steps'][0]['sse_config']['rules'][0]['assertions'][0]['expected'] = False
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_real_failed_stream_preserves_failure_when_http_counts_agree(self):
        from apps.perf_testing.services.k6_sse_metrics import SSEMetrics
        for reason in ('unexpected_eof', 'event_error', 'terminal_missing'):
            with self.subTest(reason=reason):
                streams = SSEMetrics(self.snapshot['steps']); streams.start(0, 1)
                streams.consume({'kind': 'sse_first_event', 'elapsed_ms': 2}, 0, 1)
                streams.consume({'kind': 'sse_result', 'started': True, 'closed': True, 'ok': False,
                    'reason': reason, 'phase': 'business', 'elapsed_ms': 4, 'events': 1, 'bytes': 30}, 0, 1)
                self.execution.summary.update(sse=streams.snapshot(), success_requests=0, failed_requests=1)
                self.metric.update(success=0, failed=1); self.stats[0].update(success=0, failed=1)
                self.execution.status = 'FAILED'
                self.assertEqual(self.result()['status'], 'failed')

    def test_same_named_streams_are_proved_independently_and_aggregate_exactly(self):
        self.snapshot['steps'].append({**deepcopy(self.snapshot['steps'][0]), 'id': 32, 'url': '/two'})
        self.batch.entries.append({**self.batch.entries[0], 'prepared_id': 10, 'step_id': 32})
        for entry in self.batch.entries: entry['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        metric = {**self.metric, 'step_id': 32, 'url': 'step:32'}
        self.execution.summary['step_metrics'].append(metric)
        self.stats.append({k: v for k, v in metric.items() if k not in ('step_id', 'phase')})
        self.execution.summary.update(business_total=2, business_started=2, http_total=2,
            http_started=2, total_requests=2, success_requests=2)
        value = self.execution.summary['sse']
        value['stream_metrics'].append({**deepcopy(value['stream_metrics'][0]), 'step_id': 32})
        for key in value['streams']: value['streams'][key] *= 2
        for key in ('events', 'bytes'): value[key] *= 2
        for key in ('first_event', 'completion'): value[key]['count'] *= 2
        results = self.service.evaluate_evidence(self.batch, self.execution, self.snapshot, self.stats)
        self.assertEqual([item['status'] for item in results], ['passed', 'passed'])
        value['stream_metrics'][1]['step_id'] = 31
        results = self.service.evaluate_evidence(self.batch, self.execution, self.snapshot, self.stats)
        self.assertTrue(all(item['status'] != 'passed' for item in results))

    def test_sse_setup_requires_complete_matching_stream_before_dependent_pass(self):
        setup = self.snapshot['steps'][0]; setup['is_setup'] = True
        self.snapshot['steps'].append({'id': 32, 'name': 'dependent', 'method': 'GET', 'url': '/two',
            'enabled': True, 'is_setup': False, 'assertions': [{'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'}]})
        self.batch.entries[0].update(step_id=32, setup_dependencies=[{'step_id': 31, 'key': 'setup-31',
            'source_key': 'GET /one', 'revision': 2, 'definition_hash': 'setup-definition'}])
        self.batch.entries[0]['snapshot_hash'] = self.service._snapshot_hash(self.snapshot, self.batch.entries)
        self.metric['phase'] = 'setup'
        metric = {**self.metric, 'step_id': 32, 'step_name': 'dependent', 'url': 'step:32', 'phase': 'business'}
        self.execution.summary['step_metrics'].append(metric)
        self.stats.append({k: v for k, v in metric.items() if k not in ('step_id', 'phase')})
        self.execution.summary.update(http_total=2, http_started=2)
        result = self.result()
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['dependency_results'][0]['status'], 'passed')
        self.execution.summary['sse']['stream_metrics'][0]['streams']['success'] = 0
        self.assertNotEqual(self.result()['status'], 'passed')


class SSEVerificationLaunchTests(TestCase):
    response_media = 'text/event-stream'
    start = VerificationLaunchTests.start
    launch = VerificationLaunchTests.launch

    def setUp(self):
        from .test_sse_steps import sse_config
        VerificationLaunchTests.setUp(self)
        self.environment.variables = [{'name': 'token', 'type': 'CONSTANT', 'value': 'FAKE_TOKEN'}]
        self.environment.save(update_fields=['variables'])
        editor = self.core.editable_request(self.project.pk, self.row.source_metadata['id'], self.user)
        request = {**editor['request'], 'protocol': 'SSE', 'assertions': [], 'extractors': [], 'sse_config': sse_config()}
        self.core.save_edit(self.project.pk, self.row.source_metadata['id'], {
            'expected_catalog_version': 1, 'expected_revision': self.row.revision,
            'request': request, 'preparation': {}}, self.user)
        self.row.refresh_from_db()

    def test_normal_formal_batch_pass_reusable_then_configuration_change_invalidates(self):
        self.assertEqual(self.row.gaps, [])
        from apps.perf_testing.services import executor
        from apps.perf_testing.services.k6_sse_metrics import SSEMetrics
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch): result = self.start()
        batch = self.models.PerfPreparationBatch.objects.get(pk=result['id'])
        VerificationLaunchTests.finish(self, batch)
        step = batch.scenario.steps.get()
        streams = SSEMetrics([{'id': step.pk, 'protocol': 'SSE'}]); streams.start(0, 1)
        streams.consume({'kind': 'sse_first_event', 'elapsed_ms': 2}, 0, 1)
        streams.consume({'kind': 'sse_result', 'started': True, 'closed': True, 'ok': True,
                         'reason': 'completed', 'phase': 'business', 'elapsed_ms': 4, 'events': 2, 'bytes': 60}, 0, 1)
        execution = batch.execution; execution.summary['sse'] = streams.snapshot(); execution.save(update_fields=['summary'])
        with CaptureQueriesContext(connection) as queries:
            evidence = self.service.verification_summary(self.row)
        self.assertEqual(evidence['status'], 'passed', evidence)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries))
        self.core.validate_current(self.row, require_passed=True)
        editor = self.core.editable_request(self.project.pk, self.row.source_metadata['id'], self.user)
        changed = deepcopy(editor['request']); changed['sse_config']['rules'][0]['assertions'][0]['expected'] = False
        self.core.save_edit(self.project.pk, self.row.source_metadata['id'], {
            'expected_catalog_version': 1, 'expected_revision': self.row.revision,
            'request': changed, 'preparation': {}}, self.user)
        self.row.refresh_from_db()
        self.assertEqual(self.service.verification_summary(self.row)['status'], 'unverified')
