"""Native VU source, observation boundaries and owned collector contracts."""
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


def document(vus=2, **extra):
    attrs = dict(vus=vus, **{'vus-max': 2}, status=7, running=True,
                 paused=False, stopped=False, tainted=False)
    attrs.update(extra)
    return json.dumps({'data': {'type': 'status', 'id': 'default', 'attributes': attrs}}).encode()


class NativeVUContracts(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('apps.perf_testing.services.k6_native_vu'),
                             'Native observations require an independent source protocol')
        from apps.perf_testing.services import k6_native_vu
        return k6_native_vu

    def point(self, seq=1, start=1000, end=1100, vus=2, **extra):
        m = self.module()
        row = dict(version=m.VERSION, source=m.SOURCE, execution_id=37,
                   runner_instance='a' * 32, observation_seq=seq,
                   scheduled_offset_ms=start, request_start_offset_ms=start,
                   request_end_offset_ms=end, received_at_utc_ms=1700000000000 + end,
                   timestamp_kind='collector_request_interval', rtt_ms=end-start,
                   result='ok', active_vus=vus, initialized_vus=2, status=7,
                   running=True, paused=False, stopped=False)
        row.update(extra)
        return row

    def test_rest_source_has_real_zero_and_rejects_invalid_numeric_values(self):
        m = self.module()
        self.assertEqual(m.parse_status(document(0))['active_vus'], 0)
        self.assertEqual(m.parse_status(document())['active_vus'], 2)
        for value in (None, True, -1, 1.5, '2', float('nan'), 2**54):
            with self.subTest(value=value), self.assertRaises(m.NativeVUError):
                m.parse_status(document(value))
        for body in (b'{}', b'not json', b'x' * 16385):
            with self.assertRaises(m.NativeVUError): m.parse_status(body)

    def test_protocol_whitelist_is_idempotent_and_does_not_fabricate_zeros(self):
        m = self.module()
        point = self.point(active_vus=0, headers={'Authorization': 'PRIVATE'}, url='PRIVATE')
        clean = m.sanitize_observation(point)
        self.assertEqual(clean['active_vus'], 0)
        self.assertEqual(m.sanitize_observation(clean), clean)
        self.assertNotIn('PRIVATE', json.dumps(clean))
        missing = self.point(result='timeout', active_vus=None)
        self.assertIsNone(m.sanitize_observation(missing)['active_vus'])
        self.assertEqual(m.sanitize_observation(self.point(active_vus=True)), {})

    def test_span_uses_request_boundaries_not_configured_duration(self):
        m = self.module()
        points = [self.point(), self.point(2, 2000, 2100), self.point(3, 3000, 3100)]
        result = m.summarize(points, expected_vus=2, configured_duration=600)
        self.assertEqual(result['configured_duration_seconds'], 600)
        self.assertEqual(result['longest_target_span_lower_ms'], 1900)
        self.assertEqual(result['longest_target_span_upper_ms'], 2100)
        self.assertEqual(result['valid_count'], 3)
        self.assertIsNone(result['sustained_concurrency_verified'])
        self.assertEqual(m.summarize(points[:1], expected_vus=2)['longest_target_span_lower_ms'], 0)

    def test_missing_late_pause_and_replacement_break_target_segments(self):
        m = self.module()
        for middle in (self.point(2,2000,2100,result='timeout',active_vus=None),
                       self.point(2,2000,2900), self.point(2,2000,2100,paused=True),
                       self.point(2,2000,2100,runner_instance='b'*32)):
            result=m.summarize([self.point(),middle,self.point(3,3000,3100)],expected_vus=2)
            self.assertEqual(result['longest_target_span_lower_ms'],0)
        result=m.summarize([self.point(),self.point(2,4000,4100)],expected_vus=2)
        self.assertEqual(result['longest_target_span_lower_ms'],0)
        result=m.summarize([self.point(),self.point(2,2000,2100,vus=1)],expected_vus=2)
        self.assertEqual(result['below_target_count'],1)

    def test_collector_singleflight_and_bounded_stop_drops_late_response(self):
        m = self.module(); entered=threading.Event(); exited=threading.Event()
        def transport(cancel):
            entered.set(); cancel.wait(2); exited.set(); return document()
        collector=m.NativeVUCollector(transport,execution_id=37,runner_instance='a'*32,
                                      expected_vus=2,origin=time.monotonic())
        collector.start(); self.assertTrue(entered.wait(1))
        started=time.monotonic(); self.assertTrue(collector.stop())
        self.assertLess(time.monotonic()-started,1)
        self.assertTrue(exited.is_set())
        self.assertEqual(collector.drain(),[])
        self.assertEqual(collector.summary()['late_discarded_count'],1)

    def test_late_valid_counts_remain_observed_but_never_extend_coverage(self):
        m = self.module()
        points = [self.point(), self.point(2, 2000, 2900, vus=1),
                  self.point(3, 3000, 3900), self.point(4, 4000, 4100, vus=1)]
        result = m.summarize(points, expected_vus=2)
        self.assertEqual(result['valid_count'], 4)
        self.assertEqual(result['late_count'], 2)
        self.assertEqual(result['below_target_count'], 2)
        self.assertEqual(result['target_count'], 2)
        self.assertEqual(result['target_segment_count'], 1)
        self.assertEqual(result['longest_target_span_lower_ms'], 0)
        self.assertEqual(result['longest_target_span_upper_ms'], 0)
        uncertain = m.summarize([self.point(end=1900, vus=1, paused=True)], expected_vus=2)
        self.assertEqual(uncertain['late_count'], 1)
        self.assertEqual(uncertain['state_uncertain_count'], 1)
        self.assertEqual(uncertain['below_target_count'], 0)

    def test_collector_errors_preserve_missing_and_shutdown_does_not_make_zero(self):
        m=self.module()
        with mock.patch.object(m.time,'monotonic',side_effect=[10,10.1]):
            collector=m.NativeVUCollector(lambda _: (_ for _ in ()).throw(m.NativeVUError('timeout')),
                                          execution_id=37,runner_instance='a'*32,expected_vus=2,origin=9)
            collector.observe_once(10)
        rows=collector.drain()
        self.assertEqual(rows[0]['result'],'timeout'); self.assertIsNone(rows[0]['active_vus'])
        collector.stop(); self.assertEqual(collector.drain(),[])

    def test_sample_persistence_preserves_all_native_points_and_old_unknown(self):
        m=self.module()
        from apps.perf_testing.services.k6_samples import sample_payload,sanitized_payload,project_sample
        payload=sample_payload(dict(sample_seq=1,elapsed_seconds=1,native_vu_observations=[self.point()]))
        self.assertEqual(payload['native_vu_observations'][0]['active_vus'],2)
        self.assertEqual(sanitized_payload(payload),payload)
        self.assertEqual(project_sample({'active_users':9})['native_vu_observations'],[])

    def test_entrypoint_enables_only_container_loopback_and_keeps_watchdog(self):
        self.module()
        root=Path(__file__).resolve().parents[1]
        text=(root/'engines/k6_container_entrypoint.sh').read_text(encoding='utf-8')
        self.assertIn('--address=127.0.0.1:6565',text)
        self.assertNotIn('0.0.0.0',text)
        self.assertIn('kill -KILL "$child"',text)

    def test_buffer_overflow_is_visible_and_stops_collection(self):
        m=self.module()
        collector=m.NativeVUCollector(lambda _:document(),execution_id=37,runner_instance='a'*32,
                                      expected_vus=2,origin=time.monotonic())
        for _ in range(m.MAX_OBSERVATIONS+1): collector.observe_once(time.monotonic())
        self.assertTrue(collector.cancel.is_set())
        self.assertEqual(len(collector.drain()),m.MAX_OBSERVATIONS)
        self.assertEqual(collector.summary()['overflow_count'],1)
        self.assertEqual(collector.summary()['collector_failure'],'buffer_overflow')

    def test_executor_state_and_counts_are_not_atomic_or_http_concurrency(self):
        m=self.module()
        for row in (self.point(status=6),self.point(status=99),self.point(vus=3),
                    self.point(stopped=True),self.point(running=False)):
            result=m.summarize([row],expected_vus=2)
            self.assertEqual(result['valid_count'],1)
            self.assertEqual(result['target_count'],0)

    def test_scheduler_is_singleflight_records_skipped_slots_without_catchup(self):
        m=self.module(); collector=None; calls=[]
        def sample(deadline):
            calls.append(deadline)
            if len(calls)==2: collector.cancel.set()
        collector=m.NativeVUCollector(lambda _:document(),execution_id=37,runner_instance='a'*32,
                                      expected_vus=2,origin=0)
        with mock.patch.object(collector,'observe_once',side_effect=sample), \
             mock.patch.object(m.time,'monotonic',side_effect=[0,0,3.2,4,4.1]):
            collector._run()
        self.assertEqual(calls,[0,4])
        self.assertEqual(collector.summary()['missed_deadline_count'],3)

    def test_clock_order_and_duplicate_sequence_never_extend_a_span(self):
        m=self.module()
        self.assertEqual(m.sanitize_observation(self.point(start=1100,end=1000)),{})
        result=m.summarize([self.point(),self.point(seq=1,start=2000,end=2100)],expected_vus=2)
        self.assertEqual(result['longest_target_span_lower_ms'],0)
        self.assertEqual(result['sequence_gap_count'],1)

    def test_repeated_stop_preserves_first_observation_end_boundary(self):
        m=self.module()
        collector=m.NativeVUCollector(lambda _:document(),execution_id=37,runner_instance='a'*32,
                                      expected_vus=2,origin=9)
        with mock.patch.object(m.time,'monotonic',side_effect=[10,10.1]): collector.observe_once(10)
        with mock.patch.object(m.time,'monotonic',return_value=11): collector.stop()
        before=collector.summary()['unobserved_tail_ms']
        with mock.patch.object(m.time,'monotonic',return_value=12): collector.stop()
        self.assertEqual(collector.summary()['unobserved_tail_ms'],before)


class NativeDockerContracts(unittest.TestCase):
    def setUp(self):
        self.job_patch=mock.patch('apps.perf_testing.engines.k6_docker.WindowsJob',create=True)
        self.job=self.job_patch.start(); self.addCleanup(self.job_patch.stop)
        self.job.return_value.creation_flags=0

    def guard(self, directory):
        from apps.perf_testing.engines.k6_docker import DockerGuard
        return DockerGuard(dict(docker='docker',binary='binary',image_id='image',network='fixture',
            network_id='network',cpus='2',memory='256m',binary_sha256='hash',binary_version='fixed'),directory,37)

    def test_prepare_pins_full_container_id_and_both_labels(self):
        from apps.perf_testing.engines import k6_docker
        with tempfile.TemporaryDirectory() as directory:
            guard=self.guard(directory); ident='c'*64
            state={'Id':ident,'Config':{'Labels':{k6_docker.OWNER_LABEL:guard.owner,
                    k6_docker.EXECUTION_LABEL:'37'}},'State':{'Running':False}}
            with mock.patch.object(k6_docker,'_run',return_value=subprocess.CompletedProcess([],0,ident,'')), \
                 mock.patch.object(guard,'_inspect',return_value=state), mock.patch.object(threading.Thread,'start'):
                guard.prepare_container()
            self.assertEqual(getattr(guard,'container_id',None),ident)
            state['Config']['Labels'][k6_docker.EXECUTION_LABEL]='38'
            with mock.patch.object(guard,'_inspect',return_value=state), self.assertRaises(k6_docker.DockerRunnerError):
                guard._owned()

    def test_status_transport_has_fixed_target_capped_output_and_no_shell(self):
        from apps.perf_testing.engines import k6_docker
        with tempfile.TemporaryDirectory() as directory:
            guard=self.guard(directory)
            self.assertTrue(callable(getattr(guard,'read_native_status',None)), 'Owned native transport is missing')
            guard.container_id='c'*64; guard._native_identity_verified=True
            process=mock.Mock(stdout=io.BytesIO(document()),returncode=0)
            process.poll.return_value=0
            with mock.patch.object(k6_docker.subprocess,'Popen',return_value=process) as popen:
                self.assertEqual(guard.read_native_status(threading.Event()),document())
            args,kwargs=popen.call_args
            self.assertEqual(args[0][0:3],['docker','exec','c'*64])
            self.assertEqual(args[0][-1],'http://127.0.0.1:6565/v1/status')
            self.assertIn('off',args[0]); self.assertFalse(kwargs.get('shell',False))
            self.assertEqual(kwargs['stderr'],subprocess.DEVNULL)
            if os.name == 'nt':
                self.job.return_value.attach_and_resume.assert_called_once_with(process)
                self.job.return_value.close.assert_called_once()

    def test_status_transport_ownership_unverified_never_executes(self):
        from apps.perf_testing.engines import k6_docker
        from apps.perf_testing.services.k6_native_vu import NativeVUError
        with tempfile.TemporaryDirectory() as directory:
            guard=self.guard(directory)
            self.assertTrue(callable(getattr(guard,'read_native_status',None)), 'Owned native transport is missing')
            with mock.patch.object(k6_docker.subprocess,'Popen') as popen, self.assertRaises(NativeVUError):
                guard.read_native_status(threading.Event())
            popen.assert_not_called()

    def test_transport_oversize_timeout_and_cancel_kill_local_helper(self):
        from apps.perf_testing.engines import k6_docker
        from apps.perf_testing.services.k6_native_vu import NativeVUError
        with tempfile.TemporaryDirectory() as directory:
            guard=self.guard(directory); guard.container_id='c'*64; guard._native_identity_verified=True
            for mode,expected in (('oversize','oversize'),('timeout','timeout'),('cancel','transport_error')):
                process=mock.Mock(stdout=io.BytesIO(b'x'*16385 if mode=='oversize' else b''))
                process.poll.return_value=None
                cancel=threading.Event()
                if mode=='cancel': cancel.set()
                ticks=[0,3] if mode=='timeout' else [0,0]
                with mock.patch.object(k6_docker.subprocess,'Popen',return_value=process), \
                     mock.patch.object(k6_docker.time,'monotonic',side_effect=ticks), \
                     self.assertRaises(NativeVUError) as error:
                    guard.read_native_status(cancel)
                self.assertEqual(error.exception.category,expected)
                process.kill.assert_called_once()
                process.wait.assert_called_once()

    def test_engine_stop_cancels_collector_before_container_cleanup(self):
        from apps.perf_testing.engines.k6_engine import K6Engine
        from .test_k6_engine import snapshot
        engine=K6Engine(snapshot()); order=[]
        engine._native_vu=mock.Mock(); engine._docker_guard=mock.Mock()
        engine._native_vu.stop.side_effect=lambda:(order.append('native'),True)[1]
        engine._docker_guard.close.side_effect=lambda:order.append('container')
        engine.stop(); self.assertEqual(order,['native','container'])

    def test_failed_collector_stop_still_cleans_runner_and_reports_failure(self):
        from apps.perf_testing.engines.k6_engine import K6Engine,EngineError
        from .test_k6_engine import snapshot
        engine=K6Engine(snapshot()); engine._native_vu=mock.Mock(); engine._docker_guard=mock.Mock()
        engine._native_vu.stop.return_value=False
        with self.assertRaises(EngineError): engine.stop()
        engine._docker_guard.close.assert_called_once()

    def test_engine_emits_native_points_without_overwriting_script_users(self):
        from apps.perf_testing.engines.k6_engine import K6Engine
        from apps.perf_testing.services.k6_native_vu import NativeVUCollector
        from .test_k6_engine import snapshot
        engine=K6Engine(snapshot())
        collector=NativeVUCollector(lambda _:document(),execution_id=37,runner_instance='a'*32,
                                    expected_vus=2,origin=time.monotonic()-1)
        collector.observe_once(time.monotonic())
        engine._native_vu=collector; engine._start_ts=time.monotonic()-1
        engine._active_vus={1,2,3}; samples=[]; engine.on_sample=samples.append
        engine._emit_sample(force=True)
        self.assertEqual(samples[0].get('native_vu_observations',[{}])[0].get('active_vus'),2)
        self.assertEqual(samples[0]['active_users'],3)
        self.assertIn('native_vu',engine.collect()['summary'])

    def run_eof_case(self, *, failure=None, failure_on_stop=False, exit_code=0):
        from apps.perf_testing.engines import k6_engine
        from apps.perf_testing.services.k6_native_vu import NativeVUCollector
        from .test_k6_engine import snapshot
        with tempfile.TemporaryDirectory() as directory:
            samples = []
            engine = k6_engine.K6Engine(snapshot(rounds=0), work_dir=directory, on_sample=samples.append)
            engine.collector.total = engine.collector.success = 1
            guard = mock.Mock(execution_id=37, owner='a' * 32)
            guard.sample.return_value = None
            guard.prepare_container.return_value = ['mock-docker-start']
            engine._docker_guard = guard
            collector = NativeVUCollector(lambda _: document(), execution_id=37,
                                          runner_instance='a' * 32, expected_vus=2, origin=time.monotonic())
            collector.observe_once(time.monotonic())
            if not failure_on_stop:
                collector._failure = failure or ''
            original_stop = collector.stop
            def stop():
                if failure_on_stop:
                    collector._failure = failure or ''
                return original_stop()
            process = mock.Mock(stdout=io.StringIO(''))
            process.poll.return_value = process.wait.return_value = exit_code
            caught = None
            with mock.patch.object(k6_engine, 'NativeVUCollector', return_value=collector), \
                 mock.patch.object(collector, 'start'), mock.patch.object(collector, 'stop', side_effect=stop), \
                 mock.patch.object(k6_engine, 'WindowsJob') as job, \
                 mock.patch.object(k6_engine.subprocess, 'Popen', return_value=process):
                job.return_value.creation_flags = 0
                try:
                    engine.run()
                except k6_engine.EngineError as exc:
                    caught = str(exc)
            guard.close.assert_called_once()
            self.assertTrue(process.stdout.closed)
            self.assertEqual(len(samples), 1)
            self.assertTrue(samples[0]['engine_finished'])
            self.assertEqual(len(samples[0]['native_vu_observations']), 1)
            self.assertEqual(collector.drain(), [])
            return caught

    def test_eof_rejects_collector_integrity_failure_after_final_sample_and_cleanup(self):
        for failure in ('buffer_overflow', 'collector_failed', 'ownership_mismatch'):
            with self.subTest(failure=failure):
                self.assertIn('采集完整性失败', self.run_eof_case(failure=failure) or '')

    def test_collector_failure_arriving_during_final_stop_cannot_complete(self):
        self.assertIn('采集完整性失败', self.run_eof_case(
            failure='buffer_overflow', failure_on_stop=True) or '')

    def test_clean_eof_completes_and_preserves_final_native_sample(self):
        self.assertIsNone(self.run_eof_case())

    def test_final_collector_failure_does_not_mask_prior_runner_failure(self):
        self.assertIn('退出码 9', self.run_eof_case(
            failure='buffer_overflow', failure_on_stop=True, exit_code=9) or '')


if __name__ == '__main__': unittest.main()
