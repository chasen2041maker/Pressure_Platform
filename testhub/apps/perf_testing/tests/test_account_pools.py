"""Private versioned account imports and execution bindings; fake data, no traffic."""
import importlib
import json
import tempfile
import shutil
import subprocess
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction, IntegrityError, OperationalError
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.perf_testing import models
from apps.perf_testing.services import executor
from apps.perf_testing.services.k6_execution import load_snapshot


class AccountPoolTests(TestCase):
    def setUp(self):
        self.assertTrue(hasattr(models, 'PerfAccountPoolVersion'), 'immutable account pool version model is missing')
        self.service = importlib.import_module('apps.perf_testing.services.account_pools')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = override_settings(PERF_PRIVATE_ROOT=self.root / 'private', MEDIA_ROOT=self.root / 'media')
        config.enable()
        self.addCleanup(config.disable)
        self.owner = get_user_model().objects.create_user(username='pool-owner')
        self.outside = get_user_model().objects.create_user(username='pool-outsider')
        self.project = models.PerfProject.objects.create(name='pool-project', owner=self.owner)
        self.other = models.PerfProject.objects.create(name='other', owner=self.outside)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = '/api/perf-testing/'

    def upload(self, raw=None, suffix='csv', **kwargs):
        payload = {'project': self.project.id, 'name': 'Fake pool', 'identity_column': 'key',
                   'field_mapping': json.dumps({'account_id': 'key', 'password': 'password'}),
                   'file': SimpleUploadedFile('accounts.' + suffix, raw or b'key,password\nu1,secret-one\nu2,secret-two\n')}
        payload.update(kwargs)
        return self.client.post(self.base + 'account-pools/', payload, format='multipart')

    def make_version(self, **kwargs):
        response = self.upload(**kwargs)
        self.assertEqual(response.status_code, 201, response.data)
        return models.PerfAccountPoolVersion.objects.get(pk=response.data['latest_version']['id'])

    def scene(self, version, **kwargs):
        args = dict(project=self.project, name='frozen', created_by=self.owner, engine='K6',
                    account_pool_version=version, env_config={'base_url': 'http://example.invalid'},
                    load_config={'model': 'CONCURRENCY', 'concurrency': 2, 'duration': 3, 'iterations_per_vu': 10})
        args.update(kwargs)
        scene = models.PerfScenario.objects.create(**args)
        models.PerfScenarioStep.objects.create(scenario=scene, name='business', url='/health')
        return scene

    def test_csv_and_json_are_private_and_all_cells_masked(self):
        for raw, suffix in [(None, 'csv'), (b'[{"key":"u1","password":"secret-one"}]', 'json')]:
            version = self.make_version(raw=raw, suffix=suffix)
            self.assertEqual(version.identity_column, 'key')
            self.assertEqual(len(version.content_hash), 64)
            self.assertFalse(version.pool.file)
            path = self.service.version_path(version)
            self.assertTrue(path.is_relative_to(self.root / 'private'))
            self.assertIn('secret-one', path.read_text())
            for endpoint in ('account-pools/', f'account-pools/{version.pool_id}/',
                             f'account-pool-versions/{version.pk}/', f'account-pool-versions/{version.pk}/preview/'):
                result = self.client.get(self.base + endpoint)
                self.assertEqual(result.status_code, 200)
                self.assertNotIn('secret-one', json.dumps(result.data, default=str))
                self.assertNotIn('u1', json.dumps(result.data, default=str))

    def test_strict_invalid_imports_are_atomic_and_sanitized(self):
        cases = [(b'key,key\nu1,secret-one', 'csv'), (b'key,password\nu1,secret-one\n\n', 'csv'),
                 (b'key,password\nu1,secret-one\nu1,secret-two', 'csv'),
                 (b'key,password\nu1', 'csv'), (b'key,password\nu1,secret-one,extra', 'csv'),
                 (b'key,password\n,secret-one', 'csv'), (b'key,password\nu1,\xff', 'csv'),
                 (b'key,password\nu1,"secret-one', 'csv'),
                 (b'key,password\nu1,sec"ret', 'csv'),
                 (b'[{"key":"u1","password":"secret-one"},{"key":"u2"}]', 'json'),
                 (b'[{"key":"u1","password":null}]', 'json'),
                 (b'[{"key":"u1","key":"u2","password":"secret-one"}]', 'json'),
                 (b'[{"key":"u1","password":{"nested":"secret-one"}}]', 'json'),
                 (b'[{"key":"u1","password":NaN}]', 'json'),
                 (b'[{"key":"u1","password":Infinity}]', 'json'),
                 (b'[]', 'json')]
        for raw, suffix in cases:
            with self.subTest(raw=raw):
                result = self.upload(raw, suffix)
                self.assertEqual(result.status_code, 400, result.data)
                serialized = json.dumps(result.data)
                self.assertNotIn('secret-one', serialized)
                self.assertIn('row', serialized)
        self.assertEqual(models.PerfAccountPoolVersion.objects.count(), 0)
        self.assertEqual(models.PerfDataFile.objects.count(), 0)

    def test_mapping_and_group_selection(self):
        version = self.make_version(raw=b'key,password,team\nu1,secret-one,private-group\nu2,secret-two,second\n', group_column='team')
        self.assertEqual(sum(g['count'] for g in version.groups), 2)
        self.assertNotIn('private-group', json.dumps(version.groups))
        scene = self.scene(version, account_pool_group=version.groups[0]['id'])
        snap = executor.build_snapshot(scene, user=self.owner)
        self.assertEqual(snap['account_pool']['effective_row_count'], 1)
        for mapping in ({'x': 'missing'}, {'vu_id': 'key'}, {'x': 'key', 'y': 'key'}, {}):
            self.assertEqual(self.upload(field_mapping=json.dumps(mapping)).status_code, 400)

    def test_explicit_identity_and_1000_capacity(self):
        version = self.make_version(raw=('key,password\n' + '\n'.join(f'u{i},secret-{i}' for i in range(1000))).encode())
        scene = self.scene(version, load_config={'model': 'CONCURRENCY', 'concurrency': 1000, 'duration': 3})
        from apps.perf_testing.engines.k6_engine import validate_snapshot
        snapshot = executor.build_snapshot(scene, user=self.owner)
        self.assertEqual(validate_snapshot(snapshot), [])
        snapshot['load_config']['concurrency'] = 1001
        self.assertTrue(any('不足' in error for error in validate_snapshot(snapshot)))

    def test_replacement_does_not_change_scene_or_frozen_execution(self):
        version = self.make_version()
        scene = self.scene(version)
        with mock.patch('apps.perf_testing.engines.k6_version', return_value='fixed'):
            execution = executor.create_execution(scene, user=self.owner)
        original = load_snapshot(self.root / 'private', execution.pk)
        response = self.client.post(self.base + f'account-pools/{version.pool_id}/versions/', {
            'identity_column': 'key', 'field_mapping': json.dumps(version.field_mapping),
            'file': SimpleUploadedFile('new.csv', b'key,password\nu3,secret-new\n')}, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        scene.refresh_from_db()
        execution.refresh_from_db()
        self.assertEqual(scene.account_pool_version_id, version.pk)
        self.assertEqual(execution.account_pool_version_id, version.pk)
        self.assertEqual(load_snapshot(self.root / 'private', execution.pk), original)
        self.assertEqual(execution.load_snapshot['_account_pool']['content_hash'], version.content_hash)
        self.assertNotIn('secret-one', json.dumps(execution.load_snapshot))
        usage = self.client.get(self.base + f'account-pool-versions/{version.pk}/usage/')
        self.assertEqual(usage.data['executions'][0]['id'], execution.pk)

    def test_in_use_deletion_and_parent_rollback_preserve_private_file(self):
        version = self.make_version()
        scene = self.scene(version)
        path = self.service.version_path(version)
        self.assertEqual(self.client.delete(self.base + f'account-pool-versions/{version.pk}/').status_code, 409)
        with self.assertRaises(ProtectedError), transaction.atomic():
            version.delete()
        self.assertTrue(path.exists())
        scene.project = self.other
        scene.save(update_fields=['project'])
        response = self.client.delete(self.base + f'projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 409)
        self.assertTrue(models.PerfProject.objects.filter(pk=self.project.pk).exists())
        self.assertTrue(path.exists())
        scene.project = self.project
        scene.save(update_fields=['project'])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(self.base + f'projects/{self.project.pk}/').status_code, 204)
        self.assertFalse(path.exists())

    def test_old_asset_routes_cannot_read_mutate_or_delete_pool(self):
        version = self.make_version()
        for method, tail, payload in [('get', '', None), ('get', 'preview/', None),
                                      ('patch', '', {'file_type': 'CSV'}), ('delete', '', None)]:
            url = self.base + f'data-files/{version.pool_id}/' + tail
            response = getattr(self.client, method)(url, payload, format='json')
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(self.base + 'data-files/?page_size=0').data, [])

    def test_project_acl_and_cross_project_injection(self):
        version = self.make_version()
        scene = self.scene(version)
        self.client.force_authenticate(self.outside)
        for endpoint in (f'account-pools/{version.pool_id}/', f'account-pool-versions/{version.pk}/preview/'):
            self.assertEqual(self.client.get(self.base + endpoint).status_code, 404)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.delete(self.base + f'projects/{self.project.pk}/').status_code, 403)
        other = self.scene(None, project=self.other, created_by=self.outside)
        response = self.client.patch(self.base + f'scenarios/{other.pk}/', {'account_pool_version': version.pk}, format='json')
        self.assertIn(response.status_code, (400, 403))
        with self.assertRaises(self.service.PermissionDenied):
            executor.build_snapshot(scene, user=self.outside)

    def test_conflicts_non_k6_and_illegal_groups_are_rejected(self):
        version = self.make_version()
        scene = self.scene(version)
        for payload in ({'variables': [{'name': 'password', 'value': 'x'}]}, {'engine': 'BUILTIN'},
                        {'account_pool_group': 'not-a-group'}):
            response = self.client.patch(self.base + f'scenarios/{scene.pk}/', payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_versions_reject_mutation_and_tampering(self):
        version = self.make_version()
        version.identity_column = 'password'
        with self.assertRaises(Exception):
            version.save()
        version.refresh_from_db()
        self.service.version_path(version).write_text('{"rows": []}', encoding='utf-8')
        with self.assertRaises(self.service.ValidationError):
            executor.build_snapshot(self.scene(version), user=self.owner)

    def test_inspect_chinese_headers_without_creating_version(self):
        raw = '\ufeff身份,密码\nu1,secret-one\n'.encode('utf-8')
        response = self.client.post(self.base + 'account-pools/inspect/', {
            'project': self.project.pk, 'file': SimpleUploadedFile('fake.csv', raw)}, format='multipart')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertEqual(response.data['columns'], ['身份', '密码'])
        self.assertEqual(response.data['row_count'], 1)
        self.assertNotIn('secret-one', json.dumps(response.data))
        self.assertEqual(models.PerfAccountPoolVersion.objects.count(), 0)
        version = self.make_version(raw=raw, identity_column='身份', field_mapping=json.dumps({'account_id': '身份', 'password': '密码'}))
        self.assertEqual(version.row_count, 1)

    def test_duplicate_header_rows_and_bad_parameter_types_are_safe(self):
        repeated = self.upload(b'key,password\nu1,secret-one\nkey,password\n')
        self.assertEqual(repeated.status_code, 400)
        for field, value in [('identity_column', ['key']), ('group_column', ['key'])]:
            with self.subTest(field=field):
                with self.assertRaises(self.service.ValidationError):
                    self.service.parse_accounts(b'key,password\nu1,secret-one\n', 'csv',
                        value if field == 'identity_column' else 'key', {'x': 'key'},
                        value if field == 'group_column' else '')

    def test_same_vu_keeps_same_row_through_real_javascript_initialization_and_rounds(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime is unavailable for offline JavaScript validation')
        version = self.make_version()
        snapshot = executor.build_snapshot(self.scene(version), user=self.owner)
        snapshot['steps'][0]['headers'] = {'X-Account': '${account_id}', 'X-Password': '${password}'}
        key = snapshot['account_pool']['data_key']
        snapshot['csv_files'] = {key: key}
        source = Path(__file__).resolve().parents[1] / 'engines' / 'k6_script.js'
        harness = r'''
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const code = fs.readFileSync(input.source, 'utf8').replace(/^import .*;$/gm, '')
  .replace('export default function ()', 'function tick()').replace(/export /g, '');
for (let vu = 1; vu <= 2; vu++) {
 const seen = [], logs = [];
 const context = {__ENV:{K6_TESTHUB_CONFIG:'config'}, exec:{vu:{idInTest:vu,iterationInScenario:0}},
   open:(path)=>JSON.stringify(path === 'config' ? input.snapshot : input.rows),
   SharedArray:function(name,read){return read()}, sleep:()=>{}, console:{log:(x)=>logs.push(x)},
   http:{request:(method,url,body,params)=>{seen.push(params.headers);return {status:200}}}};
 vm.createContext(context); vm.runInContext(code, context);
 for(let round=0; round<10; round++) {context.exec.vu.iterationInScenario=round;vm.runInContext('tick()',context)}
 assert.equal(seen.length,10);
 for (const headers of seen) {assert.equal(headers['x-account'],input.rows[vu-1].key);assert.equal(headers['x-password'],input.rows[vu-1].password)}
 assert.ok(!logs.join('').includes('secret-'));
}
process.stdout.write('two VUs, ten rounds, stable paired columns, no credentials in events');
'''
        result = subprocess.run([node, '-e', harness], input=json.dumps({'source': str(source),
            'snapshot': snapshot, 'rows': snapshot['csv_data'][key]['rows']}), text=True,
            encoding='utf-8', capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_business_csv_and_environment_mapping_conflicts(self):
        version = self.make_version()
        data_file = models.PerfDataFile.objects.create(project=self.project, name='Business CSV', file_type='CSV',
            file=SimpleUploadedFile('items.csv', b'product\np1\np2\n'), uploaded_by=self.owner)
        scene = self.scene(version, variables=[{'name': 'product', 'type': 'CSV', 'data_file_id': data_file.pk, 'column': 'product'}])
        from apps.perf_testing.engines.k6_engine import validate_snapshot
        self.assertEqual(validate_snapshot(executor.build_snapshot(scene, user=self.owner)), [])
        env = models.PerfEnvironment.objects.create(project=self.project, created_by=self.owner, name='conflict',
            variables=[{'name': 'password', 'value': 'environment-secret', 'secret': True}])
        scene.environment = env
        scene.save(update_fields=['environment'])
        result = executor.preflight(scene, user=self.owner)
        self.assertFalse(result['passed'])
        self.assertNotIn('environment-secret', json.dumps(result))

    def test_o_excl_collision_keeps_existing_private_file(self):
        version = self.make_version()
        path = self.service.version_path(version)
        original = path.read_bytes()
        with mock.patch.object(self.service.uuid, 'uuid4', return_value=mock.Mock(hex=path.stem)):
            response = self.upload()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(path.read_bytes(), original)

    def test_completed_run_keeps_version_and_active_project_delete_is_rejected(self):
        version = self.make_version()
        scene = self.scene(version)
        with mock.patch('apps.perf_testing.engines.k6_version', return_value='fixed'):
            execution = executor.create_execution(scene, user=self.owner)
        scene.account_pool_version = None
        scene.save(update_fields=['account_pool_version'])
        path = self.service.version_path(version)
        self.assertEqual(self.client.delete(self.base + f'projects/{self.project.pk}/').status_code, 400)
        execution.status = 'COMPLETED'
        execution.save(update_fields=['status'])
        self.assertEqual(self.client.delete(self.base + f'account-pool-versions/{version.pk}/').status_code, 409)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(self.base + f'projects/{self.project.pk}/').status_code, 204)
        self.assertFalse(path.exists())

    def test_members_admins_and_anonymous_pool_access(self):
        version = self.make_version()
        member = get_user_model().objects.create_user(username='pool-member')
        admin = get_user_model().objects.create_user(username='pool-admin', is_staff=True)
        self.project.members.add(member)
        for user in (member, admin):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.base + f'account-pools/{version.pool_id}/').status_code, 200)
            self.assertEqual(self.upload().status_code, 201)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.base + 'account-pools/').status_code, (401, 403))

    def test_shared_array_prepare_writes_private_rows_once(self):
        version = self.make_version()
        snapshot = executor.build_snapshot(self.scene(version), user=self.owner)
        from apps.perf_testing.engines.k6_engine import K6Engine
        work = self.root / 'private' / 'prepared'
        with mock.patch('apps.perf_testing.engines.k6_engine.k6_docker.runner_mode', return_value='NATIVE'), \
                mock.patch('apps.perf_testing.engines.k6_engine.is_available', return_value=True):
            engine = K6Engine(snapshot, work_dir=work)
            engine.prepare()
        files = list(work.glob('csv-*.private.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text())[0]['password'], 'secret-one')
        self.assertTrue(all(path.is_relative_to(self.root / 'private') for path in files))

    def test_outsider_project_patch_and_put_cannot_grant_pool_access(self):
        version = self.make_version()
        self.client.force_authenticate(self.outside)
        for method in ('patch', 'put'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.base + f'projects/{self.project.pk}/',
                    {'name': 'Intrusion', 'member_ids': [self.outside.pk]}, format='json')
                self.assertEqual(response.status_code, 403)
                self.project.refresh_from_db()
                self.assertEqual(self.project.name, 'pool-project')
                self.assertFalse(self.project.members.filter(pk=self.outside.pk).exists())
                pool_url = self.base + f'account-pools/{version.pool_id}/'
                self.assertEqual(self.client.get(pool_url).status_code, 404)
                self.assertEqual(self.client.post(pool_url + 'versions/', {
                    'identity_column': 'key', 'field_mapping': json.dumps({'account_id': 'key'}),
                    'file': SimpleUploadedFile('new.csv', b'key\nu3\n')}, format='multipart').status_code, 404)
                self.assertEqual(self.client.delete(pool_url).status_code, 404)
        self.assertEqual(self.client.patch(self.base + f'projects/{self.project.pk}/',
            {'description': 'Intrusion'}, format='json').status_code, 403)

    def test_member_can_edit_fields_and_resend_unchanged_members_but_not_add_remove(self):
        member = get_user_model().objects.create_user(username='project-member')
        peer = get_user_model().objects.create_user(username='project-peer')
        self.project.members.set([member, peer])
        self.client.force_authenticate(member)
        url = self.base + f'projects/{self.project.pk}/'
        for method in ('patch', 'put'):
            for payload in ({'name': 'Allowed'}, {'name': 'Allowed', 'member_ids': [peer.pk, member.pk]}):
                with self.subTest(method=method, payload=payload):
                    self.assertEqual(getattr(self.client, method)(url, payload, format='json').status_code, 200)
                    self.assertCountEqual(self.project.members.values_list('pk', flat=True), [member.pk, peer.pk])
            for members in ([member.pk, peer.pk, self.outside.pk], [member.pk], []):
                with self.subTest(method=method, members=members):
                    response = getattr(self.client, method)(url, {'name': 'Forbidden', 'member_ids': members}, format='json')
                    self.assertEqual(response.status_code, 403)
                    self.project.refresh_from_db()
                    self.assertEqual(self.project.name, 'Allowed')
                    self.assertCountEqual(self.project.members.values_list('pk', flat=True), [member.pk, peer.pk])

    def test_owner_and_admin_can_add_and_remove_project_members(self):
        admin = get_user_model().objects.create_user(username='project-admin', is_staff=True)
        for user in (self.owner, admin):
            self.client.force_authenticate(user)
            for method in ('patch', 'put'):
                for members in ([self.outside.pk], []):
                    with self.subTest(user=user.pk, method=method, members=members):
                        response = getattr(self.client, method)(self.base + f'projects/{self.project.pk}/',
                            {'name': 'Managed', 'member_ids': members}, format='json')
                        self.assertEqual(response.status_code, 200)
                        self.assertCountEqual(self.project.members.values_list('pk', flat=True), members)

    @override_settings(DEBUG=True)
    def test_import_database_and_unknown_errors_are_safe_atomic_and_retryable(self):
        self.client.raise_request_exception = False
        for error_type in (OperationalError, IntegrityError, RuntimeError):
            with self.subTest(error_type=error_type.__name__):
                before = models.PerfAccountPoolVersion.objects.count()
                pools_before = models.PerfDataFile.objects.count()
                files_before = set((self.root / 'private' / 'account-pools').glob('*.json'))
                with mock.patch.object(models.PerfAccountPoolVersion, 'save', side_effect=error_type('secret-one')), \
                        mock.patch('apps.perf_testing.views.logger.warning') as logged:
                    response = self.upload()
                self.assertEqual(response.status_code, 503)
                self.assertIn('application/json', response['Content-Type'])
                self.assertEqual(response.data['retryable'], True)
                self.assertEqual(response['Retry-After'], '1')
                self.assertNotIn(b'secret-one', response.content)
                self.assertNotIn(str(self.root).encode(), response.content)
                self.assertNotIn('secret-one', str(logged.call_args_list))
                self.assertIn(error_type.__name__, str(logged.call_args_list))
                self.assertEqual(models.PerfAccountPoolVersion.objects.count(), before)
                self.assertEqual(models.PerfDataFile.objects.count(), pools_before)
                self.assertEqual(set((self.root / 'private' / 'account-pools').glob('*.json')), files_before)
                self.assertEqual(self.upload().status_code, 201)

    @override_settings(DEBUG=True)
    def test_new_version_database_failure_preserves_existing_data_then_retry_succeeds(self):
        version = self.make_version()
        self.client.raise_request_exception = False
        original = self.service.version_path(version).read_bytes()
        url = self.base + f'account-pools/{version.pool_id}/versions/'
        def post_version():
            return self.client.post(url, {'identity_column': 'key', 'field_mapping': json.dumps({'id': 'key'}),
                'file': SimpleUploadedFile('new.csv', b'key,password\nu3,secret-new\n')}, format='multipart')
        with mock.patch.object(models.PerfAccountPoolVersion, 'save', side_effect=OperationalError('secret-new')):
            response = post_version()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'secret-new', response.content)
        self.assertEqual(version.pool.account_versions.count(), 1)
        self.assertEqual(self.service.version_path(version).read_bytes(), original)
        self.assertEqual(len(list((self.root / 'private' / 'account-pools').glob('*.json'))), 1)
        response = post_version()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['version'], 2)

    def test_user_detail_aliases_reject_outsider_writes_but_keep_directory_reads(self):
        version = self.make_version()
        path = self.service.version_path(version)
        self.client.force_authenticate(self.outside)
        for prefix in ('/api/users/', '/api/auth/'):
            with self.subTest(prefix=prefix):
                url = prefix + f'users/{self.owner.pk}/'
                self.assertEqual(self.client.get(prefix + 'users/').status_code, 200)
                self.assertEqual(self.client.get(url).status_code, 200)
                for method in ('patch', 'put', 'delete'):
                    with self.subTest(method=method):
                        response = getattr(self.client, method)(url,
                            {'username': self.owner.username, 'first_name': 'Forbidden'}, format='json')
                        self.assertEqual(response.status_code, 403)
                        self.owner.refresh_from_db()
                        self.assertEqual(self.owner.first_name, '')
                        self.assertTrue(models.PerfAccountPoolVersion.objects.filter(pk=version.pk).exists())
                        self.assertTrue(path.exists())

    def test_user_detail_aliases_allow_self_updates_and_admin_writes(self):
        admin = get_user_model().objects.create_user(username='user-admin', is_staff=True)
        superuser = get_user_model().objects.create_user(username='user-super', is_superuser=True)
        for prefix in ('/api/users/', '/api/auth/'):
            for actor in (self.owner, admin, superuser):
                self.client.force_authenticate(actor)
                for method in ('patch', 'put'):
                    response = getattr(self.client, method)(prefix + f'users/{self.owner.pk}/',
                        {'username': self.owner.username, 'first_name': 'Allowed'}, format='json')
                    self.assertEqual(response.status_code, 200)
                if actor != self.owner:
                    target = get_user_model().objects.create_user(username=f'target-{actor.pk}-{prefix.split("/")[2]}')
                    self.assertEqual(self.client.delete(prefix + f'users/{target.pk}/').status_code, 204)

    @override_settings(DEBUG=True)
    def test_authorized_user_delete_with_pool_reference_returns_safe_conflict(self):
        version = self.make_version()
        path = self.service.version_path(version)
        self.scene(version, project=self.other, created_by=self.outside)
        admin = get_user_model().objects.create_user(username='delete-admin', is_staff=True)
        self.client.raise_request_exception = False
        for actor in (self.owner, admin):
            self.client.force_authenticate(actor)
            for prefix in ('/api/users/', '/api/auth/'):
                with self.subTest(actor=actor.pk, prefix=prefix):
                    with self.captureOnCommitCallbacks(execute=True):
                        response = self.client.delete(prefix + f'users/{self.owner.pk}/')
                    self.assertEqual(response.status_code, 409)
                    self.assertIn('application/json', response['Content-Type'])
                    self.assertNotIn(b'secret-one', response.content)
                    self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())
                    self.assertTrue(models.PerfAccountPoolVersion.objects.filter(pk=version.pk).exists())
                    self.assertTrue(path.exists())

    def formal_execute(self, scene):
        with mock.patch('apps.perf_testing.engines.k6_available', return_value=True), \
                mock.patch('apps.perf_testing.engines.k6_version', return_value='offline-fixed'):
            return self.client.post(self.base + f'scenarios/{scene.pk}/execute/', {}, format='json')

    @override_settings(DEBUG=True)
    def test_formal_k6_insert_failure_is_safe_without_start_or_record(self):
        version = self.make_version()
        original = self.service.version_path(version).read_bytes()
        scene = self.scene(version)
        self.client.raise_request_exception = False
        with mock.patch.object(models.PerfExecution.objects, 'create', side_effect=OperationalError('secret-one')), \
                mock.patch.object(executor, 'spawn_execution') as spawn, \
                mock.patch('apps.perf_testing.views.logger.warning') as logged:
            response = self.formal_execute(scene)
        self.assertEqual(response.status_code, 500)
        self.assertIn('application/json', response['Content-Type'])
        self.assertFalse(response.data['retryable'])
        self.assertNotIn(b'secret-one', response.content)
        self.assertNotIn(str(self.root).encode(), response.content)
        self.assertNotIn('secret-one', str(logged.call_args_list))
        self.assertIn('OperationalError', str(logged.call_args_list))
        spawn.assert_not_called()
        self.assertEqual(models.PerfExecution.objects.count(), 0)
        self.assertEqual(self.service.version_path(version).read_bytes(), original)

    @override_settings(DEBUG=True)
    def test_formal_k6_private_snapshot_failure_is_safe_and_does_not_auto_retry(self):
        version = self.make_version()
        scene = self.scene(version)
        self.client.raise_request_exception = False
        with mock.patch('apps.perf_testing.services.k6_execution.save_snapshot',
                        side_effect=RuntimeError(f'secret-one {self.root}')), \
                mock.patch.object(executor, 'spawn_execution') as spawn:
            response = self.formal_execute(scene)
        self.assertEqual(response.status_code, 500)
        self.assertIn('application/json', response['Content-Type'])
        self.assertFalse(response.data['retryable'])
        self.assertNotIn(b'secret-one', response.content)
        self.assertNotIn(str(self.root).encode(), response.content)
        self.assertEqual(models.PerfExecution.objects.get().status, 'FAILED')
        spawn.assert_not_called()

    @override_settings(DEBUG=True)
    def test_formal_k6_popen_failure_keeps_public_execution_error_safe(self):
        version = self.make_version()
        scene = self.scene(version)
        self.client.raise_request_exception = False
        with mock.patch.object(executor.subprocess, 'Popen', side_effect=OSError(f'secret-one {self.root}')) as popen:
            response = self.formal_execute(scene)
        self.assertEqual(response.status_code, 500)
        self.assertIn('application/json', response['Content-Type'])
        self.assertFalse(response.data['retryable'])
        execution = models.PerfExecution.objects.get()
        self.assertEqual(execution.status, 'FAILED')
        self.assertNotIn('secret-one', execution.error_message)
        self.assertNotIn(str(self.root), execution.error_message)
        self.assertNotIn(b'secret-one', response.content)
        popen.assert_called_once()

    @override_settings(DEBUG=True)
    def test_formal_k6_failure_after_mocked_spawn_preserves_active_record_and_blocks_duplicate(self):
        scene = self.scene(self.make_version())
        self.client.raise_request_exception = False
        original_save = models.PerfExecution.save
        def fail_pid_save(instance, *args, **kwargs):
            if 'process_pid' in (kwargs.get('update_fields') or []):
                raise OperationalError(f'secret-one {self.root}')
            return original_save(instance, *args, **kwargs)
        with mock.patch.object(executor.subprocess, 'Popen', return_value=mock.Mock(pid=12345)) as popen, \
                mock.patch.object(models.PerfExecution, 'save', new=fail_pid_save):
            response = self.formal_execute(scene)
            duplicate = self.formal_execute(scene)
        self.assertEqual(response.status_code, 500)
        self.assertIn('application/json', response['Content-Type'])
        self.assertFalse(response.data['retryable'])
        self.assertNotIn(b'secret-one', response.content)
        self.assertNotIn(str(self.root).encode(), response.content)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(models.PerfExecution.objects.count(), 1)
        self.assertEqual(models.PerfExecution.objects.get().status, 'PENDING')
        popen.assert_called_once()

    def test_formal_execute_success_and_expected_failures_keep_contracts(self):
        scene = self.scene(self.make_version())
        with mock.patch.object(executor, 'spawn_execution') as spawn:
            self.client.force_authenticate(self.outside)
            self.assertEqual(self.formal_execute(scene).status_code, 404)
            self.client.force_authenticate(self.owner)
            from rest_framework.exceptions import PermissionDenied, ValidationError
            for error, status_code in ((PermissionDenied('denied'), 403), (ValidationError('invalid'), 400)):
                with mock.patch.object(executor, 'start_execution', side_effect=error):
                    self.assertEqual(self.formal_execute(scene).status_code, status_code)
            with mock.patch.object(executor, 'start_execution', return_value=(None, {'passed': False})):
                self.assertEqual(self.formal_execute(scene).status_code, 400)
            response = self.formal_execute(scene)
            self.assertEqual(response.status_code, 201)
            self.assertIn('execution', response.data)
            self.assertTrue(response.data['preflight']['passed'])
            self.assertEqual(self.formal_execute(scene).status_code, 409)
            spawn.assert_called_once()

    @override_settings(DEBUG=True)
    def test_k6_preflight_failure_after_reading_accounts_has_safe_response(self):
        version = self.make_version()
        scene = self.scene(version)
        original = self.service.version_path(version).read_bytes()
        self.client.raise_request_exception = False
        url = self.base + f'scenarios/{scene.pk}/preflight/'
        with mock.patch.object(self.service, 'read_version', wraps=self.service.read_version) as read, \
                mock.patch.object(models.PerfExecution.objects, 'filter',
                                  side_effect=OperationalError(f'secret-one {self.root}')), \
                mock.patch.object(executor, 'spawn_execution') as spawn, \
                mock.patch('apps.perf_testing.views.logger.warning') as logged:
            response = self.client.post(url, {}, format='json')
        read.assert_called_once()
        self.assertEqual(response.status_code, 503)
        self.assertIn('application/json', response['Content-Type'])
        self.assertFalse(response.data['passed'])
        self.assertNotIn(b'secret-one', response.content)
        self.assertNotIn(str(self.root).encode(), response.content)
        self.assertNotIn('secret-one', str(logged.call_args_list))
        self.assertIn('OperationalError', str(logged.call_args_list))
        spawn.assert_not_called()
        self.assertEqual(models.PerfExecution.objects.count(), 0)
        self.assertEqual(self.service.version_path(version).read_bytes(), original)
        with mock.patch('apps.perf_testing.engines.k6_available', return_value=True):
            successful = self.client.post(url, {}, format='json')
        self.assertEqual(successful.status_code, 200)
        self.assertTrue(successful.data['passed'])

    def test_k6_preflight_preserves_permission_and_validation_statuses(self):
        scene = self.scene(self.make_version())
        from rest_framework.exceptions import PermissionDenied, ValidationError
        for error, status_code in ((PermissionDenied('denied'), 403), (ValidationError('invalid'), 400)):
            with mock.patch.object(executor, 'preflight', side_effect=error):
                response = self.client.post(self.base + f'scenarios/{scene.pk}/preflight/', {}, format='json')
            self.assertEqual(response.status_code, status_code)

    def test_builtin_formal_preflight_and_execute_keep_existing_contract(self):
        scene = self.scene(None, engine='BUILTIN')
        with mock.patch.object(executor, 'spawn_execution') as spawn:
            preflight = self.client.post(self.base + f'scenarios/{scene.pk}/preflight/', {}, format='json')
            response = self.formal_execute(scene)
        self.assertEqual(preflight.status_code, 200)
        self.assertTrue(preflight.data['passed'])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.data), {'execution', 'preflight'})
        self.assertNotIn('_engine', response.data['execution']['load_snapshot'])
        spawn.assert_called_once()
