"""Native VU observations round-trip through the real isolated worker and API."""
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.perf_testing.engines.k6_engine import K6Engine
from apps.perf_testing.models import PerfProject, PerfScenario, PerfScenarioStep
from apps.perf_testing.services import executor
from apps.perf_testing.services.k6_native_vu import NativeVUCollector, NativeVUError
from .test_k6_native_vu import document


class NativePersistenceTests(TransactionTestCase):
    def test_worker_persists_zero_gap_and_late_final_native_point_without_rewriting_script_users(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            owner=get_user_model().objects.create_user(username='native-observer')
            project=PerfProject.objects.create(name='native-observer',owner=owner)
            scene=PerfScenario.objects.create(project=project,created_by=owner,engine='K6',name='native',
                env_config={'base_url':'http://unused.invalid'},
                load_config={'model':'CONCURRENCY','concurrency':2,'duration':30},sla_config={})
            PerfScenarioStep.objects.create(scenario=scene,name='business',method='GET',url='/a')
            client=APIClient(); client.force_authenticate(owner)
            captured=[]
            class EventEngine(K6Engine):
                def prepare(self): self._open_raw_writer()
                def run(self):
                    self._start_ts=100
                    self._native_vu=NativeVUCollector(lambda _:document(0),execution_id=self.snapshot['execution_id'],
                        runner_instance='a'*32,expected_vus=2,origin=100,configured_duration=30)
                    self._active_vus={1,2,3}
                    with mock.patch('apps.perf_testing.services.k6_native_vu.time.monotonic',side_effect=[101,101.1]):
                        self._native_vu.observe_once(101)
                    self._consume_event({'kind':'request_started','step':0,'vu':1})
                    self._consume_event({'kind':'request','step':0,'vu':1,'timestamp_ms':1700000000100,
                                         'elapsed_ms':10,'status':200,'ok':True})
                    with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic',return_value=101.2):
                        self._emit_sample(force=True)
                    captured.append(client.get(f'/api/perf-testing/executions/{run.id}/realtime/',{'after_id':0}).data)
                    self._native_vu.transport=lambda _:(_ for _ in ()).throw(NativeVUError('timeout'))
                    with mock.patch('apps.perf_testing.services.k6_native_vu.time.monotonic',side_effect=[102,102.2]):
                        self._native_vu.observe_once(102)
                    self._native_vu.transport=lambda _:document(2)
                    with mock.patch('apps.perf_testing.services.k6_native_vu.time.monotonic',side_effect=[103,103.1]):
                        self._native_vu.observe_once(103)
                    with mock.patch('apps.perf_testing.services.k6_native_vu.time.monotonic',return_value=103.2):
                        self._native_vu.stop(); self._end_ts=103.2; self._active_vus.clear()
                        self._emit_sample(force=True)
                    self._close_raw_writer()
            with override_settings(PERF_PRIVATE_ROOT=root/'private',MEDIA_ROOT=root/'media'), \
                 mock.patch('apps.perf_testing.engines.k6_version',return_value='test-fixed'), \
                 mock.patch.object(executor,'push_update'),mock.patch.object(executor,'flush_push_updates'), \
                 mock.patch.object(executor,'_notify_if_needed'), \
                 mock.patch('apps.perf_testing.engines.get_engine_class',return_value=EventEngine):
                run=executor.create_execution(scene,user=owner)
                result=executor.run_execution(run.id)
            self.assertEqual(result.status,'COMPLETED')
            active=captured[0]['samples'][0]
            self.assertEqual(active['active_users'],3)
            self.assertEqual(active['native_vu_observations'][0]['active_vus'],0)
            api=client.get(f'/api/perf-testing/executions/{run.id}/realtime/',{'after_id':0}).data
            rows=[point for sample in api['samples'] for point in sample['native_vu_observations']]
            self.assertEqual([p['observation_seq'] for p in rows],[1,2,3])
            self.assertEqual([p['active_vus'] for p in rows],[0,None,2])
            self.assertEqual([p['result'] for p in rows],['ok','timeout','ok'])
            self.assertEqual([p['request_end_offset_ms'] for p in rows],[1100,2200,3100])
            self.assertTrue(result.summary['sample_persistence']['complete'])
            self.assertEqual(result.summary['native_vu']['attempt_count'],len(rows))
            self.assertEqual(result.summary['native_vu']['longest_target_span_lower_ms'],0)
            self.assertIsNone(result.summary['native_vu']['sustained_concurrency_verified'])
            self.assertEqual(result.summary['business_total'],1)
