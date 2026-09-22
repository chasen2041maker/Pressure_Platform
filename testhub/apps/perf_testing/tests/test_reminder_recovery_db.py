"""Normal account pool/scenario/execution entry points; no external business calls."""
from copy import deepcopy
from datetime import timedelta
import json
from unittest import mock

from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from apps.perf_testing import models
from apps.perf_testing.services import executor, reminder_recovery as recovery
from apps.perf_testing.services.k6_execution import FileLease, K6ExecutionError
from . import test_account_pools as pool_fixtures
from .test_reminder_recovery import snapshot


class ReminderExecutionTests(TestCase):
    setUp=pool_fixtures.AccountPoolTests.setUp
    upload=pool_fixtures.AccountPoolTests.upload
    make_version=pool_fixtures.AccountPoolTests.make_version

    def scene(self):
        version=self.make_version(raw=b'key,password\n100,fixture-token\n',
                                  field_mapping=json.dumps({'user_id':'key','token':'password'}))
        source=snapshot(1)
        scene=models.PerfScenario.objects.create(project=self.project,name='Reminder',created_by=self.owner,
            engine='K6',account_pool_version=version,env_config=source['env_config'],
            load_config=dict(model='CONCURRENCY',concurrency=1,duration=10,iterations_per_vu=2),
            runtime_config=source['runtime_config'])
        ids=[]
        for order,step in enumerate(source['steps']):
            fields={key:deepcopy(value) for key,value in step.items() if key!='id'}
            ids.append(models.PerfScenarioStep.objects.create(scenario=scene,order=order,**fields).pk)
        for key,value in zip(('put_step_id','receipt_step_id','get_step_id','delete_step_id'),ids):
            scene.runtime_config['resource_recovery'][key]=value
        scene.save()
        return scene

    def execute(self,scene):
        store=recovery.configured_store();store.heartbeat()
        with mock.patch('apps.perf_testing.engines.k6_version',return_value='fixture-fixed'), \
                mock.patch.object(recovery,'RecoveryHTTP',return_value=lambda *_:(200,dict(recovery=dict(version='v1',enabled=True)))):
            return executor.create_execution(scene,user=self.owner)

    def test_normal_freeze_private_ready_and_copy_binding(self):
        scene=self.scene();execution=self.execute(scene)
        frozen=executor._execution_snapshot(execution)
        plan=recovery.verify_frozen(recovery.configured_store(),execution.pk,frozen)
        self.assertEqual(len(frozen['steps']),5)
        self.assertEqual(execution.steps_snapshot[0]['request_path'],'/api/v1/me')
        self.assertNotIn('fixture-token',json.dumps(execution.load_snapshot))
        self.assertNotIn('fixture-token',json.dumps(plan))
        response=self.client.post(f'{self.base}scenarios/{scene.pk}/duplicate/',{},format='json')
        self.assertEqual(response.status_code,201,response.data)
        cloned=response.data['runtime_config']['resource_recovery']
        self.assertEqual(cloned['put_step_id'],response.data['steps'][0]['id'])
        self.assertNotEqual(cloned['put_step_id'],scene.runtime_config['resource_recovery']['put_step_id'])
        self.assertEqual(self.client.get(f'{self.base}executions/{execution.pk}/').data['resource_recovery']['pending'],1)

    def test_normal_save_checks_owned_steps_and_rejects_forged_ready(self):
        scene=self.scene();runtime=deepcopy(scene.runtime_config)
        url=f'{self.base}scenarios/{scene.pk}/'
        saved=self.client.patch(url,{'runtime_config':runtime},format='json')
        self.assertEqual(saved.status_code,200,saved.data)
        invalid=deepcopy(runtime);invalid['resource_recovery']['receipt_step_id']=99999
        self.assertEqual(self.client.patch(url,{'runtime_config':invalid},format='json').status_code,400)
        invalid=deepcopy(runtime);invalid['_reminder_recovery']={'ready':True}
        self.assertEqual(self.client.patch(url,{'runtime_config':invalid},format='json').status_code,400)
        invalid=deepcopy(runtime);invalid['resource_recovery']['ready']=True
        self.assertEqual(self.client.patch(url,{'runtime_config':invalid},format='json').status_code,400)

    def test_pending_scope_blocks_reuse_retention_and_cascade_even_when_stopped(self):
        scene=self.scene();execution=self.execute(scene)
        execution.status='STOPPED';execution.created_at=timezone.now()-timedelta(days=900);execution.save()
        with self.assertRaises(Exception):self.execute(scene)
        with self.assertRaises(ProtectedError),transaction.atomic():execution.delete()
        with self.assertRaises(ProtectedError),transaction.atomic():scene.delete()
        self.assertEqual(self.client.delete(f'{self.base}scenarios/{scene.pk}/').status_code,400)
        self.assertEqual(self.client.delete(f'{self.base}executions/{execution.pk}/').status_code,400)
        self.assertEqual(self.client.delete(f'{self.base}projects/{self.project.pk}/').status_code,409)
        execution.refresh_from_db()
        self.assertIsNotNone(execution.account_pool_version_id)
        from apps.perf_testing.services.cleanup import cleanup_perf_data
        result=cleanup_perf_data(retention_days=1,artifact_days=1)
        self.assertEqual(result['executions_removed'],0)
        self.assertTrue(models.PerfExecution.objects.filter(pk=execution.pk).exists())

    def test_stop_and_orphan_are_recovered_by_independent_tick_and_visible(self):
        scene=self.scene();execution=self.execute(scene)
        executor._finalize_orphan(execution,'STOPPED','fixture stop')
        store=recovery.configured_store();plan=store.plan(execution.pk);item=plan['resources'][0]
        writes=[]
        def client(method,path,body='',key=''):
            if path=='/api/v1/me':return 200,dict(user_id='100')
            if method=='DELETE':writes.append((key,body));return 200,dict(outcome='cancelled_before_create',deleted=False)
            if path.endswith(item['recover_digest']) and writes:
                return 200,dict(request_key=item['recover_digest'],request_fingerprint=item['recover_fingerprint'],
                    operation='delete',stock_code='sz000001',state='committed',created_at='fixture-time',
                    cleanup=dict(outcome='cancelled_before_create',deleted=False))
            return 404,None
        checkpoints=[]
        with mock.patch.object(recovery,'RecoveryHTTP',return_value=client):
            for _ in range(3):
                recovery.recovery_tick(progress=lambda:checkpoints.append(store.state(execution.pk)['requests']))
        self.assertIn(3,checkpoints)
        self.assertIn(6,checkpoints)
        execution.refresh_from_db()
        self.assertEqual(execution.resource_recovery['cancelled'],1)
        self.assertEqual(len(writes),1)
        self.assertEqual(writes[0],(item['recover_key'],item['delete_body']))
        self.assertTrue(recovery.may_delete(execution))


class ReminderRecoveryAdmissionTests(TestCase):
    setUp = pool_fixtures.AccountPoolTests.setUp
    upload = pool_fixtures.AccountPoolTests.upload
    make_version = pool_fixtures.AccountPoolTests.make_version
    scene = ReminderExecutionTests.scene
    execute = ReminderExecutionTests.execute

    def assert_leases_released(self, store):
        with FileLease(store.root, 'start'), FileLease(store.root, 'run'):
            pass

    def test_pending_execution_short_circuits_before_run_lease(self):
        execution = self.execute(self.scene())
        store = recovery.configured_store()
        original = FileLease.__enter__
        entered = []

        def enter(lease):
            entered.append(lease.path.name)
            return original(lease)

        with mock.patch.object(FileLease, '__enter__', enter), \
                mock.patch.object(recovery, 'RecoveryHTTP') as client:
            self.assertEqual(recovery.recovery_tick(store=store), 0)
        self.assertEqual(entered, ['k6-start.lock'])
        client.assert_not_called()
        execution.refresh_from_db()
        self.assertEqual(execution.status, 'PENDING')
        self.assert_leases_released(store)

    def test_recovery_cannot_fail_worker_during_create_to_spawn_handoff(self):
        execution = self.execute(self.scene())
        store = recovery.configured_store()
        original = FileLease.__enter__
        intercepted = []

        def enter(lease):
            result = original(lease)
            if lease.path.name == 'k6-run.lock' and not intercepted:
                intercepted.append(True)
                # 精确安排 worker 在恢复占有 run 时入场，使用真实 OS 锁和执行记录。
                executor.run_execution(execution.pk)
            return result

        with FileLease(store.root, 'start'):
            with mock.patch.object(FileLease, '__enter__', enter), \
                    mock.patch.object(recovery, 'RecoveryHTTP', side_effect=AssertionError('no recovery request during handoff')) as client:
                self.assertEqual(recovery.recovery_tick(store=store), 0)
            execution.refresh_from_db()
            self.assertEqual(execution.status, 'PENDING', 'idle recovery marked the new worker FAILED')
            self.assertEqual(intercepted, [], 'daemon entered the parent start-to-spawn critical section')
            client.assert_not_called()
        with mock.patch.object(executor, '_run_execution', return_value=execution) as run:
            executor.run_execution(execution.pk)
            run.assert_called_once_with(execution.pk, None)
        execution.refresh_from_db()
        self.assertEqual(execution.status, 'PREPARING')
        self.assert_leases_released(store)

    def test_either_contended_lease_skips_tick_without_recovery(self):
        execution = self.execute(self.scene())
        executor._finalize_orphan(execution, 'STOPPED', 'isolated stop')
        store = recovery.configured_store()
        before = deepcopy(store.state(execution.pk))
        for name in ('start', 'run'):
            with self.subTest(lease=name), FileLease(store.root, name), \
                    mock.patch.object(recovery, 'RecoveryHTTP', side_effect=AssertionError('contended tick must not send')) as client:
                self.assertEqual(recovery.recovery_tick(store=store), 0)
                client.assert_not_called()
            self.assertEqual(store.state(execution.pk), before)
            self.assert_leases_released(store)

    def test_terminal_scan_releases_start_but_keeps_run(self):
        execution = self.execute(self.scene())
        executor._finalize_orphan(execution, 'STOPPED', 'isolated stop')
        store = recovery.configured_store()
        observed = []

        def reconcile(*args, **kwargs):
            with FileLease(store.root, 'start'):
                observed.append('start released')
            with self.assertRaises(K6ExecutionError):
                with FileLease(store.root, 'run'):
                    self.fail('terminal recovery lost its run exclusion')

        with mock.patch.object(recovery, 'RecoveryHTTP'), \
                mock.patch.object(recovery, 'reconcile_one', side_effect=reconcile) as process:
            self.assertEqual(recovery.recovery_tick(store=store), 1)
            process.assert_called_once()
        self.assertEqual(observed, ['start released'])
        self.assert_leases_released(store)

    def test_active_rechecked_inside_run_before_scanning_terminal_history(self):
        execution = self.execute(self.scene())
        executor._finalize_orphan(execution, 'STOPPED', 'isolated stop')
        store = recovery.configured_store()
        original = FileLease.__enter__

        def enter(lease):
            result = original(lease)
            if lease.path.name == 'k6-run.lock':
                models.PerfExecution.objects.filter(pk=execution.pk).update(status='PENDING')
            return result

        with mock.patch.object(FileLease, '__enter__', enter), \
                mock.patch.object(recovery, 'RecoveryHTTP') as client:
            self.assertEqual(recovery.recovery_tick(store=store), 0)
            client.assert_not_called()
        self.assertEqual(store.state(execution.pk)['requests'], 0)
        self.assert_leases_released(store)
