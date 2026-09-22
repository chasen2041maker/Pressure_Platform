"""SQLite/ORM worker tests with a fake engine; never send HTTP traffic."""
import io
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings

from apps.perf_testing.models import PerfProject, PerfScenario, PerfScenarioStep, PerfExecution
from apps.perf_testing.services import executor


class K6WorkerTests(TransactionTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.override = override_settings(PERF_PRIVATE_ROOT=root / 'private', MEDIA_ROOT=root / 'media')
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = get_user_model().objects.create_user(username='k6-worker-test')
        self.project = PerfProject.objects.create(name='worker', owner=self.user)
        self.scenario = PerfScenario.objects.create(
            project=self.project, created_by=self.user, name='worker-test', engine='K6',
            env_config={'base_url': 'http://127.0.0.1:1', 'headers': {'Authorization': 'test-secret'}},
            load_config={'model': 'CONCURRENCY', 'concurrency': 1, 'duration': 3, 'iterations_per_vu': 1})
        PerfScenarioStep.objects.create(scenario=self.scenario, name='business', method='GET', url='/test')
        self.patches = [
            mock.patch('apps.perf_testing.engines.k6_version', return_value='test-version'),
            mock.patch.object(executor, 'push_update'),
            mock.patch.object(executor, 'flush_push_updates'),
            mock.patch.object(executor, '_notify_if_needed'),
            mock.patch('apps.perf_testing.services.reporter.generate_report', return_value=''),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_run_uses_frozen_engine_environment_and_preserves_partial_failure(self):
        execution = executor.create_execution(self.scenario, user=self.user)
        self.scenario.engine = 'BUILTIN'
        self.scenario.env_config = {'base_url': 'http://changed.invalid'}
        self.scenario.save()
        captured = {}

        class Engine:
            def __init__(self, snapshot, **kwargs):
                captured.update(snapshot)
            def prepare(self):
                pass
            def run(self):
                raise RuntimeError('must-not-expose-credential')
            def stop(self):
                pass
            def collect(self):
                return {'summary': {'total_requests': 7, 'tps': 7, 'error_rate': 0}, 'duration': 1}

        with mock.patch('apps.perf_testing.engines.get_engine_class', return_value=Engine):
            result = executor.run_execution(execution.id)
        self.assertEqual(captured['engine'], 'K6')
        self.assertEqual(captured['env_config']['base_url'], 'http://127.0.0.1:1')
        self.assertEqual(result.status, 'FAILED')
        self.assertEqual(result.summary['total_requests'], 7)
        self.assertIsNone(result.summary['peak_load_gen_cpu'])
        self.assertIsNone(result.summary['data_trustworthy'])
        self.assertFalse(result.summary['load_generator_capacity_verified'])
        self.assertNotIn('must-not-expose-credential', result.error_message)

    def test_stop_request_reaches_running_engine_and_persists_stopped_results(self):
        execution = executor.create_execution(self.scenario, user=self.user)
        running, stopped = threading.Event(), threading.Event()
        thread_errors = []

        class Engine:
            def __init__(self, snapshot, **kwargs):
                pass
            def prepare(self):
                pass
            def run(self):
                running.set()
                if not stopped.wait(5):
                    raise RuntimeError('stop did not reach engine')
            def stop(self):
                stopped.set()
            def collect(self):
                return {'summary': {'total_requests': 3, 'tps': 3, 'error_rate': 0}, 'duration': 1}

        def request_stop():
            try:
                if not running.wait(5):
                    raise RuntimeError('engine did not start')
                close_old_connections()
                current = PerfExecution.objects.get(id=execution.id)
                executor.stop_execution(current)
            except Exception as exc:
                thread_errors.append(exc)
            finally:
                close_old_connections()

        thread = threading.Thread(target=request_stop)
        thread.start()
        with mock.patch('apps.perf_testing.engines.get_engine_class', return_value=Engine):
            result = executor.run_execution(execution.id)
        thread.join(timeout=10)
        self.assertEqual(thread_errors, [])
        self.assertTrue(stopped.is_set())
        self.assertEqual(result.status, 'STOPPED')
        self.assertEqual(result.summary['total_requests'], 3)

    def test_second_task_with_shared_pool_is_refused_before_spawn(self):
        first = executor.create_execution(self.scenario, user=self.user)
        other = PerfScenario.objects.create(project=self.project, created_by=self.user,
                                            name='another', engine='K6')
        with mock.patch.object(executor, 'preflight', return_value={'passed': True, 'errors': []}), \
                mock.patch.object(executor, 'spawn_execution') as spawn:
            result, check = executor.start_execution(other)
        self.assertIsNone(result)
        self.assertIn('账号池冲突', check['errors'][0])
        spawn.assert_not_called()
        self.assertEqual(PerfExecution.objects.count(), 1)

    def test_engine_failure_during_stop_is_not_reported_as_clean_stop(self):
        execution = executor.create_execution(self.scenario, user=self.user)

        class Engine:
            def __init__(self, snapshot, **kwargs):
                pass
            def prepare(self):
                pass
            def run(self):
                PerfExecution.objects.filter(id=execution.id).update(status='STOPPING')
                raise RuntimeError('unexpected engine failure while stopping')
            def stop(self):
                pass
            def collect(self):
                return {'summary': {'total_requests': 2}}

        with mock.patch('apps.perf_testing.engines.get_engine_class', return_value=Engine):
            result = executor.run_execution(execution.id)
        self.assertEqual(result.status, 'FAILED')
        self.assertEqual(result.summary['total_requests'], 2)

    def test_completion_log_distinguishes_websocket_steps_from_http_requests(self):
        for websocket in (False, True):
            with self.subTest(websocket=websocket):
                execution = executor.create_execution(self.scenario, user=self.user)
                summary = {'total_requests': 2, 'tps': 2, 'error_rate': 0}
                if websocket:
                    summary['websocket'] = {'version': 1, 'sessions': {'completed': 1}}

                class Engine:
                    def __init__(self, snapshot, **kwargs):
                        pass
                    def prepare(self):
                        pass
                    def run(self):
                        pass
                    def stop(self):
                        pass
                    def collect(self):
                        return {'summary': summary, 'duration': 1}

                output = io.StringIO()
                with mock.patch('apps.perf_testing.engines.get_engine_class', return_value=Engine):
                    executor.run_execution(execution.id, stdout=output)
                line = next(line for line in output.getvalue().splitlines() if '压测结束：' in line)
                self.assertIn('共 2 次业务步骤，业务步骤/秒 2' if websocket else
                              '共 2 次业务请求，业务 RPS 2', line)
                if websocket:
                    self.assertNotIn('业务 RPS', line)
                    self.assertNotIn('业务请求', line)
