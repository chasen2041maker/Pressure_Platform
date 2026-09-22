"""Automatic interface preparation on disposable databases; never sends requests."""
from copy import deepcopy
import json
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient
from apps.api_testing.models import ApiRequest
from apps.perf_testing import models
from . import test_api_catalog as fixtures


class PreparedRequestTests(TestCase):
    setUp = fixtures.ApiCatalogTests.setUp
    project_url = fixtures.ApiCatalogTests.project_url
    upload = fixtures.ApiCatalogTests.upload
    imported = fixtures.ApiCatalogTests.imported

    def configure(self, **changes):
        env = models.PerfEnvironment.objects.create(project=self.project, name='Synthetic',
            base_url='http://example.invalid', created_by=self.owner)
        config = dict(environment=env.pk, global_environment=None, account_pool_version=None,
            account_pool_group='', token_variable='', identity_variable='')
        config.update(changes)
        result = self.client.put(self.project_url('api-pool/config'),
            {'expected_config_revision': 0, 'config': config}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return env

    def prepare(self, ids=None, version=1, revision=1):
        body = dict(expected_catalog_version=version, expected_config_revision=revision)
        if ids is not None:
            body['request_ids'] = ids
        return self.client.post(self.project_url('api-pool/prepare'), body, format='json')

    def setup_pool(self):
        operations = self.imported()
        env = self.configure()
        response = self.prepare()
        self.assertEqual(response.status_code, 200, response.data)
        return operations, env, response

    def test_common_config_revisions_permissions_and_no_credentials(self):
        result = self.client.get(self.project_url('api-pool/config'))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['revision'], 0)
        env = self.configure()
        env.headers = {'Authorization': 'private-not-for-summary'}
        env.save()
        result = self.client.get(self.project_url('api-pool/config'))
        self.assertNotIn('private-not-for-summary', json.dumps(result.data))
        config = result.data['config']
        result = self.client.put(self.project_url('api-pool/config'),
            {'expected_config_revision': 0, 'config': config}, format='json')
        self.assertEqual(result.status_code, 409)
        config['credentials'] = 'not-accepted'
        result = self.client.put(self.project_url('api-pool/config'),
            {'expected_config_revision': 1, 'config': config}, format='json')
        self.assertEqual(result.status_code, 400)
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.client.get(self.project_url('api-pool/config')).status_code, 404)

    def test_prepare_preserves_real_gaps_and_never_creates_scene_or_runs(self):
        operations, env, response = self.setup_pool()
        rows = {item['source_key']: item for item in response.data['results']}
        self.assertEqual(rows['GET /health']['status'], 'unverified')
        self.assertEqual(rows['GET /items/{id}']['status'], 'blocked')
        self.assertIn('confirm_example', [g['code'] for g in rows['GET /items/{id}']['gaps']])
        self.assertEqual(models.PerfScenario.objects.count(), 1)
        self.assertEqual(models.PerfExecution.objects.count(), 0)
        self.assertEqual(self.scene.steps.count(), 0)
        pool = models.PerfPreparedRequest.objects.get(source_key='POST /items')
        self.assertIs(json.loads(pool.request['body'])['enabled'], False)
        self.assertEqual(pool.preparation, {})
        self.assertEqual(self.prepare().data['results'][0]['revision'], 1)

    def test_stale_config_catalog_asset_and_environment_are_not_passed(self):
        operations, env, response = self.setup_pool()
        health = next(op for op in operations if op['path'] == '/health')
        row = models.PerfPreparedRequest.objects.get(source_key=health['source_key'])
        from apps.perf_testing.services import prepared_requests as service
        original = row.request.copy()
        ApiRequest.objects.filter(pk=health['id']).update(headers={'X-Changed': 'yes'})
        self.assertEqual(service.public_summary(row)['status'], 'stale')
        self.assertEqual(row.request, original)
        self.assertEqual(self.prepare(revision=0).status_code, 409)
        self.assertEqual(self.prepare(version=0).status_code, 409)
        response = self.prepare()
        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.revision, 2)
        env.base_url = 'http://changed.invalid'
        env.save()
        self.assertEqual(service.public_summary(row)['status'], 'stale')
        doc = fixtures.contract()
        doc['paths']['/health']['get']['summary'] = 'Changed'
        self.assertEqual(self.upload(doc, version=1).status_code, 201)
        self.assertEqual(service.public_summary(row)['status'], 'stale')

    def test_prepare_batch_validation_is_atomic(self):
        operations = self.imported()
        self.configure()
        result = self.prepare([operations[0]['id'], 99999])
        self.assertEqual(result.status_code, 400)
        self.assertEqual(models.PerfPreparedRequest.objects.count(), 0)
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.prepare().status_code, 404)

    def test_catalog_summaries_are_safe_and_filterable(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        row.request['headers'] = {'Authorization': 'private-request'}
        row.save()
        listing = self.client.get(self.project_url(), {'prepared_status': 'blocked'})
        self.assertGreater(listing.data['count'], 0)
        self.assertTrue(all(item['prepared']['status'] == 'blocked' for item in listing.data['results']))
        detail = self.client.get(self.project_url(f'api-catalog/requests/{row.source_metadata["id"]}'))
        self.assertIn('prepared', detail.data)
        self.assertNotIn('private-request', json.dumps(detail.data, default=str))
        self.assertIsNone(detail.data['prepared']['last_evidence'])

    def test_manual_preparation_note_is_visible_without_changing_definition_or_evidence(self):
        from apps.perf_testing.services import prepared_requests as service
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        request_id = row.source_metadata['id']
        before = service.public_summary(row)
        definition = service.definition_hash(row)
        note = '功能未实现：当前仓储缺少读取能力。\n真实执行 #49：HTTP 503，断言失败。'
        ApiRequest.objects.filter(pk=request_id).update(description='Swagger explanation\n[压测准备备注]\n' + note + '\n[/压测准备备注]\nOther documentation')
        listing = self.client.get(self.project_url())
        summary = next(item['prepared'] for item in listing.data['results'] if item['id'] == request_id)
        self.assertEqual(summary.pop('preparation_note', None), note)
        self.assertEqual(summary, {key: value for key, value in before.items() if key != 'preparation_note'})
        for endpoint in (f'api-catalog/requests/{request_id}', f'api-pool/requests/{request_id}'):
            response = self.client.get(self.project_url(endpoint))
            self.assertEqual(response.data['prepared'].get('preparation_note'), note)
        self.prepare()
        row.refresh_from_db()
        self.assertEqual(service.definition_hash(row), definition)
        self.assertEqual(models.PerfExecution.objects.count(), 0)

    def test_preparation_note_requires_own_marker_and_works_before_preparing(self):
        operations = self.imported()
        health = next(op for op in operations if op['path'] == '/health')
        for description, expected in [('Swagger explanation', ''), ('[压测准备备注]\n\n', ''),
                                      ('[压测准备备注]\nMissing closing marker', ''),
                                      ('[压测准备备注]\n待数据：需要本人资源。\n[/压测准备备注]', '待数据：需要本人资源。')]:
            ApiRequest.objects.filter(pk=health['id']).update(description=description)
            listing = self.client.get(self.project_url())
            summary = next(item['prepared'] for item in listing.data['results'] if item['id'] == health['id'])
            self.assertEqual(summary.get('preparation_note'), expected)
            self.assertEqual(summary['status'], 'unprepared')
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.client.get(self.project_url()).status_code, 404)

    def import_prepared(self, scene, items, **changes):
        body = dict(request_ids=[item.source_metadata['id'] for item in items], use_prepared=True,
            expected_catalog_version=1,
            expected_prepared_revisions={item.source_key: item.revision for item in items})
        body.update(changes)
        return self.client.post(f'/api/perf-testing/scenarios/{scene.pk}/import-from-api/', body, format='json')

    def test_import_requires_verified_pass_and_complete_revision_map(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        scene = models.PerfScenario.objects.create(project=self.project, name='Empty', created_by=self.owner, engine='K6')
        self.assertEqual(self.import_prepared(scene, [row]).status_code, 409)
        self.assertEqual(scene.steps.count(), 0)
        with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                        return_value={'status': 'passed', 'last_evidence': None}):
            self.assertEqual(self.import_prepared(scene, [row], expected_prepared_revisions={}).status_code, 409)
            result = self.import_prepared(scene, [row])
        self.assertEqual(result.status_code, 201, result.data)
        self.assertTrue(result.data['bindings_applied'])
        scene.refresh_from_db()
        self.assertEqual(scene.environment_id, row.context['environment'])
        self.assertEqual(scene.steps.get().source_metadata, row.source_metadata)

    def test_nonempty_context_and_mixed_missing_templates_reject_without_writes(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        original = deepcopy(self.scene.env_config)
        with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                        return_value={'status': 'passed', 'last_evidence': None}):
            result = self.import_prepared(self.scene, [row])
        self.assertEqual(result.status_code, 409)
        self.scene.refresh_from_db()
        self.assertEqual(self.scene.env_config, original)
        self.assertEqual(self.scene.steps.count(), 0)
        row.delete()
        result = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/import-from-api/',
            {'use_prepared': True, 'request_ids': [row.source_metadata['id']], 'expected_catalog_version': 1,
             'expected_prepared_revisions': {row.source_key: 0}}, format='json')
        self.assertEqual(result.status_code, 409)

    def test_import_accepts_frontend_default_scenario_runtime_and_preserves_it(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        for script_ref in (None, '', {}, {'mode': 'scenario'}):
            with self.subTest(script_ref=script_ref):
                runtime = {'timeout': 30, 'sample_interval': 1, 'keep_alive': True,
                           'proxy': '', 'script_ref': script_ref}
                created = self.client.post('/api/perf-testing/scenarios/', {
                    'project': self.project.pk, 'name': 'Fresh K6 UI', 'engine': 'K6',
                    'environment': None, 'global_environment': None, 'account_pool_version': None,
                    'env_config': {'base_url': '', 'headers': {}}, 'runtime_config': runtime}, format='json')
                self.assertEqual(created.status_code, 201, created.data)
                scene = models.PerfScenario.objects.get(pk=created.data['id'])
                with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                                return_value={'status': 'passed', 'last_evidence': None}):
                    result = self.import_prepared(scene, [row])
                self.assertEqual(result.status_code, 201, result.data)
                self.assertTrue(result.data['bindings_applied'])
                scene.refresh_from_db()
                self.assertEqual(scene.runtime_config, runtime)
                self.assertEqual(scene.environment_id, row.context['environment'])
                self.assertEqual(scene.steps.get().source_metadata, row.source_metadata)

    def test_import_rejects_script_references_and_proxy_without_mutating_scene(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        invalid_refs = ({'mode': 'script', 'data_file_id': 1}, {'mode': 'unknown'},
            {'mode': 'scenario', 'data_file_id': 1}, {'mode': 'scenario', 'jmx_path': '/fixture.jmx'},
            {'mode': 'scenario', 'extra': ''}, {'data_file_id': 1}, 'script', [], False, 0)
        runtimes = [{'script_ref': value} for value in invalid_refs]
        runtimes.append({'script_ref': {'mode': 'scenario'}, 'proxy': 'http://proxy.invalid'})
        for runtime in runtimes:
            with self.subTest(runtime=runtime):
                scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner,
                    name='Rejected runtime', engine='K6', runtime_config=runtime)
                before = models.PerfScenario.objects.filter(pk=scene.pk).values().get()
                with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                                return_value={'status': 'passed', 'last_evidence': None}):
                    result = self.import_prepared(scene, [row])
                self.assertEqual(result.status_code, 409)
                self.assertEqual(models.PerfScenario.objects.filter(pk=scene.pk).values().get(), before)
                self.assertEqual(scene.steps.count(), 0)

    def test_missing_binding_fails_closed_and_plain_mode_remains_compatible(self):
        ops, env, _ = self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        env.delete()
        from apps.perf_testing.services import prepared_requests as service
        self.assertEqual(service.public_summary(row)['status'], 'stale')
        result = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/import-from-api/',
            {'request_ids': [ops[0]['id']], 'use_prepared': False}, format='json')
        self.assertEqual(result.status_code, 201)

    def test_preparation_scenes_hidden_from_regular_counts_but_readable(self):
        scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner,
            name='Preparation', engine='K6', is_preparation=True)
        result = self.client.get('/api/perf-testing/scenarios/')
        self.assertEqual(result.data['count'], 1)
        self.assertEqual(self.client.get(f'/api/perf-testing/scenarios/{scene.pk}/').status_code, 200)
        result = self.client.get(f'/api/perf-testing/projects/{self.project.pk}/')
        self.assertEqual(result.data['scenario_count'], 1)

    def test_business_assertions_resolve_refs_and_preserve_saved_rules(self):
        doc = fixtures.contract()
        doc['components']['responses'] = {'Success': {'description': 'OK', 'content': {
            'application/json': {'schema': {'$ref': '#/components/schemas/Success'}}}}}
        doc['components']['schemas']['Success'] = {'type': 'object', 'required': ['code'],
            'properties': {'code': {'type': 'string', 'const': 'OK'}, 'optional': {'const': 'NOT_REQUIRED'}}}
        doc['paths']['/health']['get']['responses'] = {'200': {'$ref': '#/components/responses/Success'}}
        self.assertEqual(self.upload(doc).status_code, 201)
        operation = next(op for op in self.client.get(self.project_url()).data['results'] if op['path'] == '/health')
        custom = {'type': 'JSON_PATH', 'expr': '$.customer_rule', 'expected': True}
        ApiRequest.objects.filter(pk=operation['id']).update(assertions=[custom])
        self.configure()
        self.assertEqual(self.prepare().status_code, 200)
        rules = models.PerfPreparedRequest.objects.get(source_key='GET /health').request['assertions']
        self.assertEqual(rules, [custom])
        ApiRequest.objects.filter(pk=operation['id']).update(assertions=[])
        self.assertEqual(self.prepare().status_code, 200)
        rules = models.PerfPreparedRequest.objects.get(source_key='GET /health').request['assertions']
        self.assertIn({'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK', 'operator': 'eq'}, rules)
        self.assertFalse(any(r.get('expr') == '$.optional' for r in rules))

    def test_fresh_frontend_inline_environment_is_semantically_empty(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner, name='UI',
            engine='K6', env_config={'base_url': '', 'headers': {}}, runtime_config={'timeout': 30})
        with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                        return_value={'status': 'passed', 'last_evidence': None}):
            result = self.import_prepared(scene, [row])
        self.assertEqual(result.status_code, 201, result.data)
        scene.refresh_from_db()
        self.assertEqual(scene.runtime_config['timeout'], 30)
        scene2 = models.PerfScenario.objects.create(project=self.project, created_by=self.owner, name='TLS',
            engine='K6', env_config={'base_url': '', 'headers': {}, 'verify_ssl': False})
        with mock.patch('apps.perf_testing.services.pool_verification.verification_summary',
                        return_value={'status': 'passed', 'last_evidence': None}):
            self.assertEqual(self.import_prepared(scene2, [row]).status_code, 409)

    def test_account_integrity_read_once_per_batch_and_public_listing(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.perf_testing.services import account_pools
        self.imported()
        result = self.client.post('/api/perf-testing/account-pools/', {
            'project': self.project.pk, 'name': 'Synthetic pool', 'identity_column': 'id',
            'field_mapping': json.dumps({'user_id': 'id', 'token': 'token'}),
            'file': SimpleUploadedFile('synthetic.csv', b'id,token\nu1,private-test-token\n')}, format='multipart')
        self.assertEqual(result.status_code, 201, result.data)
        self.configure(account_pool_version=result.data['latest_version']['id'], token_variable='token', identity_variable='user_id')
        with mock.patch.object(account_pools, 'read_version', wraps=account_pools.read_version) as read:
            result = self.prepare()
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(read.call_count, 1)
        with mock.patch.object(account_pools, 'read_version', wraps=account_pools.read_version) as read:
            result = self.client.get(self.project_url())
            self.assertEqual(result.status_code, 200)
            self.assertEqual(read.call_count, 1)
            self.assertNotIn('private-test-token', json.dumps(result.data, default=str))

    def test_unrelated_catalog_change_keeps_verified_definition_revision(self):
        self.setup_pool()
        from apps.perf_testing.services import prepared_requests as service
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        before = (row.revision, deepcopy(row.source_metadata), service.definition_hash(row))
        doc = fixtures.contract()
        doc['paths']['/items/{id}']['get']['summary'] = 'Only this operation changed'
        self.assertEqual(self.upload(doc, version=1).status_code, 201)
        self.assertEqual(self.prepare(version=2).status_code, 200)
        row.refresh_from_db()
        self.assertEqual((row.revision, row.source_metadata, service.definition_hash(row)), before)

    def test_preparation_copy_rejects_mutation_but_detail_is_readable(self):
        scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner,
            name='Verification', engine='K6', is_preparation=True)
        step = models.PerfScenarioStep.objects.create(scenario=scene, name='Read', url='/health')
        self.assertEqual(self.client.patch(f'/api/perf-testing/scenarios/{scene.pk}/', {'name': 'Changed'}, format='json').status_code, 404)
        self.assertEqual(self.client.patch(f'/api/perf-testing/steps/{step.pk}/', {'url': '/write'}, format='json').status_code, 404)
        self.assertEqual(self.client.post('/api/perf-testing/steps/', {'scenario': scene.pk, 'name': 'Write', 'url': '/write'}, format='json').status_code, 400)
        self.assertEqual(self.client.get(f'/api/perf-testing/scenarios/{scene.pk}/').status_code, 200)
        self.assertEqual(scene.steps.get().url, '/health')

    def test_repaired_source_identity_invalidates_old_snapshot_then_reprepares(self):
        self.setup_pool()
        from apps.perf_testing.services import prepared_requests as service
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        old_id = row.source_metadata['id']
        ApiRequest.objects.filter(pk=old_id).delete()
        self.assertEqual(self.upload(version=1).status_code, 201)
        self.assertEqual(service.public_summary(row)['status'], 'stale')
        self.assertEqual(self.prepare(version=2).status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.revision, 2)
        self.assertNotEqual(row.source_metadata['id'], old_id)
        self.assertEqual(service.public_summary(row)['status'], 'unverified')

    def edit_url(self, request_id):
        return self.project_url(f'api-pool/requests/{request_id}')

    def save_edit(self, request_id, revision, request=None, preparation=None, version=1):
        return self.client.put(self.edit_url(request_id), {'expected_catalog_version': version,
            'expected_revision': revision, 'request': request or {}, 'preparation': preparation or {}}, format='json')

    def test_edit_draft_get_is_readonly_and_save_fills_real_path_gap(self):
        operations = self.imported()
        self.configure()
        op = next(op for op in operations if op['path'] == '/items/{id}')
        draft = self.client.get(self.edit_url(op['id']))
        self.assertEqual(draft.status_code, 200)
        self.assertEqual(draft.data['prepared']['revision'], 0)
        self.assertEqual(models.PerfPreparedRequest.objects.count(), 0)
        self.assertIn('vu_id', draft.data['known_variable_names'])
        result = self.save_edit(op['id'], 0, {'url': '/items/real-selected-id', 'params': {'page': 0, 'enabled': False}})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared']['revision'], 1)
        self.assertFalse(any(g['code'] == 'confirm_example' for g in result.data['prepared']['gaps']))
        row = models.PerfPreparedRequest.objects.get(source_key=op['source_key'])
        self.assertIs(row.request['params']['enabled'], False)
        self.assertEqual(row.request['params']['page'], 0)
        self.assertEqual(models.PerfExecution.objects.count(), 0)

    def test_saved_edits_survive_prepare_environment_changes_and_strip_private_marker(self):
        _, env, _ = self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /items/{id}')
        result = self.save_edit(row.source_metadata['id'], 1, {'url': '/items/selected', 'params': {'page': 0}},
                                {'confirmed_fields': ['path/id']})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.prepare().status_code, 200)
        row.refresh_from_db()
        revision = row.revision
        self.assertEqual(row.request['url'], '/items/selected')
        self.assertEqual(row.request['params'], {'page': 0})
        env.base_url = 'http://new.invalid'; env.save()
        self.assertEqual(self.prepare().status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.revision, revision + 1)
        self.assertEqual(row.request['url'], '/items/selected')
        from apps.perf_testing.services import prepared_requests as service
        step = service.step_kwargs(row)
        self.assertEqual(step['preparation'], {'confirmed_fields': ['path/id']})
        self.assertFalse(any(key.startswith('_') for key in result.data['preparation']))

    def test_edit_compare_and_swap_and_project_access_are_atomic(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        rid = row.source_metadata['id']
        self.assertEqual(self.save_edit(rid, 1, {'params': {'q': 'first'}}).status_code, 200)
        self.assertEqual(self.save_edit(rid, 1, {'params': {'q': 'late'}}).status_code, 409)
        self.assertEqual(self.save_edit(rid, 2, {'params': {'q': 'bad-version'}}, version=0).status_code, 409)
        row.refresh_from_db(); self.assertEqual(row.request['params'], {'q': 'first'})
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.client.get(self.edit_url(rid)).status_code, 404)
        self.assertEqual(self.save_edit(rid, 2, {'url': '/changed'}).status_code, 404)

    def test_editor_masks_and_preserves_credentials_without_exposing_variable_values(self):
        _, env, _ = self.setup_pool()
        env.variables = [{'name': 'private_variable', 'type': 'CONSTANT', 'value': 'environment-secret', 'secret': True}]
        env.save(); self.prepare()
        row = models.PerfPreparedRequest.objects.get(source_key='POST /items')
        body = json.dumps({'name': 'one', 'enabled': False, 'password': 'body-secret'})
        result = self.save_edit(row.source_metadata['id'], row.revision, {
            'headers': {'Authorization': 'Bearer header-secret', 'X-Token': '{{token}}'},
            'params': {'api_key': 'query-secret'}, 'body': body})
        self.assertEqual(result.status_code, 200, result.data)
        public = json.dumps(result.data, default=str)
        for secret in ('environment-secret', 'header-secret', 'query-secret', 'body-secret'):
            self.assertNotIn(secret, public)
        self.assertIn('private_variable', result.data['known_variable_names'])
        self.assertEqual(result.data['request']['headers']['X-Token'], '{{token}}')
        current = result.data
        response = self.save_edit(row.source_metadata['id'], current['prepared']['revision'], current['request'], current['preparation'])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['prepared']['revision'], current['prepared']['revision'])
        row.refresh_from_db()
        self.assertEqual(row.request['headers']['Authorization'], 'Bearer header-secret')
        self.assertEqual(json.loads(row.request['body'])['password'], 'body-secret')

    def test_confirmations_do_not_clear_unsupported_schema_or_body_gaps(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='POST /items')
        result = self.save_edit(row.source_metadata['id'], row.revision, {'body': '{}'},
                                {'body_reviewed': True, 'confirmed_fields': ['body/name']})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared']['status'], 'blocked')
        self.assertIn('required_value', {g['code'] for g in result.data['prepared']['gaps']})
        self.assertEqual(self.save_edit(row.source_metadata['id'], result.data['prepared']['revision'], {},
                                      {'_known_extractors': ['forged']}).status_code, 400)

    def test_invalid_request_fields_and_unknown_mask_cannot_write(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        before = deepcopy(row.request)
        for patch in ({'params': []}, {'body_type': 'SCRIPT'}, {'source_metadata': {}},
                      {'headers': {'Authorization': '******'}}, {'method': 'DELETE'},
                      {'body_type': 'JSON', 'body': '{"password":"******"}'},
                      {'assertions': [{'type': 'JSON_PATH', 'expected': 'OK'}]}):
            with self.subTest(patch=patch):
                self.assertEqual(self.save_edit(row.source_metadata['id'], 1, patch).status_code, 400)
                row.refresh_from_db(); self.assertEqual(row.request, before)

    def test_source_update_keeps_edits_on_prepare_conflict_and_explicit_review_can_save(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        rid = row.source_metadata['id']
        self.assertEqual(self.save_edit(rid, 1, {'params': {'selected': 7}}).status_code, 200)
        doc = fixtures.contract(); doc['paths']['/health']['get']['summary'] = 'New source'
        self.assertEqual(self.upload(doc, version=1).status_code, 201)
        self.assertEqual(self.prepare(version=2).status_code, 409)
        row.refresh_from_db(); self.assertEqual(row.request['params'], {'selected': 7})
        result = self.save_edit(rid, row.revision, {'params': {'selected': 7}}, version=2)
        self.assertEqual(result.status_code, 200, result.data)
        row.refresh_from_db(); self.assertEqual(row.source_catalog_version, 2)
        self.assertEqual(row.request['params'], {'selected': 7})

    def test_explicit_business_rules_resolve_only_automatic_response_gaps(self):
        doc = fixtures.contract()
        doc['paths']['/health']['get']['responses'] = {'200': {'description': 'Complex', 'content': {
            'application/json': {'schema': {'oneOf': [{'type': 'object'}, {'type': 'string'}]}}}}}
        self.assertEqual(self.upload(doc).status_code, 201)
        self.configure(); self.prepare()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        self.assertEqual(row.status, 'blocked')
        only_http = [{'type': 'STATUS_CODE', 'expected': 200}]
        result = self.save_edit(row.source_metadata['id'], row.revision, {'assertions': only_http})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared']['status'], 'blocked')
        explicit = [*only_http, {'type': 'JSON_PATH', 'json_path': '$.code', 'expected': 'OK'}]
        result = self.save_edit(row.source_metadata['id'], result.data['prepared']['revision'], {'assertions': explicit})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared']['status'], 'unverified')
        self.assertEqual(result.data['prepared']['gaps'], [])
        self.assertEqual(self.prepare().status_code, 200)
        row.refresh_from_db(); self.assertEqual(row.request['assertions'], explicit)
        self.assertEqual(row.gaps, [])

    def test_reviewed_contains_resolves_nonjson_gap_and_is_reused_without_evidence(self):
        doc = fixtures.contract()
        doc['paths']['/health']['get']['responses'] = {'200': {'description': 'Document', 'content': {
            'text/html': {'schema': {'type': 'string'}}, 'text/plain': {'schema': {'type': 'string'}}}}}
        self.assertEqual(self.upload(doc).status_code, 201)
        self.configure(); self.prepare()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        self.assertEqual(row.status, 'blocked')
        only_http = [{'type': 'STATUS_CODE', 'expected': 200}]
        result = self.save_edit(row.source_metadata['id'], row.revision, {'assertions': only_http})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared']['status'], 'blocked')
        explicit = [*only_http, {'type': 'CONTAINS', 'expected': '<html'}]
        result = self.save_edit(row.source_metadata['id'], result.data['prepared']['revision'], {'assertions': explicit})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['prepared']['status'], 'unverified')
        self.assertEqual(result.data['prepared']['gaps'], [])
        self.assertIsNone(result.data['prepared']['last_evidence'])
        self.assertEqual(self.prepare().status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.request['assertions'], explicit)
        self.assertEqual(row.gaps, [])

    def test_invalid_contains_cannot_override_response_gaps(self):
        from apps.perf_testing.services import prepared_requests as service
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        before = deepcopy(row.request)
        for expected in (None, '', ' \n', False, 0, [], {}):
            with self.subTest(expected=expected):
                self.assertFalse(service._reviewed_business_assertions([
                    {'type': 'STATUS_CODE', 'expected': 200}, {'type': 'CONTAINS', 'expected': expected}]))
                result = self.save_edit(row.source_metadata['id'], row.revision, {'assertions': [
                    {'type': 'STATUS_CODE', 'expected': 200}, {'type': 'CONTAINS', 'expected': expected}]})
                self.assertEqual(result.status_code, 400, result.data)
                row.refresh_from_db()
                self.assertEqual(row.request, before)
        self.assertFalse(service._reviewed_business_assertions([
            {'type': 'STATUS_CODE', 'expected': 200}, {'type': 'CONTAINS', 'expected': '<html', 'operator': 'ne'}]))

    def test_edit_invalidates_proof_and_preserved_markers_never_reach_steps(self):
        self.setup_pool()
        from apps.perf_testing.services import prepared_requests as service, pool_verification
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        models.PerfPreparationBatch.objects.create(project=self.project, created_by=self.owner, request_key='proof-test',
            entries=[{'prepared_id': row.pk, 'revision': row.revision, 'definition_hash': service.definition_hash(row)}],
            context_fingerprint=row.context_fingerprint)
        proof = {'results': [{'prepared_id': row.pk, 'status': 'passed', 'verdict': 'passed', 'total': 1, 'success': 1, 'failed': 0}]}
        with mock.patch.object(pool_verification, 'batch_summary', return_value=proof):
            self.assertEqual(service.public_summary(row)['status'], 'passed')
            result = self.save_edit(row.source_metadata['id'], row.revision, {'params': {'q': 'changed'}})
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['prepared']['status'], 'unverified')
            self.assertEqual(self.prepare().status_code, 200)
            row.refresh_from_db()
            self.assertEqual(service.public_summary(row)['status'], 'unverified')
            self.assertFalse(any(key.startswith('_') for key in service.step_kwargs(row)['preparation']))

    def test_idempotency_is_dynamic_and_prepare_reports_actual_changes(self):
        doc = fixtures.contract()
        doc['paths']['/health']['get']['parameters'] = [{'in': 'header', 'name': 'Idempotency-Key',
            'required': True, 'schema': {'type': 'string'}}]
        self.assertEqual(self.upload(doc).status_code, 201)
        self.configure()
        first = self.prepare()
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['new_count'], 3)
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        self.assertEqual(row.request['headers']['Idempotency-Key'], '{{request_id}}')
        result = self.save_edit(row.source_metadata['id'], row.revision, {'headers': {'Idempotency-Key': 'explicit-test-key'}})
        self.assertEqual(result.status_code, 200, result.data)
        second = self.prepare()
        self.assertEqual(second.data['unchanged_count'], 3)
        self.assertEqual(second.data['updated_count'], 0)
        self.assertEqual(second.data['user_preserved_count'], 1)
        row.refresh_from_db(); self.assertEqual(row.request['headers']['Idempotency-Key'], 'explicit-test-key')

    def test_editor_preserves_json_scalar_body_including_null(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        for body in ('null', 'false', '0'):
            result = self.save_edit(row.source_metadata['id'], row.revision, {'body_type': 'JSON', 'body': body})
            self.assertEqual(result.status_code, 200, result.data)
            row.refresh_from_db()
            self.assertEqual(row.request['body'], body)

    def test_parameter_scalar_type_changes_are_saved_and_preserved(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        for value in (False, 0, True, 1):
            revision = row.revision
            result = self.save_edit(row.source_metadata['id'], revision, {'params': {'enabled': value}})
            self.assertEqual(result.status_code, 200, result.data)
            row.refresh_from_db()
            self.assertEqual(row.revision, revision + 1)
            self.assertIs(type(row.request['params']['enabled']), type(value))
            self.assertEqual(self.prepare().status_code, 200)
            row.refresh_from_db()
            self.assertIs(type(row.request['params']['enabled']), type(value))

    def test_masked_assertion_is_bound_to_rule_identity(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        rid = row.source_metadata['id']
        rules = [{'type': 'JSON_PATH', 'json_path': '$.token', 'expected': 'synthetic-private'}]
        saved = self.save_edit(rid, row.revision, {'assertions': rules})
        self.assertEqual(saved.status_code, 200, saved.data)
        shown = self.client.get(self.edit_url(rid)).data
        self.assertEqual(shown['request']['assertions'][0]['expected'], '******')
        for changed in ({'type': 'JSON_PATH', 'json_path': '$.code', 'expected': '******'},
                        {'type': 'CONTAINS', 'json_path': '$.token', 'expected': '******'}):
            with self.subTest(changed=changed):
                result = self.save_edit(rid, shown['prepared']['revision'], {'assertions': [changed]})
                self.assertEqual(result.status_code, 400, result.data)
                row.refresh_from_db()
                self.assertEqual(row.request['assertions'], rules)
                self.assertNotIn('synthetic-private', json.dumps(self.client.get(self.edit_url(rid)).data))
        alias = {'type': 'JSON_PATH', 'expr': '$.token', 'expected': '******'}
        result = self.save_edit(rid, shown['prepared']['revision'], {'assertions': [alias]})
        self.assertEqual(result.status_code, 200, result.data)
        row.refresh_from_db()
        self.assertEqual(row.request['assertions'][0]['expected'], 'synthetic-private')
        self.assertNotIn('synthetic-private', json.dumps(result.data))

    def test_sensitive_url_values_are_hidden_and_new_literals_rejected(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        rid = row.source_metadata['id']
        for url in ('/health?access_token=synthetic-url-secret',
                    'https://name:synthetic-url-secret@example.invalid/health'):
            with self.subTest(url=url):
                rejected = self.save_edit(rid, row.revision, {'url': url})
                self.assertEqual(rejected.status_code, 400, rejected.data)
                row.request['url'] = url
                row.save(update_fields=['request'])
                shown = self.client.get(self.edit_url(rid)).data
                self.assertEqual(shown['request']['url'], '******')
                self.assertNotIn('synthetic-url-secret', json.dumps(shown))
                kept = self.save_edit(rid, row.revision, {'url': '******'})
                self.assertEqual(kept.status_code, 200, kept.data)
                row.refresh_from_db()
                self.assertEqual(row.request['url'], url)
                self.assertNotIn('synthetic-url-secret', json.dumps(kept.data))
        safe = self.save_edit(rid, row.revision, {'url': '/health?access_token={{token}}&page=0'})
        self.assertEqual(safe.status_code, 200, safe.data)
        self.assertEqual(safe.data['request']['url'], '/health?access_token={{token}}&page=0')

    def test_json_path_assertions_reject_container_expected_without_writing(self):
        self.setup_pool()
        row = models.PerfPreparedRequest.objects.get(source_key='GET /health')
        before = deepcopy(row.request)
        for expected in ({'nonexistent': 'wrong'}, ['wrong']):
            result = self.save_edit(row.source_metadata['id'], row.revision, {'assertions': [
                {'type': 'STATUS_CODE', 'expected': 200},
                {'type': 'JSON_PATH', 'json_path': '$.data', 'expected': expected}]})
            self.assertEqual(result.status_code, 400, result.data)
            row.refresh_from_db()
            self.assertEqual(row.request, before)
        from apps.perf_testing.services import prepared_requests as service
        self.assertFalse(service._reviewed_business_assertions([
            {'type': 'STATUS_CODE', 'expected': 200},
            {'type': 'JSON_PATH', 'json_path': '$.data', 'expected': {'wrong': True}}]))
