"""Persistent environment authorization and immutable execution inputs; no traffic."""
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient, APIRequestFactory

from apps.perf_testing.models import (
    PerfDataFile, PerfEnvironment, PerfExecution, PerfProject, PerfScenario, PerfScenarioStep,
)
from apps.perf_testing.services import executor
from apps.perf_testing.services.k6_execution import load_snapshot
from apps.perf_testing.serializers import PerfScenarioSerializer


class PerfEnvironmentTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        setting = override_settings(
            PERF_PRIVATE_ROOT=self.root / 'private', MEDIA_ROOT=self.root / 'media',
            PERF_MAX_DURATION=7200, PERF_MAX_CONCURRENCY=1000,
            PERF_MAX_CONCURRENT_EXECUTIONS=2, PERF_FORBIDDEN_HOSTS=[])
        setting.enable()
        self.addCleanup(setting.disable)
        users = get_user_model().objects
        self.owner = users.create_user(username='environment-owner')
        self.member = users.create_user(username='environment-member')
        self.outsider = users.create_user(username='environment-outsider')
        self.admin = users.create_user(username='environment-admin', is_staff=True)
        self.project = PerfProject.objects.create(name='env-project', owner=self.owner)
        self.project.members.add(self.member)
        self.other = PerfProject.objects.create(name='other', owner=self.outsider)
        self.env = PerfEnvironment.objects.create(
            name='Project target', project=self.project, created_by=self.owner,
            base_url='http://api1:3000', headers={'Authorization': 'private-header'},
            variables=[{'name': 'token', 'type': 'CONSTANT', 'secret': True,
                        'value': 'private-variable'}])
        self.scenario = PerfScenario.objects.create(
            project=self.project, created_by=self.owner, name='persistent', engine='K6',
            load_config={'model': 'CONCURRENCY', 'concurrency': 1,
                         'iterations_per_vu': 1, 'duration': 3})
        PerfScenarioStep.objects.create(scenario=self.scenario, name='business', url='/health')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = '/api/perf-testing/'
        self.spawn = mock.patch.object(executor, 'spawn_execution').start()
        self.addCleanup(mock.patch.stopall)
        mock.patch('apps.perf_testing.engines.k6_available', return_value=True).start()
        mock.patch('apps.perf_testing.engines.k6_version', return_value='fixed-version').start()

    def env_url(self, pk=None):
        return self.base + 'environments/' + (f'{pk}/' if pk else '')

    def scenario_url(self, action=''):
        return self.base + f'scenarios/{self.scenario.id}/' + (f'{action}/' if action else '')

    def select(self):
        response = self.client.patch(self.scenario_url(), {'environment': self.env.id}, format='json')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.scenario.refresh_from_db()

    def test_permissions_metadata_matches_project_and_global_write_rules(self):
        for user, global_write, projects in (
                (self.owner, False, [self.project.id]),
                (self.member, False, [self.project.id]),
                (self.outsider, False, [self.other.id]),
                (self.admin, True, [self.project.id, self.other.id])):
            self.client.force_authenticate(user)
            response = self.client.get(self.env_url() + 'permissions/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['can_manage_global'], global_write)
            self.assertCountEqual(response.data['project_ids'], projects)
            self.assertEqual(set(response.data), {'can_manage_global', 'project_ids'})
        self.project.members.remove(self.member)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(self.env_url() + 'permissions/').data['project_ids'], [])
        self.assertEqual(self.client.post(self.env_url() + 'permissions/', {}, format='json').status_code, 405)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.env_url() + 'permissions/').status_code, (401, 403))

    def test_crud_redacts_secrets_and_roundtrip_preserves_values(self):
        response = self.client.get(self.env_url(self.env.id))
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data['headers']['Authorization'], '******')
        self.assertEqual(data['variables'][0]['value'], '******')
        self.assertNotIn('private-', json.dumps(data, default=str))
        old_version, old_hash = data['version'], data['content_hash']
        data['name'] = 'Renamed'
        data['headers'] = {'authorization': '******', 'Accept': 'application/json'}
        response = self.client.put(self.env_url(self.env.id), data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.env.refresh_from_db()
        self.assertEqual(self.env.headers['authorization'], 'private-header')
        self.assertEqual(self.env.variables[0]['value'], 'private-variable')
        self.assertGreater(self.env.version, old_version)
        self.assertNotEqual(self.env.content_hash, old_hash)
        created = self.client.post(self.env_url(), {
            'name': 'Reusable', 'scope': 'PROJECT', 'project': self.project.id,
            'base_url': 'http://api1:3000'}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(self.client.delete(self.env_url(created.data['id'])).status_code, 204)

    def test_project_owner_member_admin_access_and_outsider_isolation(self):
        for user in (self.owner, self.member, self.admin):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.env_url(self.env.id)).status_code, 200)
            self.assertEqual(self.client.patch(self.env_url(self.env.id),
                                              {'name': 'Accessible'}, format='json').status_code, 200)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.env_url(self.env.id)).status_code, 404)
        listed = self.client.get(self.env_url() + '?page_size=0')
        self.assertEqual(listed.data, [])
        self.assertIn(self.client.post(self.env_url(), {
            'name': 'Forbidden', 'scope': 'PROJECT', 'project': self.project.id},
            format='json').status_code, (400, 403))
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.env_url()).status_code, (401, 403))

    def test_global_shared_selection_requires_admin_for_writes_and_valid_scope(self):
        payload = {'name': 'Global', 'scope': 'GLOBAL', 'base_url': 'http://api1:3000'}
        self.assertEqual(self.client.post(self.env_url(), payload, format='json').status_code, 403)
        self.client.force_authenticate(self.admin)
        result = self.client.post(self.env_url(), payload, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        pk = result.data['id']
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(self.env_url(pk)).status_code, 200)
        self.assertEqual(self.client.patch(self.scenario_url(),
            {'global_environment': pk}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(self.env_url(pk),
            {'name': 'Forbidden'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(self.env_url(pk)).status_code, 403)
        self.client.force_authenticate(self.admin)
        for payload in ({'scope': 'PROJECT'}, {'scope': 'GLOBAL', 'project': self.project.id},
                        {'scope': 'BOGUS', 'project': self.project.id}):
            response = self.client.post(self.env_url(), dict(payload, name='Bad'), format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_unknown_masks_rejected_without_clearing_secrets_or_echoing_values(self):
        for payload in (
            {'headers': {'X-Unknown': '******'}},
            {'variables': [{'name': 'missing', 'value': '******', 'secret': True}]},
            {'variables': [{'name': 'token', 'value': '******', 'secret': False}]},
            {'headers': {'Authorization': 'new-secret', 'authorization': 'ambiguous-secret'}},
            {'base_url': 'http://user:password@api1:3000'},
        ):
            result = self.client.patch(self.env_url(self.env.id), payload, format='json')
            self.assertEqual(result.status_code, 400, result.data)
            self.assertNotIn('new-secret', json.dumps(result.data))
            self.assertNotIn('password', json.dumps(result.data))
        self.env.refresh_from_db()
        self.assertEqual(self.env.headers['Authorization'], 'private-header')
        self.assertEqual(self.env.variables[0]['value'], 'private-variable')

    def test_cross_project_and_unauthorized_scenario_entrypoints_are_rejected(self):
        other_env = PerfEnvironment.objects.create(name='Other', project=self.other, created_by=self.outsider)
        for data in ({'environment': other_env.id}, {'global_environment': self.env.id},
                     {'environment': self.env.id, 'project': self.other.id}):
            result = self.client.patch(self.scenario_url(), data, format='json')
            self.assertIn(result.status_code, (400, 403), result.data)
        self.select()
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.patch(self.scenario_url(), {'name': 'No'}, format='json').status_code, 404)
        for action in ('preflight', 'debug', 'execute'):
            self.assertEqual(self.client.post(self.scenario_url(action), {}, format='json').status_code, 404)
        self.spawn.assert_not_called()

    def test_static_priority_csv_and_single_resolution_preflight_freeze(self):
        global_env = PerfEnvironment.objects.create(
            name='Global', scope='GLOBAL', created_by=self.admin, base_url='http://global.invalid',
            headers={'X-Layer': 'global', 'X-Global': 'g'},
            variables=[{'name': 'layer', 'value': 'global'}])
        csv = PerfDataFile.objects.create(
            project=self.project, name='users.csv', file_type='CSV', uploaded_by=self.owner,
            file=SimpleUploadedFile('users.csv', b'username\nalice\n'))
        self.env.variables += [{'name': 'layer', 'value': 'project'},
                               {'name': 'username', 'type': 'CSV', 'data_file_id': csv.id, 'column': 'username'}]
        self.env.headers['x-layer'] = 'project'
        self.env.save()
        self.select()
        self.scenario.global_environment = global_env
        self.scenario.env_config = {'headers': {'X-LAYER': 'scenario'}}
        self.scenario.variables = [{'name': 'layer', 'value': 'scenario'}]
        self.scenario.save()
        real_preflight = executor.preflight
        captured = {}

        def check_then_edit(*args, **kwargs):
            result = real_preflight(*args, **kwargs)
            captured.update(result)
            self.env.base_url = 'http://changed.invalid'
            self.env.variables = []
            self.env.save()
            return result

        overrides = {'headers': {'x-layer': 'run'}, 'variables': [{'name': 'layer', 'value': 'run'}]}
        with mock.patch.object(executor, 'preflight', side_effect=check_then_edit):
            response = self.client.post(self.scenario_url('execute'),
                                        {'environment_overrides': overrides}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        frozen = load_snapshot(self.root / 'private', response.data['execution']['id'])
        self.assertEqual(captured['estimated']['target_hosts'], ['api1'])
        self.assertEqual(frozen['env_config']['base_url'], 'http://api1:3000')
        headers = {k.lower(): v for k, v in frozen['env_config']['headers'].items()}
        self.assertEqual(headers['x-layer'], 'run')
        self.assertEqual(headers['x-global'], 'g')
        self.assertEqual(headers['authorization'], 'private-header')
        self.assertEqual(len(headers), len(frozen['env_config']['headers']))
        self.assertEqual({v['name']: v['value'] for v in frozen['variables'] if 'value' in v}['layer'], 'run')
        self.assertEqual(frozen['csv_data'][str(csv.id)]['rows'], [{'username': 'alice'}])
        source = frozen['environment_sources'][-1]
        self.assertEqual(source['id'], self.env.id)
        self.assertLess(source['version'], self.env.version)
        self.assertNotIn('private-', json.dumps(response.data, default=str))

    def test_reference_delete_conflict_and_activation_never_switch_frozen_target(self):
        self.select()
        self.assertEqual(self.client.delete(self.env_url(self.env.id)).status_code, 409)
        created = self.client.post(self.scenario_url('debug'), {}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        execution = PerfExecution.objects.get(id=created.data['execution']['id'])
        before = executor._execution_snapshot(execution)
        self.env.base_url = 'http://edited.invalid'
        self.env.save()
        PerfEnvironment.objects.create(name='Active different', project=self.project,
            created_by=self.owner, is_active=True, base_url='http://active.invalid')
        self.assertEqual(executor._execution_snapshot(execution), before)
        self.scenario.refresh_from_db()
        self.assertEqual(executor.build_snapshot(self.scenario)['env_config']['base_url'], 'http://edited.invalid')
        self.client.patch(self.scenario_url(), {'environment': None}, format='json')
        self.assertEqual(self.client.delete(self.env_url(self.env.id)).status_code, 204)
        self.assertEqual(executor._execution_snapshot(execution), before)

    def test_overrides_reject_untrusted_fields_and_masks(self):
        self.select()
        for data in ({'snapshot': {}}, {'headers': {'Authorization': '******'}},
                     {'variables': [{'name': 'unknown', 'value': '******'}]},
                     {'variables': [{'name': 'username', 'type': 'CSV', 'data_file_id': {'secret': 'no-log-secret'}}]},
                     {'verify_ssl': 'false'}, {'base_url': 42}):
            response = self.client.post(self.scenario_url('execute'),
                {'environment_overrides': data}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.spawn.assert_not_called()

    def test_invalid_csv_id_is_rejected_before_file_loading(self):
        self.select()
        with mock.patch.object(executor, '_load_csv_data', wraps=executor._load_csv_data) as loader:
            response = self.client.post(self.scenario_url('execute'), {
                'environment_overrides': {'variables': [{'name': 'username', 'type': 'CSV',
                                                         'data_file_id': {'secret': 'no-log-secret'}}]}},
                format='json')
        self.assertEqual(response.status_code, 400, response.data)
        loader.assert_not_called()
        self.assertNotIn('no-log-secret', json.dumps(response.data))

    def test_inline_environment_auth_header_roundtrip_is_masked(self):
        response = self.client.patch(self.scenario_url(), {
            'env_config': {'base_url': 'http://api1:3000', 'headers': {'Authorization': 'inline-secret'}}},
            format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['env_config']['headers']['Authorization'], '******')
        result = self.client.patch(self.scenario_url(), {'env_config': response.data['env_config']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.env_config['headers']['Authorization'], 'inline-secret')

    def test_duplicate_preserves_selected_environments(self):
        self.select()
        response = self.client.post(self.scenario_url('duplicate'), {}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['environment'], self.env.id)
        self.assertEqual(response.data['resolved_environment']['env_config']['base_url'], 'http://api1:3000')

    def test_secret_enum_roundtrip_masks_all_value_fields(self):
        response = self.client.patch(self.env_url(self.env.id), {'variables': [
            {'name': 'passwords', 'type': 'ENUM', 'secret': True,
             'values': ['first-secret', 'second-secret']}]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['variables'][0]['values'], '******')
        response = self.client.patch(self.env_url(self.env.id),
                                    {'variables': response.data['variables']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.env.refresh_from_db()
        self.assertEqual(self.env.variables[0]['values'], ['first-secret', 'second-secret'])

    def test_blank_base_inherits_and_false_ssl_explicitly_overrides(self):
        self.env.verify_ssl = True
        self.env.save()
        self.select()
        response = self.client.patch(self.scenario_url(),
            {'env_config': {'base_url': '', 'verify_ssl': False}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        preview = response.data['resolved_environment']['env_config']
        self.assertEqual(preview['base_url'], 'http://api1:3000')
        self.assertIs(preview['verify_ssl'], False)
        response = self.client.post(self.scenario_url('preflight'),
            {'environment_overrides': {'verify_ssl': True}}, format='json')
        self.assertTrue(response.data['passed'], response.data)
        self.assertTrue(response.data['resolved_environment']['env_config']['verify_ssl'])

    def test_saved_k6_https_without_environment_keeps_tls_inherit_absent(self):
        response = self.client.patch(self.scenario_url(), {
            'environment': None, 'global_environment': None,
            'env_config': {'base_url': 'https://example.test'}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        public = self.client.get(self.scenario_url()).data
        self.assertEqual(public['resolved_environment']['sources'], [])
        self.assertEqual(public['resolved_environment']['env_config']['base_url'], 'https://example.test')
        self.assertNotIn('verify_ssl', public['env_config'])
        self.assertNotIn('verify_ssl', public['resolved_environment']['env_config'])
        for verify_ssl in (True, False):
            response = self.client.patch(self.scenario_url(), {
                'env_config': {'base_url': 'https://example.test', 'verify_ssl': verify_ssl}}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertIs(response.data['resolved_environment']['env_config']['verify_ssl'], verify_ssl)

    def test_referenced_scope_or_project_change_is_rejected_even_for_admin(self):
        self.select()
        self.client.force_authenticate(self.admin)
        for data in ({'scope': 'GLOBAL', 'project': None}, {'project': self.other.id}):
            response = self.client.patch(self.env_url(self.env.id), data, format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_revoked_membership_and_invalid_saved_reference_block_service_entrypoints(self):
        self.select()
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.get(self.scenario_url()).status_code, 200)
        self.project.members.remove(self.member)
        for action in ('preflight', 'execute', 'debug'):
            response = self.client.post(self.scenario_url(action), {}, format='json')
            self.assertEqual(response.status_code, 404)
        self.assertFalse(executor.preflight(self.scenario, user=self.member)['passed'])
        self.assertIsNone(executor.start_execution(self.scenario, user=self.member)[0])
        self.assertIsNone(executor.debug_run(self.scenario, user=self.member)['execution'])
        PerfEnvironment.objects.filter(pk=self.env.id).update(project=self.other)
        self.assertFalse(executor.preflight(self.scenario, user=self.owner)['passed'])
        self.assertIsNone(executor.start_execution(self.scenario, user=self.owner)[0])
        self.spawn.assert_not_called()

    def test_csv_reference_from_another_project_is_rejected(self):
        foreign = PerfDataFile.objects.create(
            project=self.other, name='foreign.csv', file_type='CSV', uploaded_by=self.outsider,
            file=SimpleUploadedFile('foreign.csv', b'username\nsecret-foreign-user\n'))
        self.env.variables = [{'name': 'username', 'type': 'CSV',
                               'data_file_id': foreign.id, 'column': 'username'}]
        self.env.save()
        self.select()
        response = self.client.post(self.scenario_url('execute'), {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertNotIn('secret-foreign-user', json.dumps(response.data))
        self.spawn.assert_not_called()

    def test_legacy_engine_rejects_references_and_overrides_but_keeps_inline_compatibility(self):
        self.select()
        result = self.client.patch(self.scenario_url(), {'engine': 'BUILTIN'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        result = self.client.patch(self.scenario_url(), {
            'engine': 'BUILTIN', 'environment': None,
            'env_config': {'base_url': 'http://legacy.invalid'}}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        result = self.client.post(self.scenario_url('preflight'),
            {'environment_overrides': {'base_url': 'http://override.invalid'}}, format='json')
        self.assertFalse(result.data['passed'])
        result = self.client.post(self.scenario_url('preflight'), {}, format='json')
        self.assertTrue(result.data['passed'], result.data)
        self.assertEqual(result.data['estimated']['target_hosts'], ['legacy.invalid'])
        self.spawn.assert_not_called()

    def test_partial_activation_preserves_current_content_version_and_fingerprint(self):
        from apps.perf_testing.services.environments import content_hash
        updated = PerfEnvironment.objects.get(pk=self.env.pk)
        updated.headers = {'Authorization': 'newer-private-header'}
        updated.save()
        self.env.is_active = True
        self.env.save(update_fields=['is_active'])
        self.env.refresh_from_db()
        self.assertEqual(self.env.headers, updated.headers)
        self.assertEqual(self.env.version, updated.version)
        self.assertEqual(self.env.content_hash, content_hash(self.env))

    def test_scenario_secret_enum_can_be_saved_from_masked_response(self):
        self.scenario.engine = 'BUILTIN'
        self.scenario.variables = [{'name': 'credential', 'secret': True,
                                    'type': 'ENUM', 'values': ['sensitive-enum']}]
        self.scenario.save()
        response = self.client.get(self.scenario_url())
        self.assertNotIn('sensitive-enum', json.dumps(response.data, default=str))
        response = self.client.patch(self.scenario_url(),
            {'variables': response.data['variables']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.variables[0]['values'], ['sensitive-enum'])

    def test_legacy_inline_nullable_headers_and_variable_names_remain_compatible(self):
        self.scenario.engine = 'BUILTIN'
        self.scenario.env_config = {'base_url': 'http://legacy.invalid', 'headers': None}
        self.scenario.save()
        response = self.client.get(self.scenario_url())
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch(self.scenario_url(),
            {'variables': [{'name': '旧变量', 'type': 'CONSTANT', 'value': 'legacy'}]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_project_delete_removes_own_references_and_preserves_shared_environment(self):
        self.select()
        global_env = PerfEnvironment.objects.create(
            name='Shared', scope='GLOBAL', created_by=self.admin)
        self.scenario.global_environment = global_env
        self.scenario.save(update_fields=['global_environment'])
        other_scene = PerfScenario.objects.create(
            name='Other project', project=self.other, created_by=self.outsider,
            engine='K6', global_environment=global_env)
        self.client.raise_request_exception = False
        response = self.client.delete(self.base + f'projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(PerfProject.objects.filter(pk=self.project.pk).exists())
        self.assertFalse(PerfEnvironment.objects.filter(pk=self.env.pk).exists())
        self.assertFalse(PerfScenario.objects.filter(pk=self.scenario.pk).exists())
        self.assertTrue(PerfEnvironment.objects.filter(pk=global_env.pk).exists())
        other_scene.refresh_from_db()
        self.assertEqual(other_scene.global_environment_id, global_env.id)
        self.assertEqual(other_scene.project_id, self.other.id)

    def test_project_delete_with_external_reference_returns_conflict_and_rolls_back(self):
        self.select()
        other_scene = PerfScenario.objects.create(
            name='Unexpected external reference', project=self.other, created_by=self.outsider,
            engine='K6', environment=self.env)
        self.client.raise_request_exception = False
        response = self.client.delete(self.base + f'projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 409)
        self.assertTrue(PerfProject.objects.filter(pk=self.project.pk).exists())
        self.assertTrue(PerfEnvironment.objects.filter(pk=self.env.pk).exists())
        for scene in (self.scenario, other_scene):
            scene.refresh_from_db()
            self.assertEqual(scene.environment_id, self.env.id)

    def test_outsider_project_delete_preserves_project_environment_and_scene_reference(self):
        self.select()
        self.client.force_authenticate(self.outsider)
        response = self.client.delete(self.base + f'projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 403, getattr(response, 'data', None))
        self.project.refresh_from_db()
        self.env.refresh_from_db()
        self.scenario.refresh_from_db()
        self.assertEqual(self.project.owner_id, self.owner.id)
        self.assertEqual(self.env.project_id, self.project.id)
        self.assertEqual(self.scenario.project_id, self.project.id)
        self.assertEqual(self.scenario.environment_id, self.env.id)

    def test_project_delete_with_active_execution_preserves_references(self):
        self.select()
        execution = PerfExecution.objects.create(
            scenario=self.scenario, project=self.project, execution_no='active-project-delete',
            status='PENDING')
        response = self.client.delete(self.base + f'projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.environment_id, self.env.id)
        self.assertTrue(PerfExecution.objects.filter(pk=execution.pk).exists())

    def test_stale_scenario_masks_preserve_rotated_headers_constant_and_enum_values(self):
        self.scenario.env_config = {'headers': {'Authorization': 'old-header'}, 'variables': [
            {'name': 'inline_token', 'type': 'CONSTANT', 'secret': True, 'value': 'old-inline'}]}
        self.scenario.variables = [
            {'name': 'token', 'type': 'CONSTANT', 'secret': True, 'value': 'old-constant'},
            {'name': 'choice', 'type': 'ENUM', 'secret': True, 'values': ['old-enum']}]
        self.scenario.save()
        public = self.client.get(self.scenario_url()).data
        request = APIRequestFactory().patch('/')
        request.user = self.owner
        serializer = PerfScenarioSerializer(self.scenario, data={
            'env_config': public['env_config'], 'variables': public['variables']}, partial=True,
            context={'request': request})
        serializer.is_valid(raise_exception=True)
        new_config = {'headers': {'Authorization': 'rotated-header'}, 'variables': [
            {'name': 'inline_token', 'type': 'CONSTANT', 'secret': True, 'value': 'rotated-inline'}]}
        new_variables = [
            {'name': 'token', 'type': 'CONSTANT', 'secret': True, 'value': 'rotated-constant'},
            {'name': 'choice', 'type': 'ENUM', 'secret': True, 'values': ['rotated-enum']}]
        PerfScenario.objects.filter(pk=self.scenario.pk).update(
            env_config=new_config, variables=new_variables)
        serializer.save()
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.env_config, new_config)
        self.assertEqual(self.scenario.variables, new_variables)
        self.assertNotIn('rotated-', json.dumps(serializer.data, default=str))

    def test_stale_scenario_save_rechecks_current_project_permission(self):
        request = APIRequestFactory().patch('/')
        request.user = self.owner
        serializer = PerfScenarioSerializer(self.scenario, data={'name': 'stale-write'},
            partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        PerfScenario.objects.filter(pk=self.scenario.pk).update(project=self.other)
        with self.assertRaises(PermissionDenied):
            serializer.save()
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.project_id, self.other.id)
        self.assertEqual(self.scenario.name, 'persistent')

    def test_stale_scenario_save_rechecks_explicit_environment_scope(self):
        request = APIRequestFactory().patch('/')
        request.user = self.owner
        serializer = PerfScenarioSerializer(self.scenario, data={'environment': self.env.pk},
            partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        PerfEnvironment.objects.filter(pk=self.env.pk).update(project=self.other)
        with self.assertRaises(ValidationError):
            serializer.save()
        self.scenario.refresh_from_db()
        self.assertIsNone(self.scenario.environment_id)
