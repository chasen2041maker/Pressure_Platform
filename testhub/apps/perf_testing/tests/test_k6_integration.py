"""K6 execution isolation and lifecycle contract (no network or application DB)."""
import copy
import importlib
import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _lock_worker(root, queue):
    from apps.perf_testing.services.k6_execution import FileLease, K6ExecutionError
    try:
        with FileLease(root, 'run'):
            queue.put('acquired')
    except K6ExecutionError:
        queue.put('blocked')


class K6ExecutionIsolationTests(unittest.TestCase):
    def test_deadline_diagnoses_are_safe_but_arbitrary_details_remain_private(self):
        safe_error = self.module().safe_engine_error
        for message in ('1 个用户在发压到期时尚未完成全部前置步骤',
                        'k6 发压窗口起点不一致，不能判定执行成功'):
            self.assertEqual(safe_error(RuntimeError(message)), message)
            self.assertNotIn('test-secret', safe_error(RuntimeError(message + ' token=test-secret')))

    def module(self):
        spec = importlib.util.find_spec('apps.perf_testing.services.k6_execution')
        self.assertIsNotNone(spec, 'K6 execution isolation service is missing')
        return importlib.import_module('apps.perf_testing.services.k6_execution')

    def test_private_snapshot_preserves_all_inputs_after_scene_and_csv_change(self):
        module = self.module()
        original = {
            'engine': 'K6', 'engine_version': 'k6 test',
            'variables': [{'name': 'token', 'type': 'CSV', 'data_file_id': 2}],
            'csv_data': {'2': {'rows': [{'token': 'test-secret'}]}},
            'env_config': {'base_url': 'http://127.0.0.1:12345'},
            'runtime_config': {'timeout': 3}, 'sla_config': {'enabled': False},
            'load_config': {'concurrency': 10}, 'steps': [{'name': 'before'}],
        }
        expected = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as root:
            path = module.save_snapshot(root, 8, original)
            original['csv_data']['2']['rows'][0]['token'] = 'changed'
            original['env_config']['base_url'] = 'http://different.invalid'
            original['steps'].clear()
            self.assertEqual(module.load_snapshot(root, 8), expected)
            envelope = json.loads(Path(path).read_text(encoding='utf-8'))
            self.assertEqual(envelope['schema_version'], 1)
            self.assertEqual(envelope['source_revision'], '272ab153c6381b8eb726847dee3b2d819f2574ca')

    def test_missing_snapshot_fails_closed(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(module.K6ExecutionError, '快照'):
                module.load_snapshot(root, 88)

    def test_snapshot_cannot_be_overwritten(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as root:
            module.save_snapshot(root, 1, {'engine': 'K6'})
            with self.assertRaises(module.K6ExecutionError):
                module.save_snapshot(root, 1, {'engine': 'OTHER'})

    def test_os_lock_blocks_other_process_and_releases_after_owner_exit(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as root:
            queue = multiprocessing.get_context('spawn').Queue()
            with module.FileLease(root, 'run'):
                process = multiprocessing.get_context('spawn').Process(target=_lock_worker, args=(root, queue))
                process.start()
                self.assertEqual(queue.get(timeout=10), 'blocked')
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            with module.FileLease(root, 'run'):
                pass

    def test_stop_monitor_stops_engine_when_database_status_changes(self):
        module = self.module()
        status = ['RUNNING']
        stopped = threading.Event()
        monitor = module.StopMonitor(lambda: status[0], stopped.set, interval=0.01)
        monitor.start()
        try:
            status[0] = 'STOPPING'
            self.assertTrue(stopped.wait(2), 'STOPPING must stop the actual engine')
        finally:
            monitor.close()

    def test_run_exception_stops_engine_and_preserves_partial_results(self):
        module = self.module()
        events = []

        class Engine:
            def prepare(self):
                events.append('prepare')
            def run(self):
                events.append('run')
                raise RuntimeError('synthetic failure')
            def stop(self):
                events.append('stop')
            def collect(self):
                events.append('collect')
                return {'summary': {'total_requests': 7}}

        result, error = module.drive_engine(Engine(), lambda: 'RUNNING', lambda: events.append('started'))
        self.assertEqual(result['summary']['total_requests'], 7)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(events, ['prepare', 'started', 'run', 'stop', 'collect'])

    def test_stop_monitor_reports_cleanup_failure_without_escaping_retry(self):
        module = self.module()
        for secondary in (None, RuntimeError('SECOND_PRIVATE_DETAIL')):
            with self.subTest(secondary=secondary is not None):
                stop = mock.Mock(side_effect=[RuntimeError('FIRST_PRIVATE_DETAIL'), secondary])
                monitor = module.StopMonitor(lambda: 'STOPPING', stop, interval=0)
                monitor._watch()
                self.assertEqual(stop.call_count, 2)
                self.assertIsInstance(monitor.error, module.K6ExecutionError)
                self.assertIn('停止清理失败', str(monitor.error))
                self.assertNotIn('无法读取', str(monitor.error))
                self.assertNotIn('PRIVATE_DETAIL', str(monitor.error))

    def test_stop_monitor_distinguishes_read_failure_and_contains_failed_cleanup(self):
        module = self.module()
        for fails in (False, True):
            with self.subTest(stop_fails=fails):
                stop = mock.Mock(side_effect=RuntimeError('PRIVATE_DETAIL') if fails else None)
                monitor = module.StopMonitor(mock.Mock(side_effect=RuntimeError('READ_PRIVATE_DETAIL')), stop, interval=0)
                monitor._watch()
                self.assertIn('无法读取执行状态', str(monitor.error))
                if fails: self.assertIn('停止清理失败', str(monitor.error))
                self.assertNotIn('PRIVATE_DETAIL', str(monitor.error))

    def test_pending_stop_never_starts_pressure(self):
        module = self.module()
        from unittest.mock import Mock
        engine = Mock()
        engine.collect.return_value = {'summary': {'total_requests': 0}}
        started = Mock()
        result, error = module.drive_engine(engine, lambda: 'STOPPING', started)
        self.assertIsNone(error)
        engine.run.assert_not_called()
        started.assert_not_called()
        engine.stop.assert_called_once()

    def test_known_runtime_error_is_actionable_but_unknown_exception_is_redacted(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'safe_engine_error'))
        safe = '10 个用户前置登录或提取失败，未发送这些用户的业务请求'
        self.assertEqual(module.safe_engine_error(RuntimeError(safe)), safe)
        self.assertNotIn('test-secret', module.safe_engine_error(RuntimeError('token=test-secret')))


class K6PlatformEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        import django
        from django.apps import apps
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
        if not apps.ready:
            django.setup()
        from apps.perf_testing.services import executor
        from apps.perf_testing import engines
        from apps.perf_testing.models import PerfExecution, PerfScenario
        cls.executor, cls.engines = executor, engines
        cls.execution_model, cls.scenario_model = PerfExecution, PerfScenario

    def test_k6_registered_in_factory_and_model(self):
        self.assertIn('K6', self.engines.ENGINES)
        self.assertIn('K6', dict(self.scenario_model.ENGINE_CHOICES))

    def test_debug_does_not_use_builtin_or_expose_request_data(self):
        scenario = SimpleNamespace(id=1, name='test', engine='K6')
        execution = SimpleNamespace(id=17)
        check = {'passed': True, 'errors': []}
        with mock.patch.object(self.executor, 'start_execution', return_value=(execution, check)) as start, \
                mock.patch('apps.perf_testing.engines.builtin.debug_run', new=mock.AsyncMock()) as builtin:
            result = self.executor.debug_run(scenario)
        builtin.assert_not_called()
        self.assertIs(result['execution'], execution)
        self.assertTrue(result['preflight']['passed'])
        self.assertNotIn('steps', result)
        self.assertEqual(start.call_args.kwargs['load_config']['concurrency'], 1)
        self.assertEqual(start.call_args.kwargs['load_config']['iterations_per_vu'], 1)

    def test_k6_validation_cannot_be_bypassed(self):
        scenario = SimpleNamespace(engine='K6', project_id=1, created_by=None, env_config={}, variables=[], get_runtime_config=lambda: {})
        denied = {'passed': False, 'errors': ['unsupported'], 'warnings': []}
        with mock.patch.object(self.executor, 'preflight', return_value=denied), \
                mock.patch.object(self.executor, 'spawn_execution'), \
                mock.patch.object(self.executor, 'create_execution') as create:
            execution, result = self.executor.start_execution(scenario, skip_preflight=True)
        self.assertIsNone(execution)
        create.assert_not_called()

    def test_k6_stop_sets_state_without_killing_parent_process(self):
        execution = mock.Mock(pk=17, status='RUNNING', process_pid=77,
                              load_snapshot={'_engine': 'K6', '_private_snapshot_version': 1})
        with mock.patch.object(self.executor, '_pid_alive', return_value=True), \
                mock.patch.object(self.execution_model, 'objects') as objects, \
                mock.patch.object(self.executor, 'STOP_GRACE_SECONDS', 0), \
                mock.patch.object(self.executor.signal, 'SIGKILL', 9, create=True), \
                mock.patch.object(self.executor.os, 'kill') as kill:
            objects.filter.return_value.update.return_value = 1
            ok, message = self.executor.stop_execution(execution)
        self.assertTrue(ok)
        self.assertEqual(execution.status, 'STOPPING')
        objects.filter.assert_called_once_with(pk=17, status__in=self.execution_model.ACTIVE_STATUSES)
        execution.save.assert_not_called()
        kill.assert_not_called()

    def test_create_freezes_secret_inputs_outside_public_artifacts(self):
        from django.test import override_settings
        from apps.perf_testing.services import k6_execution
        original = {'engine': 'K6', 'load_config': {'concurrency': 1},
                    'steps': [{'name': 'request', 'method': 'GET', 'url': '/endpoint',
                               'headers': {'Authorization': 'test-secret'}}],
                    'env_config': {'headers': {'Authorization': 'test-secret'}},
                    'variables': [], 'csv_data': {}, 'runtime_config': {}, 'sla_config': {}, 'environment_sources': []}
        execution = mock.Mock(id=19)
        execution.load_snapshot = {'_engine': 'K6', '_private_snapshot_version': 1}
        scenario = SimpleNamespace(engine='K6', project=object())
        with tempfile.TemporaryDirectory() as temporary:
            root, media = Path(temporary) / 'private', Path(temporary) / 'public'
            with override_settings(PERF_PRIVATE_ROOT=root, MEDIA_ROOT=media), \
                    mock.patch.object(self.executor, 'build_snapshot', return_value=original), \
                    mock.patch.object(self.execution_model, 'objects') as objects, \
                    mock.patch.object(self.execution_model, 'generate_execution_no', return_value='TEST-19'), \
                    mock.patch.object(self.engines, 'k6_version', return_value='test-v1', create=True), \
                    mock.patch.object(self.executor, 'abs_artifact_dir', return_value=str(media)), \
                    mock.patch.object(self.executor, 'artifact_dir_for', return_value='public'):
                objects.create.return_value = execution
                self.executor.create_execution(scenario)
                public_fields = objects.create.call_args.kwargs
                self.assertNotIn('test-secret', repr(public_fields.get('steps_snapshot')))
                original['env_config']['headers']['Authorization'] = 'changed'
                self.assertTrue((root / 'executions' / '19' / 'snapshot.json').exists())
                frozen = k6_execution.load_snapshot(root, 19)
                self.assertEqual(frozen['env_config']['headers']['Authorization'], 'test-secret')
                self.assertEqual(frozen['k6_version'], 'test-v1')
                self.assertFalse((media / 'snapshot.json').exists())

    def test_start_conflict_is_rejected_before_creating_another_execution(self):
        from django.test import override_settings
        from apps.perf_testing.services.k6_execution import FileLease
        with tempfile.TemporaryDirectory() as temporary, override_settings(PERF_PRIVATE_ROOT=temporary):
            with FileLease(temporary, 'start'), \
                    mock.patch.object(self.executor, 'preflight', return_value={'passed': True, 'errors': []}), \
                    mock.patch.object(self.executor, 'spawn_execution'), \
                    mock.patch.object(self.executor, 'create_execution') as create:
                execution, result = self.executor.start_execution(SimpleNamespace(engine='K6', project_id=1, created_by=None, env_config={}, variables=[], get_runtime_config=lambda: {}))
            self.assertIsNone(execution)
            self.assertFalse(result['passed'])
            self.assertIn('串行', result['errors'][0])
            create.assert_not_called()

    def test_worker_uses_frozen_snapshot_after_scene_engine_changes(self):
        from django.test import override_settings
        from apps.perf_testing.services.k6_execution import save_snapshot
        execution = SimpleNamespace(id=27, load_snapshot={'_engine': 'K6'},
                                    scenario=SimpleNamespace(engine='BUILTIN'))
        with tempfile.TemporaryDirectory() as root, override_settings(PERF_PRIVATE_ROOT=root):
            frozen = {'engine': 'K6', 'csv_data': {'one': {'rows': [{'user': 'original'}]}}}
            save_snapshot(root, 27, frozen)
            with mock.patch.object(self.executor, 'build_snapshot', side_effect=AssertionError('live config read')):
                self.assertEqual(self.executor._execution_snapshot(execution), frozen)

    def test_worker_rejects_changed_k6_binary_version(self):
        self.assertTrue(hasattr(self.executor, '_validate_frozen_k6_version'))
        from apps.perf_testing.services.k6_execution import K6ExecutionError
        with mock.patch.object(self.engines, 'k6_version', return_value='new-version'):
            with self.assertRaisesRegex(K6ExecutionError, '版本'):
                self.executor._validate_frozen_k6_version({'k6_version': 'old-version'})

    def _request_plan(self, rounds=10, duration=60, setup_count=1):
        from django.test import override_settings
        steps = [SimpleNamespace(name=f'login-{i}', is_setup=True, enabled=True,
                                 weight=1, url='/login', files=[]) for i in range(setup_count)]
        steps += [SimpleNamespace(name=f'business-{i}', is_setup=False, enabled=True,
                                  weight=1, url='/business', files=[]) for i in range(2)]
        steps += [SimpleNamespace(name='disabled-business', is_setup=False, enabled=False,
                                  weight=1, url='/disabled', files=[])]

        class StepManager:
            def filter(self, **kwargs):
                return [step for step in steps if step.enabled == kwargs.get('enabled', True)]

        config = {'model': 'CONCURRENCY', 'concurrency': 10, 'duration': duration,
                  'ramp_up': 0, 'iterations_per_vu': rounds}
        scenario = SimpleNamespace(id=1, project_id=1, engine='K6', steps=StepManager(),
                                   env_config={'base_url': 'http://target.example'}, variables=[], created_by=None, get_runtime_config=lambda: {},
                                   has_active_execution=lambda: False, get_load_config=lambda: config)
        with tempfile.TemporaryDirectory() as root, override_settings(PERF_PRIVATE_ROOT=root), \
                mock.patch.object(self.execution_model, 'objects') as objects, \
                mock.patch.object(self.engines, 'k6_available', return_value=True), \
                mock.patch.object(self.engines, 'validate_k6_snapshot', return_value=[]), \
                mock.patch.object(self.executor, 'build_snapshot', return_value={}):
            objects.filter.return_value.count.return_value = 0
            result = self.executor.preflight(scenario)
        self.assertTrue(result['passed'], result['errors'])
        return result['estimated']

    def test_fixed_rounds_plan_counts_business_and_login_separately(self):
        plan = self._request_plan(rounds=10)
        self.assertEqual(plan['estimated_requests'], 200)
        self.assertEqual(plan['estimated_setup_requests'], 10)
        self.assertEqual(plan['estimated_http_requests'], 210)
        self.assertEqual(plan['estimate_kind'], 'fixed_iterations')
        self.assertIn('提前停止', plan['request_estimate_note'])

    def test_fixed_rounds_request_plan_does_not_grow_with_maximum_duration(self):
        short = self._request_plan(rounds=3, duration=60, setup_count=2)
        long = self._request_plan(rounds=3, duration=300, setup_count=2)
        self.assertEqual(short['estimated_requests'], 60)
        self.assertEqual(long['estimated_requests'], 60)
        self.assertEqual(long['estimated_setup_requests'], 20)
        self.assertEqual(long['estimated_http_requests'], 80)

    def test_duration_mode_marks_request_count_as_heuristic(self):
        plan = self._request_plan(rounds=0, duration=60)
        self.assertEqual(plan['estimated_requests'], 600)
        self.assertEqual(plan.get('estimate_kind'), 'time_heuristic')
        self.assertIn('粗估', plan['request_estimate_note'])
        self.assertIsNone(plan['estimated_http_requests'])


if __name__ == '__main__':
    unittest.main()
