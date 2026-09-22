"""Database-backed dependency launch contracts; workers and target traffic are mocked."""
from copy import deepcopy
import uuid
from unittest import mock

from django.conf import settings
from django.test import TestCase

from apps.perf_testing import models
from apps.perf_testing.engines.k6_engine import validate_snapshot
from apps.perf_testing.services import api_catalog, executor, k6_execution, pool_verification, prepared_requests
from . import test_prepared_dependencies as fixtures


class DependencyLaunchTests(TestCase):
    setUp = fixtures.PreparedDependencyTests.setUp
    project_url = fixtures.PreparedDependencyTests.project_url
    upload = fixtures.PreparedDependencyTests.upload
    configure = fixtures.PreparedDependencyTests.configure
    prepare = fixtures.PreparedDependencyTests.prepare
    edit_url = fixtures.PreparedDependencyTests.edit_url
    save_edit = fixtures.PreparedDependencyTests.save_edit
    fixture = fixtures.PreparedDependencyTests.fixture
    ready = fixtures.PreparedDependencyTests.ready
    save_dependency = fixtures.PreparedDependencyTests.save_dependency
    import_prepared = fixtures.PreparedDependencyTests.import_prepared

    def start(self, rows=None, key=None, *, confirmed=True):
        return pool_verification.start_verification(self.project.pk,
            [{'id': row.pk, 'revision': row.revision} for row in (rows or [self.consumer])],
            1, key or str(uuid.uuid4()), self.owner, confirm_writes=confirmed)

    def launch(self, scene, user):
        execution = models.PerfExecution.objects.create(scenario=scene, project=self.project,
            execution_no='synthetic-' + uuid.uuid4().hex, executed_by=user, status='PENDING')
        snapshot = executor.build_snapshot(scene, user=user, load_config={
            'model': 'CONCURRENCY', 'concurrency': 1, 'iterations_per_vu': 1, 'duration': 60,
            'ramp_up': 0, 'max_requests': 0, '_purpose': 'debug'})
        self.assertEqual(validate_snapshot(snapshot), [])
        snapshot['execution_id'] = execution.pk
        k6_execution.save_snapshot(settings.PERF_PRIVATE_ROOT, execution.pk, snapshot)
        return {'execution': execution}

    def finish(self, batch):
        steps = list(batch.scenario.steps.order_by('order'))
        metrics = [{'step_id': step.pk, 'step_name': step.name, 'method': step.method,
            'url': f'step:{step.pk}', 'phase': 'setup' if step.is_setup else 'business',
            'total': 1, 'success': 1, 'failed': 0} for step in steps]
        business_count = sum(not step.is_setup for step in steps)
        execution = batch.execution
        execution.status = 'COMPLETED'
        execution.summary = {'step_metrics': metrics, 'business_total': business_count,
            'business_started': business_count, 'http_total': len(steps), 'http_started': len(steps),
            'business_incomplete': 0, 'http_incomplete': 0, 'completed_iterations': 1,
            'total_requests': business_count, 'success_requests': business_count, 'failed_requests': 0,
            'distinct_vus': 1, 'setup_failed_vus': 0, 'auth_failed_vus': 0}
        execution.save()
        for metric in metrics:
            models.PerfRequestStat.objects.create(execution=execution,
                **{key: value for key, value in metric.items() if key not in ('step_id', 'phase')})

    def test_real_get_template_requires_post_setup_confirmation_before_writes(self):
        self.ready()
        before = models.PerfScenario.objects.count()
        with mock.patch.object(executor, 'debug_run') as launch:
            with self.assertRaises(api_catalog.CatalogInputError):
                self.start(confirmed=False)
        launch.assert_not_called()
        self.assertEqual(models.PerfScenario.objects.count(), before)
        self.assertFalse(models.PerfPreparationBatch.objects.exists())

    def test_shared_real_dependency_freezes_once_and_proves_consumers_only(self):
        self.ready()
        second = models.PerfPreparedRequest.objects.get(source_key='GET /conversations/{id}/unread')
        self.assertEqual(self.save_dependency(consumer=second).status_code, 200)
        second.refresh_from_db()
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch):
            result = self.start([self.consumer, second])
        batch = models.PerfPreparationBatch.objects.get(pk=result['id'])
        self.assertEqual(batch.scenario.steps.filter(is_setup=True).count(), 1)
        self.assertEqual(batch.scenario.steps.filter(is_setup=False).count(), 2)
        self.assertEqual(batch.entries[0]['setup_dependencies'], batch.entries[1]['setup_dependencies'])
        self.finish(batch)
        self.assertEqual([item['status'] for item in pool_verification.batch_summary(batch)['results']], ['passed', 'passed'])
        self.assertEqual(pool_verification.verification_summary(self.producer)['status'], 'unverified')
        for name in ('Fresh one', 'Fresh two'):
            scene = models.PerfScenario.objects.create(project=self.project, name=name, created_by=self.owner, engine='K6')
            imported = self.import_prepared(scene, [self.consumer])
            self.assertEqual(imported.status_code, 201, imported.data)
            self.assertEqual(list(scene.steps.order_by('order').values_list('is_setup', flat=True)), [True, False])

    def test_request_key_retries_original_frozen_batch_after_dependency_change(self):
        self.ready()
        key = str(uuid.uuid4())
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch) as launch:
            first = self.start(key=key)
            self.producer.request['params'] = {'changed': 'yes'}
            self.producer.revision += 1
            self.producer.save()
            second = self.start(key=key)
            with self.assertRaises(api_catalog.CatalogConflict):
                self.start()
        launch.assert_called_once()
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(models.PerfPreparationBatch.objects.count(), 1)

    def test_dependency_change_between_freezing_and_launch_never_starts_worker(self):
        self.ready()
        original = executor.build_snapshot
        def freeze_then_change(*args, **kwargs):
            snapshot = original(*args, **kwargs)
            self.producer.request = {**self.producer.request, 'params': {'rotated': 'yes'}}
            self.producer.revision += 1
            self.producer.save()
            return snapshot
        with mock.patch.object(executor, 'build_snapshot', side_effect=freeze_then_change), \
                mock.patch.object(executor, 'debug_run') as launch:
            result = self.start()
        launch.assert_not_called()
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['error_code'], 'verification_definition_changed')
        self.assertEqual(models.PerfExecution.objects.count(), 0)

    def test_selected_producer_is_separate_business_evidence(self):
        self.fixture()
        self.assertEqual(self.save_edit(self.producer.source_metadata['id'], self.producer.revision,
            {'extractors': deepcopy(self.reference['extractors'])}).status_code, 200)
        self.producer.refresh_from_db()
        self.reference.update(revision=self.producer.revision, definition_hash=prepared_requests.definition_hash(self.producer))
        self.assertEqual(self.save_dependency().status_code, 200)
        self.consumer.refresh_from_db()
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch):
            result = self.start([self.producer, self.consumer])
        batch = models.PerfPreparationBatch.objects.get(pk=result['id'])
        self.assertEqual(batch.scenario.steps.count(), 3)
        self.assertEqual(batch.entries[0]['setup_dependencies'], [])
        self.assertEqual(len(batch.entries[1]['setup_dependencies']), 1)
        self.finish(batch)
        self.assertEqual(pool_verification.verification_summary(self.producer)['status'], 'passed')
        self.assertEqual(pool_verification.verification_summary(self.consumer)['status'], 'passed')
