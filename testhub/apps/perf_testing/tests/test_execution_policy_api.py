from io import StringIO
from unittest import mock
from django.core.management import call_command
from django.test import TestCase
from apps.perf_testing.services import executor
from . import test_api_catalog as fixtures
from .test_execution_policy import policy
from .test_execution_policy import data


class PolicyApiTests(TestCase):
    setUp = fixtures.ApiCatalogTests.setUp

    def payload(self):
        value = policy(); value.update(vu_start=1, vu_end=1)
        return {'scenario': self.scene.pk, 'name': 'Bounded', 'url': '/own', 'execution_policy': value}

    def test_save_copy_freeze_preserve_policy(self):
        response = self.client.post('/api/perf-testing/steps/', self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data.get('execution_policy'), self.payload()['execution_policy'])
        saved = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/save-steps/',
                                 {'steps': [response.data]}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        cloned = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/duplicate/', {}, format='json')
        self.assertEqual(cloned.status_code, 201, cloned.data)
        self.assertEqual(cloned.data['steps'][0]['execution_policy'], self.payload()['execution_policy'])
        with mock.patch('apps.perf_testing.engines.k6_version', return_value='fixture-version'):
            execution = executor.create_execution(self.scene, user=self.owner)
        before = executor._execution_snapshot(execution)
        self.assertEqual(before['steps'][0]['execution_policy'], self.payload()['execution_policy'])
        self.assertEqual(execution.steps_snapshot[0]['execution_policy']['vu_start'], 1)
        self.assertNotIn('group_id', execution.steps_snapshot[0]['execution_policy'])
        self.scene.steps.update(execution_policy={})
        self.assertEqual(executor._execution_snapshot(execution), before)

    def test_bad_policies_and_setup_are_rejected(self):
        for extra in [{'execution_policy': {'vu_start': 1}}, {'is_setup': True}]:
            response = self.client.post('/api/perf-testing/steps/', dict(self.payload(), **extra), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.scene.steps.count(), 0)

    def test_bulk_group_conflict_rolls_back(self):
        first = self.payload(); second = dict(first, name='Other', execution_policy=dict(first['execution_policy'], max_runs_per_vu=2))
        result = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/save-steps/', {'steps': [first, second]}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertEqual(self.scene.steps.count(), 0)

    def test_bulk_save_uses_effective_headers_with_auth_profile(self):
        snapshot = data(users=1)
        self.scene.env_config = {'headers': {'X-Owned': '{{owned}}', 'Authorization': '{{owned}}'}}
        self.scene.runtime_config = {'auth_profile': {
            'mode': 'STATIC', 'transport': 'BEARER', 'access_token_variable': 'user_id'}}
        self.scene.save(update_fields=['env_config', 'runtime_config'])
        snapshot['steps'].append({'id': 4, 'url': '/after'})
        for step in snapshot['steps']:
            step['name'] = f"fixture-{step.pop('id')}"
            step.update(headers={'x-OWNED': 'fixed', 'cookie': '{{owned}}'})
            if step.get('execution_policy'):
                step['execution_policy'].update(vu_start=1, vu_end=1)
        response = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/save-steps/',
                                    {'steps': snapshot['steps']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.scene.steps.count(), 4)

    def test_matching_migrations(self):
        call_command('makemigrations', 'perf_testing', check=True, dry_run=True, stdout=StringIO(), verbosity=0)

    def test_preflight_budget_and_public_policy_match_frozen_participation(self):
        from apps.perf_testing.models import PerfScenarioStep
        snapshot = data()
        self.scene.load_config = snapshot['load_config']; self.scene.save(update_fields=['load_config'])
        for order, step in enumerate(snapshot['steps']):
            PerfScenarioStep.objects.create(scenario=self.scene, order=order,
                **{key: value for key, value in step.items() if key != 'id'})
        with mock.patch('apps.perf_testing.engines.k6_available', return_value=True):
            result = executor.preflight(self.scene, user=self.owner)
        self.assertTrue(result['passed'], result['errors'])
        self.assertEqual(result['estimated']['estimated_requests'], 10)
        self.assertEqual(result['estimated']['estimated_http_requests'], 10)
        self.assertEqual(result['estimated']['execution_groups'][0]['attempt_limit'], 1)

    def test_public_report_resanitizes_policy_without_private_group_names(self):
        from types import SimpleNamespace
        from apps.perf_testing.services import reporter
        step = {'protocol': 'WEBSOCKET', 'execution_policy': {'group_index': 1, **policy(vu_start=1, vu_end=1), 'token': 'SECRET'}}
        execution = SimpleNamespace(pk=None, steps_snapshot=[step], load_snapshot={'_engine': 'K6'}, summary={})
        value = reporter.report_steps(execution)[0]['execution_policy']
        self.assertEqual(value['group_index'], 1)
        self.assertNotIn('token', value); self.assertNotIn('group_id', value)
