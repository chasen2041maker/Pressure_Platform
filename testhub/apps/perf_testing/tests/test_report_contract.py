"""C12 isolated report scope, identity, export and clone contracts. No network or live data."""
import csv
import io
import json
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from asgiref.sync import async_to_sync
from apps.perf_testing.models import PerfProject, PerfScenario, PerfScenarioStep, PerfExecution, PerfBaseline
from apps.perf_testing.services import reporter
from apps.perf_testing.services.compare_report import build_snapshot, _compute_deltas
from apps.perf_testing.consumers import PerfExecutionConsumer


class ReportContractTests(TestCase):
    def test_websocket_export_keeps_http_counts_and_session_labels(self):
        self.run1.steps_snapshot[0].update(protocol='WEBSOCKET', request_path='/socket', websocket_commands=[])
        self.run1.summary['websocket'] = {'version': 1, 'connections': {'observed': True, 'current': None, 'peak': 1, 'unclosed': 1},
                                        'command_metrics': []}
        self.run1.save()
        response = self.client.get(self.url(self.run1, 'report'), {'export': 'csv'})
        rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0]['protocol'], 'WEBSOCKET')
        self.assertEqual(rows[0]['latency_kind'], 'session')
        html = reporter._render(self.run1, [], [])
        self.assertIn('业务步骤/s 不是消息 QPS', html)
        self.assertIn('未知 / 1 / 1', html)
        self.assertIn('<th>协议</th><th>耗时口径</th>', html)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        override = override_settings(MEDIA_ROOT=self.root / 'media', PERF_PRIVATE_ROOT=self.root / 'private')
        override.enable(); self.addCleanup(override.disable)
        self.owner = get_user_model().objects.create_user(username='report-owner')
        self.member = get_user_model().objects.create_user(username='report-member')
        self.outsider = get_user_model().objects.create_user(username='report-outsider')
        self.admin = get_user_model().objects.create_user(username='report-admin', is_staff=True)
        self.project = PerfProject.objects.create(name='authorized', owner=self.owner)
        self.project.members.add(self.member)
        self.other = PerfProject.objects.create(name='other', owner=self.outsider)
        self.scene = PerfScenario.objects.create(project=self.project, created_by=self.owner, name='original', engine='K6')
        self.step1 = PerfScenarioStep.objects.create(scenario=self.scene, name='same', method='GET', url='/a')
        self.step2 = PerfScenarioStep.objects.create(scenario=self.scene, name='same', method='GET', url='/b')
        self.run1 = self.create_run('run-one')
        self.run2 = self.create_run('run-two')
        self.client = APIClient(); self.client.force_authenticate(self.owner)
        self.base = '/api/perf-testing/'

    def create_run(self, name, **kwargs):
        return PerfExecution.objects.create(scenario=self.scene, project=self.project,
            execution_no=name, executed_by=self.owner, status='COMPLETED',
            load_snapshot={'_engine': 'K6'}, steps_snapshot=[{'id': s.pk, 'name': s.name, 'method': s.method, 'enabled': True} for s in (self.step1, self.step2)],
            summary={'business_total': 12, 'http_total': 14, 'total_requests': 12, 'failed_requests': 6,
                'error_rate': 50, 'tps': 3, 'business_rps': 3, 'avg_rt': 20, 'p95_rt': 40,
                'step_metrics': [dict(step_id=s.pk, step_name='same', method='GET', phase='business', total=6,
                    success=6-i*6, failed=i*6, avg_rt=10+i*20, p95_rt=20+i*40, tps=1.5, error_rate=i*100) for i,s in enumerate((self.step1,self.step2))]}, **kwargs)

    def url(self, run=None, action=''):
        return self.base + 'executions/' + (f'{run.pk}/' if run else '') + (action + '/' if action else '')

    def test_execution_actions_are_project_scoped(self):
        self.client.force_authenticate(self.outsider)
        for action in ('', 'realtime', 'samples', 'request-stats', 'report', 'download-raw', 'run-log'):
            response = self.client.get(self.url(self.run1, action))
            self.assertEqual(response.status_code, 404, (action,response.data if hasattr(response,'data') else ''))
        with mock.patch('apps.perf_testing.services.executor.stop_execution') as stop:
            self.assertEqual(self.client.post(self.url(self.run1,'stop'), {}, format='json').status_code, 404)
            stop.assert_not_called()
        self.assertEqual(self.client.delete(self.url(self.run1)).status_code, 404)
        self.assertEqual(self.client.get(self.url(None,'compare'), {'ids': f'{self.run1.pk},{self.run2.pk}'}).status_code, 404)
        self.assertEqual(self.client.get(self.url(None,'dashboard')).data['total_executions'], 0)
        self.assertEqual(self.client.post(self.url(None,'reap-stale')).status_code, 403)
        for user in (self.owner,self.member,self.admin):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.url(self.run1)).status_code, 200)

    def test_share_only_authorizes_matching_report_and_raw_without_execution_user(self):
        token = self.run1.generate_share_token(1)
        client = APIClient()
        response = client.get(self.url(self.run1,'report'), {'token':token, 'export':'json'})
        self.assertEqual(response.status_code, 200)
        for action in ('report', 'download-raw'):
            self.assertIn(client.get(self.url(self.run2,action), {'token':token}).status_code, (401,403))
        self.assertIn(client.get(self.url(self.run1), {'token':token}).status_code, (401,403))
        self.run1.executed_by = None; self.run1.save()
        self.assertEqual(client.get(self.url(self.run1,'report'), {'token':token,'export':'json'}).status_code, 200)
        self.run1.share_expires_at=timezone.now()-timedelta(seconds=1); self.run1.save()
        self.assertIn(client.get(self.url(self.run1,'report'), {'token':token}).status_code,(401,403))
        self.run1.revoke_share_token()
        self.assertIn(client.get(self.url(self.run1,'report'), {'token':token}).status_code,(401,403))

    def test_valid_raw_share_and_invalid_token_are_scoped(self):
        artifact = self.root / 'media' / 'only-this-run'
        artifact.mkdir(parents=True)
        (artifact / 'raw.csv.gz').write_bytes(b'isolated-fixture')
        self.run1.artifact_dir = 'only-this-run'; self.run1.save()
        token = self.run1.generate_share_token(1)
        response = APIClient().get(self.url(self.run1, 'download-raw'), {'token': token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'isolated-fixture')
        response.close()
        self.assertIn(APIClient().get(self.url(self.run1, 'report'), {'token': 'invalid'}).status_code, (401, 403))

    def test_same_project_baseline_scene_mismatch_and_mixed_comparison_are_rejected(self):
        another = PerfScenario.objects.create(project=self.project, name='another', created_by=self.owner)
        response = self.client.post(self.base + 'baselines/', {'scenario': another.pk, 'execution': self.run1.pk}, format='json')
        self.assertEqual(response.status_code, 400)
        self.run2.project = self.other; self.run2.save()
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.base + 'comparison-reports/', {'execution_ids': [self.run1.pk, self.run2.pk]}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_websocket_scopes_login_and_share(self):
        consumer=PerfExecutionConsumer()
        consumer.scope={'user':self.outsider,'url_route':{'kwargs':{'execution_id':self.run1.pk}},'query_string':b''}
        self.assertFalse(async_to_sync(consumer._authenticate)())
        consumer.scope['user']=self.member
        self.assertTrue(async_to_sync(consumer._authenticate)())
        consumer.scope['user']=self.outsider
        token=self.run2.generate_share_token(1)
        consumer.scope['query_string']=f'token={token}'.encode()
        self.assertFalse(async_to_sync(consumer._authenticate)())
        token=self.run1.generate_share_token(1)
        consumer.scope['query_string']=f'token={token}'.encode()
        self.assertTrue(async_to_sync(consumer._authenticate)())

    def test_baseline_and_saved_comparison_scopes(self):
        baseline=self.client.post(self.base+'baselines/set-from-execution/', {'execution_id':self.run1.pk}, format='json')
        self.assertEqual(baseline.status_code,200)
        saved=self.client.post(self.base+'comparison-reports/', {'execution_ids':[self.run1.pk,self.run2.pk]}, format='json')
        self.assertEqual(saved.status_code,201)
        other_scene=PerfScenario.objects.create(project=self.other, name='foreign', created_by=self.outsider)
        bad=self.client.patch(self.base+f"baselines/{baseline.data['id']}/", {'scenario':other_scene.pk}, format='json')
        self.assertIn(bad.status_code,(400,403))
        self.client.force_authenticate(self.outsider)
        for action in ('compare','set-from-execution'):
            response = self.client.get(self.base+'baselines/compare/', {'execution_id':self.run1.pk}) if action=='compare' else self.client.post(self.base+'baselines/set-from-execution/',{'execution_id':self.run1.pk},format='json')
            self.assertEqual(response.status_code,404)
        self.assertEqual(self.client.get(self.base+f"comparison-reports/{saved.data['id']}/").status_code,404)
        self.assertEqual(self.client.delete(self.base+f"comparison-reports/{saved.data['id']}/").status_code,404)
        self.assertEqual(self.client.post(self.base+'comparison-reports/', {'execution_ids':[self.run1.pk,self.run2.pk]},format='json').status_code,404)
        self.assertEqual(self.client.get(self.base+'comparison-reports/').data['total'],0)

    def test_duplicate_remaps_same_name_step_rules_and_protects_references(self):
        self.scene.sla_config={'enabled':True,'thresholds':{'error_rate':60},'step_thresholds':[
            {'step_id':s.pk,'thresholds':{'error_rate':0}} for s in (self.step1,self.step2)]}
        self.scene.save()
        response=self.client.post(self.base+f'scenarios/{self.scene.pk}/duplicate/',{},format='json')
        self.assertEqual(response.status_code,201,response.data)
        clone=PerfScenario.objects.get(pk=response.data['id'])
        self.assertEqual([r['step_id'] for r in clone.sla_config['step_thresholds']],list(clone.steps.values_list('id',flat=True)))
        self.assertEqual(clone.sla_config['step_thresholds'][0]['thresholds']['error_rate'],0)
        self.scene.refresh_from_db()
        self.assertEqual(self.scene.sla_config['step_thresholds'][0]['step_id'],self.step1.pk)
        self.assertEqual(self.client.delete(self.base+f'steps/{self.step1.pk}/').status_code,400)
        self.assertEqual(self.client.patch(self.base+f'steps/{self.step1.pk}/',{'enabled':False},format='json').status_code,400)
        self.assertEqual(self.client.post(self.base+f'scenarios/{self.scene.pk}/save-steps/',{'steps':[]},format='json').status_code,400)

    def test_shared_export_and_exact_comparison_identity(self):
        document=json.loads(self.client.get(self.url(self.run1,'report'),{'export':'json'}).content)
        self.assertEqual([(r['step_id'],r['failed']) for r in document['interfaces']],[(self.step1.pk,0),(self.step2.pk,6)])
        raw=self.client.get(self.url(self.run1,'report'),{'export':'csv'}).content.decode('utf-8-sig')
        interfaces=[row for row in csv.DictReader(io.StringIO(raw)) if row['section']=='interface']
        self.assertEqual([(int(row['step_id']), int(row['failed']), float(row['p95_rt'])) for row in interfaces],
            [(row['step_id'], row['failed'], row['p95_rt']) for row in document['interfaces']])
        self.assertEqual(interfaces[1]['phase'],'business')
        html=self.client.get(self.url(self.run1,'report')).content.decode()
        self.assertIn('运行来源与版本',html)
        comparison=build_snapshot([self.run1,self.run2])
        self.assertEqual(len(comparison['step_comparison']),2)
        self.assertEqual(comparison['step_comparison'][1]['values'][1]['failed'],6)
        other_scene=PerfScenario.objects.create(project=self.project,name='different',created_by=self.owner)
        self.run2.scenario=other_scene;self.run2.save()
        self.assertEqual(len(build_snapshot([self.run1,self.run2])['step_comparison']),4)
        self.assertIsNone(_compute_deltas({'tps':3},{'tps':float('nan')})['tps'])

    def test_missing_metrics_and_ambiguous_legacy_are_explicit(self):
        self.run1.summary={'business_total':0,'http_total':2,'avg_rt':0,'error_rate':0}
        self.assertIsNone(reporter.normalized_summary(self.run1)['avg_rt'])
        self.run1.steps_snapshot=[{'id':2,'name':'same','method':'GET'},{'name':'same','method':'GET'}]
        rows=reporter.interface_rows(self.run1,[{'url':'step:2','step_name':'same','method':'GET','total':9}])
        self.assertTrue(all(r['match_status']=='ambiguous_or_missing' for r in rows[:2]))
        self.assertEqual(rows[-1]['total'],9)
        self.assertEqual(rows[-1]['match_status'],'unattributed')

    def test_provenance_does_not_export_private_values(self):
        snapshot={'scenario_name':'frozen','k6_version':'fixed','k6_adapter_version':'adapter',
            'env_config':{'token':'TOPSECRET'},'account_pool':{'version_id':1,'password':'TOPSECRET'},
            'environment_sources':[{'id':1,'version':2,'headers':{'token':'TOPSECRET'}}],
            'steps':[{'id':1,'headers':{'Authorization':'TOPSECRET'},'source_metadata':{'version_id':9,'request':{'token':'TOPSECRET'}}}],
            'sla_config':{'enabled':True,'thresholds':{'error_rate':0}}}
        with mock.patch('apps.perf_testing.services.executor._execution_snapshot',return_value=snapshot):
            evidence=reporter.report_evidence(self.run1)
        self.assertNotIn('TOPSECRET',json.dumps(evidence))
        self.assertEqual(evidence['interfaces'][0]['source']['version_id'],9)
        self.assertEqual(evidence['sla_config']['thresholds']['error_rate'],0)

    def test_report_paths_use_frozen_step_ids_and_exclude_private_url_parts(self):
        from apps.perf_testing.services.k6_execution import save_snapshot
        snapshot = {'steps': [
            {'id': self.step2.pk, 'method': 'GET', 'url': '/api/b/{{item_id}}?token=PRIVATE_QUERY#PRIVATE_FRAGMENT'},
            {'id': self.step1.pk, 'method': 'GET', 'url': 'https://user:PRIVATE_PASSWORD@example.test/api/a',
             'headers': {'Authorization': 'PRIVATE_HEADER'}, 'body': {'secret': 'PRIVATE_BODY'}},
        ]}
        save_snapshot(self.root / 'private', self.run1.pk, snapshot)
        self.step1.url = '/changed-after-execution'; self.step1.save()
        expected = ['/api/a', '/api/b/{{item_id}}']
        detail = self.client.get(self.url(self.run1)).json()
        self.assertEqual([s.get('request_path') for s in detail['steps_snapshot']], expected)
        document = json.loads(self.client.get(self.url(self.run1, 'report'), {'export': 'json'}).content)
        self.assertEqual([s.get('request_path') for s in document['interfaces']], expected)
        self.assertEqual([s['url'] for s in document['interfaces']], [f'step:{self.step1.pk}', f'step:{self.step2.pk}'])
        raw = self.client.get(self.url(self.run1, 'report'), {'export': 'csv'}).content.decode('utf-8-sig')
        rows = [s for s in csv.DictReader(io.StringIO(raw)) if s['section'] == 'interface']
        self.assertEqual([s.get('request_path') for s in rows], expected)
        report = self.client.get(self.url(self.run1, 'report')).content.decode()
        for path in expected:
            self.assertIn(path, report)
        for output in (json.dumps(detail), json.dumps(document), raw, report):
            self.assertNotIn('PRIVATE_', output)
            self.assertNotIn('/changed-after-execution', output)

    def test_missing_or_ambiguous_frozen_paths_never_use_current_scene(self):
        from apps.perf_testing.services.k6_execution import save_snapshot
        detail = self.client.get(self.url(self.run1)).json()
        self.assertEqual([s.get('request_path') for s in detail['steps_snapshot']], [None, None])
        save_snapshot(self.root / 'private', self.run1.pk, {'steps': [
            {'id': self.step1.pk, 'method': 'GET', 'url': '/first'},
            {'id': self.step1.pk, 'method': 'GET', 'url': '/conflicting'},
            {'id': self.step2.pk, 'method': 'POST', 'url': '/wrong-method'},
        ]})
        detail = self.client.get(self.url(self.run1)).json()
        self.assertEqual([s.get('request_path') for s in detail['steps_snapshot']], [None, None])

    def test_frozen_auth_paths_and_public_projection_survive_private_expiry(self):
        from apps.perf_testing.services.k6_execution import save_snapshot
        self.run1.steps_snapshot = [{'id': 'auth:login', 'name': '登录', 'method': 'POST', 'auth_phase': 'login'}]
        self.run1.save()
        save_snapshot(self.root / 'private', self.run1.pk, {'runtime_config': {'auth_profile': {
            'login': {'method': 'POST', 'url': '/api/v1/auth/login?secret=PRIVATE_VALUE'},
        }}})
        detail = self.client.get(self.url(self.run1)).json()
        self.assertEqual(detail['steps_snapshot'][0].get('request_path'), '/api/v1/auth/login')
        self.run1.steps_snapshot = detail['steps_snapshot']; self.run1.save()
        (self.root / 'private' / 'executions' / str(self.run1.pk) / 'snapshot.json').unlink()
        detail = self.client.get(self.url(self.run1)).json()
        self.assertEqual(detail['steps_snapshot'][0].get('request_path'), '/api/v1/auth/login')

    def test_reap_uses_same_admin_definition_as_project_access(self):
        superuser = get_user_model().objects.create_user(username='superuser-only', is_superuser=True, is_staff=False)
        self.client.force_authenticate(superuser)
        with mock.patch('apps.perf_testing.services.cleanup.reap_stale_executions', return_value=0) as reap:
            self.assertEqual(self.client.post(self.url(None, 'reap-stale')).status_code, 200)
            reap.assert_called_once()

    def test_dashboard_full_date_range_counts_more_than_recent_slice(self):
        for n in range(11):
            self.create_run(f'new-{n}')
        old=self.create_run('older')
        PerfExecution.objects.filter(pk=old.pk).update(created_at=timezone.now()-timedelta(days=40))
        short=self.client.get(self.url(None,'dashboard'),{'days':7}).data
        long=self.client.get(self.url(None,'dashboard'),{'days':90}).data
        self.assertEqual(short['total_scenarios'],1)
        self.assertEqual(short['total_executions'],13)
        self.assertEqual(sum(r['count'] for r in short['trend']),13)
        self.assertEqual(len(short['recent']),10)
        self.assertEqual(long['total_executions'],14)
        self.assertIsNone(short['sla_pass_rate'])

    def test_baseline_project_move_denies_all_paths_and_preserves_history(self):
        made = self.client.post(self.base + 'baselines/set-from-execution/', {'execution_id': self.run1.pk}, format='json')
        baseline_id = made.data['id']
        self.other.members.add(self.owner)
        moved = self.client.patch(self.base + f'scenarios/{self.scene.pk}/', {'project': self.other.pk}, format='json')
        self.assertEqual(moved.status_code, 200)
        self.client.force_authenticate(self.outsider)
        endpoint = self.base + f'baselines/{baseline_id}/'
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(self.client.get(self.base + 'baselines/').data['count'], 0)
        self.assertEqual(self.client.patch(endpoint, {'note': 'overwrite'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(endpoint).status_code, 404)
        self.assertEqual(self.client.post(self.base + 'baselines/set-from-execution/', {'execution_id': self.run1.pk}, format='json').status_code, 404)
        self.assertEqual(self.client.get(self.base + 'baselines/compare/', {'execution_id': self.run1.pk}).status_code, 404)
        self.run2.project = self.other; self.run2.save()
        compared = self.client.get(self.base + 'baselines/compare/', {'execution_id': self.run2.pk})
        self.assertFalse(compared.data['has_baseline'])
        self.assertEqual(compared.data['evaluation'], 'NOT_EVALUATED')
        self.assertNotIn('metrics', compared.data)
        self.assertEqual(self.client.post(self.base + 'baselines/set-from-execution/', {'execution_id': self.run2.pk}, format='json').status_code, 409)
        # A user who can access both projects still cannot attach an old execution to the moved scene.
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.base + 'baselines/set-from-execution/', {'execution_id': self.run1.pk}, format='json').status_code, 404)
        self.assertEqual(self.client.get(self.base + 'baselines/compare/', {'execution_id': self.run1.pk}).status_code, 404)
        self.assertEqual(PerfBaseline.objects.get(pk=baseline_id).execution_id, self.run1.pk)
        # Moving back restores the original compatible historical baseline without modifying it.
        PerfScenario.objects.filter(pk=self.scene.pk).update(project=self.project)
        self.assertEqual(self.client.get(endpoint).status_code, 200)
        self.assertEqual(self.client.get(self.base + 'baselines/compare/', {'execution_id': self.run1.pk}).data['evaluation'], 'EVALUATED')

    def test_baseline_without_execution_never_exports_unowned_metrics(self):
        old = PerfBaseline.objects.create(scenario=self.scene, execution=None, metrics={'avg_rt': 12345}, set_by=self.owner)
        for user in (self.owner, self.member, self.admin):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.base + f'baselines/{old.pk}/').status_code, 404)
            comparison = self.client.get(self.base + 'baselines/compare/', {'execution_id': self.run1.pk})
            self.assertEqual(comparison.data['evaluation'], 'NOT_EVALUATED')
            self.assertIn('来源缺失', comparison.data['reason'])
            self.assertEqual(comparison.data['items'], [])
            self.assertEqual(self.client.post(self.base + 'baselines/set-from-execution/', {'execution_id': self.run1.pk}, format='json').status_code, 409)
        PerfScenario.objects.filter(pk=self.scene.pk).update(project=self.other)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.base + 'baselines/').data['count'], 0)
        self.assertEqual(PerfBaseline.objects.get(pk=old.pk).metrics, {'avg_rt': 12345})

    def test_baseline_source_is_required_and_cannot_be_removed(self):
        response = self.client.post(self.base + 'baselines/', {'scenario': self.scene.pk, 'metrics': {'avg_rt': 1}}, format='json')
        self.assertEqual(response.status_code, 400)
        made = self.client.post(self.base + 'baselines/set-from-execution/', {'execution_id': self.run1.pk}, format='json')
        response = self.client.patch(self.base + f"baselines/{made.data['id']}/", {'execution': None}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(PerfBaseline.objects.get(pk=made.data['id']).execution_id, self.run1.pk)

    def test_legacy_baseline_with_frozen_compatible_engine_remains_available(self):
        for run, tps in ((self.run1, 2), (self.run2, 4)):
            run.load_snapshot = {'_engine': 'BUILTIN'}
            run.summary = {'total_requests': 5, 'tps': tps, 'avg_rt': 10, 'p95_rt': 20}
            run.save()
        self.assertEqual(self.client.post(self.base + 'baselines/set-from-execution/', {'execution_id': self.run1.pk}, format='json').status_code, 200)
        result = self.client.get(self.base + 'baselines/compare/', {'execution_id': self.run2.pk}).data
        self.assertEqual(result['evaluation'], 'EVALUATED')
        self.assertEqual(next(item for item in result['items'] if item['metric'] == 'tps')['change_pct'], 100)

    def test_baseline_incompatible_or_unknown_frozen_engines_are_not_evaluated(self):
        self.client.post(self.base + 'baselines/set-from-execution/', {'execution_id': self.run1.pk}, format='json')
        for engine in ('BUILTIN', 'JMETER', None):
            with self.subTest(engine=engine):
                self.run2.load_snapshot = {'_engine': engine} if engine else {}
                self.run2.summary = {'total_requests': 5, 'tps': 30, 'avg_rt': 10}
                self.run2.save()
                result = self.client.get(self.base + 'baselines/compare/', {'execution_id': self.run2.pk}).data
                self.assertEqual(result['evaluation'], 'NOT_EVALUATED')
                self.assertIsNone(result['degraded'])
                self.assertEqual(result['items'], [])
                self.assertIn('引擎', result['reason'])
                self.assertEqual(result['baseline_engine'], 'K6')

    def test_reordered_legacy_interfaces_match_unique_names_not_positions(self):
        for run, names, values in ((self.run1, ['alpha', 'beta'], [10, 20]), (self.run2, ['beta', 'alpha'], [200, 100])):
            run.steps_snapshot = [{'name': name, 'method': 'GET'} for name in names]
            run.summary = {'http_total': 2, 'business_total': 2, 'step_metrics': [
                {'step_name': name, 'method': 'GET', 'total': 1, 'avg_rt': value} for name, value in zip(names, values)]}
        rows = build_snapshot([self.run1, self.run2])['step_comparison']
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['step_name']: [v['avg_rt'] for v in row['values']] for row in rows}, {'alpha': [10, 100], 'beta': [20, 200]})
        self.assertTrue(all(row['step_id'] is None and '唯一历史名称' in row['match_basis'] for row in rows))

    def test_ambiguous_legacy_names_remain_independent(self):
        for run in (self.run1, self.run2):
            run.steps_snapshot = [{'name': 'same', 'method': 'GET'}, {'name': 'same', 'method': 'GET'}]
            run.summary = {'http_total': 2, 'business_total': 2, 'step_metrics': [
                {'step_id': f'legacy:{n}', 'step_name': 'same', 'method': 'GET', 'total': 1, 'avg_rt': n} for n in (1, 2)]}
        rows = build_snapshot([self.run1, self.run2])['step_comparison']
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(sum('total' in v for v in row['values']) == 1 for row in rows))

    def test_html_missing_and_null_metrics_remain_uncollected_with_samples(self):
        keys = ('avg_rt', 'p95_rt', 'p99_rt', 'max_rt', 'error_rate', 'business_rps', 'tps', 'peak_tps', 'max_concurrency')
        labels = ('平均业务响应', '累计 P95（估算）', '累计 P99（估算）', '最大业务响应', '业务失败率', '平均业务 RPS', '峰值业务 RPS', '脚本活动用户峰值')
        for explicit_null in (False, True):
            with self.subTest(explicit_null=explicit_null):
                self.run1.summary = {'http_total': 2, 'business_total': 2, 'total_requests': 2, 'success_requests': 2, 'failed_requests': 0}
                if explicit_null:
                    self.run1.summary.update(dict.fromkeys(keys))
                rendered = reporter._render(self.run1, [], [])
                cards = dict(re.findall(r'<div class="card-label">(.*?)</div><div class="card-value[^"]*">(.*?)</div>', rendered))
                self.assertTrue(all(cards[label] == '未采集' for label in labels), cards)
                self.assertEqual(cards['业务成功 / 失败'], '2 / 0')
        self.run1.summary.update(dict.fromkeys(keys, 0))
        rendered = reporter._render(self.run1, [], [])
        cards = dict(re.findall(r'<div class="card-label">(.*?)</div><div class="card-value[^"]*">(.*?)</div>', rendered))
        self.assertEqual(cards['平均业务响应'], '0 ms')
        self.assertEqual(cards['业务失败率'], '0%')
        self.assertEqual(cards['平均业务 RPS'], '0')

    def test_html_chart_missing_null_and_measured_zero_are_distinct(self):
        self.run1.summary['throughput'] = {'version': 'completion_epoch_1s_v1', 'verified': True, 'peak_rps': 0, 'sample_windows': {'2': None, '3': None}}
        samples = [
            {'ts_offset': 0, 'total_requests': 2},
            {'ts_offset': 1, 'total_requests': 2, 'avg_rt': None, 'p95_rt': None, 'error_rate': None, 'tps': None},
            {'ts_offset': 2, 'total_requests': 2, 'avg_rt': 0, 'p95_rt': 0, 'error_rate': 0, 'tps': 0},
            {'ts_offset': 3, 'total_requests': 0, 'avg_rt': 0, 'p95_rt': 0, 'error_rate': 0, 'tps': 0},
        ]
        from apps.perf_testing.services.k6_samples import sample_payload
        from apps.perf_testing.services.k6_throughput import CompletionBuckets
        for index in (2, 3):
            samples[index]['k6_payload'] = sample_payload(dict(samples[index], sample_seq=index, elapsed_seconds=index, window_count=1 if index == 2 else 0, throughput=CompletionBuckets().snapshot()))
        rendered = reporter._render(self.run1, samples, [])
        chart = json.loads(re.search(r'var D = (.*);', rendered).group(1))
        for key in ('avg', 'p95', 'err'):
            self.assertEqual(chart[key], [None, None, 0, None])
        self.assertEqual(chart['tps'], [None, None, 0, 0])

    def test_history_and_list_filter_execution_projects_before_selecting_latest(self):
        self.other.members.add(self.owner)
        self.client.patch(self.base + f'scenarios/{self.scene.pk}/', {'project': self.other.pk}, format='json')
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.base + f'scenarios/{self.scene.pk}/execution-history/').data, [])

        self.assertIsNone(self.client.get(self.base + 'scenarios/', {'project': self.other.pk}).data['results'][0]['last_execution'])
        self.client.force_authenticate(self.owner)
        self.assertEqual(len(self.client.get(self.base + f'scenarios/{self.scene.pk}/execution-history/').data), 2)
        # Revoking the old-project membership must also close the serializer fallback.
        self.project.members.add(self.outsider)
        self.client.force_authenticate(self.outsider)
        from rest_framework.test import APIRequestFactory
        from apps.perf_testing.serializers import PerfScenarioListSerializer
        request = APIRequestFactory().get('/'); request.user = self.outsider
        self.scene.refresh_from_db()
        self.assertIsNotNone(PerfScenarioListSerializer(self.scene, context={'request': request}).data['last_execution'])
        self.project.members.remove(self.outsider)
        self.assertIsNone(PerfScenarioListSerializer(self.scene, context={'request': request}).data['last_execution'])
        self.assertEqual(self.client.get(self.base + f'scenarios/{self.scene.pk}/execution-history/').data, [])

    def test_html_nullable_charts_render_real_isolated_points_without_gap_segments(self):
        from apps.perf_testing.services.k6_samples import sample_payload
        from apps.perf_testing.services.k6_throughput import CompletionBuckets
        fixtures = []
        for engine in ('K6', 'BUILTIN'):
            self.run1.load_snapshot = {'_engine': engine}
            for values, indices in (([None, 50, None], [1]), ([None, 0, None], [1]),
                                    ([0, None, 3], [0, 2]), ([0], [0]), ([None, None, None], [])):
                samples = []
                for i, value in enumerate(values):
                    row = dict(ts_offset=i, total_requests=2, avg_rt=value, p95_rt=value, p99_rt=value,
                               error_rate=value, tps=value, active_users=7)
                    if engine == 'K6':
                        row['k6_payload'] = sample_payload(dict(row, sample_seq=i+1, elapsed_seconds=i,
                            window_count=0 if value is None else 1, throughput=CompletionBuckets().snapshot()))
                    samples.append(row)
                fixtures.append(dict(html=reporter._render(self.run1, samples, []),
                                     indices=indices if engine == 'K6' else [], values=values))
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for real ECharts SVG export regression')
        script = '''
import * as echarts from 'echarts';
import assert from 'node:assert/strict';
const fixtures = FIXTURES;
for (const fixture of fixtures) {
  const options = {};
  const body = fixture.html.match(/<script>\\s*([\\s\\S]*?)<\\/script>/)[1];
  new Function('echarts','document','window',body)(
    {init:el=>({setOption:option=>options[el.id]=option})},
    {getElementById:id=>({id})}, {addEventListener(){}});
  for (const [id, count] of [['c2',3],['c3',1]]) {
    for(let i=0;i<count;i++) {
      const series=options[id].series[i];
      assert.deepEqual(series.data,fixture.values);
      const chart=echarts.init(null,null,{renderer:'svg',ssr:true,width:600,height:300});
      chart.setOption({...options[id],animation:false,legend:{show:false},
        series:[{...series,yAxisIndex:0}],yAxis:{type:'value'}});
      const paths=[...chart.renderToSVGString().matchAll(/<path\\b[^>]*>/g)].map(m=>m[0]);
      chart.dispose();
      const markers=paths.filter(p=>p.includes('ecmeta_data_index=')&&!/matrix\\(0[, ]/.test(p))
        .map(p=>Number(p.match(/ecmeta_data_index="(\\d+)"/)[1]));
      assert.deepEqual(markers,fixture.indices);
      if(fixture.indices.length) {
        const line=paths.filter(p=>p.includes('fill="none"')&&p.includes('stroke="'+series.itemStyle.color+'"'))
          .map(p=>p.match(/ d="([^"]*)"/)[1]).join(' ');
        assert.doesNotMatch(line,/[LC]/);
      }
    }
  }
}
console.log('HTML SVG: 10 actual rendered exports; 40 series checked');
'''.replace('FIXTURES', json.dumps(fixtures))
        result = subprocess.run([node, '--input-type=module', '-'], input=script, text=True,
            encoding='utf-8', capture_output=True, cwd=Path(__file__).resolve().parents[3] / 'frontend', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_history_preserves_missing_and_measured_zero(self):
        self.run1.summary = {'http_total': 2, 'business_total': 0, 'avg_rt': None, 'p95_rt': None, 'error_rate': None}
        self.run1.save()
        self.run2.summary = {'http_total': 2, 'business_total': 2, 'avg_rt': 0, 'p95_rt': 0, 'error_rate': 0, 'tps': 0}
        self.run2.save()
        points = self.client.get(self.base + f'scenarios/{self.scene.pk}/execution-history/').data
        by_id = {point['id']: point for point in points}
        for key in ('tps', 'avg_rt', 'p95_rt', 'error_rate'):
            self.assertIsNone(by_id[self.run1.id][key])
            self.assertEqual(by_id[self.run2.id][key], 0)

    def test_historical_peak_is_unknown_across_read_apis_without_rewriting_storage(self):
        from apps.perf_testing.models import PerfMetricSample
        self.run1.summary.update(peak_tps=22000, adapter_version='0.6.0')
        self.run1.save()
        PerfMetricSample.objects.create(execution=self.run1, ts_offset=1, total_requests=12, tps=22000)
        detail = self.client.get(self.url(self.run1)).data
        self.assertIsNone(detail['summary']['peak_tps'])
        self.assertEqual(detail['summary']['tps'], 3)
        self.assertIn('历史', detail['summary']['throughput_notice'])
        for action in ('samples', 'realtime'):
            response = self.client.get(self.url(self.run1, action)).data
            self.assertIsNone(response['samples'][0]['tps'])
        document = json.loads(self.client.get(self.url(self.run1, 'report'), {'export': 'json'}).content)
        self.assertIsNone(document['summary']['peak_tps'])
        self.assertIn('无法验证', self.client.get(self.url(self.run1, 'report')).content.decode())
        comparison = build_snapshot([self.run1, self.run2])
        self.assertIsNone(comparison['executions'][0]['summary']['peak_tps'])
        self.run1.refresh_from_db()
        self.assertEqual(self.run1.summary['peak_tps'], 22000)
        self.assertEqual(PerfMetricSample.objects.get(execution=self.run1).tps, 22000)

    def test_saved_comparison_read_masks_unverified_peak_without_mutation(self):
        from apps.perf_testing.services.compare_report import normalized_snapshot
        snapshot = {'executions': [{'id': self.run1.id, 'engine': 'K6', 'load_snapshot': {'model': 'CONCURRENCY'},
            'is_reference': True, 'summary': {'tps': 3, 'peak_tps': 22000}, 'delta_pct': {'peak_tps': 0},
            'samples': [{'tps': 22000, 'total_requests': 2}]}]}
        result = normalized_snapshot(snapshot)
        self.assertIsNone(result['executions'][0]['summary']['peak_tps'])
        self.assertIsNone(result['executions'][0]['delta_pct']['peak_tps'])
        self.assertIsNone(result['executions'][0]['samples'][0]['tps'])
        self.assertEqual(snapshot['executions'][0]['summary']['peak_tps'], 22000)

    def test_final_rate_samples_preserve_observation_instead_of_correcting_late_events(self):
        from apps.perf_testing.services.k6_samples import sample_payload
        from apps.perf_testing.services.k6_throughput import CompletionBuckets
        bins = CompletionBuckets(); bins.record(1100)
        payload = sample_payload(dict(sample_seq=1, elapsed_seconds=1.1, window_count=1, throughput=bins.snapshot()))
        bins.record(1200); bins.record(1300)
        self.run1.summary['throughput'] = bins.snapshot()
        samples = reporter.normalized_samples(self.run1, [{'ts_offset':1, 'k6_payload':payload}, {'ts_offset':2,'tps':22000}])
        self.assertEqual([row['tps'] for row in samples],[1,None])
        self.assertEqual(samples[0]['throughput']['latest_bucket_start_ms'],1000)
        self.assertEqual(reporter.normalized_summary(self.run1)['peak_tps'],3)
