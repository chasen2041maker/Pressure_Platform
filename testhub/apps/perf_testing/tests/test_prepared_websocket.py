"""Catalog-bound native WebSocket verification; all resources are local fixtures."""
from copy import deepcopy
import os
import json
import uuid
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.test import SimpleTestCase, TransactionTestCase

from apps.perf_testing import models
from apps.perf_testing.services import prepared_requests, pool_verification
from apps.perf_testing.services.k6_websocket_metrics import WebSocketMetrics
from . import test_api_catalog as catalog_fixtures
from . import test_prepared_requests as prepared_fixtures
from . import test_pool_verification as evidence_fixtures
from .test_websocket_steps import push_config
from .fixtures.support_websocket_server import SupportWebSocketServer
from apps.perf_testing.engines import k6_engine


class PreparedWebSocketTests(TransactionTestCase):
    setUp = catalog_fixtures.ApiCatalogTests.setUp
    project_url = catalog_fixtures.ApiCatalogTests.project_url
    upload = catalog_fixtures.ApiCatalogTests.upload
    configure = prepared_fixtures.PreparedRequestTests.configure
    prepare = prepared_fixtures.PreparedRequestTests.prepare
    save_edit = prepared_fixtures.PreparedRequestTests.save_edit
    edit_url = prepared_fixtures.PreparedRequestTests.edit_url

    def setup_socket(self, *, declared=True, base_url=None, required_header=False):
        raw = {'responses': {'101': {'description': 'Upgrade'}}, 'x-websocket': declared}
        if required_header:
            raw['parameters'] = [{'in': 'header', 'name': 'X-Workspace', 'required': True, 'schema': {'type': 'string'}}]
        document = {'openapi': '3.0.3', 'info': {'title': 'Socket', 'version': '1'},
                    'paths': {'/socket': {'get': raw}}}
        self.assertEqual(self.upload(document).status_code, 201)
        from django.core.files.uploadedfile import SimpleUploadedFile
        pool = self.client.post('/api/perf-testing/account-pools/', {
            'project': self.project.pk, 'name': 'Socket fixture', 'identity_column': 'id',
            'field_mapping': json.dumps({'user_id': 'id', 'access_token': 'token'}),
            'file': SimpleUploadedFile('synthetic.csv', b'id,token\nu1,FAKE_TOKEN_0\n')}, format='multipart')
        self.assertEqual(pool.status_code, 201, pool.data)
        env = self.configure(token_variable='access_token', identity_variable='user_id',
                             account_pool_version=pool.data['latest_version']['id'])
        if base_url:
            env.base_url = base_url
        env.save()
        self.assertEqual(self.prepare().status_code, 200)
        return models.PerfPreparedRequest.objects.get(source_key='GET /socket')

    def convert(self, row, **overrides):
        return self.save_edit(row.source_metadata['id'], row.revision, {
            'protocol': 'WEBSOCKET', 'websocket_config': push_config(), 'headers': {},
            'params': {}, 'assertions': [], 'extractors': [], **overrides})

    def test_declared_upgrade_save_reload_and_revision_are_normal(self):
        row = self.setup_socket()
        response = self.convert(row)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['prepared']['status'], 'unverified', response.data)
        row.refresh_from_db()
        self.assertEqual(row.source_metadata['raw']['x-websocket'], True)
        saved = self.client.get(self.edit_url(row.source_metadata['id'])).data
        same = self.save_edit(row.source_metadata['id'], row.revision, saved['request'], saved['preparation'])
        self.assertEqual(same.status_code, 200, same.data)
        self.assertEqual(same.data['prepared']['revision'], row.revision)
        self.assertEqual(same.data['prepared']['protocol'], 'WEBSOCKET')
        old_hash = prepared_requests.definition_hash(row)
        altered = deepcopy(saved['request'])
        altered['websocket_config']['commands'][0]['assertions'][0]['expected'] = 'sh600036'
        self.assertEqual(self.save_edit(row.source_metadata['id'], row.revision, altered).status_code, 200)
        row.refresh_from_db()
        self.assertNotEqual(prepared_requests.definition_hash(row), old_hash)

    def test_undeclared_or_retargeted_source_cannot_upgrade(self):
        row = self.setup_socket(declared=False)
        self.assertEqual(self.convert(row).status_code, 400)

    def test_declared_source_cannot_retarget_or_lie_about_protocol_assertions(self):
        row = self.setup_socket()
        for override in ({'url': '/elsewhere'}, {'method': 'POST'}, {'headers': {'Authorization': 'Bearer {{access_token}}'}},
                         {'assertions': [{'type': 'STATUS_CODE', 'expected': 101}]}):
            self.assertEqual(self.convert(row, **override).status_code, 400)

    def test_upgrade_preserves_required_handshake_contract(self):
        row = self.setup_socket(required_header=True)
        response = self.convert(row)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['prepared']['status'], 'blocked')
        self.assertIn('header/X-Workspace', [gap['field'] for gap in response.data['prepared']['gaps']])
        row.refresh_from_db()
        ready = self.convert(row, headers={'X-Workspace': 'fixture'})
        self.assertEqual(ready.data['prepared']['status'], 'unverified', ready.data)

    @skipUnless(k6_engine.is_available(), 'native fixed k6 required')
    def test_normal_pool_worker_real_native_events_and_import(self):
        from apps.perf_testing.services import executor
        from apps.perf_testing.services.k6_execution import load_snapshot
        first_batch = None
        for mode in ('push_success', 'push_error', 'push_timeout'):
            with self.subTest(mode=mode), SupportWebSocketServer(mode) as server, mock.patch.dict(os.environ, {
                    'K6_RUNNER': 'NATIVE', 'PERF_PRIVATE_ROOT': str(settings.PERF_PRIVATE_ROOT)}):
                # Reuse one catalog/environment; every next preparation is a real revision.
                if mode == 'push_success':
                    row = self.setup_socket(base_url=server.url)
                else:
                    env = models.PerfEnvironment.objects.get(project=self.project)
                    env.base_url = server.url; env.save()
                    self.assertEqual(self.prepare().status_code, 200)
                    row.refresh_from_db()
                config = push_config()
                config.update(command_timeout_ms=200, connect_timeout_ms=500, max_session_ms=1500)
                self.assertEqual(self.convert(row, websocket_config=config).status_code, 200)
                row.refresh_from_db()
                if mode == 'push_success':
                    from apps.perf_testing.services.api_catalog import CatalogInputError
                    with self.assertRaises(CatalogInputError):
                        pool_verification.start_verification(self.project.pk, [{'id': row.pk, 'revision': row.revision}],
                            1, str(uuid.uuid4()), self.owner)
                    self.assertFalse(models.PerfPreparationBatch.objects.exists())
                preflight_results = []
                debug_run = executor.debug_run
                def debug(scene, user):
                    result = debug_run(scene, user=user)
                    preflight_results.append(result['preflight'])
                    return result
                with mock.patch.object(executor, 'spawn_execution') as spawn, mock.patch.object(executor, 'debug_run', side_effect=debug):
                    result = pool_verification.start_verification(self.project.pk,
                        [{'id': row.pk, 'revision': row.revision}], 1, str(uuid.uuid4()), self.owner, confirm_writes=True)
                batch = models.PerfPreparationBatch.objects.get(pk=result['id'])
                self.assertIsNotNone(batch.execution_id, (result, preflight_results))
                spawn.assert_called_once()
                frozen = load_snapshot(executor._private_root(), batch.execution_id)
                self.assertEqual(frozen['steps'][0]['source_metadata']['id'], row.source_metadata['id'])
                self.assertEqual(k6_engine.K6Engine(frozen).work_dir.parent,
                                 Path(settings.PERF_PRIVATE_ROOT) / 'k6_runs')
                executor.run_execution(batch.execution_id)
                batch.execution.refresh_from_db()
                result = pool_verification.batch_summary(batch)
                self.assertEqual(result['results'][0]['status'], 'passed' if mode == 'push_success' else 'failed', result)
                self.assertEqual(batch.execution.summary['websocket']['connections']['unclosed'], 0)
                if mode == 'push_success':
                    first_batch = batch
                    evidence = pool_verification.verification_summary(row)
                    self.assertEqual(evidence['last_evidence']['protocol'], 'WEBSOCKET')
                    scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner,
                        name='Imported socket', load_config={'model': 'CONCURRENCY', 'concurrency': 1, 'duration': 30},
                        **prepared_requests.scenario_kwargs(row))
                    payload = {'expected_catalog_version': 1, 'request_ids': [row.source_metadata['id']],
                               'expected_prepared_revisions': {row.source_key: row.revision}}
                    prepared_requests.import_steps(scene, payload, self.owner)
                    step = scene.steps.get()
                    self.assertEqual(step.protocol, 'WEBSOCKET')
                    self.assertEqual(step.source_request_id, row.source_metadata['id'])
                    self.assertEqual(step.websocket_config, row.request['websocket_config'])
                    scene.refresh_from_db()
                    check = executor.preflight(scene, user=self.owner)
                    self.assertTrue(check['passed'], check['errors'])
        self.assertEqual(pool_verification.batch_summary(first_batch)['results'][0]['status'], 'passed')
        self.assertEqual(pool_verification.verification_summary(row)['status'], 'failed')


class WebSocketEvidenceTests(SimpleTestCase):
    result = evidence_fixtures.EvidenceTests.result

    def setUp(self):
        evidence_fixtures.EvidenceTests.setUp(self)
        step = self.snapshot['steps'][0]
        step.update(protocol='WEBSOCKET', assertions=[], websocket_config=push_config())
        self.batch.entries[0]['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        self.metric['method'] = self.stats[0]['method'] = 'WEBSOCKET'
        metrics = WebSocketMetrics([step])
        metrics.stages['sessions'].update(started=1, completed=1, success=1)
        for stage in ('connect', 'auth', 'event'):
            fields = {'stage': stage, 'command': 0}
            metrics.consume(dict(fields, state='started'), 0, 1)
            metrics.consume(dict(fields, state='completed', ok=True, elapsed_ms=1), 0, 1)
        metrics.consume({'kind': 'ws_connection', 'state': 'opened'}, 0, 1)
        metrics.consume({'kind': 'ws_connection', 'state': 'closed'}, 0, 1)
        self.execution.summary.update(http_total=0, http_started=0, websocket=metrics.snapshot(1, ended=True))

    def test_native_session_event_and_close_evidence_pass(self):
        self.assertEqual(self.result()['status'], 'passed')

    def test_handshake_only_wrong_command_or_unclosed_never_pass(self):
        original = deepcopy(self.execution.summary['websocket'])
        for mutate in (
            lambda ws: ws.update(command_metrics=[]),
            lambda ws: ws['command_metrics'][0].update(step_id=999),
            lambda ws: ws['command_metrics'][0].update(command_index=1),
            lambda ws: ws['events'].update(success=0, failed=1),
            lambda ws: ws['connections'].update(current=None, unclosed=1),
            lambda ws: ws['auth'].update(started=0, completed=0, success=0),
        ):
            self.execution.summary['websocket'] = deepcopy(original)
            mutate(self.execution.summary['websocket'])
            self.assertNotEqual(self.result()['status'], 'passed')

    def test_config_change_invalidates_existing_proof(self):
        self.snapshot['steps'][0]['websocket_config']['commands'][0]['assertions'][0]['expected'] = 'other'
        self.assertEqual(self.result()['verdict'], 'evidence_invalid')

    def test_mixed_http_and_websocket_count_separately(self):
        step = {'id': 32, 'name': 'HTTP', 'method': 'GET', 'url': '/http', 'enabled': True,
                'is_setup': False, 'assertions': [{'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'}]}
        self.snapshot['steps'].append(step)
        self.batch.entries.append({**self.batch.entries[0], 'step_id': 32, 'prepared_id': 10})
        for entry in self.batch.entries:
            entry['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
        metric = dict(self.metric, step_id=32, step_name='HTTP', method='GET', url='step:32')
        self.execution.summary['step_metrics'].append(metric)
        self.stats.append({key: value for key, value in metric.items() if key not in ('step_id', 'phase')})
        self.execution.summary.update(http_total=1, http_started=1, business_started=2,
                                      business_total=2, total_requests=2, success_requests=2)
        self.assertEqual([r['status'] for r in self.service.evaluate_evidence(
            self.batch, self.execution, self.snapshot, self.stats)], ['passed', 'passed'])
        self.execution.summary['http_total'] = 2
        self.assertNotEqual(self.result()['status'], 'passed')

    def test_empty_business_rules_or_malformed_contract_cannot_pass(self):
        for change in (lambda c: c['commands'][0].update(assertions=[]), lambda c: c.update(version=9)):
            change(self.snapshot['steps'][0]['websocket_config'])
            self.batch.entries[0]['snapshot_hash'] = self.service._snapshot_hash(self.snapshot)
            self.assertNotEqual(self.result()['status'], 'passed')

    def test_invalid_saved_ws_container_reports_readiness_gap(self):
        from apps.perf_testing.services import api_catalog
        self.assertFalse(api_catalog.request_readiness(
            {'protocol': 'WEBSOCKET', 'websocket_config': ['invalid']}, {})['ready'])
