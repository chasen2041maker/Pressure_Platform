"""Portfolio recovery contracts use only private temporary files and loopback fixtures."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import unittest
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from apps.perf_testing.services import reminder_recovery as recovery


def snapshot(users=2):
    config = dict(version=1, kind='portfolio_reminder', group_id='reminder_once',
                  put_step_id=11, receipt_step_id=12, get_step_id=13, delete_step_id=14,
                  stock_code='sz000001', max_resources=1000)
    policy = dict(group_id='reminder_once', vu_start=1, vu_end=users, max_runs_per_vu=1, min_interval_ms=0)
    path = '/api/v1/portfolio/reminders/sz000001'
    conditions={name:dict(enabled=False,value='0') for name in (
        'price_above','price_below','daily_pct_up','daily_pct_down','five_min_pct_up','five_min_pct_down')}
    conditions['price_above']=dict(enabled=True,value='1000000')
    conditions['price_below']=dict(enabled=False,value='12.50')
    body = {'conditions': conditions,
            'policy': {'channels': {'app_push': False, 'message_center': True},
                'frequency': {'mode':'cooldown','interval_minutes':30},
                'validity': {'mode':'permanent','valid_until':None},'trading_session_only':True},
            'precondition': {'mode': 'absent'}}
    return dict(engine='K6', load_config=dict(concurrency=users, iterations_per_vu=10),
                env_config=dict(base_url='http://127.0.0.1:12345', headers={}),
                runtime_config=dict(resource_recovery=config, auth_profile=dict(mode='STATIC', transport='BEARER',
                    access_token_variable='token', max_attempts=1)),
                account_pool=dict(version_id=6, content_hash='a'*64, data_key='account-version-6', identity_column='user_id'),
                variables=[dict(name='user_id', type='CSV', data_file_id='account-version-6', column='user_id'),
                           dict(name='token', type='CSV', data_file_id='account-version-6', column='token')],
                csv_data={'account-version-6': {'columns': ['user_id', 'token'], 'rows': [
                    {'user_id': str(100+i), 'token': 'PRIVATE_TOKEN_'+str(i)} for i in range(users)]}},
                steps=[dict(id=sid, name=str(sid), protocol='HTTP', method=method, url=url, enabled=True,
                    body_type='JSON' if method in ('PUT','DELETE') else 'NONE', body=json.dumps(body) if method=='PUT' else '{}' if method=='DELETE' else '',
                    headers={}, params={}, extractors=[], assertions=[], execution_policy=deepcopy(policy))
                    for sid,method,url in [(11,'PUT',path),(12,'GET','/api/v1/portfolio/reminder-commands/{{rr_put_digest}}'),
                                          (13,'GET',path),(14,'DELETE',path)]])


class ReminderDaemonProgressTests(unittest.TestCase):
    def test_progress_deadline_tracks_each_resource_not_the_entire_tick(self):
        from apps.perf_testing.management.commands.recover_portfolio_reminders import RecoveryProgressGuard
        now=[0.0]
        guard=RecoveryProgressGuard(clock=lambda:now[0])
        for _ in range(8):
            now[0]+=15
            self.assertFalse(guard.expired())
            guard.checkpoint()
        self.assertEqual(now[0],120)
        now[0]+=31
        self.assertTrue(guard.expired())

    def test_slow_delete_headers_kill_daemon_and_restart_uses_same_receipt(self):
        holder={};writes=[];peer_close=[];disconnected=threading.Event();committed=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def respond(self,status,data):
                raw=json.dumps(dict(code='OK',data=data)).encode()
                self.send_response(status);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                item=holder['item']
                if self.path=='/api/v1/me':self.respond(200,dict(user_id='100'))
                elif self.path.endswith(item['recover_digest']) and writes:
                    self.respond(200,dict(request_key=item['recover_digest'],request_fingerprint=item['recover_fingerprint'],
                        operation='delete',stock_code='sz000001',state='committed',created_at='fixture-time',
                        cleanup=dict(outcome='cancelled_before_create',deleted=False)))
                else:self.respond(404,{})
            def do_DELETE(self):
                raw=self.rfile.read(int(self.headers['Content-Length'])).decode()
                writes.append((self.headers['Idempotency-Key'],raw))
                holder['committed_at']=time.monotonic()
                committed.set()
                # Commit then never finish the response headers: socket inactivity timeout cannot detect this.
                try:
                    self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ');self.wfile.flush()
                    for _ in range(500):
                        if select.select([self.connection],[],[],0)[0]:
                            if self.connection.recv(1,socket.MSG_PEEK)==b'':
                                peer_close.append('EOF');disconnected.set();return
                        self.wfile.write(b'x');self.wfile.flush();time.sleep(.01)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                    # An unread partial response can make Windows close with RST instead of FIN.
                    try:
                        self.connection.settimeout(2)
                        if self.connection.recv(1)==b'':peer_close.append('EOF')
                    except (ConnectionResetError,ConnectionAbortedError):
                        peer_close.append('RESET')
                    except TimeoutError:
                        pass
                    if peer_close:disconnected.set()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        listener=threading.Thread(target=server.serve_forever,daemon=True);listener.start()
        script='''
import sys,time
from apps.perf_testing.services import reminder_recovery as recovery
from apps.perf_testing.services.k6_execution import FileLease
from apps.perf_testing.management.commands.recover_portfolio_reminders import RecoveryProgressGuard
store=recovery.RecoveryStore(sys.argv[1],'test-secret')
plan=store.plan(31)
with RecoveryProgressGuard() as guard, FileLease(store.root,'recovery'), FileLease(store.root,'run'):
    transport=recovery.RecoveryHTTP(plan['origin'],'PRIVATE_TOKEN_0',timeout=1)
    def client(method,path,body='',key=''):
        if method=='DELETE':
            # Inject the short deadline only into the actual stalled request, after intent accounting.
            with RecoveryProgressGuard(timeout=.5):
                return transport(method,path,body,key)
        return transport(method,path,body,key)
    # Preparation deliberately exceeds the injected network deadline; it must not satisfy the kill assertion.
    time.sleep(.65)
    for _ in range(4):
        store.heartbeat();guard.checkpoint()
        recovery.reconcile_one(store,31,1,client)
        store.heartbeat();guard.checkpoint()
'''
        try:
            with tempfile.TemporaryDirectory() as root:
                source=snapshot(1);source['env_config']['base_url']=f'http://127.0.0.1:{server.server_port}'
                plan=recovery.build_plan(source,31,'test-secret');holder['item']=plan['resources'][0]
                store=recovery.RecoveryStore(root,'test-secret');store.create(plan,'b'*64)
                env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[3]))
                first=subprocess.run([sys.executable,'-c',script,root],env=env,capture_output=True,timeout=8)
                exited_at=time.monotonic()
                self.assertEqual(first.returncode,74,first.stderr.decode(errors='replace'))
                self.assertTrue(committed.is_set(),'daemon exited before the slow DELETE was committed')
                self.assertLess(exited_at-holder['committed_at'],4)
                self.assertTrue(disconnected.wait(2))
                self.assertEqual(len(peer_close),1)
                self.assertIn(peer_close[0],('EOF','RESET'))
                interrupted=store.public(31)
                self.assertEqual(interrupted['pending'],1)
                self.assertEqual(interrupted['unknown_requests'],1)
                second=subprocess.run([sys.executable,'-c',script,root],env=env,capture_output=True,timeout=8)
                self.assertEqual(second.returncode,0,second.stderr.decode(errors='replace'))
                self.assertEqual(store.public(31)['cancelled'],1)
                self.assertEqual(store.public(31)['unknown_requests'],1)
                self.assertEqual(writes,[(holder['item']['recover_key'],holder['item']['delete_body'])])
        finally:
            server.shutdown();server.server_close();listener.join(2)


class ReminderPlanTests(unittest.TestCase):
    def contracted_snapshot(self, users=2):
        from .test_prepared_recovery import cleanup_schema
        source = snapshot(users)
        put_schema = {'type': 'object', 'additionalProperties': False,
            'required': ['conditions', 'policy', 'precondition'], 'properties': {
                'conditions': {'type': 'object'}, 'policy': {'type': 'object'},
                'precondition': {'oneOf': [
                    {'type': 'object', 'additionalProperties': False, 'required': ['mode'],
                     'properties': {'mode': {'type': 'string', 'enum': ['absent']}}},
                    {'type': 'object', 'additionalProperties': False,
                     'required': ['mode', 'rule_id', 'revision', 'active'], 'properties': {
                        'mode': {'type': 'string', 'enum': ['revision']},
                        'rule_id': {'type': 'string', 'pattern': '^[1-9][0-9]*$'},
                        'revision': {'type': 'integer', 'minimum': 1},
                        'active': {'type': 'boolean', 'enum': [False]}}}]}}}
        for step, schema in ((source['steps'][0], put_schema), (source['steps'][3], cleanup_schema())):
            step['source_metadata'] = {'request_body': {
                'content': {'application/json': {'schema': schema}}}}
        return source

    def test_frozen_actual_wires_obey_source_schema_including_typed_lineage(self):
        source = self.contracted_snapshot()
        first = recovery.build_plan(source, 31, 'test-secret', run_id='a'*32)
        scope = first['resources'][1]['scope']
        plan = recovery.build_plan(source, 32, 'test-secret', run_id='b'*32,
            lineage={scope: {'rule_id': '77', 'revision': 7}})
        frozen = recovery.freeze_snapshot(source, plan)
        self.assertEqual(frozen['steps'][-1]['body'], '{}')
        self.assertEqual(json.loads(plan['resources'][1]['put_body'])['precondition']['revision'], 7)
        for role, field, value in (('delete', 'put_request_key', {'maxLength': 20}),
                                   ('delete', 'put_request_fingerprint', {'pattern': '^INVALID$'}),
                                   ('put', 'precondition', {'properties': {'revision': {'maximum': 6}}})):
            changed = deepcopy(source)
            step = changed['steps'][3 if role == 'delete' else 0]
            schema = step['source_metadata']['request_body']['content']['application/json']['schema']
            if role == 'delete': schema['properties'][field].update(value)
            else: schema['properties'][field]['allOf'] = [value]
            with self.subTest(role=role, field=field), self.assertRaises(recovery.RecoveryError):
                recovery.build_plan(changed, 32, 'test-secret', run_id='b'*32,
                    lineage={scope: {'rule_id': '77', 'revision': 7}})

    def test_freeze_rejects_changed_source_or_non_generated_role_key_hash_body(self):
        source = self.contracted_snapshot()
        plan = recovery.build_plan(source, 31, 'test-secret', run_id='a'*32)
        for field, value in (('put_key', 'rr1-'+'a'*32+'-1-delete'),
                ('put_fingerprint', '0'*64), ('delete_body', '{}'),
                ('delete_key', 'rr1-'+'a'*32+'-1-put'), ('delete_fingerprint', 'f'*64),
                ('delete_digest', 'f'*64), ('owner', '999')):
            altered = deepcopy(plan); altered['resources'][0][field] = value
            with self.subTest(field=field), self.assertRaises(recovery.RecoveryError):
                recovery.freeze_snapshot(source, altered)
        altered = deepcopy(plan); altered['pool']['content_hash'] = '0'*64
        with self.assertRaises(recovery.RecoveryError): recovery.freeze_snapshot(source, altered)
        changed = deepcopy(source)
        changed['steps'][3]['source_metadata']['request_body']['content']['application/json']['schema']['properties']['put_request_key']['maxLength'] = 20
        with self.assertRaises(recovery.RecoveryError): recovery.freeze_snapshot(changed, plan)

    def test_bound_catalog_missing_body_schema_and_header_constraints_block_freeze(self):
        source = self.contracted_snapshot(1)
        source['steps'][3]['source_metadata']['request_body'] = {}
        with self.assertRaisesRegex(recovery.RecoveryError, '^recovery_wire_schema_missing$'):
            recovery.build_plan(source, 31, 'test-secret')
        source = self.contracted_snapshot(1)
        source['steps'][3]['source_metadata']['parameters'] = [{'in': 'header', 'name': 'Idempotency-Key',
            'required': True, 'schema': {'type': 'string', 'maxLength': 5}}]
        with self.assertRaisesRegex(recovery.RecoveryError, '^recovery_wire_key_schema$'):
            recovery.build_plan(source, 31, 'test-secret')

    def test_real_mobile_condition_and_policy_shapes_are_required(self):
        for field,value in [('value',999999),('value','0.0'),('value','1e3'),('enabled',1)]:
            source=snapshot();body=json.loads(source['steps'][0]['body'])
            body['conditions']['price_below'][field]=value
            source['steps'][0]['body']=json.dumps(body)
            self.assertTrue(recovery.validate_snapshot(source))

    def test_frozen_group_injects_one_observed_identity_step_and_shared_noncredential_rows(self):
        source=snapshot();plan=recovery.build_plan(source,31,'test-secret',run_id='a'*32)
        frozen=recovery.freeze_snapshot(source,plan)
        self.assertEqual(source['steps'][0]['id'],11)
        self.assertEqual(frozen['steps'][0]['id'],'reminder:identity')
        self.assertEqual(frozen['steps'][0]['execution_policy'],frozen['steps'][1]['execution_policy'])
        self.assertNotIn('PRIVATE_TOKEN',json.dumps(frozen['csv_data'][recovery.INTERNAL_DATA]))
        self.assertEqual(recovery.validate_snapshot(frozen),[])
        from apps.perf_testing.engines.k6_engine import validate_snapshot
        self.assertEqual(validate_snapshot(frozen),[])

    def test_strict_scope_and_group(self):
        self.assertEqual(recovery.validate_snapshot(snapshot()), [])
        unmanaged=snapshot();unmanaged['runtime_config']['resource_recovery']={}
        self.assertIn('recovery_required_for_conditional_write',recovery.validate_snapshot(unmanaged))
        for mutate in (
            lambda s:s['runtime_config']['resource_recovery'].update(kind='arbitrary'),
            lambda s:s['steps'][0].update(url='http://other.invalid/path'),
            lambda s:s['steps'][1].update(params={'secret':'value'}),
            lambda s:s['steps'][3]['execution_policy'].update(max_runs_per_vu=2),
            lambda s:s['steps'].append(dict(s['steps'][0],id=99,execution_policy={})),
            lambda s:s['csv_data']['account-version-6']['rows'][1].update(user_id='100'),
            lambda s:s['csv_data']['account-version-6']['rows'][0].update(user_id='0100'),
        ):
            changed=snapshot();mutate(changed)
            self.assertTrue(recovery.validate_snapshot(changed))

    def test_stable_keys_exact_bytes_and_no_credentials_in_intent(self):
        source=snapshot();original=deepcopy(source)
        plan=recovery.build_plan(source,31,'fixed-test-secret',run_id='a'*32)
        self.assertEqual(plan,recovery.build_plan(source,31,'fixed-test-secret',run_id='a'*32))
        self.assertEqual(source,original)
        self.assertNotIn('PRIVATE_TOKEN',json.dumps(plan))
        row=plan['resources'][0];path='/api/v1/portfolio/reminders/sz000001'
        self.assertEqual(row['put_digest'],hashlib.sha256(('PUT\n'+path+'\nuser:100\n'+row['put_key']).encode()).hexdigest())
        self.assertEqual(row['put_fingerprint'],hashlib.sha256(('PUT\n'+path+'\n\n'+row['put_body']).encode()).hexdigest())
        self.assertNotEqual(row['delete_key'],row['recover_key'])
        self.assertEqual(json.loads(row['delete_body']),dict(put_request_key=row['put_key'],put_request_fingerprint=row['put_fingerprint']))
        later=recovery.build_plan(source,32,'fixed-test-secret',run_id='b'*32)
        self.assertEqual(row['scope'],later['resources'][0]['scope'])
        self.assertNotEqual(row['put_key'],later['resources'][0]['put_key'])

    def test_revision_precondition_comes_only_from_owned_lineage_as_json_number(self):
        source=snapshot();first=recovery.build_plan(source,31,'fixed-test-secret',run_id='a'*32)
        scope=first['resources'][0]['scope']
        lineage={scope:dict(rule_id='9223372036854775807',revision=7)}
        row=recovery.build_plan(source,32,'fixed-test-secret',run_id='b'*32,lineage=lineage)['resources'][0]
        self.assertEqual(json.loads(row['put_body'])['precondition'],dict(mode='revision',rule_id='9223372036854775807',revision=7,active=False))
        for value in ('7',True,0,4294967296):
            with self.assertRaises(recovery.RecoveryError):
                recovery.build_plan(source,32,'fixed-test-secret',lineage={scope:dict(rule_id='1',revision=value)})


class ReminderStoreTests(unittest.TestCase):
    def test_existing_snapshot_directory_chain_is_durable_before_ready(self):
        from apps.perf_testing.services.k6_execution import save_snapshot
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'private'
            source=snapshot(1);plan=recovery.build_plan(source,31,'test-secret',run_id='a'*32)
            save_snapshot(root,31,recovery.freeze_snapshot(source,plan))
            store=recovery.RecoveryStore(root,'test-secret');events=[];original=store._write
            def observe_write(path,value,exclusive=False):
                events.append(('write',path.name))
                return original(path,value,exclusive)
            with mock.patch.object(recovery,'_fsync_directory',side_effect=lambda path:events.append(('sync',path))), \
                    mock.patch.object(store,'_write',side_effect=observe_write):
                store.create(plan,'b'*64)
            ready_index=events.index(('write','ready.json'))
            self.assertEqual(events[ready_index-5:ready_index],[('sync',path) for path in (
                root/'executions/31/reminder-recovery',root/'executions/31',root/'executions',root,root.parent)])
            store.ready(31,'b'*64)

    def test_existing_ancestor_sync_failure_cannot_leave_a_readable_ready(self):
        from apps.perf_testing.services.k6_execution import save_snapshot
        for ancestor in ('executions','root_parent'):
            with self.subTest(ancestor=ancestor),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)/'private'
                source=snapshot(1);plan=recovery.build_plan(source,31,'test-secret',run_id='a'*32)
                save_snapshot(root,31,recovery.freeze_snapshot(source,plan))
                store=recovery.RecoveryStore(root,'test-secret')
                # Existing parents must also be synced, even when mkdir did no work.
                store._execution(31);store._directory(root/'reminder-recovery')
                failed=root/'executions' if ancestor=='executions' else root.parent
                def fail_parent(path):
                    if path==failed:raise OSError('injected ancestor sync failure')
                with mock.patch.object(recovery,'_fsync_directory',side_effect=fail_parent):
                    with self.assertRaises(recovery.RecoveryError):store.create(plan,'b'*64)
                self.assertFalse((root/'executions/31/reminder-recovery/ready.json').exists())
                with self.assertRaises(recovery.RecoveryError):store.ready(31,'b'*64)
                self.assertTrue(store.occupied(plan['resources'][0]['scope']))

    def test_external_capability_requires_boolean_true_before_preparation(self):
        with tempfile.TemporaryDirectory() as root:
            store=recovery.RecoveryStore(root,'test-secret');store.heartbeat()
            for enabled in (False,1,'true',None):
                with mock.patch.object(recovery,'configured_store',return_value=store), \
                        mock.patch.object(recovery,'RecoveryHTTP',return_value=lambda *_:(200,dict(recovery=dict(version='v1',enabled=enabled)))), \
                        self.assertRaises(recovery.RecoveryError):recovery.prepare_managed(snapshot(1))

    def test_crash_between_terminal_state_and_registry_is_repaired_before_release(self):
        with tempfile.TemporaryDirectory() as root:
            store=recovery.RecoveryStore(root,'test-secret');source=snapshot(1)
            plan=recovery.build_plan(source,31,'test-secret');store.create(plan,'b'*64)
            original=store._write
            def fail(path,value,exclusive=False):
                if path.name=='registry.json':raise recovery.RecoveryError('injected')
                return original(path,value,exclusive)
            with mock.patch.object(store,'_write',side_effect=fail),self.assertRaises(recovery.RecoveryError):
                store.change(31,1,lambda _state,row:row.update(state='CLEANED',cleanup=dict(rule_id='10',after_revision=2)))
            self.assertTrue(store.occupied(plan['resources'][0]['scope']))
            with self.assertRaises(recovery.RecoveryError):store.public(31)
            store.repair_registry(31)
            self.assertFalse(store.occupied(plan['resources'][0]['scope']))
            next_plan=recovery.build_plan(source,32,'test-secret',lineage=store.lineage());store.create(next_plan,'c'*64)
            store.change(32,1,lambda _state,row:row.update(state='CANCELLED'))
            later=recovery.build_plan(source,33,'test-secret',lineage=store.lineage())
            self.assertEqual(json.loads(later['resources'][0]['put_body'])['precondition']['revision'],2)

    def test_daemon_freshness_and_frozen_snapshot_token_binding(self):
        with tempfile.TemporaryDirectory() as root:
            store=recovery.RecoveryStore(root,'test-secret')
            with self.assertRaises(recovery.RecoveryError):store.require_daemon(now=100)
            store.heartbeat(now=100);store.require_daemon(now=101)
            with self.assertRaises(recovery.RecoveryError):store.require_daemon(now=131)
            source=snapshot(1);plan=recovery.build_plan(source,31,'test-secret')
            frozen=recovery.freeze_snapshot(source,plan)
            store.create(plan,recovery.digest(recovery.encode(frozen)))
            self.assertEqual(recovery.verify_frozen(store,31,frozen),plan)
            frozen['csv_data']['account-version-6']['rows'][0]['token']='swapped'
            with self.assertRaises(recovery.RecoveryError):recovery.verify_frozen(store,31,frozen)

    def test_exclusive_plan_and_signed_state_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            store=recovery.RecoveryStore(root,'test-secret')
            plan=recovery.build_plan(snapshot(),31,'test-secret',run_id='a'*32)
            store.create(plan,'b'*64)
            self.assertEqual(store.plan(31),plan)
            self.assertEqual(store.public(31)['pending'],2)
            self.assertTrue(store.occupied(plan['resources'][0]['scope']))
            with self.assertRaises(recovery.RecoveryError):store.create(plan,'b'*64)
            state_path=Path(root)/'executions/31/reminder-recovery/state.json'
            state_path.write_text('{}')
            with self.assertRaises(recovery.RecoveryError):store.state(31)
            self.assertTrue(store.occupied(plan['resources'][0]['scope']))


class ReminderTransportTests(unittest.TestCase):
    def test_real_loopback_json_redirect_and_malformed_response_are_bounded(self):
        mode=['ok'];calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                calls.append(self.path)
                if mode[0]=='redirect':
                    self.send_response(302);self.send_header('Location','/must-not-follow');self.end_headers();return
                raw=b'{"code":"OK","data":{"user_id":"100"}}' if mode[0]=='ok' else b'{"code":"OK","data":{},"data":{}}'
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=recovery.RecoveryHTTP(f'http://127.0.0.1:{server.server_port}','fixture')
            self.assertEqual(client('GET','/api/v1/me'),(200,{'user_id':'100'}))
            for value in ('redirect','bad'):
                mode[0]=value
                with self.assertRaises(recovery.RecoveryError) as caught:client('GET','/api/v1/me')
                self.assertTrue(caught.exception.response_received)
            self.assertNotIn('/must-not-follow',calls)
            with self.assertRaises(recovery.RecoveryError):client('PUT','/api/v1/portfolio/reminders/sz000001','{}','key')
        finally:
            server.shutdown();server.server_close();thread.join(2)


class ReminderReconciliationTests(unittest.TestCase):
    def test_resumed_write_rechecks_real_owner_before_any_delete(self):
        with tempfile.TemporaryDirectory() as root:
            store=recovery.RecoveryStore(root,'test-secret')
            plan=recovery.build_plan(snapshot(1),31,'test-secret');store.create(plan,'b'*64)
            calls=[]
            def client(method,path,body='',key=''):
                calls.append((method,path))
                return (200,dict(user_id='100')) if path=='/api/v1/me' else (404,None)
            recovery.reconcile_one(store,31,1,client,now=100)
            self.assertEqual(store.state(31)['resources']['1']['phase'],'recover_write')
            def wrong(method,path,body='',key=''):
                calls.append((method,path));return 200,dict(user_id='101')
            recovery.reconcile_one(store,31,1,wrong,now=101)
            self.assertEqual(store.public(31)['conflict'],1)
            self.assertFalse(any(method=='DELETE' for method,path in calls))

    def test_unknown_put_uses_stable_cancellation_and_receipt_not_a_404_guess(self):
        with tempfile.TemporaryDirectory() as root:
            store=recovery.RecoveryStore(root,'test-secret')
            plan=recovery.build_plan(snapshot(1),31,'test-secret',run_id='a'*32);store.create(plan,'b'*64)
            item=plan['resources'][0];calls=[];created=[False]
            def client(method,path,body='',key=''):
                calls.append((method,path,body,key))
                if path=='/api/v1/me':return 200,dict(user_id='100')
                if method=='DELETE':
                    self.assertEqual(key,item['recover_key']);self.assertEqual(body,item['delete_body'])
                    created[0]=True
                    return 200,dict(outcome='cancelled_before_create',deleted=False)
                if created[0] and path.endswith(item['recover_digest']):
                    return 200,dict(request_key=item['recover_digest'],request_fingerprint=item['recover_fingerprint'],
                        operation='delete',stock_code='sz000001',state='committed',created_at='2026-09-20T00:00:00Z',
                        cleanup=dict(outcome='cancelled_before_create',deleted=False))
                return 404,None
            for _ in range(3):recovery.reconcile_one(store,31,1,client,now=100)
            self.assertEqual(store.public(31)['cancelled'],1)
            self.assertEqual(sum(c[0]=='DELETE' for c in calls),1)
            self.assertFalse(store.occupied(item['scope']))
            count=len(calls);recovery.reconcile_one(store,31,1,client,now=100)
            self.assertEqual(len(calls),count)

    def test_existing_normal_delete_receipt_never_repeats_mutation(self):
        for active in (False,True):
            with self.subTest(active=active),tempfile.TemporaryDirectory() as root:
                store=recovery.RecoveryStore(root,'test-secret')
                plan=recovery.build_plan(snapshot(1),31,'test-secret',run_id='a'*32);store.create(plan,'b'*64)
                item=plan['resources'][0];calls=[]
                cleanup=dict(outcome='deleted',deleted=True,rule_id='9223372036854775807',before_revision=1,after_revision=2)
                def client(method,path,body='',key=''):
                    calls.append(method)
                    if path=='/api/v1/me':return 200,dict(user_id='100')
                    if path.endswith(item['delete_digest']):
                        return 200,dict(request_key=item['delete_digest'],request_fingerprint=item['delete_fingerprint'],
                            operation='delete',stock_code='sz000001',state='committed',created_at='2026-09-20T00:00:00Z',cleanup=cleanup)
                    return (200,{'id':10}) if active else (404,None)
                recovery.reconcile_one(store,31,1,client,now=100)
                self.assertEqual(store.public(31)['conflict' if active else 'cleaned'],1)
                self.assertNotIn('DELETE',calls)
                self.assertEqual(store.occupied(item['scope']),active)

    def test_lost_delete_response_queries_same_command_and_limits_attempts(self):
        with tempfile.TemporaryDirectory() as root:
            store=recovery.RecoveryStore(root,'test-secret')
            plan=recovery.build_plan(snapshot(1),31,'test-secret',run_id='a'*32);store.create(plan,'b'*64)
            calls=[]
            def client(method,path,body='',key=''):
                calls.append((method,key))
                if path=='/api/v1/me':return 200,dict(user_id='100')
                if method=='DELETE':raise recovery.RecoveryError('transport_unavailable')
                return 404,None
            for tick in range(1,60):recovery.reconcile_one(store,31,1,client,now=tick*1000)
            self.assertLessEqual(len(calls),recovery.MAX_ATTEMPTS)
            keys={key for method,key in calls if method=='DELETE'}
            self.assertEqual(keys,{plan['resources'][0]['recover_key']})
            self.assertEqual(store.public(31)['pending'],1)
            self.assertGreater(store.public(31)['unknown_requests'],0)

    def test_failure_before_ready_never_reports_ready_or_releases_reservation(self):
        with tempfile.TemporaryDirectory() as root:
            store=recovery.RecoveryStore(root,'test-secret')
            plan=recovery.build_plan(snapshot(),31,'test-secret',run_id='a'*32)
            original=store._write
            def fail_ready(path,value,exclusive=False):
                if path.name=='ready.json':raise OSError('injected fsync failure')
                return original(path,value,exclusive)
            with mock.patch.object(store,'_write',side_effect=fail_ready):
                with self.assertRaises(recovery.RecoveryError):store.create(plan,'b'*64)
            with self.assertRaises(recovery.RecoveryError):store.ready(31,'b'*64)
            self.assertTrue(store.occupied(plan['resources'][0]['scope']))
