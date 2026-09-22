"""Bounded debug diagnostics from the shipped script; no HTTP or live database."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.perf_testing.engines.k6_engine import K6Engine, _debug_response, _debug_auth_outputs
from apps.perf_testing.tests.test_k6_auth_refresh import auth_snapshot

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.script, 'utf8').replace(/^import .*;$/mg, '')
  .replace('export default function ()', 'function iteration()').replace(/export /g, '');
const events = [], calls = [];
const sandbox = { __ENV: {K6_TESTHUB_CONFIG:'config'}, exec:{vu:{idInTest:1,iterationInScenario:0}},
  open: name => JSON.stringify(name === 'config' ? input.config : input.rows),
  SharedArray: function(name, load) { return load(); }, sleep:()=>{},
  console:{log: line => events.push(JSON.parse(line.slice('TESTHUB_K6_EVENT '.length)))},
  http: {CookieJar:class { cookiesForURL() { return input.cookies === false ? {} : {sid:['COOKIE_SECRET']}; } },
    request:(method,url,body,params)=>{
      calls.push(url);
      const row = input.responses.shift() || {};
      if (row.throw) throw Error('PASSWORD_SECRET must never escape');
      return {status:row.status ?? 200, error_code:row.error_code || 0,
        headers:{'Content-Type':row.type || 'application/json'}, body:row.body,
        json:()=>JSON.parse(row.body)};
    }} };
vm.runInNewContext(source + '\niteration();', sandbox);
process.stdout.write(JSON.stringify({events, count:calls.length}));
"""


class DebugDetailsTests(SimpleTestCase):
    def test_invalid_json_reason_does_not_contaminate_status_mismatch(self) -> None:
        summary, output = self.run_script([{'body': 'invalid json', 'status': 200}], [
            {'id': 17, 'url': '/x', 'assertions': [
                {'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'},
                {'type': 'STATUS_CODE', 'expected': 201}]}])
        row = summary['debug_details']['steps'][0]
        self.assertEqual([item['result'] for item in row['assertions']], ['invalid_json', 'mismatch'])
        self.assertEqual(summary['failed_requests'], 1)
        self.assertEqual(summary['business_total'], 1)
        self.assertEqual(output['count'], 1)

    def test_identity_variable_validation_preserves_other_engines(self) -> None:
        from rest_framework.exceptions import ValidationError
        from apps.perf_testing.serializers import PerfScenarioSerializer
        value = {'account_identity_variable': 'legacy unused value'}
        for engine in (None, 'LOCUST'):
            data = {'runtime_config': value, **({'engine': engine} if engine else {})}
            self.assertEqual(PerfScenarioSerializer(partial=True).to_internal_value(data)['runtime_config'], value)
        with self.assertRaises(ValidationError):
            PerfScenarioSerializer(partial=True).to_internal_value({'engine': 'K6', 'runtime_config': value})

    def test_contains_checks_actual_html_without_json_or_response_leak(self) -> None:
        for body, expected, passed in [('<html>BODY_SECRET</html>', '<html', True),
                                       ('<html>BODY_SECRET</html>', '<missing', False),
                                       ('<html>BODY_SECRET</html>', ' <html', False),
                                       (None, '<html', False)]:
            with self.subTest(body=body, expected=expected):
                summary, output = self.run_script([{'body': body, 'type': 'text/html'}], [
                    {'id': 17, 'url': '/document', 'assertions': [
                        {'type': 'STATUS_CODE', 'expected': 200},
                        {'type': 'CONTAINS', 'expected': expected}]}])
                row = summary['debug_details']['steps'][0]
                self.assertEqual([item['result'] for item in row['assertions']],
                                 ['passed', 'passed' if passed else 'mismatch'])
                self.assertEqual(summary['success_requests'], int(passed))
                self.assertEqual(summary['failed_requests'], int(not passed))
                self.assertEqual(summary['business_total'], 1)
                self.assertEqual(row['response']['state'], 'non_json_omitted')
                self.assertNotIn('BODY_SECRET', json.dumps([summary, output]))

    def test_contains_cannot_accept_invalid_expected_or_hide_failed_http(self) -> None:
        for expected in (None, '', ' \n', 0, False, [], {}):
            with self.subTest(expected=expected):
                summary, _ = self.run_script([{'body': '<html>BODY_SECRET</html>', 'type': 'text/html'}], [
                    {'id': 17, 'url': '/document', 'assertions': [{'type': 'CONTAINS', 'expected': expected}]}])
                self.assertEqual(summary['failed_requests'], 1)
                self.assertEqual(summary['debug_details']['steps'][0]['assertions'][0]['result'], 'mismatch')
        summary, output = self.run_script([{'body': '<html>BODY_SECRET</html>', 'type': 'text/html', 'status': 503}], [
            {'id': 17, 'url': '/document', 'assertions': [{'type': 'CONTAINS', 'expected': '<html'}]}])
        self.assertEqual(summary['failed_requests'], 1)
        self.assertEqual(summary['debug_details']['steps'][0]['outcome'], 'http_failed')
        self.assertNotIn('BODY_SECRET', json.dumps([summary, output]))

    def run_script(self, responses: list, steps: list | None = None, *, auth: bool = False,
                   debug: bool = True, profile: dict | None = None, cookies: bool = True) -> tuple:
        snapshot = auth_snapshot()
        snapshot['load_config'].update(concurrency=1, iterations_per_vu=1)
        if debug:
            snapshot['load_config']['_purpose'] = 'debug'
        if not auth:
            snapshot['runtime_config'] = {}
        else:
            snapshot['runtime_config']['auth_profile']['max_attempts'] = 1
        if profile is not None:
            snapshot['runtime_config']['auth_profile'] = deepcopy(profile)
        if steps is not None:
            snapshot['steps'] = steps
        engine = K6Engine(snapshot)
        config = {**snapshot, 'steps': engine.steps, 'csv_files': {'1': 'rows'}}
        result = subprocess.run([shutil.which('node') or 'C:/Program Files/nodejs/node.exe', '-e', HARNESS],
            input=json.dumps({'script': str(Path(__file__).parents[1] / 'engines/k6_script.js'),
                              'config': config, 'rows': snapshot['csv_data']['1']['rows'], 'responses': responses, 'cookies': cookies}),
            capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        for event in output['events']:
            engine._consume_event(event)
        return engine.collect()['summary'], output

    def test_auth_output_failures_identify_rule_role_and_safe_reason_for_login_and_refresh(self) -> None:
        good = {'code': 0, 'data': {'token': 'TOKEN_SECRET', 'refresh_token': 'REFRESH_SECRET', 'expires_in': 90}}
        for phase in ('login', 'refresh'):
            for field, value, role, index, reason in [
                ('token', 42, 'access_token', 0, 'invalid_type'),
                ('token', '  ', 'access_token', 0, 'empty'),
                ('expires_in', 0, 'expires_in', 2, 'invalid_expiry'),
                ('expires_in', -1, 'expires_in', 2, 'invalid_expiry'),
                ('expires_in', 'EXPIRY_SECRET', 'expires_in', 2, 'invalid_expiry')]:
                with self.subTest(phase=phase, field=field, reason=reason):
                    bad = deepcopy(good)
                    bad['data'][field] = value
                    responses = ([{'body': json.dumps(good)}, {'status': 401, 'body': '{}'}] if phase == 'refresh' else [])
                    summary, output = self.run_script(responses + [{'body': json.dumps(bad)}], [{'id': 1, 'url': '/x'}], auth=True)
                    row = summary['debug_details']['steps'][-1]
                    self.assertEqual(row['phase'], phase)
                    self.assertEqual(row['outcome'], 'extraction_failed')
                    self.assertEqual(row['extractors'][index]['result'], reason)
                    self.assertEqual(row['auth_outputs'], [{'role': role, 'index': index, 'result': reason}])
                    self.assertEqual(row['response'], {'state': 'auth_omitted'})
                    self.assertEqual(summary['auth_failed_vus'], 1)
                    self.assertEqual(summary['business_total'], 1 if phase == 'refresh' else 0)
                    self.assertNotIn('SECRET', json.dumps([summary, output]))

    def test_cookie_failure_identifies_cookie_source_without_any_extraction_rule(self) -> None:
        profile = {'mode': 'LOGIN', 'transport': 'COOKIE', 'cookie_name': 'sid',
            'login': {'url': '/auth/login', 'body_type': 'JSON', 'body': '{"user_id":"{{user_id}}"}',
                      'extractors': [], 'assertions': []}}
        summary, output = self.run_script([{'body': '{}'}], [{'id': 1, 'url': '/x'}], profile=profile, cookies=False)
        row = summary['debug_details']['steps'][0]
        self.assertEqual(row['extractors'], [])
        self.assertEqual(row['auth_outputs'], [{'role': 'cookie', 'index': None, 'result': 'cookie_missing'}])
        self.assertEqual(summary['business_total'], 0)
        self.assertNotIn('SECRET', json.dumps([summary, output]))

    def test_auth_output_metadata_is_rederived_and_rejects_unsafe_roles_reasons_and_values(self) -> None:
        engine = K6Engine(auth_snapshot())
        step = next(step for step in engine.steps if step.get('auth_phase') == 'login')
        result = _debug_auth_outputs([
            {'role': 'access_token', 'index': 'SECRET', 'result': 'invalid_type', 'actual': 'SECRET'},
            {'role': ['SECRET'], 'result': 'invalid_type'},
            {'role': 'expires_in', 'result': ['SECRET']}], step, engine.auth_profile)
        self.assertEqual(result, [{'role': 'access_token', 'index': 0, 'result': 'invalid_type'}])
        self.assertEqual(_debug_auth_outputs([{'role': 'cookie', 'result': 'cookie_missing'}], step, engine.auth_profile), [])
        self.assertEqual(_debug_auth_outputs(result, engine.steps[0], engine.auth_profile), [])

    def test_per_rule_failures_and_missing_dependency_are_distinct_without_counting_unsent(self) -> None:
        steps = [{'id': 1, 'name': 'list', 'url': '/items',
                  'assertions': [{'type': 'STATUS_CODE', 'expected': '200'}, {'type': 'JSON_PATH', 'expr': '$.code', 'expected': 'OK'}],
                  'extractors': [{'name': 'item_id', 'expr': '$.data.id'}]},
                 {'id': 2, 'name': 'detail', 'url': '/items/{{item_id}}'}]
        summary, output = self.run_script([{'body': '{"code":"FAIL","message":"PASSWORD_SECRET","token":"TOKEN_SECRET"}'}], steps)
        rows = summary['debug_details']['steps']
        self.assertEqual(rows[0]['outcome'], 'assertion_failed')
        self.assertEqual([r['result'] for r in rows[0]['assertions']], ['passed', 'mismatch'])
        self.assertEqual(rows[0]['extractors'][0]['result'], 'skipped')
        self.assertEqual(rows[1]['outcome'], 'not_sent')
        self.assertEqual(output['count'], 1)
        self.assertEqual(summary['http_total'], 1)
        self.assertEqual(summary['completed_iterations'], 0)
        self.assertNotIn('SECRET', json.dumps([summary, output]))

    def test_extraction_json_parse_http_and_transport_results(self) -> None:
        step = [{'id': 1, 'url': '/x', 'extractors': [{'name': 'id', 'expr': '$.data.id'}]}]
        for response, outcome, result in [({'body': '{"data":{}}'}, 'extraction_failed', 'missing'),
            ({'body': 'not json'}, 'extraction_failed', 'invalid_json'),
            ({'body': '{}', 'status': 403}, 'http_failed', 'skipped'),
            ({'throw': True}, 'transport_failed', 'skipped')]:
            with self.subTest(outcome=outcome, result=result):
                summary, _ = self.run_script([response], step)
                row = summary['debug_details']['steps'][0]
                self.assertEqual(row['outcome'], outcome)
                self.assertEqual(row['extractors'][0]['result'], result)
                self.assertEqual(summary['completed_iterations'], 1, 'completed iteration is not successful transaction')

    def test_response_shape_redacts_all_values_and_arbitrary_keys_before_truncation(self) -> None:
        body = {'code': 123456789, 'message': 'password PASSWORD_SECRET and token TOKEN_SECRET',
                'PASSWORD_SECRET': {'value': 'TOKEN_SECRET'}, 'data': {'id': 'PASSWORD_SECRET'},
                'items': [{f'unknown_{i}': 'TOKEN_SECRET' for i in range(20)} for _ in range(10)]}
        summary, output = self.run_script([{'body': json.dumps(body)}], [{'id': 1, 'url': '/x'}])
        response = summary['debug_details']['steps'][0]['response']
        self.assertEqual(response['state'], 'json')
        self.assertTrue(response['truncated'])
        self.assertLessEqual(len(response['preview']), 2048)
        self.assertNotIn('SECRET', json.dumps([summary, output]))
        self.assertNotIn('123456789', json.dumps([summary, output]))

    def test_auth_body_is_always_omitted_and_auth_phase_is_explicit(self) -> None:
        login = {'code': 0, 'data': {'token': 'TOKEN_SECRET', 'refresh_token': 'REFRESH_SECRET', 'expires_in': 90}}
        summary, output = self.run_script([{'body': json.dumps(login)}, {'body': '{}'}], [{'id': 1, 'url': '/x'}], auth=True)
        rows = summary['debug_details']['steps']
        self.assertEqual(rows[0]['step_id'], 'auth:login')
        self.assertEqual(rows[0]['phase'], 'login')
        self.assertEqual(rows[0]['response'], {'state': 'auth_omitted'})
        self.assertEqual(summary['auth_requests']['login']['success'], 1)
        self.assertNotIn('SECRET', json.dumps([summary, output]))

    def test_binary_text_and_oversize_json_are_omitted(self) -> None:
        for row, state in [({'body': 'SECRET', 'type': 'application/octet-stream'}, 'non_json_omitted'),
                           ({'body': 'SECRET', 'type': 'text/plain'}, 'non_json_omitted'),
                           ({'body': None}, 'binary_omitted'),
                           ({'body': json.dumps({'data': 'x' * 65536})}, 'oversize_omitted'),
                           ({'body': '{"data":"' + '中' * 22000 + '"}'}, 'oversize_omitted')]:
            summary, output = self.run_script([row], [{'id': 1, 'url': '/x'}])
            self.assertEqual(summary['debug_details']['steps'][0]['response']['state'], state)
            self.assertNotIn('SECRET', json.dumps(output))

    def test_401_refresh_has_separate_ordered_diagnostics_and_preserves_failed_request(self) -> None:
        credentials = {'code': 0, 'data': {'token': 'TOKEN_SECRET', 'refresh_token': 'REFRESH_SECRET', 'expires_in': 90}}
        summary, output = self.run_script([
            {'body': json.dumps(credentials)}, {'status': 401, 'body': '{}'},
            {'body': json.dumps(credentials)}, {'body': '{}'}], [{'id': 1, 'url': '/x'}], auth=True)
        self.assertEqual([row['phase'] for row in summary['debug_details']['steps']], ['login', 'business', 'refresh', 'business'])
        self.assertEqual([row['outcome'] for row in summary['debug_details']['steps']], ['passed', 'http_failed', 'passed', 'passed'])
        self.assertEqual(summary['auth_requests']['refresh']['success'], 1)
        self.assertEqual(summary['business_total'], 2)
        self.assertEqual(summary['failed_requests'], 1)
        self.assertEqual(summary['completed_iterations'], 1)
        self.assertNotIn('SECRET', json.dumps([summary, output]))

    def test_request_rule_and_diagnostic_limits_and_formal_load_opt_out(self) -> None:
        steps = [{'id': i, 'name': f'step-{i}', 'url': '/x', 'assertions': [{'type': 'STATUS_CODE', 'expected': 200}] * 40} for i in range(105)]
        summary, output = self.run_script([{'body': '{}'}] * 105, steps)
        self.assertEqual(len(summary['debug_details']['steps']), 100)
        self.assertTrue(summary['debug_details']['truncated'])
        self.assertTrue(summary['debug_details']['steps'][0]['rules_truncated'])
        self.assertEqual(len(summary['debug_details']['steps'][0]['assertions']), 32)
        self.assertEqual(summary['http_total'], 105)
        formal, events = self.run_script([{'body': '{"token":"SECRET"}'}], [steps[0]], debug=False)
        self.assertNotIn('debug_details', formal)
        self.assertFalse(any(event['kind'] == 'diagnostic' for event in events['events']))

    def test_adapter_sanitizes_untrusted_event_again(self) -> None:
        response = _debug_response({'state': 'json', 'body': {'SECRET': {'data': 'SECRET'}, 'code': 123}}, False)
        self.assertNotIn('SECRET', json.dumps(response))
        snapshot = auth_snapshot()
        snapshot['load_config'].update(_purpose='debug', concurrency=1, iterations_per_vu=1)
        engine = K6Engine(snapshot)
        for _ in range(102):
            engine._consume_event({'kind': 'diagnostic', 'step': 0, 'vu': 1, 'outcome': 'SECRET',
                'status': 'SECRET', 'assertions': [{'index': 0, 'result': 'SECRET'}], 'response': {'state': 'SECRET'}})
        details = engine.collect()['summary']['debug_details']
        self.assertEqual(len(details['steps']), 100)
        self.assertTrue(details['truncated'])
        self.assertNotIn('SECRET', json.dumps(details))


class DebugMetadataTests(TestCase):
    def test_new_execution_safe_auth_metadata_does_not_mutate_old_snapshot(self) -> None:
        from django.contrib.auth import get_user_model
        from apps.perf_testing.models import PerfProject, PerfScenario, PerfScenarioStep
        from apps.perf_testing.services import executor
        user = get_user_model().objects.create_user(username='c10-metadata')
        project = PerfProject.objects.create(name='c10-metadata', owner=user)
        scenario = PerfScenario.objects.create(name='c10', project=project, created_by=user, engine='K6',
            env_config={'base_url': 'http://fixture'}, runtime_config={})
        PerfScenarioStep.objects.create(scenario=scenario, name='business', url='/business')
        with tempfile.TemporaryDirectory() as private, override_settings(PERF_PRIVATE_ROOT=private), \
                patch('apps.perf_testing.engines.k6_version', return_value='test-fixed'):
            old = executor.create_execution(scenario, user=user)
            old_steps = deepcopy(old.steps_snapshot)
            scenario.runtime_config = auth_snapshot()['runtime_config']
            scenario.save()
            new = executor.create_execution(scenario, user=user)
            self.assertEqual([row.get('auth_phase') for row in new.steps_snapshot], [None, 'login', 'refresh'])
            self.assertEqual(set(new.steps_snapshot[-1]), {'id', 'name', 'method', 'is_setup', 'auth_phase', 'request_path'})
            self.assertEqual(new.steps_snapshot[0]['request_path'], '/business')
            self.assertEqual(new.steps_snapshot[-1]['request_path'], auth_snapshot()['runtime_config']['auth_profile']['refresh']['url'])
            self.assertNotIn('SECRET', json.dumps(new.steps_snapshot))
            old.refresh_from_db()
            self.assertEqual(old.steps_snapshot, old_steps)
