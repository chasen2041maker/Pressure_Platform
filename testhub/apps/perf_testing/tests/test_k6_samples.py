"""Real worker persistence and authenticated HTTP polling in isolated test databases."""
import importlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.perf_testing.models import PerfProject, PerfScenario, PerfScenarioStep, PerfExecution, PerfMetricSample
from apps.perf_testing.engines.k6_engine import K6Engine
from apps.perf_testing.services import executor, reporter
from apps.perf_testing.services.k6_samples import sample_payload, sanitized_payload, MAX_PAYLOAD_BYTES
from apps.perf_testing.services.k6_throughput import CompletionBuckets


def observation(seq=1, bucket=1100, total=3):
    bins = CompletionBuckets()
    for _ in range(total): bins.record(bucket)
    return dict(sample_seq=seq, elapsed_seconds=1 + seq / 10, window_count=total,
                tps=total, total_requests=total, business_total=total, http_total=total+1,
                http_started=total+2, business_started=total+1, http_incomplete=1,
                business_incomplete=1, failed_requests=1, completed_iterations=2,
                avg_rt=12, p95_rt=15, error_rate=1/total*100, throughput=bins.snapshot(),
                steps=[dict(step_id=1, phase='business', total=total, success=total-1, failed=1)])


class SamplePayloadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='sample-owner')
        self.project = PerfProject.objects.create(name='sample', owner=self.user)
        self.scene = PerfScenario.objects.create(name='sample', project=self.project, created_by=self.user, engine='K6')
        self.run = PerfExecution.objects.create(execution_no='sample-run', scenario=self.scene,
            project=self.project, status='RUNNING', load_snapshot={'_engine':'K6'}, summary={})
        self.client = APIClient(); self.client.force_authenticate(self.user)
        self.url = f'/api/perf-testing/executions/{self.run.id}/realtime/'

    def test_running_api_reads_row_provenance_without_final_summary_and_preserves_late_observation(self):
        row = PerfMetricSample.objects.create(execution=self.run, ts_offset=1, tps=999,
            k6_payload=sample_payload(observation()))
        data = self.client.get(self.url, {'after_id':0}).data
        sample = data['samples'][0]
        self.assertEqual(data['summary'], {})
        self.assertEqual((sample['tps'],sample['throughput']['latest_bucket_start_ms']), (3,1000))
        self.assertEqual((sample['http_total'],sample['business_total'],sample['failed_requests']), (4,3,1))
        self.assertEqual(sample['steps'][0]['step_id'],1)
        self.run.summary = {'business_total':7,'throughput':{'version':'completion_epoch_1s_v1','verified':True,'peak_rps':7,
            'sample_windows':{'1':2000}, 'buckets':[{'start_ms':1000,'count':7}]}}
        self.run.save()
        after = self.client.get(self.url, {'after_id':0}).data['samples'][0]
        self.assertEqual((after['tps'],after['throughput']['latest_bucket_start_ms']), (3,1000))
        self.assertTrue(after['throughput']['provisional'])
        row.refresh_from_db(); self.assertEqual(row.tps,999)

    def test_cursor_pages_more_than_2000_equal_offsets_without_loss_and_legacy_since(self):
        PerfMetricSample.objects.bulk_create([PerfMetricSample(execution=self.run,ts_offset=1) for _ in range(2002)])
        first = self.client.get(self.url, {'after_id':0}).data
        self.assertEqual(len(first['samples']),2000); self.assertTrue(first['has_more'])
        second = self.client.get(self.url, {'after_id':first['next_after_id']}).data
        self.assertEqual(len(second['samples']),2); self.assertFalse(second['has_more'])
        ids = [s['id'] for s in first['samples']+second['samples']]
        self.assertEqual(len(set(ids)),2002); self.assertEqual(ids,sorted(ids))
        self.assertEqual(self.client.get(self.url, {'since':1}).data['samples'],[])
        self.assertEqual(self.client.get(self.url, {'after_id':-1}).status_code,400)
        outsider=get_user_model().objects.create_user(username='sample-outsider')
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url,{'after_id':0}).status_code,404)

    def test_payload_bounds_exclude_private_values_and_empty_window_is_unknown(self):
        raw=observation(); raw.update(headers={'Authorization':'PRIVATE'}, url='PRIVATE', response='PRIVATE', snapshot='PRIVATE')
        raw['steps']=[dict(step_id=i+1,total=1,success=1,failed=0,name='PRIVATE',url='PRIVATE',error_detail='PRIVATE') for i in range(1000)]
        raw['throughput']['buckets']=[{'start_ms':1000,'count':3}]*1000
        raw['avg_rt']=float('nan');raw['window_count']=0
        payload=sample_payload(raw);encoded=json.dumps(payload,allow_nan=False)
        self.assertNotIn('PRIVATE',encoded);self.assertNotIn('buckets',encoded)
        self.assertLessEqual(len(encoded.encode()),MAX_PAYLOAD_BYTES)
        self.assertEqual(len(payload['steps']),200);self.assertTrue(payload['steps_truncated'])
        self.assertEqual(payload['steps_total'],1000);self.assertEqual(payload['business_total'],3)
        self.assertIsNone(payload['p95_rt']);self.assertIsNone(payload['avg_rt'])
        self.assertEqual(sanitized_payload(payload),payload)
        self.assertEqual(sanitized_payload({'version':'unsupported','tps':55}),{})

    def test_authentication_step_ids_survive_allowlist(self):
        raw=observation(); raw['steps']=[dict(step_id='auth:login',phase='login',total=1),dict(step_id='auth:refresh',phase='refresh',total=1)]
        payload=sample_payload(raw)
        self.assertEqual([row['step_id'] for row in payload['steps']],['auth:login','auth:refresh'])
        self.assertFalse(payload['steps_truncated'])

    def test_legacy_k6_missing_payload_unknown_other_engine_retains_scalar(self):
        sample={'ts_offset':1,'tps':999,'avg_rt':0,'total_requests':4}
        self.assertIsNone(reporter.normalized_samples(self.run,[sample])[0]['tps'])
        self.assertIsNone(reporter.normalized_samples(self.run,[sample])[0]['avg_rt'])
        self.run.load_snapshot={'_engine':'BUILTIN'}
        self.assertEqual(reporter.normalized_samples(self.run,[sample]),[sample])


class WorkerSampleTests(TransactionTestCase):
    def test_real_executor_keeps_colliding_emissions_and_immediate_running_http_payload(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        owner=get_user_model().objects.create_user(username='sample-worker')
        project=PerfProject.objects.create(name='worker',owner=owner)
        scene=PerfScenario.objects.create(project=project,created_by=owner,engine='K6',name='sample',
            env_config={'base_url':'http://unused.invalid'},load_config={'model':'CONCURRENCY','concurrency':1,'duration':30},sla_config={})
        first=PerfScenarioStep.objects.create(scenario=scene,name='same',method='GET',url='/a')
        second=PerfScenarioStep.objects.create(scenario=scene,name='same',method='GET',url='/b')
        captured=[];client=APIClient();client.force_authenticate(owner)
        class EventEngine(K6Engine):
            def prepare(self): self._open_raw_writer()
            def run(self):
                self._start_ts=100
                original_callback=self.on_sample
                self.on_sample=lambda sample: (original_callback(sample), original_callback(sample))
                for index,stamp in [(0,1100),(0,1200),(1,1300)]:
                    self._consume_event({'kind':'request_started','step':index,'vu':1})
                    self._consume_event({'kind':'request','step':index,'vu':1,'timestamp_ms':stamp,'elapsed_ms':10,'status':200,'ok':index==0})
                with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic',return_value=101.05): self._emit_sample(force=True)
                captured.append(client.get(f'/api/perf-testing/executions/{execution.id}/realtime/',{'after_id':0}).data)
                self._consume_event({'kind':'request_started','step':1,'vu':1})
                self._consume_event({'kind':'request','step':1,'vu':1,'timestamp_ms':2100,'elapsed_ms':10,'status':200,'ok':True})
                self._end_ts=101.4
                with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic',return_value=101.4): self._emit_sample(force=True)
                with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic',return_value=101.4): self._emit_sample(force=True)
                self._close_raw_writer()
        with override_settings(PERF_PRIVATE_ROOT=root/'private',MEDIA_ROOT=root/'media'), \
             mock.patch('apps.perf_testing.engines.k6_version',return_value='test-fixed'), \
             mock.patch.object(executor,'push_update'),mock.patch.object(executor,'flush_push_updates'), \
             mock.patch.object(executor,'_notify_if_needed'),mock.patch('apps.perf_testing.engines.get_engine_class',return_value=EventEngine):
            execution=executor.create_execution(scene,user=owner)
            result=executor.run_execution(execution.id)
        self.assertEqual(result.status,'COMPLETED')
        active=captured[0];self.assertEqual(active['status'],'RUNNING');self.assertEqual(active['summary'],{})
        self.assertEqual(active['samples'][0]['tps'],3)
        self.assertEqual(active['samples'][0]['http_total'],3)
        self.assertEqual({s['step_id'] for s in active['samples'][0]['steps']},{first.id,second.id})
        rows=list(result.samples.order_by('id'))
        self.assertEqual([r.ts_offset for r in rows],[1,1,1])
        projected=reporter.normalized_samples(result,[dict(id=r.id,ts_offset=r.ts_offset,k6_payload=r.k6_payload) for r in rows])
        self.assertEqual([r['tps'] for r in projected],[3,1,1])
        self.assertEqual([r['throughput']['latest_bucket_start_ms'] for r in projected],[1000,2000,2000])
        self.assertEqual([r['sample_seq'] for r in projected],[1,2,3])
        self.assertTrue(projected[-1]['engine_finished'])
        self.assertEqual(projected[-1]['business_total'],4)
        self.assertEqual(projected[-1]['window_count'],0)
        self.assertIsNone(projected[-1]['p95_rt'])
        self.assertEqual(result.summary['peak_tps'],3)

    def test_both_additive_migrations_preserve_old_row_columns(self):
        current_leaves = MigrationExecutor(connection).loader.graph.leaf_nodes('perf_testing')
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(current_leaves))
        loader=MigrationExecutor(connection)
        old_target=[('perf_testing','0005_api_catalog')]
        new_target=[('perf_testing','0006_metric_sample_payload')]
        for module in ('backend.pressure_migrations.perf_testing.0006_metric_sample_payload',
                       'apps.perf_testing.migrations.0015_metric_sample_payload'):
            loader=MigrationExecutor(connection);loader.migrate(old_target)
            state=loader.loader.project_state(old_target)
            apps=state.apps
            user=apps.get_model('users','User').objects.create(username=module)
            project=apps.get_model('perf_testing','PerfProject').objects.create(name='legacy',owner_id=user.id)
            scene=apps.get_model('perf_testing','PerfScenario').objects.create(name='legacy',project_id=project.id,created_by_id=user.id)
            run=apps.get_model('perf_testing','PerfExecution').objects.create(execution_no=module,scenario_id=scene.id,project_id=project.id)
            old=apps.get_model('perf_testing','PerfMetricSample').objects.create(execution_id=run.id,ts_offset=1,tps=22000,total_requests=12)
            migration=importlib.import_module(module).Migration('payload','perf_testing')
            with connection.schema_editor() as editor: new_state=migration.apply(state,editor)
            row=new_state.apps.get_model('perf_testing','PerfMetricSample').objects.get(id=old.id)
            self.assertEqual((row.ts_offset,row.tps,row.total_requests,row.k6_payload),(1,22000,12,{}))
            # Restore migration bookkeeping to match the applied test-only schema.
            from django.db.migrations.recorder import MigrationRecorder
            MigrationRecorder(connection).record_applied('perf_testing','0006_metric_sample_payload')
        MigrationExecutor(connection).migrate(new_target)

class SampleStorageFailureTests(TransactionTestCase):
    def exercise(self, mode):
        import gzip
        import csv
        from django.db import OperationalError
        from django.db.models.query import QuerySet
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        owner=get_user_model().objects.create_user(username='storage-'+mode)
        project=PerfProject.objects.create(name='storage',owner=owner)
        scene=PerfScenario.objects.create(project=project,created_by=owner,engine='K6',name='storage',
            env_config={'base_url':'http://unused.invalid'},load_config={'model':'CONCURRENCY','concurrency':1,'duration':2},
            sla_config={'enabled':True,'thresholds':{'error_rate':0}},perf_targets={'enabled':True})
        PerfScenarioStep.objects.create(scenario=scene,name='business',method='GET',url='/a')
        instances=[]
        class EventEngine(K6Engine):
            def prepare(self): self._open_raw_writer()
            def run(self):
                instances.append(self);self._start_ts=100
                original=self.on_sample
                self.on_sample=lambda sample: (original(sample),original(sample))
                try:
                    for stamp in (1100,1200,1300):
                        self._consume_event({'kind':'request_started','step':0,'vu':1})
                        self._consume_event({'kind':'request','step':0,'vu':1,'timestamp_ms':stamp,'elapsed_ms':10,'status':200,'ok':True})
                    with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic',return_value=101):self._emit_sample(force=True)
                    if mode == 'stubborn':
                        for index in range(8):
                            with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic',return_value=101.1 + index / 100):self._emit_sample(force=True)
                    if not self._stopping:
                        self._consume_event({'kind':'request_started','step':0,'vu':1})
                        self._consume_event({'kind':'request','step':0,'vu':1,'timestamp_ms':2100,'elapsed_ms':10,'status':200,'ok':True})
                finally:
                    self._end_ts=102
                    try:
                        with mock.patch('apps.perf_testing.engines.k6_engine.time.monotonic',return_value=102):self._emit_sample(force=True)
                    finally:self._close_raw_writer()
        bulk=PerfMetricSample.objects.bulk_create
        update=QuerySet.update
        original_commit=connection.commit
        attempts=[]; heartbeat_failures=[]; commit_fault=[False]
        def insert(rows,*args,**kwargs):
            attempts.append([row.k6_payload['sample_seq'] for row in rows])
            call=len(attempts)
            if mode in ('persistent','stubborn','terminal_db_failure') or mode=='recover_final' and call<=3 or mode=='first' and call==1 or mode=='final' and call==2:
                raise OperationalError('isolated sample insert failure')
            result=bulk(rows,*args,**kwargs)
            if mode=='post_insert' and call==1:raise OperationalError('after real insert before commit')
            if mode=='uncertain_commit' and call==1:commit_fault[0]=True
            return result
        def commit():
            original_commit()
            if commit_fault[0]:
                commit_fault[0]=False
                raise OperationalError('commit completed but acknowledgement lost')
        def heartbeat(query,**kwargs):
            if mode=='heartbeat' and query.model is PerfExecution and set(kwargs)=={'heartbeat_at'} and not heartbeat_failures:
                heartbeat_failures.append(1);raise OperationalError('heartbeat only')
            return update(query,**kwargs)
        with override_settings(PERF_PRIVATE_ROOT=root/'private',MEDIA_ROOT=root/'media'), \
             mock.patch('apps.perf_testing.engines.k6_version',return_value='test-fixed'), \
             mock.patch.object(executor,'push_update'),mock.patch.object(executor,'flush_push_updates'), \
             mock.patch.object(executor,'_notify_if_needed'),mock.patch('apps.perf_testing.engines.get_engine_class',return_value=EventEngine), \
             mock.patch.object(PerfMetricSample.objects,'bulk_create',side_effect=insert), \
             mock.patch.object(QuerySet,'update',heartbeat),mock.patch.object(connection,'commit',side_effect=commit):
            run=executor.create_execution(scene,user=owner)
            if mode=='terminal_db_failure':
                with mock.patch.object(PerfExecution,'refresh_from_db',side_effect=OperationalError('terminal database unavailable')):
                    with self.assertRaises(OperationalError):executor.run_execution(run.id)
                result=PerfExecution.objects.get(id=run.id)
            else:
                result=executor.run_execution(run.id)
        if mode=='terminal_db_failure':
            self.assertNotEqual(result.status,'COMPLETED')
            evidence=json.loads((root/'media'/result.artifact_dir/'sample-integrity.json').read_text())
            self.assertFalse(evidence['complete']);self.assertEqual(evidence['missing_count'],2)
            self.assertTrue(evidence['stopped_due_to_storage']);self.assertTrue(instances[0]._stopping)
            self.assertIsNone(instances[0]._raw_fh)
            with gzip.open(instances[0].raw_csv_path,'rt',encoding='utf-8') as stream:self.assertEqual(len(list(csv.DictReader(stream))),3)
            self.assertNotIn('terminal database unavailable',json.dumps(evidence))
            self.assertNotIn('isolated',json.dumps(evidence))
            return evidence
        rows=list(result.samples.order_by('id'))
        evidence=result.summary['sample_persistence']
        artifact=json.loads((root/'media'/result.artifact_dir/'sample-integrity.json').read_text())
        self.assertEqual(artifact,evidence)
        self.assertIsNone(instances[0]._raw_fh)
        with gzip.open(instances[0].raw_csv_path,'rt',encoding='utf-8') as stream:raw=list(csv.DictReader(stream))
        self.assertEqual(len(raw),result.summary['business_total'])
        if mode in ('persistent','stubborn'):
            self.assertEqual(len(attempts),6)
            self.assertEqual(rows,[]);self.assertEqual(evidence['missing_count'],10 if mode=='stubborn' else 2)
            self.assertEqual(evidence['pending_sequences'],[1,2,3,4] if mode=='stubborn' else [1,2])
            self.assertEqual(evidence['discarded_count'],6 if mode=='stubborn' else 0)
            self.assertEqual(result.status,'FAILED');self.assertFalse(result.summary['execution_complete'])
            self.assertEqual(result.sla_result,'NOT_EVALUATED');self.assertEqual(result.verdict,'NOT_EVALUATED')
            self.assertIn('缺失 10' if mode=='stubborn' else '缺失 2',result.error_message)
            html=(root/'media'/result.report_url).read_text(encoding='utf-8')
            self.assertIn('采样数据不完整',html)
        else:
            self.assertEqual([row.k6_payload['sample_seq'] for row in rows],[1,2])
            self.assertEqual(evidence['persisted_count'],2);self.assertEqual(evidence['missing_count'],0)
            if mode=='recover_final':
                self.assertEqual(result.status,'STOPPED');self.assertFalse(result.summary['execution_complete'])
                self.assertEqual(result.sla_result,'NOT_EVALUATED');self.assertEqual(result.verdict,'NOT_EVALUATED')
                self.assertTrue(evidence['complete']);self.assertTrue(evidence['stopped_due_to_storage'])
            else:
                self.assertEqual(result.status,'COMPLETED');self.assertTrue(result.summary['execution_complete'])
                self.assertEqual(result.sla_result,'PASSED')
            if mode=='heartbeat':
                self.assertEqual(len(attempts),2);self.assertEqual(evidence['heartbeat_failures'],1)
            if mode=='uncertain_commit':self.assertEqual(attempts,[[1],[2]])
        return evidence

    def test_stubborn_callbacks_keep_bounded_queue_and_explicit_gap(self):self.exercise('stubborn')
    def test_terminal_database_failure_still_leaves_raw_and_local_evidence(self):self.exercise('terminal_db_failure')

    def test_first_insert_recovers_without_duplicate(self):self.exercise('first')
    def test_final_insert_recovers_without_duplicate(self):self.exercise('final')
    def test_after_insert_before_commit_rolls_back_then_retries(self):self.exercise('post_insert')
    def test_uncertain_successful_commit_is_reconciled_without_duplicate(self):self.exercise('uncertain_commit')
    def test_heartbeat_failure_never_reinserts_committed_samples(self):self.exercise('heartbeat')
    def test_persistent_failure_stops_and_discloses_missing_samples_and_raw(self):self.exercise('persistent')
    def test_final_recovery_preserves_samples_but_early_stop_cannot_pass(self):self.exercise('recover_final')

    def test_callback_error_in_actual_engine_finally_still_closes_raw_writer(self):
        import gzip
        import io
        from apps.perf_testing.engines import k6_engine
        with tempfile.TemporaryDirectory() as directory:
            engine=K6Engine({'steps':[]},work_dir=directory,raw_csv_path=str(Path(directory)/'raw.csv.gz'))
            engine._open_raw_writer()
            process=mock.Mock();process.stdout=io.StringIO('');process.poll.return_value=0;process.wait.return_value=0
            engine._stopping=True
            # Enter run rather than its early stopped return; stop after Popen setup.
            engine._stopping=False
            def open_process(*args,**kwargs):engine._stopping=True;return process
            with mock.patch.object(k6_engine,'_binary',return_value='unused'),mock.patch.object(k6_engine.subprocess,'Popen',side_effect=open_process), \
                 mock.patch.object(k6_engine,'WindowsJob') as job,mock.patch.object(engine,'_emit_sample',side_effect=RuntimeError('callback')):
                job.return_value.creation_flags=0
                with self.assertRaisesRegex(RuntimeError,'callback'):engine.run()
            self.assertIsNone(engine._raw_fh)
            with gzip.open(engine.raw_csv_path,'rt',encoding='utf-8') as stream:self.assertIn('timestamp_ms',stream.read())
            if k6_engine.os.name == 'nt':job.return_value.close.assert_called_once()
            self.assertTrue(process.stdout.closed)
