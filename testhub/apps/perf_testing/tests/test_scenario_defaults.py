"""Reusable creation defaults on disposable platform data; no target requests."""
from copy import deepcopy
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.perf_testing import models
from . import test_api_catalog as fixtures
from apps.perf_testing.services import prepared_requests


class ScenarioDefaultsTests(TestCase):
    setUp = fixtures.ApiCatalogTests.setUp
    project_url = fixtures.ApiCatalogTests.project_url
    upload = fixtures.ApiCatalogTests.upload

    def defaults(self):
        return self.client.get(self.project_url('scenario-defaults'))

    def test_imported_assertion_aliases_survive_normal_scene_save(self):
        rules = [{'type': 'STATUS_CODE', 'expected': 200},
                 {'type': 'JSON_PATH', 'expr': '$.code', 'operator': 'eq', 'expected': 'OK'},
                 {'type': 'JSON_PATH', 'json_path': '$.active', 'operator': '==', 'expected': False},
                 {'type': 'JSON_PATH', 'expr': '$.count', 'json_path': '$.count', 'expected': 0}]
        step = models.PerfScenarioStep.objects.create(scenario=self.scene, name='Imported', url='/items', assertions=rules)
        payload = dict(self.client.get(f'/api/perf-testing/steps/{step.pk}/').data)
        payload['name'] = 'Reordered and saved'
        response = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/save-steps/', {'steps': [payload]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        step.refresh_from_db()
        self.assertEqual(step.assertions, rules)
        self.assertFalse(models.PerfExecution.objects.exists())

    def test_assertion_missing_conflicting_or_nontext_paths_are_rejected(self):
        step = models.PerfScenarioStep.objects.create(scenario=self.scene, name='Safe', url='/items')
        for paths in [{}, {'expr': ''}, {'expr': '   '}, {'expr': 123}, {'json_path': []},
                      {'expr': '$.a', 'json_path': '$.b'}]:
            with self.subTest(paths=paths):
                response = self.client.patch(f'/api/perf-testing/steps/{step.pk}/', {'assertions': [
                    {'type': 'JSON_PATH', 'expected': 'OK', **paths}]}, format='json')
                self.assertEqual(response.status_code, 400, response.data)

    def configure(self):
        env = models.PerfEnvironment.objects.create(project=self.project, name='Chosen',
            base_url='http://example.invalid', created_by=self.owner)
        pool = self.client.post('/api/perf-testing/account-pools/', {
            'project': self.project.pk, 'name': 'Synthetic pool', 'identity_column': 'id',
            'field_mapping': json.dumps({'user_id': 'id', 'token': 'credential'}),
            'file': SimpleUploadedFile('pool.csv', b'id,credential\nsynthetic-id,synthetic-private-token\n')}, format='multipart')
        self.assertEqual(pool.status_code, 201, pool.data)
        version = pool.data['latest_version']['id']
        result = self.client.put(self.project_url('api-pool/config'), {
            'expected_config_revision': 0, 'config': dict(environment=env.pk,
                global_environment=None, account_pool_version=version, account_pool_group='',
                token_variable='token', identity_variable='user_id')}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return env, version

    def test_read_only_defaults_and_every_new_scene_inherit_fixed_project_binding(self):
        env, version = self.configure()
        result = self.defaults()
        self.assertEqual(result.status_code, 200, result.data)
        defaults = result.data['defaults']
        self.assertEqual((defaults['environment'], defaults['account_pool_version']), (env.pk, version))
        self.assertEqual(defaults['runtime_config']['account_identity_variable'], 'user_id')
        self.assertEqual(defaults['runtime_config']['auth_profile']['access_token_variable'], 'token')
        self.assertNotIn('synthetic-private-token', json.dumps(result.data))
        self.assertEqual(models.PerfScenario.objects.count(), 1)
        for name in ['First', 'Later']:
            created = self.client.post('/api/perf-testing/scenarios/', {'project': self.project.pk, 'name': name}, format='json')
            self.assertEqual(created.status_code, 201, created.data)
            self.assertEqual(created.data['engine'], 'K6')
            self.assertEqual(created.data['environment'], env.pk)
            self.assertEqual(created.data['account_pool_version'], version)
            self.assertEqual(created.data['load_config']['concurrency'], 1)
            self.assertEqual(created.data['load_config']['iterations_per_vu'], 1)
            self.assertEqual(created.data['load_config']['duration'], 30)
            self.assertEqual(created.data['perf_targets']['max_p95_rt'], 2000)
            self.assertEqual(created.data['perf_targets']['max_error_rate'], 0)
            self.assertTrue(created.data['sla_config']['enabled'])
            self.assertEqual(created.data['sla_config']['thresholds'], {'p95_response_time': 2000, 'error_rate': 0})
            self.assertFalse(created.data['sla_config']['abort_on_breach'])
        self.assertFalse(models.PerfExecution.objects.exists())

    def test_without_public_config_selects_only_unique_preferred_environment_and_never_first_pool(self):
        ordinary = models.PerfEnvironment.objects.create(project=self.project, name='Not preferred', created_by=self.owner)
        self.assertEqual(self.defaults().data['defaults']['environment'], None)
        preferred = models.PerfEnvironment.objects.create(project=self.project, name='Preferred', is_active=True, created_by=self.owner)
        result = self.defaults().data
        self.assertEqual(result['defaults']['environment'], preferred.pk)
        self.assertIsNone(result['defaults']['account_pool_version'])
        self.assertNotEqual(result['defaults']['environment'], ordinary.pk)

    def test_custom_creation_and_existing_scene_settings_are_not_overwritten(self):
        self.configure()
        explicit = dict(project=self.project.pk, name='Custom', engine='K6', environment=None,
            global_environment=None, account_pool_version=None, account_pool_group='',
            load_config={'model': 'CONCURRENCY', 'concurrency': 7, 'duration': 90, 'iterations_per_vu': 0},
            runtime_config={'timeout': 55, 'auth_profile': {}}, perf_targets={'max_p95_rt': 900, 'max_error_rate': 3},
            sla_config={'enabled': False, 'thresholds': {}})
        result = self.client.post('/api/perf-testing/scenarios/', explicit, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        for key in ('environment', 'account_pool_version', 'load_config', 'runtime_config', 'perf_targets'):
            self.assertEqual(result.data[key], explicit[key])
        self.assertFalse(result.data['sla_config']['enabled'])
        previous = deepcopy(self.scene.runtime_config)
        patched = self.client.patch(f'/api/perf-testing/scenarios/{self.scene.pk}/', {'name': 'Renamed'}, format='json')
        self.assertEqual(patched.status_code, 200, patched.data)
        self.scene.refresh_from_db()
        self.assertEqual(self.scene.runtime_config, previous)
        self.assertIsNone(self.scene.environment)
        copied = self.client.post(f'/api/perf-testing/scenarios/{result.data["id"]}/duplicate/', {}, format='json')
        self.assertEqual(copied.status_code, 201, copied.data)
        for key in ('load_config', 'runtime_config', 'perf_targets', 'sla_config'):
            self.assertEqual(copied.data[key], result.data[key])

    def test_defaults_do_not_cross_project_or_access_boundaries(self):
        self.configure()
        second = models.PerfProject.objects.create(name='Own separate', owner=self.owner)
        result = self.client.get(self.project_url('scenario-defaults', second))
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIsNone(result.data['defaults']['environment'])
        self.assertIsNone(result.data['defaults']['account_pool_version'])
        self.client.force_authenticate(self.outside)
        self.assertEqual(self.defaults().status_code, 404)

    def test_partial_binding_override_does_not_mix_environment_and_credentials(self):
        self.configure()
        other = models.PerfEnvironment.objects.create(project=self.project, name='Other target', created_by=self.owner)
        for explicit in ({'environment': other.pk}, {'account_pool_version': None}):
            with self.subTest(explicit=explicit):
                result = self.client.post('/api/perf-testing/scenarios/', {
                    'project': self.project.pk, 'name': 'Explicit target', **explicit}, format='json')
                self.assertEqual(result.status_code, 201, result.data)
                self.assertEqual(result.data['environment'], explicit.get('environment'))
                self.assertIsNone(result.data['account_pool_version'])
                self.assertNotIn('auth_profile', result.data['runtime_config'])

    def test_implicit_k6_validates_custom_identity_the_same_as_explicit_k6(self):
        for engine in ({}, {'engine': 'K6'}):
            result = self.client.post('/api/perf-testing/scenarios/', {
                'project': self.project.pk, 'name': 'Invalid identity', **engine,
                'runtime_config': {'account_identity_variable': 'vu_id'}}, format='json')
            self.assertEqual(result.status_code, 400, result.data)

    def test_invalid_non_object_creation_returns_validation_error(self):
        for payload in ([], 'text'):
            result = self.client.post('/api/perf-testing/scenarios/', payload, format='json')
            self.assertEqual(result.status_code, 400)

    def test_raw_import_derives_contract_assertions_without_verification_or_forcing_auth(self):
        doc = fixtures.contract()
        doc['paths']['/health']['get']['responses']['200']['content'] = {'application/json': {'schema': {
            'type': 'object', 'required': ['healthy'], 'properties': {'healthy': {'type': 'boolean', 'const': True}}}}}
        self.assertEqual(self.upload(doc).status_code, 201)
        op = next(x for x in self.client.get(self.project_url()).data['results'] if x['path'] == '/health')
        response = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/import-from-api/', {'request_ids': [op['id']]}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        step = self.scene.steps.get()
        self.assertEqual(step.assertions, [{'type': 'STATUS_CODE', 'expected': 200},
            {'type': 'JSON_PATH', 'json_path': '$.healthy', 'expected': True, 'operator': 'eq'}])
        saved = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/save-steps/', {
            'steps': [{'id': step.pk, 'name': step.name, 'method': step.method, 'url': step.url,
                       'assertions': step.assertions}]}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['steps'][0]['assertions'], step.assertions)
        self.assertFalse(any(k.lower() in ('authorization', 'content-type') for k in step.headers))
        self.assertEqual(step.preparation, {})
        self.assertFalse(models.PerfPreparedRequest.objects.exists())
        self.assertFalse(models.PerfExecution.objects.exists())

    def test_conditional_status_uses_inherited_environment_headers(self):
        doc = fixtures.contract()
        doc['paths']['/health']['get']['responses']['304'] = {'description': 'not modified'}
        self.upload(doc)
        op = next(x for x in self.client.get(self.project_url()).data['results'] if x['path'] == '/health')
        for headers, expected in [({}, [{'type': 'STATUS_CODE', 'expected': 200}]), ({'If-None-Match': '{{etag}}'}, [])]:
            self.scene.env_config['headers'] = headers
            self.scene.save()
            response = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/import-from-api/', {'request_ids': [op['id']]}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(self.scene.steps.last().assertions, expected)

    def test_source_custom_assertions_headers_and_resource_gaps_are_preserved(self):
        self.upload()
        listed = self.client.get(self.project_url()).data['results']
        op = next(item for item in listed if item['path'] == '/items/{id}')
        from apps.api_testing.models import ApiRequest
        rules = [{'type': 'STATUS_CODE', 'expected': 404}, {'type': 'JSON_PATH', 'expr': '$.error', 'expected': 'missing'}]
        ApiRequest.objects.filter(pk=op['id']).update(assertions=rules, headers={'Authorization': 'Bearer {{custom_token}}'})
        response = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/import-from-api/', {'request_ids': [op['id']]}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        step = self.scene.steps.get()
        self.assertEqual(step.assertions, rules)
        self.assertEqual(step.headers, {'Authorization': 'Bearer {{custom_token}}'})
        self.assertEqual(step.preparation, {})
        self.assertTrue(any(req['provenance'] == 'example' for req in step.source_metadata['requirements']))
        self.assertEqual(step.params['page'], 1)


class ContractDefaultTests(TestCase):
    def operation(self, schema, *, method='GET', content=None, status='200'):
        response = {'description': 'safe', 'content': content or {'application/json': {'schema': schema}}}
        return {'method': method, 'raw': {'responses': {status: response}}}

    def test_nullable_writeonly_and_bodyless_contracts_do_not_assert_missing_json(self):
        required = {'type': 'object', 'required': ['value'], 'properties': {'value': {'const': True}}}
        cases = [self.operation({**required, 'nullable': True}), self.operation({**required, 'type': ['object', 'null']}),
            self.operation({'type': 'object', 'required': ['secret'], 'properties': {'secret': {'const': 'hidden', 'writeOnly': True}}}),
            self.operation(required, method='HEAD'), self.operation(required, status='204')]
        for operation in cases:
            with self.subTest(operation=operation):
                rules, _ = prepared_requests._success_assertions(operation, {})
                self.assertFalse(any(rule['type'] == 'JSON_PATH' for rule in rules))

    def test_ambiguous_media_and_malformed_response_are_conservative(self):
        schema = {'type': 'object', 'required': ['ok'], 'properties': {'ok': {'const': True}}}
        operation = self.operation(schema, content={'application/json': {'schema': schema}, 'text/plain': {'schema': {'type': 'string'}}})
        rules, gaps = prepared_requests._success_assertions(operation, {})
        self.assertEqual(rules, [{'type': 'STATUS_CODE', 'expected': 200}])
        self.assertTrue(gaps)
        for bad in [{'required': 7, 'properties': {}}, {'required': ['ok'], 'properties': []},
                    {'required': ['ok'], 'properties': {'ok': {'const': True, 'enum': None}}}]:
            with self.subTest(schema=bad):
                rules, gaps = prepared_requests._success_assertions(self.operation(bad), {})
                self.assertIsInstance(rules, list)

    def test_original_failure_assertions_and_auth_are_not_changed_by_prepare(self):
        operation = self.operation({'type': 'object', 'required': ['ok'], 'properties': {'ok': {'const': True}}})
        operation.update(security=[{'session': []}], security_schemes={'session': {'type': 'http', 'scheme': 'bearer'}})
        request = {'assertions': [{'type': 'STATUS_CODE', 'expected': 409}], 'headers': {'authorization': 'Bearer {{custom}}'}}
        result = prepared_requests.import_defaults(deepcopy(request), operation, {}, {'auth_profile': {'transport': 'BEARER', 'access_token_variable': 'token'}})
        self.assertEqual(result, request)

    def test_multiple_successes_and_conditional_304_do_not_guess_status(self):
        operation = {'method': 'GET', 'raw': {'responses': {'200': {}, '202': {}}}}
        self.assertEqual(prepared_requests._success_assertions(operation, {})[0], [])
        operation['raw']['responses'] = {'200': {}, '304': {}}
        self.assertEqual(prepared_requests._success_assertions(operation, {})[0], [])
        self.assertEqual(prepared_requests._success_assertions(operation, {}, {})[0], [{'type': 'STATUS_CODE', 'expected': 200}])
        request = {'assertions': [], 'headers': {'If-None-Match': '{{etag}}'}}
        result = prepared_requests.import_defaults(request, operation, {}, {})
        self.assertEqual(result['assertions'], [])

    def test_empty_string_constants_remain_unconfirmed_but_zero_and_false_are_safe(self):
        operation = self.operation({'type': 'object', 'required': ['blank', 'zero', 'flag'],
            'properties': {'blank': {'const': ''}, 'zero': {'const': 0}, 'flag': {'enum': [False]}}})
        rules, gaps = prepared_requests._success_assertions(operation, {})
        self.assertEqual([rule['expected'] for rule in rules], [200, 0, False])
        self.assertTrue(gaps)
