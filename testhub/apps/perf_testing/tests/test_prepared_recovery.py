"""Formal prepared-pool reminder groups; isolated account files and synthetic targets."""
from copy import deepcopy
import json
from types import SimpleNamespace
import uuid
from unittest import mock

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.perf_testing import models
from apps.perf_testing.services import api_catalog, executor, prepared_requests as prepared
from apps.perf_testing.services import pool_verification as verification, reminder_recovery as recovery
from . import test_account_pools as pool_fixtures
from .test_reminder_recovery import snapshot


def cleanup_schema():
    # Mobile ReminderCleanupRequest: the body is optional, but {} is not a cleanup request.
    return {'type': 'object', 'additionalProperties': False,
        'required': ['put_request_key', 'put_request_fingerprint'],
        'properties': {'put_request_key': {'type': 'string', 'minLength': 16, 'maxLength': 128},
            'put_request_fingerprint': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
            'expected_rule_id': {'type': 'string', 'pattern': '^[1-9][0-9]*$'},
            'expected_revision': {'type': 'integer', 'minimum': 1}},
        'oneOf': [{'required': ['expected_rule_id', 'expected_revision']},
            {'not': {'anyOf': [{'required': ['expected_rule_id']}, {'required': ['expected_revision']}]}}]}


class PreparedRecoveryTests(TestCase):
    upload = pool_fixtures.AccountPoolTests.upload
    make_version = pool_fixtures.AccountPoolTests.make_version

    def setUp(self):
        pool_fixtures.AccountPoolTests.setUp(self)
        self.version = self.make_version(raw=b'key,password\n100,fixture-token\n',
            field_mapping=json.dumps({'user_id': 'key', 'token': 'password'}))
        self.environment = models.PerfEnvironment.objects.create(project=self.project, scope='PROJECT',
            name='loopback', created_by=self.owner, base_url='http://127.0.0.1:12345')
        schema = {'type': 'object', 'required': ['code'], 'properties': {'code': {'type': 'string', 'const': 'OK'}}}
        operation = {'parameters': [{'name': 'code', 'in': 'path', 'required': True,
            'schema': {'type': 'string'}, 'example': 'sz000001'}],
            'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': schema}}}}}
        doc = {'openapi': '3.1.0', 'paths': {'/api/v1/portfolio/reminders/{code}':
            {method: deepcopy(operation) for method in ('put', 'get', 'delete')}}}
        for method in ('put', 'delete'):
            doc['paths']['/api/v1/portfolio/reminders/{code}'][method]['requestBody'] = {
                'required': True, 'content': {'application/json': {'schema': {'type': 'object'}}}}
        doc['paths']['/api/v1/portfolio/reminders/{code}']['delete']['requestBody'] = {
            'required': False, 'content': {'application/json': {'schema': cleanup_schema()}}}
        api_catalog.import_document(self.project.pk, api_catalog.parse_document(doc), 0, self.owner)
        config = {**prepared.DEFAULT_CONFIG, 'environment': self.environment.pk,
                  'account_pool_version': self.version.pk}
        prepared.save_config(self.project.pk, {'expected_config_revision': 0, 'config': config}, self.owner)
        prepared.prepare(self.project.pk, {'expected_catalog_version': 1, 'expected_config_revision': 1}, self.owner)
        self.declaration = dict(version=1, kind='portfolio_reminder', group_id='owned_reminder',
                                stock_code='sz000001', max_resources=1000)
        self.source = snapshot(1)
        self.rows = {}
        for row in self.project.prepared_requests.all():
            method = row.request['method']
            step = next(s for s in self.source['steps'] if s['method'] == method and 'reminders/' in s['url'])
            editor = prepared.editable_request(self.project.pk, row.source_metadata['id'], self.owner)
            payload = dict(expected_catalog_version=1, expected_revision=row.revision,
                request={**editor['request'], **{key: deepcopy(step[key]) for key in
                    ('method', 'url', 'body_type', 'body')}, 'assertions': [
                        {'type': 'STATUS_CODE', 'expected': 200},
                        {'type': 'JSON_PATH', 'json_path': '$.code', 'expected': 'OK'}]},
                preparation=dict(confirmed_fields=['path/code'], body_reviewed=True,
                                 resource_recovery=deepcopy(self.declaration)))
            prepared.save_edit(self.project.pk, row.source_metadata['id'], payload, self.owner)
            row.refresh_from_db(); self.rows[method] = row

    def start(self, methods=('PUT', 'GET', 'DELETE'), key=None):
        return verification.start_verification(self.project.pk,
            [{'id': self.rows[m].pk, 'revision': self.rows[m].revision} for m in methods],
            1, key or str(uuid.uuid4()), self.owner, confirm_writes=True)

    def launch(self, scene, user):
        recovery.configured_store().heartbeat()
        with mock.patch('apps.perf_testing.engines.k6_version', return_value='fixture-fixed'), \
                mock.patch.object(recovery, 'RecoveryHTTP', return_value=lambda *_:
                    (200, {'recovery': {'version': 'v1', 'enabled': True}})):
            execution = executor.create_execution(scene, user=user, load_config=dict(model='CONCURRENCY',
                concurrency=1, iterations_per_vu=1, duration=60, ramp_up=0, max_requests=0, _purpose='debug'))
        return dict(execution=execution, preflight={'passed': True})

    def test_normal_save_and_formal_launch_generate_exact_five_without_fake_catalog(self):
        self.assertTrue(all(not row.gaps for row in self.rows.values()), [r.gaps for r in self.rows.values()])
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch): result = self.start()
        self.assertEqual(result['launch_state'], 'started', result)
        batch = models.PerfPreparationBatch.objects.get(pk=result['id'])
        frozen = executor._execution_snapshot(batch.execution)
        self.assertEqual([s['method'] for s in frozen['steps']], ['GET', 'PUT', 'GET', 'GET', 'DELETE'])
        self.assertEqual(len(batch.entries), 3)
        helper = batch.scenario.steps.get(url=recovery.RECEIPTS + '{{rr_put_digest}}')
        self.assertIsNone(helper.source_request_id)
        self.assertEqual(helper.source_metadata, {})
        self.assertEqual(batch.scenario.steps.count(), 4)
        self.assertEqual(self.project.prepared_requests.count(), 3)
        recovery.verify_frozen(recovery.configured_store(), batch.execution.pk, frozen)

    def test_official_cleanup_schema_preflight_uses_bound_wire_without_mutating_template(self):
        from apps.perf_testing.services import prepared_recovery
        scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner,
            load_config={'concurrency': 1}, **prepared.scenario_kwargs(self.rows['PUT']))
        prepared_recovery.create_steps(scene, list(self.rows.values()), self.owner, verification=True)
        original = list(scene.steps.values('id', 'body', 'preparation', 'source_metadata'))
        resolved = executor._resolve_environment_inputs(scene, self.owner)
        ready = api_catalog.scenario_readiness(scene, resolved)
        self.assertTrue(all(r['ready'] for r in ready), ready)
        self.assertEqual(original, list(scene.steps.values('id', 'body', 'preparation', 'source_metadata')))
        scene.load_config = {}  # A hidden pool scene defaults to 10, debug execution explicitly uses 1.
        preflight = executor.preflight(scene, load_config={'concurrency': 1, 'duration': 30}, user=self.owner)
        self.assertFalse(any('resource_recovery' in error or 'put_request_' in error
                             for error in preflight['errors']), preflight['errors'])
        scene.load_config = {'concurrency': 1}
        delete = scene.steps.get(method='DELETE')
        self.assertEqual(delete.body, '{}')
        for change in ('role', 'policy', 'body', 'disabled'):
            with self.subTest(change=change):
                saved_runtime = deepcopy(scene.runtime_config)
                if change == 'role':
                    scene.runtime_config['resource_recovery']['delete_step_id'] = scene.runtime_config['resource_recovery']['get_step_id']
                if change == 'policy': delete.execution_policy['max_runs_per_vu'] = 2
                if change == 'body': delete.body = '{"put_request_key":"manual"}'
                if change == 'disabled': scene.steps.filter(method='PUT').update(enabled=False)
                delete.save()
                rows = api_catalog.scenario_readiness(scene, resolved)
                self.assertFalse(next(r for r in rows if r['step_id'] == delete.pk)['ready'], rows)
                scene.runtime_config = saved_runtime
                delete.body = '{}'; delete.execution_policy['max_runs_per_vu'] = 1
                scene.steps.filter(method='PUT').update(enabled=True)

    def test_missing_declaration_keeps_cleanup_required(self):
        row = self.rows['DELETE']
        self.assertEqual(row.request['body'], '{}')
        editor = prepared.editable_request(self.project.pk, row.source_metadata['id'], self.owner)
        prepared.save_edit(self.project.pk, row.source_metadata['id'], dict(expected_catalog_version=1,
            expected_revision=row.revision, request=editor['request'],
            preparation={**editor['preparation'], 'resource_recovery': {}}), self.owner)
        row.refresh_from_db()
        self.assertEqual({g['field'] for g in row.gaps}, {'body/put_request_key', 'body/put_request_fingerprint'})

    def test_actual_concurrency_controls_auth_rows_without_changing_saved_load(self):
        scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner,
            load_config={'concurrency': 10}, **prepared.scenario_kwargs(self.rows['GET']))
        models.PerfScenarioStep.objects.create(scenario=scene, **prepared.step_kwargs(self.rows['GET']))
        resolved = executor._resolve_environment_inputs(scene, self.owner)
        pool = resolved['account_pool']
        rows = resolved['csv_data'][pool['data_key']]['rows']
        rows.append({**rows[0], pool['identity_column']: '101'})  # Distinct owner, duplicate credential.
        for stored, actual, expected in ((10, 1, True), (1, 2, False), (10, None, False), (1, None, True)):
            with self.subTest(stored=stored, actual=actual):
                scene.load_config = {'concurrency': stored}; scene.save(update_fields=['load_config'])
                ready = api_catalog.scenario_readiness(scene, resolved,
                    **({'load_config': {'concurrency': actual}} if actual is not None else {}))
                self.assertEqual(all(row['ready'] for row in ready), expected, ready)
                if not expected:
                    self.assertTrue(any(g['code'] == 'auth_binding' for row in ready for g in row['gaps']))
                scene.refresh_from_db()
                self.assertEqual(scene.load_config, {'concurrency': stored})

    def test_old_editor_preserves_declaration_and_explicit_change_invalidates_revision(self):
        row = self.rows['PUT']; original = row.revision
        editor = prepared.editable_request(self.project.pk, row.source_metadata['id'], self.owner)
        payload = dict(expected_catalog_version=1, expected_revision=row.revision,
                       request=editor['request'], preparation={'confirmed_fields': ['path/code'], 'body_reviewed': True})
        prepared.save_edit(self.project.pk, row.source_metadata['id'], payload, self.owner)
        row.refresh_from_db()
        self.assertEqual(row.preparation['resource_recovery'], self.declaration)
        self.assertEqual(row.revision, original)
        payload['preparation']['resource_recovery'] = {**self.declaration, 'group_id': 'changed_group'}
        prepared.save_edit(self.project.pk, row.source_metadata['id'], payload, self.owner)
        row.refresh_from_db(); self.assertEqual(row.revision, original + 1)
        with self.assertRaises(api_catalog.CatalogConflict):
            prepared.save_edit(self.project.pk, row.source_metadata['id'], payload, self.owner)

    def test_partial_or_mismatched_group_rejected_before_launch(self):
        with mock.patch.object(executor, 'debug_run') as launch:
            with self.assertRaises((ValidationError, api_catalog.CatalogInputError)):
                self.start(('PUT', 'GET'))
            self.rows['DELETE'].preparation['resource_recovery']['group_id'] = 'different'
            self.rows['DELETE'].save()
            with self.assertRaises((ValidationError, api_catalog.CatalogInputError)):
                self.start()
        launch.assert_not_called()
        self.assertFalse(models.PerfPreparationBatch.objects.exists())

    def completed_batch(self):
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch): result = self.start()
        batch = models.PerfPreparationBatch.objects.select_related('execution', 'scenario').get(pk=result['id'])
        frozen = executor._execution_snapshot(batch.execution)
        metrics = [dict(step_id=s['id'], step_name=s['name'], method=s['method'], url=f"step:{s['id']}",
            phase='business', total=1, success=1, failed=0) for s in frozen['steps']]
        batch.execution.status = 'COMPLETED'
        batch.execution.summary = dict(step_metrics=metrics, business_total=5, business_started=5, http_total=5,
            http_started=5, business_incomplete=0, http_incomplete=0, completed_iterations=1, total_requests=5,
            success_requests=5, failed_requests=0, distinct_vus=1, setup_failed_vus=0, auth_failed_vus=0)
        batch.execution.save()
        for metric in metrics:
            models.PerfRequestStat.objects.create(execution=batch.execution,
                **{key: val for key, val in metric.items() if key not in ('step_id', 'phase')})
        return batch, frozen

    def clean(self, batch):
        recovery.configured_store().change(batch.execution_id, 1,
            lambda state, row: row.update(state='CLEANED', cleanup={'rule_id': '77', 'after_revision': 2}))

    def results(self, batch):
        return verification.batch_summary(batch)['results']

    def test_exact_five_plus_signed_cleaned_is_required_and_existing_key_does_not_repeat(self):
        batch, frozen = self.completed_batch()
        self.assertTrue(all(r['status'] != 'passed' for r in self.results(batch)))
        self.clean(batch)
        from apps.perf_testing.services.prepared_recovery import original_snapshot
        original, state = original_snapshot(frozen, batch.execution_id)
        self.assertEqual(verification._snapshot_hash(original, batch.entries), batch.entries[0]['snapshot_hash'])
        self.assertEqual([r['status'] for r in self.results(batch)], ['passed'] * 3, self.results(batch))
        for row in self.rows.values(): prepared.validate_current(row, require_passed=True)
        with mock.patch.object(executor, 'debug_run') as launch:
            repeated = self.start(key=batch.request_key)
        launch.assert_not_called(); self.assertEqual(repeated['id'], batch.pk)
        self.assertNotIn('fixture-token', json.dumps(repeated))

    def test_cancelled_failed_identity_and_missing_auxiliary_are_never_passed(self):
        batch, frozen = self.completed_batch()
        store = recovery.configured_store()
        store.change(batch.execution_id, 1, lambda state, row: row.update(state='CANCELLED'))
        self.assertTrue(all(r['status'] != 'passed' for r in self.results(batch)))
        self.clean(batch)
        identity = batch.execution.summary['step_metrics'][0]
        identity.update(success=0, failed=1)
        batch.execution.summary.update(success_requests=4, failed_requests=1)
        batch.execution.save()
        batch.execution.request_stats.filter(url='step:reminder:identity').update(success=0, failed=1)
        self.assertTrue(all(r['status'] != 'passed' for r in self.results(batch)))
        batch.execution.summary['step_metrics'].pop(2)
        self.assertTrue(all(r['status'] != 'passed' for r in self.results(batch)))

    def test_changed_plan_or_snapshot_cannot_use_prepared_proof(self):
        from apps.perf_testing.services import prepared_recovery
        batch, frozen = self.completed_batch(); self.clean(batch)
        original, _ = prepared_recovery.original_snapshot(frozen, batch.execution_id)
        self.assertEqual(len(original['steps']), 4)
        for field in ('variables', 'csv_data', 'runtime_config', 'steps'):
            corrupted = deepcopy(frozen)
            if field == 'variables': corrupted[field][-1]['secret'] = False
            if field == 'csv_data': corrupted[field][recovery.INTERNAL_DATA]['rows'][0]['rr_owner'] = '999'
            if field == 'runtime_config': corrupted[field]['_reminder_recovery']['origin'] = 'http://other.invalid'
            if field == 'steps': corrupted[field][1]['body'] = '{}'
            with self.subTest(field=field), self.assertRaises(recovery.RecoveryError):
                prepared_recovery.original_snapshot(corrupted, batch.execution_id)

    def test_import_maps_real_ids_and_refuses_enabled_old_delete_without_mutation(self):
        batch, _ = self.completed_batch(); self.clean(batch)
        scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner, name='target', engine='K6',
            load_config={'model': 'CONCURRENCY', 'concurrency': 1000, 'duration': 300})
        rows = [self.rows[m] for m in ('PUT', 'GET', 'DELETE')]
        payload = dict(expected_catalog_version=1, request_ids=[r.source_metadata['id'] for r in rows],
                       expected_prepared_revisions={r.source_key: r.revision for r in rows})
        created, _ = prepared.import_steps(scene, payload, self.owner)
        scene.refresh_from_db()
        self.assertEqual(len(created), 4)
        self.assertEqual(scene.runtime_config['resource_recovery']['put_step_id'], created[0].pk)
        self.assertTrue(all(s.execution_policy['vu_end'] == 1000 for s in created))
        old_count = scene.steps.count()
        with self.assertRaises(ValidationError): prepared.import_steps(scene, payload, self.owner)
        self.assertEqual(scene.steps.count(), old_count)

    def test_lost_recovery_read_ack_keeps_unknown_count_but_confirmed_cleanup_can_pass(self):
        batch, _ = self.completed_batch()
        store = recovery.configured_store(); plan = store.plan(batch.execution_id); item = plan['resources'][0]
        def lost_read(*args):
            raise recovery.RecoveryError('transport_unavailable')
        recovery.reconcile_one(store, batch.execution_id, 1, lost_read, now=100)
        def confirmed(method, path, body='', key=''):
            self.assertEqual(method, 'GET')
            if path == '/api/v1/me': return 200, {'user_id': '100'}
            if path.endswith(item['delete_digest']):
                return 200, dict(request_key=item['delete_digest'], request_fingerprint=item['delete_fingerprint'],
                    operation='delete', stock_code='sz000001', state='committed', created_at='fixture-time',
                    cleanup=dict(outcome='deleted', deleted=True, rule_id='77', before_revision=1, after_revision=2))
            if path == plan['path']: return 404, None
            raise AssertionError('unexpected endpoint')
        recovery.reconcile_one(store, batch.execution_id, 1, confirmed, now=1000)
        self.assertEqual(store.public(batch.execution_id)['unknown_requests'], 1)
        self.assertEqual(store.public(batch.execution_id)['writes'], 0)
        self.assertEqual([r['status'] for r in self.results(batch)], ['passed'] * 3)

    def test_put_delete_pair_creates_unbound_read_and_current_revision_three_four_five_cas(self):
        row = self.rows['PUT']
        for revision in (3, 4, 5):
            editor = prepared.editable_request(self.project.pk, row.source_metadata['id'], self.owner)
            prepared.save_edit(self.project.pk, row.source_metadata['id'], dict(expected_catalog_version=1,
                expected_revision=row.revision, request={**editor['request'], 'name': f'review {revision}'},
                preparation=editor['preparation']), self.owner)
            row.refresh_from_db(); self.assertEqual(row.revision, revision)
        with mock.patch.object(executor, 'debug_run', side_effect=self.launch):
            result = self.start(('DELETE', 'PUT'))
        self.assertEqual(result['launch_state'], 'started')
        batch = models.PerfPreparationBatch.objects.get(pk=result['id'])
        self.assertEqual(batch.scenario.steps.filter(source_request__isnull=True).count(), 2)
        self.assertEqual(len(executor._execution_snapshot(batch.execution)['steps']), 5)

    def test_unknown_fields_and_mismatched_stock_are_not_recovery_declarations(self):
        row = self.rows['PUT']; editor = prepared.editable_request(self.project.pk, row.source_metadata['id'], self.owner)
        for declaration in ({**self.declaration, 'ready': True}, {**self.declaration, 'max_resources': True},
                            {**self.declaration, 'stock_code': 'sz000002'}, None):
            with self.subTest(value=declaration), self.assertRaises(ValidationError):
                prepared.save_edit(self.project.pk, row.source_metadata['id'], dict(expected_catalog_version=1,
                    expected_revision=row.revision, request=editor['request'],
                    preparation={**editor['preparation'], 'resource_recovery': declaration}), self.owner)

    def test_missing_or_double_auxiliary_stats_and_wrong_denominator_cannot_pass(self):
        batch, _ = self.completed_batch(); self.clean(batch)
        self.assertTrue(all(r['status'] == 'passed' for r in self.results(batch)))
        stats = list(batch.execution.request_stats.values('step_name', 'method', 'url', 'total', 'success', 'failed'))
        frozen = executor._execution_snapshot(batch.execution)
        for altered in (stats[1:], stats + [stats[0]]):
            result = verification.evaluate_evidence(batch, batch.execution, frozen, altered)
            self.assertTrue(all(r['status'] != 'passed' for r in result))
        batch.execution.summary['business_total'] = 3
        self.assertTrue(all(r['status'] != 'passed' for r in self.results(batch)))

    def test_only_target_mutation_changes_old_pool_result_and_import_refuses_existing_delete(self):
        batch, _ = self.completed_batch(); self.clean(batch)
        row = self.rows['GET']; old_hash = prepared.definition_hash(row)
        editor = prepared.editable_request(self.project.pk, row.source_metadata['id'], self.owner)
        prepared.save_edit(self.project.pk, row.source_metadata['id'], dict(expected_catalog_version=1,
            expected_revision=row.revision, request={**editor['request'], 'name': 'changed'},
            preparation=editor['preparation']), self.owner)
        row.refresh_from_db(); self.assertNotEqual(prepared.definition_hash(row), old_hash)
        with self.assertRaises(api_catalog.CatalogConflict): prepared.validate_current(row, require_passed=True)
        from apps.perf_testing.services import prepared_recovery
        scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner, name='with-old-delete',
            engine='K6', load_config={'concurrency': 1000})
        old = models.PerfScenarioStep.objects.create(scenario=scene, method='DELETE', url=recovery.PREFIX + 'sz000001')
        with self.assertRaises(ValidationError): prepared_recovery.create_steps(scene, list(self.rows.values()), self.owner)
        self.assertEqual(list(scene.steps.values_list('id', flat=True)), [old.pk])

