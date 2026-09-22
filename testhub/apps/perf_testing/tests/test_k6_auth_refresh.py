"""Execute the shipped JS in isolated VU contexts; never use a live database."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.perf_testing.engines.k6_engine import K6Engine, validate_snapshot
from apps.perf_testing.services.auth_profiles import normalize_profile, public_profile


def auth_snapshot(transport: str = 'BEARER') -> dict:
    extractors = [{'name': name, 'type': 'JSON_PATH', 'expr': '$.data.' + path}
                  for name, path in [('access_token', 'token'), ('refresh_token', 'refresh_token'),
                                     ('expires_in', 'expires_in')]]
    profile = {'mode': 'LOGIN', 'transport': transport, 'access_token_variable': 'access_token',
               'refresh_token_variable': 'refresh_token', 'expires_in_variable': 'expires_in',
               'cookie_name': 'sid', 'max_attempts': 2,
               'login': {'method': 'POST', 'url': '/auth/login', 'body_type': 'JSON',
                         'body': '{"user_id":"{{user_id}}","password":"{{password}}"}',
                         'assertions': [{'type': 'JSON_PATH', 'expr': '$.code', 'expected': 0}],
                         'extractors': deepcopy(extractors)},
               'refresh': {'method': 'POST', 'url': '/auth/refresh', 'body_type': 'JSON',
                           'body': '{"refresh_token":"{{refresh_token}}"}',
                           'extractors': deepcopy(extractors)}}
    return {'load_config': {'model': 'CONCURRENCY', 'concurrency': 2, 'duration': 30, 'iterations_per_vu': 3},
            'runtime_config': {'auth_profile': profile},
            'env_config': {'base_url': 'http://fixture', 'headers': {'Authorization': 'Bearer SHARED_SECRET',
                           'Cookie': 'sid=SHARED_COOKIE', 'X-Test-VU': '{{vu_id}}'}},
            'variables': [{'name': name, 'type': 'CSV', 'data_file_id': 1, 'column': name}
                          for name in ('user_id', 'password')],
            'csv_data': {'1': {'rows': [{'user_id': 'u1', 'password': 'PASSWORD_SECRET_1'},
                                      {'user_id': 'u2', 'password': 'PASSWORD_SECRET_2'}]}},
            'steps': [{'id': 1, 'name': '资源列表', 'url': '/auth/items',
                       'extractors': [{'name': 'item_id', 'expr': '$.data.items[0].id'}]},
                      {'id': 2, 'name': '资源详情', 'url': '/auth/items/{{item_id}}'}]}


def static_refresh_snapshot(transport: str = 'BEARER') -> dict:
    snapshot = auth_snapshot(transport)
    profile = snapshot['runtime_config']['auth_profile']
    profile['mode'] = 'STATIC'
    profile.pop('login')
    profile['cookie_variable'] = 'session_cookie'
    for name, prefix in [('access_token', 'PRESET_TOKEN_SECRET_'),
                         ('session_cookie', 'PRESET_COOKIE_SECRET_'), ('refresh_token', 'PRESET_REFRESH_SECRET_')]:
        snapshot['variables'].append({'name': name, 'type': 'CSV', 'data_file_id': 1, 'column': name})
        for row in snapshot['csv_data']['1']['rows']:
            row[name] = prefix + row['user_id']
    return snapshot


HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let source = fs.readFileSync(input.script, 'utf8').replace(/^import .*;$/mg, '')
  .replace('export default function ()', 'function iteration()').replace(/export /g, '');
const events = [], requests = [], cookieSets = [], sessions = {}, refreshed = {}, business = {}, vus = [];
let tick = 100000, serial = 0;
class Jar {
 constructor() { this.cookies = {}; }
 set(url, name, val) { this.cookies[name] = [val]; cookieSets.push({url,name,val}); }
 cookiesForURL(url) { return this.cookies; }
}
function response(status, data) { return {status, error_code:0, json:()=>data}; }
for (let vu = 1; vu <= 2; vu++) {
  const legacyJar = new Jar();
  sessions['PRESET_TOKEN_SECRET_u'+vu] = 'u'+vu;
  sessions['PRESET_COOKIE_SECRET_u'+vu] = 'u'+vu;
  sessions['PRESET_REFRESH_SECRET_u'+vu] = 'u'+vu;
  const execution = {vu:{idInTest:vu, iterationInScenario:0}};
  const config = input.config;
  const sandbox = {__ENV:{K6_TESTHUB_CONFIG:'config'}, exec:execution,
    open:name=>JSON.stringify(name === 'config' ? config : input.rows),
    SharedArray:function(name, fn) { return fn(); }, sleep:()=>{},
    Date:{now:()=>tick}, console:{log:line=>events.push(JSON.parse(line.split('TESTHUB_K6_EVENT ')[1]))},
    http:{CookieJar:Jar, request:(method,url,body,params)=>{
      const path = new URL(url).pathname, data = body ? JSON.parse(body) : {};
      const jar = params.jar || legacyJar;
      const wireCookie = (params.headers.cookie ? params.headers.cookie + '; ' : '')
        + (jar.cookiesForURL(url).sid || []).map(value=>'sid='+value).join('; ');
      const cookie = wireCookie.split(';').map(value=>value.trim()).find(value=>value.startsWith('sid='))?.slice(4);
      const token = (params.headers.authorization || '').replace('Bearer ', '');
      const row = {vu,path,data,cookie,token,headers:params.headers}; requests.push(row);
      if (path === '/auth/login' || path === '/auth/refresh') {
        let user;
        if (path.endsWith('login')) {
          user = data.user_id || params.headers['x-user-id'] || token;
          if (input.case === 'login_fail' && vu === 1) return response(401, {});
        } else {
          user = sessions[data.refresh_token || params.headers['x-refresh-token'] || token];
          if (!user) throw Error('invalid refresh identity');
          if (input.case === 'refresh_fail' && vu === 1) return response(401, {});
          refreshed[vu] = (refreshed[vu] || 0) + 1;
        }
        if (user !== 'u'+vu) throw Error('crossed identity');
        const id = ++serial, tok = 'TOKEN_SECRET_'+user+'_'+id, ref = 'REFRESH_SECRET_'+user+'_'+id;
        sessions[tok] = user; sessions[ref] = user;
        const sid = 'COOKIE_SECRET_'+user+'_'+id; sessions[sid] = user;
        if (input.case !== 'missing_cookie') jar.set(url,'sid',sid);
        return response(200, {code:0,data:{token:input.case==='empty_login'&&vu===1?'':tok,
          refresh_token:ref,expires_in:input.case==='expiry'?1:input.case==='zero_expiry'?0:
            input.case==='negative_expiry'?-1:input.case==='invalid_expiry'?'invalid':3600}});
      }
      const transport = config.runtime_config.auth_profile?.transport;
      const user = sessions[transport === 'COOKIE' ? cookie : token];
      if (transport && (user !== 'u'+vu || (config.runtime_config.auth_profile.mode === 'LOGIN' && sessions[cookie] !== 'u'+vu))) throw Error('crossed business identity');
      if (input.case === 'unauthorized_twice') return response(401, {});
      if (['401','refresh_fail'].includes(input.case) && path !== '/auth/items' && !refreshed[vu]) return response(401, {});
      business[vu] = (business[vu] || 0) + 1;
      if (path === '/auth/items') return response(200,{code:0,data:{items:input.case==='empty_resource' && execution.vu.iterationInScenario === 1?[]:[{id:'item-u'+vu+'-'+execution.vu.iterationInScenario}]}});
      if (!path.endsWith('item-u'+vu+'-'+execution.vu.iterationInScenario)) throw Error('stale resource');
      return response(200, {code:0});
    }} };
  vm.createContext(sandbox); vm.runInContext(source, sandbox); vus.push({sandbox, execution});
}
for (let round=0; round<3; round++) {
  for (const {sandbox,execution} of vus) { execution.vu.iterationInScenario=round; vm.runInContext('iteration()',sandbox); }
  if(input.case==='expiry') tick += 1100;
}
process.stdout.write(JSON.stringify({events,requests,refreshed,cookieSets}));
"""


class AuthProfileTests(SimpleTestCase):
    def assert_catalog_binding(self, snapshot: dict, ready: bool) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch
        from apps.perf_testing.services.api_catalog import REQUEST_FIELDS, scenario_readiness
        fields = {field: snapshot['steps'][0].get(field, {} if field in ('headers', 'params') else
                  [] if field in ('files', 'assertions', 'extractors') else
                  '' if field in ('url', 'body') else 'NONE') for field in REQUEST_FIELDS}
        step = SimpleNamespace(**fields, pk=1, enabled=True, is_setup=False, source_request=None,
                               source_metadata={}, preparation={})

        class Steps:
            def select_related(self, *_args): return self
            def order_by(self, *_args): return [step]

        scenario = SimpleNamespace(runtime_config=snapshot['runtime_config'], steps=Steps(),
                                   created_by=None, load_config=snapshot['load_config'])
        with patch('apps.perf_testing.services.api_catalog.allow_legacy_edit', return_value=True):
            result = scenario_readiness(scenario, {key: snapshot[key]
                for key in ('env_config', 'variables', 'csv_data')})[0]
        self.assertEqual(result['ready'], ready, result)

    def test_auth_binding_rejects_keys_assertions_incidental_and_conflicting_values(self) -> None:
        from apps.perf_testing.services.auth_profiles import request_input_issues
        for transport in ('BEARER', 'COOKIE'):
            for mode in ('LOGIN', 'STATIC'):
                for phase in (('login', 'refresh') if mode == 'LOGIN' else ('refresh',)):
                    name = 'user_id' if phase == 'login' else 'refresh_token'
                    invalid_inputs = [
                        {'body': '{}'},
                        {'body': json.dumps({name: 'SHARED'})},
                        {'body': json.dumps({'{{' + name + '}}': 'SHARED'})},
                        {'body': json.dumps({name: 'SHARED', 'trace': '{{' + name + '}}'})},
                        {'body': json.dumps({name: 'SHARED'}), 'headers': {'X-Trace': '{{' + name + '}}'}},
                        {'body': json.dumps({name: '{{password}}'})},
                        {'body': json.dumps({name: 'SHARED'}), 'headers': {'Authorization': 'Bearer {{' + name + '}}'}},
                        {'body': json.dumps({name: 'prefix-{{' + name + '}}'})},
                    ]
                    for changes in invalid_inputs:
                        snap = auth_snapshot(transport) if mode == 'LOGIN' else static_refresh_snapshot(transport)
                        step = snap['runtime_config']['auth_profile'][phase]
                        step.update(changes)
                        step['assertions'] = [{'type': 'JSON_PATH', 'expr': '$.trace', 'expected': '{{' + name + '}}'}]
                        with self.subTest(transport=transport, mode=mode, phase=phase, changes=changes):
                            self.assertTrue(validate_snapshot(snap))
                            issues = request_input_issues(snap['runtime_config']['auth_profile'], snap)
                            self.assertTrue(any(issue['code'] == 'auth_input_binding'
                                                and issue['field'] == 'auth_profile.' + phase for issue in issues))
                            self.assert_catalog_binding(snap, False)

    def test_auth_binding_accepts_explicit_body_and_header_credentials(self) -> None:
        for transport in ('BEARER', 'COOKIE'):
            for mode in ('LOGIN', 'STATIC'):
                for placeholder in ('{{%s}}', '${%s}'):
                    for location in ('body', 'header', 'authorization', 'cookie'):
                        snap = auth_snapshot(transport) if mode == 'LOGIN' else static_refresh_snapshot(transport)
                        profile = snap['runtime_config']['auth_profile']
                        for phase in ('login', 'refresh'):
                            if phase not in profile:
                                continue
                            name = 'user_id' if phase == 'login' else 'refresh_token'
                            value = placeholder % name
                            step = profile[phase]
                            step['body'] = '{}'
                            if location == 'body':
                                step['body'] = json.dumps({'credentials': {name: value}})
                            else:
                                key = {'header': 'X-' + name.replace('_', '-'),
                                       'authorization': 'Authorization', 'cookie': 'Cookie'}[location]
                                step['headers'] = {key: ('Bearer ' if location == 'authorization' else
                                                        'sid=' if location == 'cookie' else '') + value}
                        with self.subTest(transport=transport, mode=mode, placeholder=placeholder, location=location):
                            self.assertEqual(validate_snapshot(snap), [])
                            self.assert_catalog_binding(snap, True)

    def test_auth_json_field_names_are_static_at_every_depth(self) -> None:
        from apps.perf_testing.services.auth_profiles import profile_steps, request_binding_variables
        for transport in ('BEARER', 'COOKIE'):
            for mode, phase in (('LOGIN', 'login'), ('LOGIN', 'refresh'), ('STATIC', 'refresh')):
                for syntax in ('{{credential_field}}', '${credential_field}'):
                    for depth in ('root', 'object', 'list'):
                        snap = auth_snapshot(transport) if mode == 'LOGIN' else static_refresh_snapshot(transport)
                        profile = normalize_profile(snap['runtime_config']['auth_profile'])
                        snap['runtime_config']['auth_profile'] = profile
                        name = 'user_id' if phase == 'login' else 'refresh_token'
                        snap['variables'].append({'name': 'credential_field', 'value': name})
                        body = {name: '{{' + name + '}}'}
                        if depth == 'root':
                            body[syntax] = 'SHARED'
                        else:
                            nested = {syntax: 'SHARED'}
                            body['metadata'] = nested if depth == 'object' else [{'nested': [nested]}]
                        profile[phase]['body'] = json.dumps(body)
                        with self.subTest(transport=transport, mode=mode, phase=phase, syntax=syntax, depth=depth):
                            with self.assertRaisesMessage(ValidationError, '字段名不支持变量'):
                                normalize_profile(profile)
                            self.assertTrue(validate_snapshot(snap))
                            self.assert_catalog_binding(snap, False)
                            self.assertEqual(request_binding_variables(profile, phase, snap), [])
                            compiled = next(step for step in profile_steps(profile, snap) if step['auth_phase'] == phase)
                            self.assertEqual(compiled['auth_input_variables'], [])
                            profile[phase]['body'] = json.dumps({name: '{{' + name + '}}',
                                'metadata': [{'trace': syntax}]})
                            self.assertEqual(validate_snapshot(snap), [])
                            self.assert_catalog_binding(snap, True)

    def test_login_request_must_reference_selected_identity_not_password_or_assertions(self) -> None:
        for assertion_only in (False, True):
            snap = auth_snapshot()
            login = snap['runtime_config']['auth_profile']['login']
            login['body'] = '{"user_id":"u1","password":"{{password}}"}'
            for row in snap['csv_data']['1']['rows']:
                row['password'] = 'COMMON_PASSWORD'
            if assertion_only:
                login['assertions'].append({'type': 'JSON_PATH', 'expr': '$.user_id', 'expected': '{{user_id}}'})
            with self.subTest(assertion_only=assertion_only):
                self.assertTrue(any('身份' in error for error in validate_snapshot(snap)))

    def test_custom_identity_sources_and_pool_identity_mapping(self) -> None:
        for pool_mode in (False, True):
            snap = auth_snapshot()
            snap['variables'][0]['name'] = 'customer_login'
            snap['runtime_config']['auth_profile']['login']['body'] = '{"principal":"{{customer_login}}","password":"{{password}}"}'
            if pool_mode:
                snap['account_pool'] = {'data_key': '1', 'identity_column': 'user_id',
                                        'field_mapping': {'customer_login': 'user_id', 'password': 'password'}}
            else:
                snap['runtime_config']['account_identity_variable'] = 'customer_login'
            with self.subTest(pool_mode=pool_mode):
                self.assertEqual(validate_snapshot(snap), [])
                custom_header = deepcopy(snap)
                custom_header['runtime_config']['auth_profile']['login'].update(
                    body='{}', headers={'X-Customer-Login': '${customer_login}'})
                self.assertEqual(validate_snapshot(custom_header), [])
                snap['runtime_config']['auth_profile']['login']['body'] = '{"principal":"constant","password":"{{password}}"}'
                self.assertTrue(any('身份' in error for error in validate_snapshot(snap)))

    def test_login_and_refresh_input_dependency_checks(self) -> None:
        from apps.perf_testing.services.auth_profiles import request_input_issues
        for phase in ('login', 'refresh'):
            snap = auth_snapshot()
            profile = normalize_profile(snap['runtime_config']['auth_profile'])
            profile[phase]['headers'] = {'X-Input': '{{missing_input}}'}
            with self.subTest(phase=phase):
                issues = request_input_issues(profile, snap)
                self.assertTrue(any(issue['variable'] == 'missing_input'
                                    and issue['field'] == f'auth_profile.{phase}.headers' for issue in issues))

    def test_explicit_pool_login_identity_takes_precedence_and_must_be_unique_same_pool(self) -> None:
        snap = auth_snapshot()
        snap['account_pool'] = {'data_key': '1', 'identity_column': 'user_id', 'field_mapping': {'user_id': 'user_id'}}
        snap['variables'].append({'name': 'phone', 'type': 'CSV', 'data_file_id': 1, 'column': 'phone'})
        for i, row in enumerate(snap['csv_data']['1']['rows']):
            row['phone'] = 'phone-' + str(i)
        snap['runtime_config']['account_identity_variable'] = 'phone'
        snap['runtime_config']['auth_profile']['login']['body'] = '{"principal":"{{phone}}","password":"{{password}}"}'
        self.assertEqual(validate_snapshot(snap), [])
        snap['csv_data']['1']['rows'][1]['phone'] = 'phone-0'
        self.assertTrue(any('身份' in error for error in validate_snapshot(snap)))
        snap['csv_data']['1']['rows'][1]['phone'] = 'phone-1'
        snap['csv_data']['2'] = deepcopy(snap['csv_data']['1'])
        snap['variables'][-1]['data_file_id'] = 2
        self.assertTrue(any('身份' in error for error in validate_snapshot(snap)))

    def test_static_refresh_rotates_token_but_cannot_overwrite_pool_identity(self) -> None:
        snap = auth_snapshot()
        profile = snap['runtime_config']['auth_profile']
        profile['mode'] = 'STATIC'
        profile.pop('login')
        profile['access_token_variable'] = 'token'
        profile['refresh']['extractors'][0]['name'] = 'token'
        for name in ('token', 'refresh_token'):
            snap['variables'].append({'name': name, 'type': 'CSV', 'data_file_id': 1, 'column': name})
            for row in snap['csv_data']['1']['rows']:
                row[name] = name + '-' + row['user_id']
        self.assertEqual(validate_snapshot(snap), [])
        snap['account_pool'] = {'data_key': '1', 'identity_column': 'user_id',
                               'field_mapping': {item['name']: item['column'] for item in snap['variables']}}
        self.assertEqual(validate_snapshot(snap), [])
        profile['refresh']['extractors'].append({'name': 'user_id', 'expr': '$.data.user_id'})
        self.assertTrue(any('不能覆盖' in error for error in validate_snapshot(snap)))

    def test_static_rotating_credentials_reject_every_stable_identity_alias(self) -> None:
        from apps.perf_testing.services.auth_profiles import normalize_profile, validate_binding
        for transport in ('BEARER', 'COOKIE'):
            for source in ('csv_default', 'csv_explicit', 'pool', 'pool_explicit'):
                snap = auth_snapshot(transport)
                profile = snap['runtime_config']['auth_profile']
                profile['mode'] = 'STATIC'
                profile.pop('login')
                profile['cookie_variable'] = 'session_cookie'
                for name in ('access_token', 'refresh_token', 'session_cookie', 'phone'):
                    snap['variables'].append({'name': name, 'type': 'CSV', 'data_file_id': 1, 'column': name})
                    for row in snap['csv_data']['1']['rows']:
                        row[name] = name + '-' + row['user_id']
                snap['variables'].append({'name': 'identity_alias', 'type': 'CSV', 'data_file_id': 1, 'column': 'user_id'})
                if source.startswith('pool'):
                    snap['account_pool'] = {'data_key': '1', 'identity_column': 'user_id',
                        'field_mapping': {item['name']: item['column'] for item in snap['variables']}}
                if source.endswith('explicit'):
                    snap['runtime_config']['account_identity_variable'] = 'phone' if source.startswith('pool') else 'user_id'
                with self.subTest(transport=transport, source=source, role='valid_control'):
                    self.assertEqual(validate_snapshot(snap), [])
                for name in ('user_id', 'identity_alias'):
                    for role in ('access_token_variable', 'refresh_token_variable', 'cookie_variable'):
                        bad = deepcopy(snap)
                        bad_profile = bad['runtime_config']['auth_profile']
                        previous = bad_profile[role]
                        bad_profile[role] = name
                        if role == 'refresh_token_variable':
                            bad_profile['refresh']['body'] = '{"refresh_token":"{{' + name + '}}"}'
                        for rule in bad_profile['refresh']['extractors']:
                            if rule['name'] == previous:
                                rule['name'] = name
                        if role == 'cookie_variable':
                            bad_profile['refresh']['extractors'].append({'name': name, 'expr': '$.data.sid'})
                        with self.subTest(transport=transport, source=source, name=name, role=role):
                            self.assertTrue(any('稳定身份' in error for error in validate_snapshot(bad)))
                            self.assertTrue(validate_binding(normalize_profile(bad_profile), bad))

    def test_valid_profile_and_bounded_schema(self) -> None:
        self.assertEqual(validate_snapshot(auth_snapshot()), [])
        profile = auth_snapshot()['runtime_config']['auth_profile']
        for field, value in [('max_attempts', 4), ('retry_delay_ms', -1), ('mode', 'SHARED'),
                             ('refresh_on_status', [500]), ('refresh_on_status', []),
                             ('access_token_variable', '__proto__')]:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                normalize_profile({**profile, field: value})
        for url in ('https://other/login', '//other/login', '/login?password=secret', '/{{username}}'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                normalize_profile({**profile, 'login': {**profile['login'], 'url': url}})

    def test_no_shared_credentials_or_output_overwrites(self) -> None:
        snap = auth_snapshot()
        snap['runtime_config']['auth_profile'] = {'mode': 'STATIC', 'access_token_variable': 'token'}
        snap['variables'].append({'name': 'token', 'value': 'SHARED_SECRET'})
        self.assertTrue(any('预存认证' in s for s in validate_snapshot(snap)))
        snap = auth_snapshot()
        snap['variables'].append({'name': 'access_token', 'value': 'SHARED_SECRET'})
        self.assertTrue(any('不能覆盖' in s for s in validate_snapshot(snap)))
        snap = auth_snapshot()
        snap['steps'][0]['url'] = 'http://other/auth/items'
        self.assertTrue(any('同一服务' in s for s in validate_snapshot(snap)))
        snap = auth_snapshot()
        snap['env_config']['base_url'] = 'http://user:password@fixture'
        self.assertTrue(any('内嵌认证' in s for s in validate_snapshot(snap)))

    def test_mask_round_trip_retains_secrets_and_normalizes(self) -> None:
        from apps.perf_testing.serializers import PerfScenarioSerializer
        from types import SimpleNamespace
        profile = normalize_profile(auth_snapshot()['runtime_config']['auth_profile'])
        profile['login']['body'] = '{"password":"LITERAL_PASSWORD_SECRET","user_id":"{{user_id}}"}'
        profile['login']['headers']['X-Private'] = 'PRIVATE_HEADER_SECRET'
        profile['login']['assertions'][0]['expected'] = 'PRIVATE_ASSERT_SECRET'
        masked = public_profile(profile)
        self.assertNotIn('SECRET', json.dumps(masked))
        serializer = PerfScenarioSerializer(instance=SimpleNamespace(engine='K6', runtime_config={'auth_profile': profile}), partial=True)
        restored = serializer.to_internal_value({'runtime_config': {'auth_profile': masked}})['runtime_config']['auth_profile']
        self.assertEqual(restored, profile)


class AuthRuntimeTests(SimpleTestCase):
    def harness(self, case: str = 'success', transport: str = 'BEARER',
                snapshot: dict | None = None, invalid: bool = False) -> tuple[K6Engine, dict]:
        snap = deepcopy(snapshot) if snapshot is not None else auth_snapshot(transport)
        if case in ('cross_origin', 'userinfo_url'):
            snap['steps'][0]['url'] = '{{target}}'
            snap['variables'].append({'name': 'target', 'value': 'http://other/auth/items' if case == 'cross_origin'
                                     else 'http://user:password@fixture/auth/items'})
        if case == 'static_base_override':
            snap['variables'].append({'name': 'base_url', 'value': 'http://other'})
        if case == 'legacy':
            login = snap['runtime_config'].pop('auth_profile')['login']
            snap['steps'].insert(0, {**login, 'is_setup': True, 'name': 'legacy login'})
            for step in snap['steps'][1:]:
                step['headers'] = {'Authorization': 'Bearer {{access_token}}'}
        if case in ('static', 'static_base_override'):
            snap['runtime_config']['auth_profile'] = {'mode': 'STATIC', 'transport': transport,
                'access_token_variable': 'access_token', 'cookie_variable': 'session_cookie', 'cookie_name': 'sid'}
            for name, prefix in [('access_token', 'PRESET_TOKEN_SECRET_'), ('session_cookie', 'PRESET_COOKIE_SECRET_')]:
                snap['variables'].append({'name': name, 'type': 'CSV', 'data_file_id': 1, 'column': name})
                for row in snap['csv_data']['1']['rows']:
                    row[name] = prefix + row['user_id']
        if invalid:
            self.assertTrue(validate_snapshot(snap))
        else:
            self.assertEqual(validate_snapshot(snap), [])
        engine = K6Engine(snap)
        config = {**snap, 'steps': engine.steps, 'csv_files': {'1': 'rows'},
                  'runtime_config': {'auth_profile': engine.auth_profile}}
        script = Path(__file__).resolve().parents[1] / 'engines' / 'k6_script.js'
        node = shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
        result = subprocess.run([node, '-e', HARNESS], input=json.dumps({'case': case, 'config': config,
            'rows': snap['csv_data']['1']['rows'], 'script': str(script)}), capture_output=True, text=True,
            encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        for event in output['events']:
            engine._consume_event(event)
        self.assertNotIn('SECRET', json.dumps(output['events']))
        return engine, output

    def test_auth_header_inputs_login_and_refresh_with_own_credential(self) -> None:
        for transport in ('BEARER', 'COOKIE'):
            for mode in ('LOGIN', 'STATIC'):
                for syntax in ('{{%s}}', '${%s}'):
                    snap = auth_snapshot(transport) if mode == 'LOGIN' else static_refresh_snapshot(transport)
                    profile = snap['runtime_config']['auth_profile']
                    if mode == 'LOGIN':
                        profile['login']['body'] = '{}'
                        profile['login']['headers'] = {'X-User-Id': syntax % 'user_id'}
                    profile['refresh']['body'] = '{}'
                    profile['refresh']['headers'] = {'Authorization': 'Bearer ' + syntax % 'refresh_token'}
                    with self.subTest(transport=transport, mode=mode, syntax=syntax):
                        engine, output = self.harness('401', snapshot=snap)
                        self.assertEqual(engine._auth_counts['refresh']['success'], 2)
                        self.assertEqual(engine.collector.total, 14)
                        self.assertEqual(engine._auth_failed_vus, set())
                        refresh_calls = [row for row in output['requests'] if row['path'] == '/auth/refresh']
                        self.assertEqual(len(refresh_calls), 2)
                        self.assertTrue(all('u' + str(row['vu']) in row['headers']['authorization']
                                            for row in refresh_calls))

    def test_invalid_refresh_template_is_closed_before_refresh_http(self) -> None:
        for transport in ('BEARER', 'COOKIE'):
            for mode in ('LOGIN', 'STATIC'):
                snap = auth_snapshot(transport) if mode == 'LOGIN' else static_refresh_snapshot(transport)
                snap['runtime_config']['auth_profile']['refresh']['body'] = '{"refresh_token":"SHARED"}'
                with self.subTest(transport=transport, mode=mode):
                    engine, output = self.harness('401', snapshot=snap, invalid=True)
                    self.assertEqual(engine._auth_failed_vus, {1, 2})
                    self.assertEqual(engine.collector.total, 4)
                    self.assertFalse(any(row['path'] == '/auth/refresh' for row in output['requests']))

    def test_unvalidated_dynamic_auth_keys_fail_closed_at_runtime(self) -> None:
        from unittest.mock import patch
        for transport in ('BEARER', 'COOKIE'):
            for mode, phase in (('LOGIN', 'login'), ('LOGIN', 'refresh'), ('STATIC', 'refresh')):
                snap = auth_snapshot(transport) if mode == 'LOGIN' else static_refresh_snapshot(transport)
                profile = normalize_profile(snap['runtime_config']['auth_profile'])
                snap['runtime_config']['auth_profile'] = profile
                name = 'user_id' if phase == 'login' else 'refresh_token'
                snap['variables'].append({'name': 'credential_field', 'value': name})
                profile[phase]['body'] = json.dumps({name: '{{' + name + '}}', '${credential_field}': 'SHARED'})
                with self.subTest(transport=transport, mode=mode, phase=phase), \
                        patch('apps.perf_testing.engines.k6_engine.auth_profiles.normalize_profile', return_value=profile):
                    # Bypass normalization to verify the independent private-step compilation guard.
                    engine, output = self.harness('401', snapshot=snap, invalid=True)
                    self.assertEqual(engine._auth_failed_vus, {1, 2})
                    self.assertFalse(any(row['path'] == '/auth/' + phase for row in output['requests']))
                    self.assertEqual(engine.collector.total, 0 if phase == 'login' else 4)

    def test_business_json_dynamic_keys_and_values_remain_supported(self) -> None:
        for syntax in ('{{payload_key}}', '${payload_key}'):
            snap = auth_snapshot()
            snap['variables'].append({'name': 'payload_key', 'value': 'trace'})
            snap['steps'][0].update(body_type='JSON', body=json.dumps({syntax: '{{user_id}}'}))
            with self.subTest(syntax=syntax):
                engine, output = self.harness(snapshot=snap)
                self.assertEqual(engine.collector.total, 12)
                self.assertEqual(engine._auth_failed_vus, set())
                self.assertTrue(all(row['data'] == {'trace': 'u' + str(row['vu'])}
                                    for row in output['requests'] if row['path'] == '/auth/items'))

    def test_final_header_names_preserve_vu_ownership_and_non_auth_headers(self) -> None:
        for transport in ('BEARER', 'COOKIE'):
            for mode in ('LOGIN', 'STATIC'):
                for syntax in ('{{%s}}', '${%s}'):
                    snap = auth_snapshot(transport) if mode == 'LOGIN' else static_refresh_snapshot(transport)
                    snap['variables'].extend({'name': name, 'value': value} for name, value in
                        [('CookieHeader', 'cOoKiE'), ('AuthHeader', 'AUTHorization'), ('TraceHeader', 'X-Trace')])
                    snap['env_config']['headers'].update({syntax % 'CookieHeader': 'sid=SHARED_COOKIE',
                        syntax % 'AuthHeader': 'Bearer SHARED_TOKEN', syntax % 'TraceHeader': 'environment'})
                    snap['steps'].insert(0, {'name': 'protected setup', 'url': '/auth/items', 'is_setup': True})
                    for step in snap['steps']:
                        step['headers'] = {syntax % 'CookieHeader': 'sid=SHARED_COOKIE',
                            syntax % 'AuthHeader': 'Bearer SHARED_TOKEN', syntax % 'TraceHeader': '{{user_id}}'}
                    with self.subTest(transport=transport, mode=mode, syntax=syntax):
                        engine, output = self.harness(snapshot=snap)
                        self.assertEqual(engine.collector.total, 12)
                        self.assertEqual(engine._auth_failed_vus, set())
                        self.assertEqual(engine._runtime_failures, 0)
                        for row in output['requests']:
                            self.assertTrue(all(key == key.lower() for key in row['headers']))
                            self.assertNotIn('cookie', row['headers'])
                            self.assertNotIn('SHARED', json.dumps(row))
                            if row['path'] not in ('/auth/login', '/auth/refresh'):
                                self.assertEqual(row['headers']['x-trace'], 'u' + str(row['vu']))
                                if transport == 'COOKIE':
                                    self.assertNotIn('authorization', row['headers'])

    def test_invalid_rendered_header_does_not_reach_http(self) -> None:
        for name, value in [('Invalid Header', 'safe'), ('X-Test', 'unsafe\r\nvalue')]:
            snap = auth_snapshot()
            snap['variables'].append({'name': 'header_name', 'value': name})
            snap['variables'].append({'name': 'header_value', 'value': value})
            snap['steps'][0]['headers'] = {'{{header_name}}': '{{header_value}}'}
            with self.subTest(name=name):
                engine, output = self.harness(snapshot=snap)
                self.assertEqual(engine.collector.total, 0)
                self.assertEqual(engine._auth_failed_vus, {1, 2})
                self.assertTrue(all(row['path'] == '/auth/login' for row in output['requests']))

    def test_overridden_and_discarded_environment_headers_are_not_request_inputs(self) -> None:
        snap = auth_snapshot()
        snap['env_config']['headers'].update({'X-Tenant': '{{absent_tenant}}',
                                             'Authorization': 'Bearer {{absent_shared_token}}'})
        for step in snap['steps'] + [snap['runtime_config']['auth_profile'][phase] for phase in ('login', 'refresh')]:
            step['headers'] = {'x-tenant': '{{user_id}}'}
        engine, output = self.harness('401', snapshot=snap)
        self.assertEqual(engine._auth_failed_vus, set())
        self.assertEqual(engine._runtime_failures, 0)
        self.assertTrue(all(row['headers']['x-tenant'] == 'u' + str(row['vu']) for row in output['requests']))

    def test_two_vus_token_cookie_and_resources_remain_isolated(self) -> None:
        for transport in ('BEARER', 'COOKIE'):
            with self.subTest(transport=transport):
                engine, output = self.harness(transport=transport)
                self.assertEqual(engine._http_total, 14)
                self.assertEqual(engine.collector.total, 12)
                self.assertEqual(engine._runtime_failures, 0)
                self.assertEqual(engine._completed_iterations, 6)
                self.assertEqual(engine._auth_failed_vus, set())
                for row in output['requests']:
                    self.assertNotIn('SHARED', json.dumps(row))
                self.assertEqual(engine._auth_counts['login']['success'], 2)

    def test_rendered_cross_origin_and_userinfo_never_reach_http(self) -> None:
        for case in ('cross_origin', 'userinfo_url'):
            with self.subTest(case=case):
                engine, output = self.harness(case)
                self.assertEqual(engine.collector.total, 0)
                self.assertEqual(engine._runtime_failures, 2)
                self.assertEqual(engine._auth_failed_vus, {1, 2})
                self.assertTrue(all(row['path'] == '/auth/login' for row in output['requests']))
        engine, output = self.harness('static_base_override', 'COOKIE')
        self.assertEqual(engine._auth_failed_vus, {1, 2})
        self.assertEqual(output['requests'], [])
        self.assertEqual(output['cookieSets'], [])

    def test_legacy_setup_and_explicit_static_token_cookie(self) -> None:
        for case, transport in [('legacy', 'BEARER'), ('static', 'BEARER'), ('static', 'COOKIE')]:
            with self.subTest(case=case, transport=transport):
                engine, _ = self.harness(case, transport)
                self.assertEqual(engine.collector.total, 12)
                self.assertEqual(engine.collector.failed, 0)
                self.assertEqual(engine._completed_iterations, 6)
                self.assertEqual(engine._runtime_failures, 0)

    def test_401_is_counted_once_then_only_own_identity_refreshed(self) -> None:
        engine, output = self.harness('401')
        self.assertEqual(engine._http_total, 18)
        self.assertEqual(engine.collector.total, 14)
        self.assertEqual(engine._auth_counts['refresh']['success'], 2)
        self.assertEqual(engine.collector.failed, 2)
        self.assertEqual(engine._runtime_failures, 0)
        self.assertEqual(engine._completed_iterations, 6)

    def test_expiry_refresh_before_business(self) -> None:
        engine, output = self.harness('expiry')
        self.assertEqual(engine._auth_counts['refresh']['success'], 4)
        self.assertEqual(engine.collector.total, 12)
        self.assertEqual(engine.collector.failed, 0)

    def test_invalid_expiry_closes_identity_before_any_business(self) -> None:
        for case in ('zero_expiry', 'negative_expiry', 'invalid_expiry'):
            with self.subTest(case=case):
                engine, _ = self.harness(case)
                self.assertEqual(engine.collector.total, 0)
                self.assertEqual(engine._auth_counts['login']['failed'], 4)
                self.assertEqual(engine._auth_failed_vus, {1, 2})

    def test_failed_login_or_empty_token_blocks_only_failed_vu(self) -> None:
        for case in ('login_fail', 'empty_login'):
            with self.subTest(case=case):
                engine, output = self.harness(case)
                self.assertEqual(engine._auth_counts['login']['failed'], 2)
                self.assertEqual(engine._auth_failed_vus, {1})
                self.assertEqual(engine.collector.total, 6)
                self.assertFalse(any(r['vu'] == 1 and '/items' in r['path'] for r in output['requests']))

    def test_failed_refresh_bounded_and_no_later_business(self) -> None:
        engine, output = self.harness('refresh_fail')
        self.assertEqual(engine._auth_counts['refresh']['failed'], 2)
        self.assertEqual(engine._auth_counts['refresh']['success'], 1)
        self.assertEqual(engine._auth_failed_vus, {1})
        self.assertEqual(engine.collector.total, 9)
        self.assertEqual(engine.collector.failed, 2)
        self.assertEqual(sum(r['vu'] == 1 and '/items' in r['path'] for r in output['requests']), 2)

    def test_second_401_closes_identity_without_refresh_loop(self) -> None:
        engine, _ = self.harness('unauthorized_twice')
        self.assertEqual(engine.collector.total, 4)
        self.assertEqual(engine.collector.failed, 4)
        self.assertEqual(engine._auth_counts['refresh']['success'], 2)
        self.assertEqual(engine._auth_failed_vus, {1, 2})

    def test_missing_cookie_and_stale_resources_cannot_pass(self) -> None:
        engine, _ = self.harness('missing_cookie', 'COOKIE')
        self.assertEqual(engine.collector.total, 0)
        self.assertEqual(engine._auth_counts['login']['failed'], 4)
        engine, output = self.harness('empty_resource')
        self.assertEqual(engine.collector.total, 6)
        self.assertEqual(engine._runtime_failures, 2)
        self.assertEqual(sum('/auth/items/item-' in r['path'] for r in output['requests']), 2)

    def test_public_csv_and_summary_count_inflight_refresh_without_payloads(self) -> None:
        engine, output = self.harness('401')
        with tempfile.TemporaryDirectory() as tmp:
            engine = K6Engine(auth_snapshot(), raw_csv_path=str(Path(tmp) / 'raw.csv.gz'))
            engine._open_raw_writer()
            for event in output['events']:
                engine._consume_event(event)
            engine._consume_event({'kind': 'request_started', 'vu': 1, 'step': 3})
            engine._close_raw_writer()
            report = engine.collect()
            text = gzip.open(engine.raw_csv_path, 'rt', encoding='utf-8').read()
            self.assertNotIn('SECRET', text + json.dumps(report))
            self.assertEqual(report['summary']['auth_requests']['refresh']['incomplete'], 1)
            self.assertEqual(report['summary']['http_incomplete'], 1)
            self.assertEqual(report['summary']['business_incomplete'], 0)
            self.assertIn('refresh', text)


class AuthPersistenceTests(TestCase):
    def test_serializer_masked_update_and_private_execution_freeze(self) -> None:
        from unittest.mock import patch
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory
        from apps.perf_testing.models import PerfProject, PerfScenarioStep
        from apps.perf_testing.serializers import PerfScenarioSerializer
        from apps.perf_testing.services import executor
        from apps.perf_testing.services.k6_execution import load_snapshot
        user = get_user_model().objects.create_user(username='auth-persistence')
        project = PerfProject.objects.create(name='auth-persistence', owner=user)
        request = APIRequestFactory().post('/')
        request.user = user
        profile = auth_snapshot()['runtime_config']['auth_profile']
        profile['login']['body'] = '{"user_id":"{{user_id}}","password":"LITERAL_PASSWORD_SECRET"}'
        data = {'project': project.pk, 'name': 'auth scenario', 'engine': 'K6',
                'runtime_config': {'auth_profile': profile},
                'env_config': {'base_url': 'http://fixture', 'headers': {'Authorization': 'ENV_SECRET'}}}
        serializer = PerfScenarioSerializer(data=data, context={'request': request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        scenario = serializer.save()
        public = PerfScenarioSerializer(scenario, context={'request': request}).data
        self.assertNotIn('SECRET', json.dumps(public))
        update = PerfScenarioSerializer(scenario, data={'runtime_config': public['runtime_config']},
                                        partial=True, context={'request': request})
        self.assertTrue(update.is_valid(), update.errors)
        scenario = update.save()
        self.assertIn('LITERAL_PASSWORD_SECRET', scenario.runtime_config['auth_profile']['login']['body'])
        PerfScenarioStep.objects.create(scenario=scenario, name='business', url='/items')
        with tempfile.TemporaryDirectory() as tmp, override_settings(PERF_PRIVATE_ROOT=tmp), \
                patch('apps.perf_testing.engines.k6_version', return_value='fixed'):
            execution = executor.create_execution(scenario, user=user)
            frozen = load_snapshot(tmp, execution.pk)
            self.assertIn('LITERAL_PASSWORD_SECRET', json.dumps(frozen))
            self.assertNotIn('SECRET', json.dumps([execution.load_snapshot, execution.steps_snapshot]))
            scenario.runtime_config['auth_profile']['login']['body'] = '{}'
            scenario.save()
            self.assertEqual(frozen, load_snapshot(tmp, execution.pk))

    def test_catalog_bearer_and_cookie_readiness_requires_bound_profile(self) -> None:
        from unittest.mock import patch
        from types import SimpleNamespace
        from apps.perf_testing.services.api_catalog import REQUEST_FIELDS, scenario_readiness
        for transport in ('BEARER', 'COOKIE'):
            snap = auth_snapshot(transport)
            metadata = {'gaps': [{'code': 'authentication', 'field': 'auth', 'message': 'required'}],
                        'security': [{'user': []}], 'security_schemes': {'user':
                            {'type': 'http', 'scheme': 'bearer'} if transport == 'BEARER' else
                            {'type': 'apiKey', 'in': 'cookie', 'name': 'sid'}}}
            fields = {field: snap['steps'][0].get(field, {} if field in ('headers', 'params') else
                       [] if field == 'files' else '' if field in ('url', 'body') else 'NONE') for field in REQUEST_FIELDS}
            fields['extractors'] = []
            step = SimpleNamespace(**fields, is_setup=False, enabled=True, source_request=None,
                                   source_metadata=metadata, preparation={}, pk=1)
            class Steps:
                def select_related(self, *_args): return self
                def order_by(self, *_args): return [step]
            scenario = SimpleNamespace(runtime_config=snap['runtime_config'], steps=Steps(), created_by=None)
            resolved = {key: snap[key] for key in ('env_config', 'variables', 'csv_data')}
            resolved['env_config']['headers'] = {}
            with patch('apps.perf_testing.services.api_catalog.allow_legacy_edit', return_value=True):
                self.assertTrue(scenario_readiness(scenario, resolved)[0]['ready'])
                resolved['variables'] = [item for item in snap['variables'] if item['name'] != 'password']
                missing = scenario_readiness(scenario, resolved)[0]
                self.assertFalse(missing['ready'])
                self.assertTrue(any(issue.get('variable') == 'password'
                    and issue['field'] == 'auth_profile.login.body' for issue in missing['gaps']))
                resolved['variables'] = []
                self.assertFalse(scenario_readiness(scenario, resolved)[0]['ready'])


class StartupIsolationTests(SimpleTestCase):
    def test_exact_test_command_skips_cleanup_but_normal_testhub_path_does_not(self) -> None:
        import os
        import sys
        from unittest.mock import patch
        import apps.perf_testing as app_module
        from apps.perf_testing.apps import PerfTestingConfig
        config = PerfTestingConfig('apps.perf_testing', app_module)
        for command, expected in [('test', False), ('runserver', True)]:
            with self.subTest(command=command), patch.dict(os.environ, {'RUN_MAIN': 'true'}), \
                    patch.object(sys, 'argv', ['C:/example/testhub/manage.py', command]), \
                    patch('apps.perf_testing.services.cleanup.reap_stale_executions') as reap:
                config.ready()
                if expected:
                    reap.assert_called_once_with(startup=True)
                else:
                    reap.assert_not_called()
