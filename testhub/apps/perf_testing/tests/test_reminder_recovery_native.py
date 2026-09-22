"""Native k6 exercises exact bytes and genuine request counts against loopback only."""
import json
import os
import tempfile
import threading
import unittest
from decimal import Decimal
from http.server import ThreadingHTTPServer
from unittest import mock

from apps.perf_testing.engines import k6_engine
from apps.perf_testing.services import reminder_recovery as recovery
from .test_k6_engine import TestHandler, TEST_ROOT
from .test_reminder_recovery import snapshot


@unittest.skipUnless(k6_engine.is_available(),'native fixed k6 required')
class NativeReminderTests(unittest.TestCase):
    def test_late_put_after_stopped_native_k6_is_cancelled_with_original_intent(self):
        entered=threading.Event();release=threading.Event();late_done=threading.Event()
        cancelled=threading.Event();active=[];holder={};errors=[]
        class Handler(TestHandler):
            def log_message(self,*_):pass
            def respond(self,status,data):
                raw=json.dumps(dict(code='OK' if status==200 else 'CONFLICT',data=data)).encode()
                self.send_response(status);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            def do_GET(self):
                item=holder['item']
                if self.path=='/api/v1/me':self.respond(200,dict(user_id='100'))
                elif self.path.endswith(item['recover_digest']) and cancelled.is_set():
                    self.respond(200,dict(request_key=item['recover_digest'],request_fingerprint=item['recover_fingerprint'],
                        operation='delete',stock_code='sz000001',state='committed',created_at='fixture-time',
                        cleanup=dict(outcome='cancelled_before_create',deleted=False)))
                else:self.respond(404,{})
            def do_PUT(self):
                raw=self.rfile.read(int(self.headers['Content-Length'])).decode()
                assert raw==holder['item']['put_body']
                entered.set();release.wait(15)
                if not cancelled.is_set():active.append('late-created')
                try:self.respond(409 if cancelled.is_set() else 200,{})
                finally:late_done.set()
            def do_DELETE(self):
                raw=self.rfile.read(int(self.headers['Content-Length'])).decode()
                assert raw==holder['item']['delete_body']
                assert self.headers['Idempotency-Key']==holder['item']['recover_key']
                cancelled.set();self.respond(200,dict(outcome='cancelled_before_create',deleted=False))
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        listener=threading.Thread(target=server.serve_forever,daemon=True);listener.start()
        try:
            with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work,mock.patch.dict(os.environ,{'K6_RUNNER':'NATIVE'}):
                source=snapshot(1);source['env_config']['base_url']=f'http://127.0.0.1:{server.server_port}'
                source['load_config'].update(model='CONCURRENCY',duration=10);source['runtime_config']['timeout']=10
                plan=recovery.build_plan(source,31,'test-secret');holder['item']=plan['resources'][0]
                frozen=recovery.freeze_snapshot(source,plan)
                store=recovery.RecoveryStore(work+'/private','test-secret');store.create(plan,recovery.digest(recovery.encode(frozen)))
                engine=k6_engine.K6Engine(frozen,work_dir=work+'/engine');engine.prepare()
                def run():
                    try:engine.run()
                    except Exception as exc:errors.append(type(exc).__name__)
                worker=threading.Thread(target=run);worker.start()
                try:
                    self.assertTrue(entered.wait(5));engine.stop();worker.join(5)
                    self.assertFalse(worker.is_alive())
                    # Reopen the signed journal, as a separately restarted daemon would.
                    store=recovery.RecoveryStore(work+'/private','test-secret')
                    client=recovery.RecoveryHTTP(plan['origin'],'PRIVATE_TOKEN_0')
                    for _ in range(3):recovery.reconcile_one(store,31,1,client)
                    self.assertEqual(store.public(31)['cancelled'],1)
                    release.set();self.assertTrue(late_done.wait(3))
                    self.assertEqual(active,[])
                    self.assertEqual(store.public(31)['writes'],1)
                finally:
                    release.set();engine.stop();worker.join(5)
        finally:
            release.set();server.shutdown();server.server_close();listener.join(2)

    def run_case(self, wrong_owner=False):
        calls=[];items={};plan={}
        class Handler(TestHandler):
            def log_message(self,*_):pass
            def request(self):
                token=self.headers.get('Authorization','').removeprefix('Bearer ')
                item=items[token]
                body=self.rfile.read(int(self.headers.get('Content-Length','0'))).decode()
                calls.append((self.command,self.path,body,self.headers.get('Idempotency-Key')))
                expected=json.loads(item['put_body'])
                for name,condition in expected['conditions'].items():
                    value=condition['value']
                    condition['value']='' if not condition['enabled'] and value in ('','0') else format(Decimal(value),'.6f' if name.startswith('price_') else '.4f')
                rule=dict(id='9223372036854775807',revision=1,conditions=expected['conditions'],policy=expected['policy'])
                if self.path=='/api/v1/me':
                    data=dict(user_id='999' if wrong_owner else item['owner'])
                elif self.path.endswith(item['put_digest']):
                    data=dict(request_key=item['put_digest'],request_fingerprint=item['put_fingerprint'],
                        operation='put',stock_code='sz000001',state='committed',created_at='fixture',rule=rule)
                elif self.command=='DELETE':
                    assert body==item['delete_body'] and self.headers['Idempotency-Key']==item['delete_key']
                    data=dict(outcome='deleted',deleted=True,rule_id=rule['id'],before_revision=1,after_revision=2)
                else:
                    if self.command=='PUT':
                        assert body==item['put_body'] and self.headers['Idempotency-Key']==item['put_key']
                    data=dict(rule,stock_code='sz000001')
                raw=json.dumps(dict(code='OK',data=data)).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            do_GET=request
            do_PUT=request
            do_DELETE=request
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            source=snapshot(2)
            source['env_config']['base_url']=f'http://127.0.0.1:{server.server_port}'
            source['runtime_config']['timeout']=2
            source['load_config'].update(model='CONCURRENCY',duration=5)
            plan.update(recovery.build_plan(source,31,'test-secret'))
            items.update({f'PRIVATE_TOKEN_{item["vu"]-1}':item for item in plan['resources']})
            frozen=recovery.freeze_snapshot(source,plan)
            with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work,mock.patch.dict(os.environ,{'K6_RUNNER':'NATIVE'}):
                self.assertEqual(k6_engine.validate_snapshot(frozen),[])
                engine=k6_engine.K6Engine(frozen,work_dir=work)
                engine.prepare()
                try:
                    engine.run()
                except k6_engine.EngineError:
                    if not wrong_owner:
                        raise
                return engine.collect()['summary'],calls
        finally:
            server.shutdown();server.server_close();thread.join(2)

    def test_two_vus_ten_rounds_send_one_real_five_step_group_each(self):
        summary,calls=self.run_case()
        self.assertEqual(len(calls),10)
        self.assertEqual(summary['http_total'],10)
        self.assertEqual(summary['success_requests'],10)
        self.assertEqual(summary['execution_policy']['groups'][0]['success'],2)
        self.assertEqual(sum(method=='PUT' for method,*_ in calls),2)
        self.assertEqual(sum(method=='DELETE' for method,*_ in calls),2)

    def test_false_csv_owner_blocks_every_write(self):
        summary,calls=self.run_case(wrong_owner=True)
        self.assertEqual(calls,[(method,path,body,key) for method,path,body,key in calls if method=='GET' and path=='/api/v1/me'])
        self.assertEqual(len(calls),2)
        self.assertEqual(summary['failed_requests'],2)
        self.assertEqual(summary['execution_policy']['groups'][0]['blocked_steps'],8)
