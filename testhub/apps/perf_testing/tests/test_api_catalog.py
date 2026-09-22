"""Project catalog contracts on disposable Django test databases; no target traffic."""
from copy import deepcopy
import importlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.api_testing.models import ApiProject, ApiCollection, ApiRequest
from apps.perf_testing import models


def contract():
    return {'openapi': '3.1.0', 'info': {'title': 'Fixture', 'version': '1'}, 'paths': {
        '/items/{id}': {'get': {'operationId': 'item', 'tags': ['Items'], 'parameters': [
            {'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}, 'example': 'fake-id'},
            {'name': 'page', 'in': 'query', 'schema': {'type': 'integer', 'default': 1}},
            {'name': 'X-Mode', 'in': 'header', 'schema': {'type': 'string', 'default': 'safe'}}],
            'responses': {'200': {'description': 'ok'}}}},
        '/health': {'get': {'operationId': 'health', 'responses': {'200': {'description': 'ok'}}}},
        '/items': {'post': {'operationId': 'create', 'requestBody': {'required': True, 'content': {
            'application/json': {'schema': {'$ref': '#/components/schemas/Input'}}}},
            'responses': {'201': {'description': 'created'}}}}},
        'components': {'schemas': {'Input': {'allOf': [
            {'type': 'object', 'required': ['name'], 'properties': {'name': {'type': 'string'}}},
            {'type': 'object', 'properties': {'enabled': {'type': 'boolean', 'default': False}}}]}}}}


class ApiCatalogTests(TestCase):
    def setUp(self):
        self.service = importlib.import_module('apps.perf_testing.services.api_catalog')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = override_settings(PERF_PRIVATE_ROOT=Path(self.tmp.name) / 'private',
                                     MEDIA_ROOT=Path(self.tmp.name) / 'media')
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = get_user_model().objects.create_user(username='catalog-owner')
        self.member = get_user_model().objects.create_user(username='catalog-member')
        self.outside = get_user_model().objects.create_user(username='catalog-outside')
        self.project = models.PerfProject.objects.create(name='Catalog', owner=self.owner)
        self.project.members.add(self.member)
        self.other = models.PerfProject.objects.create(name='Other', owner=self.outside)
        self.scene = models.PerfScenario.objects.create(project=self.project, created_by=self.owner,
            name='Scene', engine='K6', env_config={'base_url': 'http://example.invalid'},
            load_config={'model': 'CONCURRENCY', 'concurrency': 1, 'duration': 2})
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def project_url(self, action='api-catalog', project=None):
        return f'/api/perf-testing/projects/{(project or self.project).pk}/{action}/'

    def scene_url(self, action):
        return f'/api/perf-testing/scenarios/{self.scene.pk}/{action}/'

    def upload(self, doc=None, preview=False, version=0, project=None):
        return self.client.post(self.project_url('api-catalog/preview' if preview else 'api-catalog/import', project),
            {'file': SimpleUploadedFile('contract.json', json.dumps(doc or contract()).encode()),
             'expected_version': version}, format='multipart')

    def imported(self):
        result = self.upload()
        self.assertEqual(result.status_code, 201, result.data)
        return self.client.get(self.project_url()).data['results']

    def test_request_containers_rejected_before_crud_and_bulk_writes(self):
        op = next(op for op in self.imported() if op['path'] == '/items/{id}')
        step = self.service.import_steps(self.scene, [op['id']], self.owner)[0]
        before = models.PerfScenarioStep.objects.filter(pk=step.pk).values().get()
        for field in ('headers', 'params'):
            for value in (['bad'], 'bad', 9, None):
                with self.subTest(field=field, value=value):
                    result = self.client.patch(f'/api/perf-testing/steps/{step.pk}/', {field: value}, format='json')
                    self.assertEqual(result.status_code, 400)
                    self.assertEqual(models.PerfScenarioStep.objects.filter(pk=step.pk).values().get(), before)
                    result = self.client.post('/api/perf-testing/steps/', {
                        'scenario': self.scene.pk, 'name': 'Invalid', 'url': '/health', field: value}, format='json')
                    self.assertEqual(result.status_code, 400)
                    self.assertEqual(self.scene.steps.count(), 1)
                    payload = dict(self.client.get(f'/api/perf-testing/steps/{step.pk}/').data)
                    payload[field] = value
                    result = self.client.post(self.scene_url('save-steps'), {'steps': [payload]}, format='json')
                    self.assertEqual(result.status_code, 400)
                    self.assertEqual(models.PerfScenarioStep.objects.filter(pk=step.pk).values().get(), before)

    def test_old_malformed_containers_remain_readable_and_repairable(self):
        op = next(op for op in self.imported() if op['path'] == '/items/{id}')
        step = self.service.import_steps(self.scene, [op['id']], self.owner)[0]
        for metadata in (step.source_metadata, {}):
            models.PerfScenarioStep.objects.filter(pk=step.pk).update(headers=['old'], params=9, source_metadata=metadata)
            result = self.client.get(f'/api/perf-testing/steps/{step.pk}/')
            self.assertEqual(result.status_code, 200)
            readiness = result.data['readiness']
            self.assertFalse(readiness['ready'])
            self.assertEqual({g['field'] for g in readiness['gaps'] if g['code'] == 'request_container'}, {'headers', 'params'})
            result = self.client.patch(f'/api/perf-testing/steps/{step.pk}/', {'headers': {}, 'params': {}}, format='json')
            self.assertEqual(result.status_code, 200)

    def test_consumed_metadata_shapes_rejected_without_catalog_writes(self):
        for field, value in [('tags', [{'tag': 'x'}]), ('tags', 'Items'), ('tags', [1]),
                             ('operationId', 123), ('summary', []), ('servers', [{'url': 1}]),
                             ('parameters', {}), ('security', 'bearer')]:
            with self.subTest(field=field, value=value):
                doc = contract()
                doc['paths']['/health']['get'][field] = value
                for preview in (True, False):
                    self.assertEqual(self.upload(doc, preview=preview).status_code, 400)
                self.assertFalse(ApiProject.objects.exists())
                self.assertFalse(models.PerfApiCatalogVersion.objects.exists())
                result = self.client.get(self.project_url(), {'search': 'health'})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.data['count'], 0)

    @override_settings(DEBUG=True)
    def test_bridge_read_numeric_and_exception_boundaries(self):
        self.imported()
        self.client.raise_request_exception = False
        for resource in ('projects', 'collections', 'requests'):
            for value in ('²', '9' * 5001, '-1', '0', str(2**63)):
                self.assertEqual(self.client.get(f'/api/api-testing/{resource}/{value}/').status_code, 400)
                if resource != 'projects':
                    self.assertEqual(self.client.get(f'/api/api-testing/{resource}/', {'project': value}).status_code, 400)
                if resource == 'requests':
                    self.assertEqual(self.client.get('/api/api-testing/requests/', {'collection': value}).status_code, 400)
            for action in ('', '1/'):
                with mock.patch(f'backend.pressure_api_urls.{resource.title()}.get_queryset',
                                side_effect=OperationalError('fixture-private-exception')):
                    result = self.client.get(f'/api/api-testing/{resource}/{action}')
                self.assertEqual(result.status_code, 503)
                self.assertNotIn(b'fixture-private-exception', result.content)
                self.assertNotIn(b'Traceback', result.content)
        with mock.patch('backend.pressure_api_urls.RequestSerializer.to_representation',
                        side_effect=RuntimeError('fixture-private-serialization')):
            result = self.client.get('/api/api-testing/requests/')
        self.assertEqual(result.status_code, 503)
        self.assertNotIn(b'fixture-private', result.content)
        self.assertEqual(self.client.get('/api/api-testing/requests/999999/').status_code, 404)
        self.assertEqual(self.client.post('/api/api-testing/requests/', {}, format='json').status_code, 405)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get('/api/api-testing/requests/').status_code, (401, 403))

    def test_catalog_numeric_inputs_are_bounded(self):
        for value in ('²', '9' * 5001, str(2**63), '-1'):
            self.assertEqual(self.upload(version=value).status_code, 400)
            self.assertEqual(self.client.get(f'/api/perf-testing/projects/{value}/api-catalog/').status_code, 400)
            self.assertEqual(self.client.get(self.project_url(f'api-catalog/requests/{value}')).status_code, 400)
            for action in ('catalog-diff', 'readiness'):
                self.assertEqual(self.client.get(f'/api/perf-testing/scenarios/{value}/{action}/').status_code, 400)
            self.assertEqual(self.client.post(self.scene_url('catalog-update'),
                {'expected_version': value, 'step_ids': [1]}, format='json').status_code, 400)
        self.assertFalse(ApiProject.objects.exists())

    def test_catalog_search_filters_operations_without_filtering_parent(self):
        self.imported()
        result = self.client.get(self.project_url(), {'search': 'items', 'tag': 'Items', 'method': 'GET', 'page_size': 1})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['count'], 1)
        self.assertEqual(result.data['results'][0]['path'], '/items/{id}')
        self.assertEqual(self.client.get(self.project_url(), {'search': 'health'}).data['count'], 1)
        projects = self.client.get('/api/perf-testing/projects/', {'search': 'health'})
        self.assertEqual(projects.data['count'], 0)
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.client.get(self.project_url(), {'search': 'health'}).status_code, 404)

    def test_reference_budgets_reject_tiny_fixtures_before_sampling_or_writes(self):
        doc = {'openapi': '3.1.0', 'info': {'title': 'Bounded', 'version': '1'},
            'components': {'schemas': {'Value': {'type': 'string', 'default': 'x' * 128},
                'Input': {'type': 'object', 'properties': {f'p{i}': {'$ref': '#/components/schemas/Value'} for i in range(8)}}}},
            'paths': {f'/r{i}': {'post': {'requestBody': {'content': {
                'application/json': {'schema': {'$ref': '#/components/schemas/Input'}}}}}} for i in range(4)}}
        for setting, limit in [('MAX_EXPANDED_BYTES', 2000), ('MAX_OPERATION_BYTES', 2000),
                               ('MAX_EXPANDED_NODES', 10)]:
            with self.subTest(setting=setting), mock.patch.object(self.service, setting, limit, create=True):
                with mock.patch.object(self.service, '_sample', wraps=self.service._sample) as sample:
                    self.assertEqual(self.upload(doc).status_code, 400)
                    self.assertEqual(sample.call_count, 0)
                self.assertFalse(ApiProject.objects.exists())
                self.assertFalse(models.PerfApiCatalogVersion.objects.exists())

    def test_reference_byte_budget_is_shared_across_operations(self):
        one = {'openapi': '3.1.0', 'info': {'title': 'Bounded', 'version': '1'},
            'components': {'requestBodies': {'Shared': {'content': {'application/json': {
                'schema': {'type': 'string', 'default': 'x' * 128}}}}}},
            'paths': {'/a': {'post': {'requestBody': {'$ref': '#/components/requestBodies/Shared'}}}}}
        many = deepcopy(one)
        many['paths'] = {f'/p{i}': deepcopy(one['paths']['/a']) for i in range(20)}
        with mock.patch.object(self.service, 'MAX_EXPANDED_BYTES', 50000, create=True):
            self.assertEqual(len(self.service.parse_document(one)['operations']), 1)
            with mock.patch.object(self.service, '_operation', wraps=self.service._operation) as operation:
                with self.assertRaises(self.service.CatalogInputError):
                    self.service.parse_document(many)
                self.assertGreater(operation.call_count, 1)

    def test_synthetic_all_184_operations_and_local_refs(self):
        path = Path(__file__).resolve().parent / 'fixtures/synthetic-catalog.openapi.json'
        parsed = self.service.parse_document(path.read_bytes())
        self.assertEqual(len(parsed['operations']), 184)
        self.assertEqual(len(parsed['document']['paths']), 161)
        self.assertEqual(len(parsed['document']['components']['schemas']), 373)
        self.assertTrue(any(op['request']['body_type'] != 'NONE' for op in parsed['operations']))
        self.assertTrue(any(op['gaps'] for op in parsed['operations']))

    def test_preview_does_not_create_assets_import_is_automatic_and_idempotent(self):
        result = self.upload(preview=True)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['operation_count'], 3)
        self.assertFalse(ApiProject.objects.exists())
        first = self.upload()
        self.assertEqual(first.status_code, 201, first.data)
        self.project.refresh_from_db()
        self.assertIsNotNone(self.project.api_project_id)
        self.assertEqual(ApiRequest.objects.count(), 3)
        repeat = self.upload(version=1)
        self.assertEqual(repeat.status_code, 200, repeat.data)
        self.assertFalse(repeat.data['changed'])
        self.assertEqual(models.PerfApiCatalogVersion.objects.count(), 1)
        self.assertEqual(self.upload(version=0).status_code, 409)

    def test_defaults_examples_required_and_body_are_preserved(self):
        operations = self.service.parse_document(json.dumps(contract()).encode())['operations']
        item, health, create = operations
        self.assertEqual(item['request']['params']['page'], 1)
        self.assertEqual(item['request']['headers']['X-Mode'], 'safe')
        self.assertEqual(item['parameters'][0]['example'], 'fake-id')
        self.assertEqual(item['requirements'][0]['provenance'], 'example')
        self.assertEqual(create['request']['body_type'], 'JSON')
        self.assertIs(json.loads(create['request']['body'])['enabled'], False)
        self.assertTrue(create['request_body']['content']['application/json']['schema']['allOf'])
        self.assertTrue(self.service.request_readiness(item['request'], item)['gaps'])
        self.assertTrue(self.service.request_readiness(health['request'], health)['ready'])

    def test_recursive_refs_composition_content_and_serialization_are_explicit(self):
        doc = contract()
        doc['components']['schemas']['Input'] = {'oneOf': [
            {'$ref': '#/components/schemas/Input'}, {'type': 'string'}]}
        doc['paths']['/items']['post']['requestBody']['content']['image/png'] = {'schema': {'type': 'string', 'format': 'binary'}}
        doc['paths']['/items/{id}']['get']['parameters'][1]['schema'] = {'type': 'array', 'items': {'type': 'string'}}
        parsed = self.service.parse_document(json.dumps(doc).encode())
        codes = {g['code'] for op in parsed['operations'] for g in op['gaps']}
        self.assertTrue({'recursive_ref', 'schema_choice', 'content_type_choice', 'parameter_serialization'} <= codes, codes)
        doc['paths']['/health']['get']['parameters'] = [{'$ref': 'https://example.invalid/secret'}]
        with self.assertRaises(self.service.CatalogInputError):
            self.service.parse_document(json.dumps(doc).encode())

    def test_swagger2_body_and_form_files_are_not_lost(self):
        doc = {'swagger': '2.0', 'info': {'title': 'legacy', 'version': '1'}, 'paths': {
            '/json': {'post': {'parameters': [{'in': 'body', 'name': 'body', 'required': True,
                'schema': {'type': 'object', 'properties': {'n': {'type': 'integer', 'default': 0}}}}]}},
            '/upload': {'post': {'consumes': ['multipart/form-data'], 'parameters': [
                {'in': 'formData', 'name': 'picture', 'type': 'file', 'required': True}]}}}}
        ops = self.service.parse_document(json.dumps(doc).encode())['operations']
        self.assertEqual(json.loads(ops[0]['request']['body']), {'n': 0})
        self.assertEqual(ops[1]['request']['files'][0]['file_id'], None)
        self.assertFalse(self.service.request_readiness(ops[1]['request'], ops[1])['ready'])

    def test_catalog_and_legacy_bridge_are_scoped_paginated_and_read_only(self):
        self.imported()
        for prefix in ('api-catalog', 'api-catalog/versions'):
            result = self.client.get(self.project_url(prefix), {'page_size': 0})
            self.assertEqual(result.status_code, 200)
            self.assertIn('results', result.data)
        for path in ('projects', 'collections', 'requests'):
            url = f'/api/api-testing/{path}/'
            result = self.client.get(url, {'page_size': 0})
            self.assertEqual(result.status_code, 200, result.data)
            self.assertIn('results', result.data)
            self.assertEqual(self.client.post(url, {}, format='json').status_code, 405)
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.client.get(self.project_url()).status_code, 404)
        self.assertEqual(self.client.get('/api/api-testing/requests/').data['count'], 0)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(self.project_url()).data['count'], 3)

    def test_batch_reference_rejects_cross_project_and_preserves_selected_order(self):
        ops = self.imported()
        alien_api = ApiProject.objects.create(name='alien', owner=self.outside, project_type='HTTP', status='IN_PROGRESS')
        folder = ApiCollection.objects.create(project=alien_api, name='alien')
        alien = ApiRequest.objects.create(collection=folder, name='alien', created_by=self.outside, url='/alien')
        result = self.client.post(self.scene_url('import-from-api'), {'request_ids': [ops[0]['id'], alien.pk]}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertFalse(self.scene.steps.exists())
        result = self.client.post(self.scene_url('import-from-api'), {'request_ids': [ops[1]['id'], ops[0]['id']]}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(list(self.scene.steps.values_list('source_request_id', flat=True)), [ops[1]['id'], ops[0]['id']])
        self.assertEqual(self.scene.steps.first().source_metadata['version'], 1)

    def test_diff_and_explicit_update_preserve_custom_values_rules_and_old_snapshot(self):
        ops = self.imported()
        self.client.post(self.scene_url('import-from-api'), {'request_ids': [ops[0]['id']]}, format='json')
        step = self.scene.steps.get()
        step.params = {'page': 7}
        step.assertions = [{'type': 'STATUS_CODE', 'expected': 200}]
        step.extractors = [{'name': 'item', 'type': 'JSON_PATH', 'expr': '$.id'}]
        step.save()
        from apps.perf_testing.services.executor import build_snapshot
        frozen = deepcopy(build_snapshot(self.scene))
        doc = contract()
        doc['paths']['/items/{id}']['get']['parameters'][1]['schema']['default'] = 2
        updated = self.upload(doc, version=1)
        self.assertEqual(updated.status_code, 201, updated.data)
        step.refresh_from_db()
        self.assertEqual(step.source_metadata['version'], 1)
        diff = self.client.get(self.scene_url('catalog-diff'))
        self.assertEqual(diff.status_code, 200, diff.data)
        self.assertTrue(diff.data['results'][0]['changed'])
        applied = self.client.post(self.scene_url('catalog-update'), {'step_ids': [step.pk], 'expected_version': 2}, format='json')
        self.assertEqual(applied.status_code, 200, applied.data)
        step.refresh_from_db()
        self.assertEqual(step.params, {'page': 7})
        self.assertEqual(step.assertions, [{'type': 'STATUS_CODE', 'expected': 200}])
        self.assertEqual(step.extractors[0]['name'], 'item')
        self.assertEqual(step.source_metadata['version'], 2)
        self.assertEqual(frozen['steps'][0]['source_metadata']['version'], 1)
        self.assertEqual(frozen['steps'][0]['params']['page'], 7)

    def test_save_steps_keeps_source_and_rejects_forgery_and_cross_project_crud(self):
        ops = self.imported()
        self.client.post(self.scene_url('import-from-api'), {'request_ids': [ops[0]['id']]}, format='json')
        step = self.scene.steps.get()
        response = self.client.get(f'/api/perf-testing/steps/{step.pk}/')
        payload = dict(response.data)
        payload['source_metadata'] = {'version': 999, 'requirements': []}
        response = self.client.post(self.scene_url('save-steps'), {'steps': [payload]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.scene.steps.get().source_metadata['version'], 1)
        self.client.force_authenticate(self.outside)
        pk = self.scene.steps.get().pk
        self.assertEqual(self.client.get(f'/api/perf-testing/steps/{pk}/').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/perf-testing/steps/{pk}/').status_code, 404)

    @override_settings(DEBUG=True)
    def test_invalid_and_unexpected_import_errors_never_echo_secrets(self):
        for raw in (b'{"password":"test-secret"', b'&r [*r]', b'{"openapi":"3.1.0","paths":NaN}'):
            result = self.client.post(self.project_url('api-catalog/import'), {
                'file': SimpleUploadedFile('secret.json', raw), 'expected_version': 0}, format='multipart')
            self.assertEqual(result.status_code, 400)
            self.assertNotIn('test-secret', str(result.data))
        with mock.patch.object(ApiProject.objects, 'create', side_effect=OperationalError('secret-value')):
            result = self.upload()
        self.assertEqual(result.status_code, 503, result.data)
        self.assertNotIn('secret-value', str(result.data))
        self.assertFalse(ApiRequest.objects.exists())

    def test_static_and_unneeded_routes_stay_404(self):
        self.client.force_authenticate(None)
        for path in ('/media/test.csv', '/private/accounts.json', '/api/media/test.csv',
                     '/api/api-testing/environments/', '/api/api-testing/requests/1/execute/'):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_synthetic_import_all_metadata_without_scenes_or_executions(self):
        path = Path(__file__).resolve().parent / 'fixtures/synthetic-catalog.openapi.json'
        parsed = self.service.parse_document(path.read_bytes())
        result = self.service.import_document(self.project.pk, parsed, 0, self.owner)
        self.assertEqual(result['version']['operation_count'], 184)
        self.assertEqual(ApiRequest.objects.count(), 184)
        self.assertEqual(self.scene.steps.count(), 0)
        self.assertEqual(models.PerfExecution.objects.count(), 0)
        version = models.PerfApiCatalogVersion.objects.get()
        from collections import Counter
        self.assertEqual(dict(Counter(op['method'] for op in version.operations)),
            {'GET': 107, 'POST': 42, 'PUT': 21, 'DELETE': 11, 'PATCH': 3})
        self.assertEqual(self.client.get(self.project_url(), {'page_size': 20}).data['count'], 184)
        self.assertEqual(len(self.client.get(self.project_url(), {'page_size': 20, 'page': 2}).data['results']), 20)
        self.assertTrue(all(op['servers'] for op in version.operations))

    def test_required_body_blocks_real_preflight_and_confirmation_resolves_example(self):
        ops = self.imported()
        self.client.post(self.scene_url('import-from-api'), {'request_ids': [ops[0]['id'], ops[2]['id']]}, format='json')
        from apps.perf_testing.services import executor
        with mock.patch('apps.perf_testing.engines.k6_available', return_value=True):
            result = executor.preflight(self.scene, user=self.owner)
        self.assertFalse(result['passed'])
        self.assertTrue(any('body/name' in error for error in result['errors']), result)
        step = self.scene.steps.first()
        data = {key: getattr(step, key) for key in self.service.REQUEST_FIELDS}
        readiness = self.service.request_readiness(data, step.source_metadata, {'confirmed_fields': ['path/id']})
        self.assertTrue(readiness['ready'], readiness)

    def test_auth_uses_inherited_environment_and_setup_execution_order(self):
        doc = contract()
        doc['components']['securitySchemes'] = {'token': {'type': 'http', 'scheme': 'bearer'}}
        doc['paths']['/health']['get']['security'] = [{'token': []}]
        self.upload(doc)
        version = models.PerfApiCatalogVersion.objects.get()
        op = next(op for op in version.operations if op['path'] == '/health')
        self.service.import_steps(self.scene, [op['id']], self.owner)
        step = self.scene.steps.get()
        step.headers = {'Authorization': 'Bearer {{token}}'}
        step.save()
        resolved = {'variables': [], 'env_config': {'headers': {}}}
        self.assertFalse(self.service.scenario_readiness(self.scene, resolved)[0]['ready'])
        setup = models.PerfScenarioStep.objects.create(scenario=self.scene, name='Login', url='/login',
            order=99, is_setup=True, extractors=[{'type': 'JSON_PATH', 'name': 'token', 'expr': '$.token'}])
        ready = self.service.scenario_readiness(self.scene, resolved)
        self.assertEqual(ready[0]['step_id'], setup.pk)
        self.assertTrue(ready[1]['ready'], ready)
        setup.delete()
        step.headers = {}
        step.save()
        for authorization, variables, expected in [('Bearer', [], False), ('Bearer {{token}}', [], False),
            ('Bearer {{token}}', [{'name': 'token', 'type': 'CONSTANT', 'value': 'fake-explicit-token'}], True)]:
            resolved = {'variables': variables, 'env_config': {'headers': {'Authorization': authorization}}}
            self.assertEqual(self.service.scenario_readiness(self.scene, resolved)[0]['ready'], expected)

    def test_old_api_body_and_assertions_are_imported_and_scripts_stay_explicit(self):
        ops = self.imported()
        api = ApiRequest.objects.get(pk=ops[2]['id'])
        api.body = {'type': 'json', 'data': {'name': 'configured', 'enabled': False}}
        api.assertions = [{'type': 'STATUS_CODE', 'expected': 201}]
        api.post_request_script = 'extract a response value'
        api.save()
        self.service.import_steps(self.scene, [api.pk], self.owner)
        step = self.scene.steps.get()
        self.assertEqual(json.loads(step.body), api.body['data'])
        self.assertEqual(step.assertions, api.assertions)
        self.assertTrue(any(g['code'] == 'source_scripts' for g in step.source_metadata['gaps']))
        self.assertEqual(step.source_metadata['source_scripts']['post'], api.post_request_script)

    def test_servers_swagger_base_path_and_operation_order_are_not_dropped(self):
        doc = {'swagger': '2.0', 'host': 'example.invalid', 'basePath': '/v2', 'schemes': ['https'],
            'info': {'version': '1'}, 'paths': {'/pets': {'get': {}}}}
        op = self.service.parse_document(json.dumps(doc).encode())['operations'][0]
        self.assertEqual(op['request']['url'], '/v2/pets')
        self.assertEqual(op['servers'][0]['url'], 'https://example.invalid/v2')
        doc = contract()
        before = self.service.parse_document(json.dumps(doc).encode())
        doc['paths'] = dict(reversed(list(doc['paths'].items())))
        after = self.service.parse_document(json.dumps(doc).encode())
        self.assertNotEqual(before['content_hash'], after['content_hash'])

    def test_source_is_immutable_duplicate_preserves_and_cross_project_move_rejected(self):
        ops = self.imported()
        self.service.import_steps(self.scene, [ops[1]['id']], self.owner)
        version = models.PerfApiCatalogVersion.objects.get()
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            version.save()
        with self.assertRaises(ValidationError):
            models.PerfApiCatalogVersion.objects.filter(pk=version.pk).update(source_version='bad')
        duplicate = self.client.post(self.scene_url('duplicate'), {}, format='json')
        self.assertEqual(duplicate.status_code, 201, duplicate.data)
        self.assertEqual(duplicate.data['steps'][0]['source_metadata']['version'], 1)
        self.other.members.add(self.owner)
        move = self.client.patch(f'/api/perf-testing/scenarios/{self.scene.pk}/', {'project': self.other.pk}, format='json')
        self.assertEqual(move.status_code, 400, move.data)

    def test_project_link_cannot_be_forged_and_stale_acl_rechecked(self):
        ops = self.imported()
        self.project.refresh_from_db()
        api = self.project.api_project
        payload = {'api_project': api.pk}
        self.other.members.add(self.owner)
        result = self.client.patch(f'/api/perf-testing/projects/{self.other.pk}/', payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.other.refresh_from_db()
        self.assertIsNone(self.other.api_project_id)
        self.project.members.remove(self.member)
        from rest_framework.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.service.import_steps(self.scene, [ops[0]['id']], self.member)

    def test_removal_does_not_delete_old_scenario_and_cannot_be_reimported(self):
        ops = self.imported()
        self.service.import_steps(self.scene, [ops[1]['id']], self.owner)
        doc = contract()
        del doc['paths']['/health']
        result = self.upload(doc, version=1)
        self.assertEqual(result.status_code, 201)
        self.assertIn('GET /health', result.data['diff']['removed'])
        self.assertEqual(self.scene.steps.count(), 1)
        self.assertEqual(self.scene.steps.get().source_metadata['version'], 1)
        self.assertEqual(self.client.post(self.scene_url('import-from-api'), {'request_ids': [ops[1]['id']]}, format='json').status_code, 400)

    def test_created_execution_private_snapshot_stays_on_old_catalog(self):
        ops = self.imported()
        self.service.import_steps(self.scene, [ops[1]['id']], self.owner)
        from apps.perf_testing.services import executor
        from apps.perf_testing.services.k6_execution import load_snapshot
        with mock.patch('apps.perf_testing.engines.k6_version', return_value='fixed-version'):
            execution = executor.create_execution(self.scene, user=self.owner)
        frozen = load_snapshot(Path(self.tmp.name) / 'private', execution.pk)
        doc = contract()
        doc['paths']['/health']['get']['parameters'] = [{'in': 'query', 'name': 'p', 'schema': {'type': 'integer', 'default': 3}}]
        self.upload(doc, version=1)
        self.service.update_steps(self.scene, [self.scene.steps.get().pk], 2, self.owner)
        self.assertEqual(load_snapshot(Path(self.tmp.name) / 'private', execution.pk), frozen)
        self.assertEqual(executor._execution_snapshot(execution), frozen)
        self.assertEqual(frozen['steps'][0]['source_metadata']['version'], 1)
        self.assertEqual(execution.status, 'PENDING')

    def test_optional_resource_example_is_not_implicitly_ready(self):
        doc = contract()
        doc['paths']['/health']['get']['parameters'] = [{'in': 'query', 'name': 'user_id',
            'required': False, 'schema': {'type': 'integer', 'example': 123}}]
        op = self.service.parse_document(json.dumps(doc).encode())['operations'][1]
        ready = self.service.request_readiness(op['request'], op)
        self.assertFalse(ready['ready'])

    def test_object_example_never_hides_missing_required_children(self):
        for keyword in ('example', 'default'):
            doc = contract()
            doc['components']['schemas']['Input'] = {'type': 'object', 'required': ['name'],
                'properties': {'name': {'type': 'string'}}, keyword: {'name': 'sample'}}
            op = self.service.parse_document(json.dumps(doc).encode())['operations'][2]
            request = deepcopy(op['request'])
            request['body'] = '{}'
            state = self.service.request_readiness(request, op, {'confirmed_fields': ['body']})
            self.assertFalse(state['ready'], (keyword, state))
            self.assertTrue(any(g['field'] == 'body/name' for g in state['gaps']))
            request['body'] = '{"name":"explicit value"}'
            self.assertTrue(self.service.request_readiness(request, op)['ready'])

    def test_nested_optional_object_and_required_without_properties_are_checked(self):
        doc = contract()
        doc['components']['schemas']['Input'] = {'type': 'object', 'properties': {
            'options': {'type': 'object', 'required': ['unknown'], 'properties': {}}}}
        op = self.service.parse_document(json.dumps(doc).encode())['operations'][2]
        request = deepcopy(op['request'])
        request['body'] = '{"options":{}}'
        self.assertFalse(self.service.request_readiness(request, op)['ready'])
        request['body'] = '{}'
        self.assertTrue(self.service.request_readiness(request, op)['ready'])

    def test_manual_review_cannot_silence_unsupported_schema_or_serialization(self):
        doc = contract()
        doc['components']['schemas']['Input'] = {'oneOf': [{'type': 'object', 'required': ['a']}, {'type': 'object', 'required': ['b']}]}
        op = self.service.parse_document(json.dumps(doc).encode())['operations'][2]
        request = deepcopy(op['request'])
        request['body'] = '{}'
        ready = self.service.request_readiness(request, op, {'body_reviewed': True, 'confirmed_fields': ['body']})
        self.assertFalse(ready['ready'])
        self.assertTrue(any(g['code'] == 'schema_choice' for g in ready['gaps']))

    def test_optional_variable_reference_must_have_mapping(self):
        op = self.service.parse_document(json.dumps(contract()).encode())['operations'][1]
        request = deepcopy(op['request'])
        request['params'] = {'filter': '{{missing}}'}
        self.assertFalse(self.service.request_readiness(request, op)['ready'])

    def test_all_reference_writes_keep_actual_source_semantics(self):
        ops = self.imported()
        api = ApiRequest.objects.get(pk=ops[1]['id'])
        api.auth = {'type': 'bearer', 'token': 'fake-source-token'}
        api.body = {'type': 'unknown-body', 'data': 'source-value'}
        api.params = [{'key': 'unsupported', 'value': 'value'}]
        api.pre_request_script = 'source pre script'
        api.assertions = [{'type': 'STATUS_CODE', 'expected': 200}]
        api.save()
        expected = {'source_auth', 'source_body_format', 'source_parameters', 'source_scripts'}
        imported = self.service.import_steps(self.scene, [api.pk], self.owner)[0]
        for path in ('steps', 'save-steps'):
            payload = {'source_request': api.pk, 'name': path, 'url': '/health', 'scenario': self.scene.pk}
            if path == 'steps':
                result = self.client.post('/api/perf-testing/steps/', payload, format='json')
                self.assertEqual(result.status_code, 201, result.data)
                step = models.PerfScenarioStep.objects.get(pk=result.data['id'])
            else:
                result = self.client.post(self.scene_url(path), {'steps': [payload]}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                step = self.scene.steps.get()
            self.assertTrue(expected <= {g['code'] for g in step.source_metadata['gaps']})
            self.assertEqual(step.assertions, api.assertions)
            self.assertEqual(step.body, 'source-value')
            self.assertEqual(step.source_metadata['source_asset']['auth'], api.auth)
        step.params = {'custom': 'preserved'}
        step.body_type = 'NONE'
        step.body = ''
        step.assertions = [{'type': 'STATUS_CODE', 'expected': 201}]
        step.extractors = [{'type': 'JSON_PATH', 'name': 'keep', 'expr': '$.keep'}]
        step.save()
        self.service.update_steps(self.scene, [step.pk], 1, self.owner)
        step.refresh_from_db()
        self.assertTrue(expected <= {g['code'] for g in step.source_metadata['gaps']})
        self.assertEqual(step.params, {'custom': 'preserved'})
        self.assertEqual(step.assertions[0]['expected'], 201)
        self.assertEqual(step.extractors[0]['name'], 'keep')
        readiness = self.service.request_readiness({k: getattr(step, k) for k in self.service.REQUEST_FIELDS}, step.source_metadata)
        self.assertFalse(readiness['ready'])

    def test_array_items_schema_and_resource_provenance_remain_checked(self):
        variants = [
            ({'oneOf': [{'type': 'object', 'required': ['cat_id']}, {'type': 'object', 'required': ['dog_id']}]}, '[{}]', 'schema_choice'),
            ({'anyOf': [{'type': 'string'}, {'type': 'number'}]}, '[true]', 'schema_choice'),
            ({'$ref': '#/components/schemas/Input'}, '[[]]', 'recursive_ref'),
            (True, '[{}]', 'schema_unsupported'),
            ({}, '[{}]', 'schema_unsupported'),
            ({'description': 'untyped item'}, '[{}]', 'schema_unsupported'),
            ({'type': 'object', 'required': ['id'], 'properties': {'id': {'type': 'string', 'example': 'example-resource'}}},
                '[{"id":"example-resource"}]', 'confirm_example'),
        ]
        for items, body, code in variants:
            with self.subTest(code=code, items=items):
                doc = contract()
                doc['components']['schemas']['Input'] = {'type': 'array', 'items': items}
                op = self.service.parse_document(doc)['operations'][2]
                request = {**op['request'], 'body': body}
                ready = self.service.request_readiness(request, op, {'body_reviewed': True})
                self.assertFalse(ready['ready'], ready)
                self.assertTrue(any(g['code'] == code for g in ready['gaps']), ready)
                if code == 'confirm_example':
                    self.assertTrue(any(g['field'] == 'body/0/id' for g in ready['gaps']))
                    confirmed = self.service.request_readiness(request, op, {'confirmed_fields': ['body/0/id']})
                    self.assertTrue(confirmed['ready'], confirmed)
                    request['body'] = '[{"id":"real-input"}]'
                    self.assertTrue(self.service.request_readiness(request, op)['ready'])

    def test_media_type_parameters_unknown_types_and_unsent_body_cannot_skip_schema(self):
        doc = contract()
        doc['components']['schemas']['Input'] = {'type': 'object', 'required': ['id']}
        op = self.service.parse_document(doc)['operations'][2]
        for content_type in ('application/json; charset=utf-8', 'Application/JSON; charset=UTF-8', 'application/unknown'):
            request = {**op['request'], 'body': '{}', 'headers': {'Content-Type': content_type}}
            ready = self.service.request_readiness(request, op, {'body_reviewed': True})
            self.assertFalse(ready['ready'], (content_type, ready))
        for body_type, body in [('NONE', '{"id":"present-but-unsent"}'), ('NONE', ''), ('RAW', '{"id":"wrong-encoding"}')]:
            request = {**op['request'], 'body_type': body_type, 'body': body}
            self.assertFalse(self.service.request_readiness(request, op)['ready'])
        request = {**op['request'], 'body': '{"id":"configured"}', 'headers': {'Content-Type': 'Application/JSON; charset=UTF-8'}}
        self.assertTrue(self.service.request_readiness(request, op)['ready'])

    def test_legacy_source_edit_is_limited_to_existing_unchanged_authorized_reference(self):
        original = ApiProject.objects.create(name='legacy', owner=self.owner, project_type='HTTP', status='IN_PROGRESS')
        folder = ApiCollection.objects.create(name='legacy', project=original)
        api = ApiRequest.objects.create(collection=folder, name='legacy', created_by=self.owner, url='/old')
        step = models.PerfScenarioStep.objects.create(scenario=self.scene, source_request=api, name='old', url='/old')
        url = f'/api/perf-testing/steps/{step.pk}/'
        result = self.client.patch(url, {'name': 'renamed'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['source_metadata'], {})
        result = self.client.post(self.scene_url('save-steps'), {'steps': [result.data]}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        new = self.client.post('/api/perf-testing/steps/', {'scenario': self.scene.pk, 'source_request': api.pk, 'name': 'injected', 'url': '/old'}, format='json')
        self.assertEqual(new.status_code, 400)
        other_scene = models.PerfScenario.objects.create(project=self.project, name='other scene', created_by=self.owner)
        self.assertEqual(self.client.patch(url, {'scenario': other_scene.pk}, format='json').status_code, 400)
        alien_project = ApiProject.objects.create(name='alien', owner=self.outside, project_type='HTTP', status='IN_PROGRESS')
        alien = ApiRequest.objects.create(collection=ApiCollection.objects.create(project=alien_project, name='alien'),
            name='alien', created_by=self.outside, url='/alien')
        self.assertEqual(self.client.patch(url, {'source_request': alien.pk}, format='json').status_code, 400)
        original.owner = self.outside
        original.save()
        self.assertIn(self.client.patch(url, {'name': 'no-longer-authorized'}, format='json').status_code, (400, 403))

    def test_identical_contract_repairs_deleted_assets_with_new_immutable_version(self):
        ops = self.imported()
        self.service.import_steps(self.scene, [ops[1]['id']], self.owner)
        initial_metadata = deepcopy(self.scene.steps.get().source_metadata)
        for deletion in ('request', 'collection', 'project'):
            version = self.service.latest_version(self.project.pk)
            old_operations = deepcopy(version.operations)
            self.project.refresh_from_db()
            if deletion == 'request':
                ApiRequest.objects.get(pk=version.operations[0]['id']).delete()
                survivor = ApiRequest.objects.get(pk=version.operations[1]['id'])
                survivor.body = {'type': 'json', 'data': {'manual': 'preserve'}}
                survivor.save()
            elif deletion == 'collection':
                ApiCollection.objects.get(pk=version.operations[0]['collection']).delete()
            else:
                ApiProject.objects.get(pk=self.project.api_project_id).delete()
            response = self.upload(version=version.version)
            self.assertEqual(response.status_code, 201, response.data)
            self.assertTrue(response.data['changed'])
            self.assertTrue(response.data['repaired'])
            repaired = self.service.latest_version(self.project.pk)
            self.assertEqual(repaired.version, version.version + 1)
            self.assertTrue(all(ApiRequest.objects.filter(pk=op['id']).exists() for op in repaired.operations))
            version.refresh_from_db()
            self.assertEqual(version.operations, old_operations)
            if deletion == 'request':
                survivor.refresh_from_db()
                self.assertEqual(survivor.body, {'type': 'json', 'data': {'manual': 'preserve'}})
            self.assertEqual(self.upload(version=repaired.version).status_code, 200)
            self.scene.steps.get().refresh_from_db()
            self.assertEqual(self.scene.steps.get().source_metadata, initial_metadata)

    def test_conversion_gaps_survive_repeated_updates_after_source_changes(self):
        ops = self.imported()
        api = ApiRequest.objects.get(pk=ops[1]['id'])
        api.body = {'type': 'unknown', 'data': 'original-unconverted-body'}
        api.params = ['unconverted-parameters']
        api.save()
        step = self.service.import_steps(self.scene, [api.pk], self.owner)[0]
        api.body = {'type': 'none', 'data': ''}
        api.params = {}
        api.save()
        for _ in range(2):
            self.service.update_steps(self.scene, [step.pk], 1, self.owner)
            step.refresh_from_db()
            self.assertTrue({'source_body_format', 'source_parameters'} <= {g['code'] for g in step.source_metadata['gaps']})
            self.assertEqual(step.source_metadata['prior_source_asset']['body']['data'], 'original-unconverted-body')
            self.assertFalse(self.service.request_readiness({k: getattr(step, k) for k in self.service.REQUEST_FIELDS}, step.source_metadata)['ready'])

    def test_parameterized_json_media_in_contract_is_converted_to_json(self):
        doc = contract()
        content = doc['paths']['/items']['post']['requestBody']['content']
        content['Application/JSON; charset=UTF-8'] = content.pop('application/json')
        op = self.service.parse_document(doc)['operations'][2]
        self.assertEqual(op['request']['body_type'], 'JSON')
        self.assertFalse(any(g['code'] == 'engine_body_type' for g in op['gaps']))
        request = {**op['request'], 'body': '{"name":"configured"}'}
        self.assertTrue(self.service.request_readiness(request, op)['ready'])
