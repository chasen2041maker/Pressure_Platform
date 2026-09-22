"""K6 debug uses the normal execution lifecycle without sending traffic."""
import copy
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.perf_testing.models import PerfDataFile, PerfExecution, PerfProject, PerfScenario, PerfScenarioStep
from apps.perf_testing.services import executor
from apps.perf_testing.services.k6_execution import FileLease, load_snapshot
from apps.perf_testing.views import PerfScenarioViewSet


class K6DebugTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = override_settings(
            PERF_PRIVATE_ROOT=self.root / 'private', MEDIA_ROOT=self.root / 'media',
            PERF_MAX_DURATION=7200, PERF_MAX_CONCURRENCY=1000,
            PERF_MAX_CONCURRENT_EXECUTIONS=2, PERF_FORBIDDEN_HOSTS=[])
        settings.enable()
        self.addCleanup(settings.disable)
        self.user = get_user_model().objects.create_user(username='k6-debug-test')
        self.project = PerfProject.objects.create(name='debug', owner=self.user)
        self.pool = PerfDataFile.objects.create(
            project=self.project, name='accounts.csv', file_type='CSV', uploaded_by=self.user,
            file=SimpleUploadedFile('accounts.csv', b'username,token\ndebug-user,private-token\n'))
        self.scenario = PerfScenario.objects.create(
            project=self.project, created_by=self.user, name='debug scenario', engine='K6',
            load_config={'model': 'CONCURRENCY', 'concurrency': 1000,
                         'duration': 7200, 'iterations_per_vu': 10},
            env_config={'base_url': 'http://target.invalid',
                        'headers': {'Authorization': 'private-header'}},
            variables=[{'name': 'username', 'type': 'CSV',
                        'data_file_id': self.pool.id, 'column': 'username'}],
            runtime_config={'timeout': 30, 'account_identity_variable': 'username'})
        self.step = PerfScenarioStep.objects.create(
            scenario=self.scenario, name='business', url='/resource',
            params={'user': '${username}'})
        self.spawn = mock.patch.object(executor, 'spawn_execution').start()
        self.addCleanup(mock.patch.stopall)
        mock.patch('apps.perf_testing.engines.k6_available', return_value=True).start()
        mock.patch('apps.perf_testing.engines.k6_version', return_value='frozen-docker-fingerprint').start()

    def request(self, payload=None, action='debug'):
        request = APIRequestFactory().post('/', payload or {}, format='json')
        force_authenticate(request, user=self.user)
        return PerfScenarioViewSet.as_view({'post': action})(request, pk=self.scenario.pk)

    def test_debug_freezes_one_user_one_round_and_preserves_scenario_and_inputs(self):
        before = executor.build_snapshot(self.scenario)
        result = self.request()
        self.assertEqual(result.status_code, 201, result.data)
        execution = PerfExecution.objects.get(id=result.data['execution']['id'])
        private = load_snapshot(self.root / 'private', execution.id)
        for load in (execution.load_snapshot, private['load_config']):
            self.assertEqual(load['concurrency'], 1)
            self.assertEqual(load['iterations_per_vu'], 1)
            self.assertEqual(load['model'], 'CONCURRENCY')
            self.assertEqual(load['duration'], 60)
            self.assertEqual(load['_planned_duration'], 60)
            self.assertEqual(load['_purpose'], 'debug')
        for field in ('steps', 'runtime_config', 'env_config', 'variables', 'csv_data'):
            self.assertEqual(private[field], before[field])
        self.scenario.refresh_from_db()
        self.assertEqual(executor.build_snapshot(self.scenario), before)
        self.assertEqual(private['k6_version'], 'frozen-docker-fingerprint')
        self.assertEqual(execution.executed_by, self.user)
        self.assertEqual(result.data['monitor_url'],
                         f'/performance-testing/executions/{execution.id}/monitor')
        self.assertNotIn('private-token', json.dumps(result.data, default=str))
        self.assertNotIn('private-header', json.dumps(result.data, default=str))
        self.spawn.assert_called_once_with(execution)

    def test_preflight_checks_the_same_load_that_is_frozen(self):
        with mock.patch.object(executor, 'preflight', wraps=executor.preflight) as preflight:
            result = self.request()
        self.assertEqual(result.status_code, 201, result.data)
        checked = preflight.call_args.kwargs['load_config']
        execution = PerfExecution.objects.get()
        frozen = load_snapshot(self.root / 'private', execution.id)['load_config']
        self.assertEqual(checked, {key: value for key, value in frozen.items()
                                   if key != '_planned_duration'})
        self.assertEqual(result.data['preflight']['estimated']['peak_concurrency'], 1)
        self.assertEqual(result.data['preflight']['estimated']['estimated_requests'], 1)

    def test_untrusted_request_cannot_expand_debug_or_replace_inputs(self):
        result = self.request({
            'load_config': {'model': 'RPS', 'concurrency': 999999, 'iterations_per_vu': 9999,
                            'duration': 999999, 'stages': [{'target': 999999, 'duration': 9999}]},
            'concurrency': 999999, 'duration': 999999, 'skip_preflight': True,
            'runtime_config': {'worker_processes': 100, 'timeout': 999999},
            'env_config': {'base_url': 'http://evil.invalid'},
        })
        self.assertEqual(result.status_code, 201, result.data)
        private = load_snapshot(self.root / 'private', PerfExecution.objects.get().id)
        self.assertEqual(private['load_config']['concurrency'], 1)
        self.assertEqual(private['load_config']['iterations_per_vu'], 1)
        self.assertEqual(private['load_config']['duration'], 60)
        self.assertEqual(private['runtime_config']['worker_processes'], 1)
        self.assertEqual(private['env_config']['base_url'], 'http://target.invalid')

    @override_settings(PERF_MAX_DURATION=12)
    def test_debug_limit_respects_lower_platform_bound(self):
        result = self.request()
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['execution']['load_snapshot']['duration'], 12)

    def test_engine_script_applies_hard_bound_despite_long_timeout_and_think_time(self):
        from apps.perf_testing.engines.k6_engine import K6Engine
        self.scenario.runtime_config['timeout'] = 300
        self.scenario.save()
        self.step.think_time = {'type': 'FIXED', 'min': 9999000}
        self.step.save()
        result = self.request()
        self.assertEqual(result.status_code, 201, result.data)
        private = load_snapshot(self.root / 'private', PerfExecution.objects.get().id)
        with mock.patch('apps.perf_testing.engines.k6_engine.k6_docker.runner_mode', return_value='NATIVE'), \
                mock.patch('apps.perf_testing.engines.k6_engine.is_available', return_value=True):
            engine = K6Engine(private, work_dir=self.root / 'engine')
            engine.prepare()
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required to evaluate the generated k6 options without HTTP')
        harness = r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8')
  .replace(/^import .*;$/gm, '')
  .replace('export default function', 'function runIteration')
  .replace(/^export /gm, '');
const context = { __ENV: {K6_TESTHUB_CONFIG: process.argv[2]},
  open: path => fs.readFileSync(path, 'utf8'),
  SharedArray: function(name, load) { return load(); },
  http: {request() { throw new Error('HTTP must not run'); }} };
vm.runInNewContext(source + '\nthis.result = options;', context);
process.stdout.write(JSON.stringify(context.result));
'''
        evaluated = subprocess.run(
            [node, '-e', harness, str(engine.work_dir / 'scenario.js'),
             str(engine.work_dir / 'scenario.private.json')],
            check=False, capture_output=True, text=True, timeout=10)
        self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
        options = json.loads(evaluated.stdout)['scenarios']['business']
        self.assertEqual(options, {'executor': 'per-vu-iterations', 'vus': 1,
                                  'iterations': 1, 'maxDuration': '60s', 'gracefulStop': '0s'})
        self.assertEqual(private['runtime_config']['timeout'], 300)
        self.assertEqual(private['steps'][0]['think_time']['min'], 9999000)

    def test_debug_replaces_even_invalid_original_load_before_building_profile(self):
        self.scenario.load_config = {'model': 'RAMPING', 'stages': 'invalid', 'duration': 'invalid'}
        self.scenario.save()
        result = self.request()
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['execution']['load_snapshot']['duration'], 60)

    def test_regular_execution_keeps_original_load(self):
        self.scenario.variables = []
        self.step.params = {}
        self.scenario.save()
        self.step.save()
        result = self.request(action='execute')
        self.assertEqual(result.status_code, 201, result.data)
        public = result.data['execution']['load_snapshot']
        self.assertEqual(public['concurrency'], 1000)
        self.assertEqual(public['iterations_per_vu'], 10)
        self.assertEqual(public['duration'], 7200)
        self.assertNotIn('_purpose', public)

    def test_empty_override_does_not_preflight_original_load_then_freeze_empty_load(self):
        self.scenario.variables = []
        self.step.params = {}
        self.scenario.save()
        self.step.save()
        execution, check = executor.start_execution(self.scenario, load_config={})
        self.assertIsNone(execution)
        self.assertFalse(check['passed'])
        self.spawn.assert_not_called()

    def assert_rejected(self, payload=None, contains=None):
        result = self.request(payload)
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(result.data['preflight']['passed'])
        if contains:
            self.assertIn(contains, ' '.join(result.data['preflight']['errors']))
        self.spawn.assert_not_called()
        return result

    def test_no_business_steps_does_not_spawn(self):
        self.step.delete()
        self.assert_rejected(contains='业务步骤')
        self.assertEqual(PerfExecution.objects.count(), 0)

    def test_missing_account_rows_does_not_spawn(self):
        self.pool.file.delete(save=False)
        self.assert_rejected(contains='账号数据行数不足')
        self.assertEqual(PerfExecution.objects.count(), 0)

    def test_unavailable_engine_does_not_spawn(self):
        with mock.patch('apps.perf_testing.engines.k6_available', return_value=False):
            self.assert_rejected(contains='引擎不可用')

    def test_active_execution_does_not_spawn(self):
        executor.create_execution(self.scenario)
        self.assert_rejected(contains='正在执行')
        self.assertEqual(PerfExecution.objects.count(), 1)

    def test_run_lease_blocks_debug_before_creation(self):
        with FileLease(self.root / 'private', 'run'):
            self.assert_rejected(contains='账号池冲突')
        self.assertEqual(PerfExecution.objects.count(), 0)

    def test_request_and_persisted_script_modes_cannot_bypass_debug_limit(self):
        for raw in ({'mode': 'script', 'jmx_path': 'private-path'},
                    {'mode': 'ScRiPt'}, 'private-string'):
            with self.subTest(raw=raw):
                result = self.assert_rejected({'script_ref': raw}, contains='脚本')
                self.assertNotIn('private-', json.dumps(result.data, default=str))
        self.scenario.runtime_config['script_ref'] = {'mode': 'script', 'data_file_id': 1}
        self.scenario.save()
        self.assert_rejected({'script_ref': {'mode': 'scenario'}}, contains='脚本')
        self.assertEqual(PerfExecution.objects.count(), 0)

    def test_error_does_not_expose_credentials(self):
        with mock.patch.object(executor, 'preflight', side_effect=ValueError('private-token')):
            result = self.request()
        self.assertEqual(result.status_code, 400)
        self.assertNotIn('private-token', json.dumps(result.data, default=str))
        self.spawn.assert_not_called()

    def test_legacy_debug_keeps_synchronous_response(self):
        self.scenario.engine = 'BUILTIN'
        self.scenario.save()
        expected = {'engine': 'BUILTIN', 'passed': True, 'steps': [{'success': True}]}
        with mock.patch.object(executor, 'debug_run', return_value=copy.deepcopy(expected)):
            result = self.request()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data, expected)
        self.spawn.assert_not_called()
