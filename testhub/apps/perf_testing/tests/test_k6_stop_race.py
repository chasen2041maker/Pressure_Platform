"""Real database interleavings: a stale stop request cannot overwrite a terminal run."""
import threading
import io
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone

from apps.perf_testing.models import PerfExecution, PerfProject, PerfScenario
from apps.perf_testing.services import executor


class K6StopRaceTests(TransactionTestCase):
    def setUp(self):
        user = get_user_model().objects.create(username='k6-stop-race-owner')
        project = PerfProject.objects.create(name='stop-race-test', owner=user)
        self.scenario = PerfScenario.objects.create(project=project, name='stop-race',
                                                    engine='K6', created_by=user)

    def make_execution(self, number, status='RUNNING'):
        return PerfExecution.objects.create(scenario=self.scenario, project=self.scenario.project,
            execution_no=f'STOP-RACE-{number}', status=status, process_pid=987654,
            load_snapshot={'_engine': 'K6'}, summary={})

    def test_worker_finishes_after_stop_request_reads_running(self):
        for final_status in PerfExecution.FINAL_STATUSES:
            with self.subTest(final_status=final_status):
                execution = self.make_execution(final_status)
                # The view already loaded RUNNING before the worker commits its result.
                stale_request = PerfExecution.objects.get(pk=execution.pk)
                self.assertEqual(stale_request.status, 'RUNNING')
                done = threading.Event()
                errors = []
                finished_at = timezone.now()

                def finish_worker():
                    try:
                        PerfExecution.objects.filter(pk=execution.pk).update(
                            status=final_status, end_time=finished_at,
                            summary={'business_total': 210})
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        connections['default'].close()
                        done.set()

                worker = threading.Thread(target=finish_worker)
                worker.start()
                self.assertTrue(done.wait(5), 'Independent worker update must complete')
                worker.join(5)
                self.assertEqual(errors, [])
                with mock.patch.object(executor.os, 'kill') as kill:
                    ok, message = executor.stop_execution(stale_request)
                execution.refresh_from_db()
                self.assertEqual(execution.status, final_status,
                                 'A completed run must never return to STOPPING')
                self.assertEqual(stale_request.status, final_status)
                self.assertEqual(execution.summary, {'business_total': 210})
                self.assertEqual(execution.end_time, finished_at)
                self.assertFalse(ok)
                self.assertEqual(message, '执行已结束，无需停止')
                kill.assert_not_called()

    def test_active_k6_stop_is_state_only_and_keeps_other_executions_untouched(self):
        other = self.make_execution('OTHER', status='COMPLETED')
        for initial in PerfExecution.ACTIVE_STATUSES:
            with self.subTest(initial=initial):
                execution = self.make_execution(initial, status=initial)
                with mock.patch.object(executor.os, 'kill') as kill:
                    ok, message = executor.stop_execution(execution)
                execution.refresh_from_db()
                self.assertTrue(ok)
                self.assertEqual(execution.status, 'STOPPING')
                self.assertIn('等待 K6 退出', message)
                kill.assert_not_called()
        other.refresh_from_db()
        self.assertEqual(other.status, 'COMPLETED')

    def test_multicore_k6_cpu_does_not_emit_legacy_overload_warning(self):
        for engine_name, cpu in (('K6', 311.9), ('K6', 4.7), ('BUILTIN', 311.9)):
            with self.subTest(engine=engine_name, cpu=cpu):
                execution = self.make_execution(f'CPU-{engine_name}-{cpu}', status='PREPARING')
                if engine_name != 'K6':
                    execution.load_snapshot = {}; execution.save(update_fields=['load_snapshot'])
                class FakeEngine:
                    def __init__(self, snapshot, on_sample, **kwargs): self.on_sample = on_sample
                    def prepare(self): pass
                    def stop(self): pass
                    def run(self):
                        self.on_sample({'ts_offset': 1, 'cpu_percent': cpu, 'cpu_sampled': True})
                    def collect(self):
                        return {'summary': {'total_requests': 1, 'peak_load_gen_cpu': cpu}, 'duration': 1}
                snapshot = {'engine': engine_name, 'load_config': {}, 'runtime_config': {}}
                output = io.StringIO()
                with tempfile.TemporaryDirectory() as work, \
                        mock.patch.object(executor, 'abs_artifact_dir', return_value=work), \
                        mock.patch.object(executor, '_execution_snapshot', return_value=snapshot), \
                        mock.patch.object(executor, '_validate_frozen_k6_version'), \
                        mock.patch.object(executor, 'push_update'), \
                        mock.patch.object(executor, 'flush_push_updates'), \
                        mock.patch.object(executor.signal, 'signal'), \
                        mock.patch('apps.perf_testing.engines.get_engine_class', return_value=FakeEngine), \
                        mock.patch('apps.perf_testing.services.reporter.generate_report', return_value=''):
                    executor._run_execution(execution.id, stdout=output)
                execution.refresh_from_db()
                self.assertEqual(execution.status, 'COMPLETED', output.getvalue())
                if engine_name == 'K6':
                    self.assertNotIn('报告数据可信度下降', output.getvalue())
                    self.assertNotIn('压力机 CPU 达', output.getvalue())
                    self.assertEqual(execution.summary['peak_load_gen_cpu'], cpu)
                    self.assertIsNone(execution.summary['data_trustworthy'])
                    self.assertFalse(execution.summary['load_generator_capacity_verified'])
                else:
                    self.assertIn('报告数据可信度下降', output.getvalue())
