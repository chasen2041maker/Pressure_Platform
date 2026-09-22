"""Native WS configuration contracts; no target connections or deployment data."""
from copy import deepcopy
from io import StringIO
import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.core.management import call_command
from rest_framework.exceptions import ValidationError

from apps.perf_testing.engines.k6_engine import validate_snapshot
from apps.perf_testing.services import api_catalog, executor, reporter
from . import test_api_catalog as fixtures
from .test_k6_auth_refresh import static_refresh_snapshot, auth_snapshot


def ws_config():
    return {'version': 1, 'connect_timeout_ms': 5000, 'command_timeout_ms': 5000,
            'hold_open_ms': 0, 'max_session_ms': 15000, 'heartbeat_interval_ms': 25000,
            'auth': {'request': {'version': 1, 'action': 'auth', 'payload': {'token': '{{access_token}}'}},
                     'assertions': [{'type': 'JSON_PATH', 'expr': '$.ok', 'expected': True}], 'extractors': []},
            'commands': [{'name': 'Read history', 'request': {'version': 1, 'action': 'timeline.list',
                          'payload': {'channel': 'demo', 'limit': 1}},
                          'assertions': [{'type': 'JSON_PATH', 'expr': '$.ok', 'expected': True}], 'extractors': []}]}


def ws_step():
    return {'id': 1, 'name': 'Support WS', 'protocol': 'WEBSOCKET', 'url': '/api/v1/support/ws',
            'method': 'GET', 'websocket_config': ws_config()}


def ws_snapshot():
    snapshot = static_refresh_snapshot()
    snapshot['engine'] = 'K6'
    snapshot['steps'] = [ws_step()]
    return snapshot


def push_config():
    return {'version': 1, 'mode': 'PUSH', 'heartbeat_interval_ms': 0,
            'auth': {'type': 'BEARER', 'token': '{{access_token}}'},
            'commands': [{'name': 'Quote snapshot', 'request': {'type': 'quote.subscribe', 'codes': ['sz000001']},
                          'event_type': 'quote.snapshot', 'error_types': ['quote.error'],
                          'assertions': [{'type': 'JSON_PATH', 'expr': '$.quotes[0].code', 'expected': 'sz000001'}],
                          'extractors': [{'type': 'JSON_PATH', 'expr': '$.cycle_id', 'name': 'quote_cycle'}]}]}


class PushWebSocketSnapshotTests(SimpleTestCase):
    def test_push_bearer_and_outputs_validate(self):
        snapshot = ws_snapshot()
        snapshot['steps'][0]['websocket_config'] = push_config()
        snapshot['steps'].append({'id': 2, 'url': '/cycle/{{quote_cycle}}'})
        self.assertEqual(validate_snapshot(snapshot), [])

    def test_push_wait_without_send_requires_real_event_assertion(self):
        snapshot = ws_snapshot()
        config = push_config()
        del config['commands'][0]['request']
        snapshot['steps'][0]['websocket_config'] = config
        self.assertEqual(validate_snapshot(snapshot), [])
        config['commands'][0]['assertions'] = []
        self.assertTrue(validate_snapshot(snapshot))

    def test_push_rejects_unbounded_or_credential_bearing_frames(self):
        for mutate in [lambda c: c['auth'].update(token='literal-secret'),
                       lambda c: c.update(heartbeat_interval_ms=25000),
                       lambda c: c['commands'][0].update(event_type='{{event_type}}'),
                       lambda c: c['commands'][0]['request'].update(token='{{access_token}}'),
                       lambda c: c['commands'][0].update(error_types=['quote.snapshot'])]:
            snapshot = ws_snapshot()
            config = push_config()
            mutate(config)
            snapshot['steps'][0]['websocket_config'] = config
            self.assertTrue(validate_snapshot(snapshot))


class WebSocketSnapshotTests(SimpleTestCase):
    def test_static_and_login_bearer_accept_ordered_command_outputs(self):
        for snapshot in [ws_snapshot(), dict(auth_snapshot(), engine='K6', steps=[ws_step()])]:
            config = snapshot['steps'][0]['websocket_config']
            config['commands'][0]['extractors'] = [{'type': 'JSON_PATH', 'expr': '$.data.cursor', 'name': 'cursor'}]
            config['commands'].append({'name': 'Next', 'request': {'version': 1, 'action': 'timeline.list',
                'payload': {'cursor': '{{cursor}}'}}, 'assertions': [], 'extractors': []})
            snapshot['steps'].append({'id': 2, 'url': '/items/{{cursor}}'})
            self.assertEqual(validate_snapshot(snapshot), [])

    def test_unknown_protocol_and_ws_non_k6_fail_closed(self):
        for protocol, engine in [('SOCKETIO', 'K6'), ('WEBSOCKET', 'BUILTIN'), ('WEBSOCKET', 'LOCUST')]:
            snapshot = ws_snapshot()
            snapshot['engine'] = engine
            snapshot['steps'][0]['protocol'] = protocol
            self.assertTrue(validate_snapshot(snapshot))

    def test_ws_urls_reject_cross_origin_downgrade_credentials_and_dynamic_authority(self):
        for url in ['ws://elsewhere/api', 'ws://fixture:81/api', '//elsewhere/api',
                    'ws://user:secret@fixture/api', '/api?token=secret', '/api?access_token={{access_token}}',
                    'ws://{{hostname}}/api', '/api#secret', '/api\\evil', 'ftp://fixture/api']:
            snapshot = ws_snapshot()
            snapshot['steps'][0]['url'] = url
            errors = validate_snapshot(snapshot)
            self.assertTrue(errors, url)
            self.assertNotIn('secret', str(errors))
        snapshot = ws_snapshot()
        snapshot['env_config']['base_url'] = 'https://fixture'
        snapshot['steps'][0]['url'] = 'ws://fixture/api'
        self.assertTrue(validate_snapshot(snapshot))

    def test_native_same_origin_ws_url_and_http_mapping(self):
        for url in ['ws://fixture/api', 'http://fixture/api', '{{base_url}}/api', '/api', 'api']:
            snapshot = ws_snapshot()
            snapshot['steps'][0]['url'] = url
            self.assertEqual(validate_snapshot(snapshot), [], url)

    def test_cookie_or_wrong_auth_reference_is_rejected(self):
        snapshot = ws_snapshot()
        snapshot['runtime_config']['auth_profile']['transport'] = 'COOKIE'
        self.assertTrue(validate_snapshot(snapshot))
        for token in ['secret', '{{refresh_token}}', 'Bearer {{access_token}}']:
            snapshot = ws_snapshot()
            snapshot['steps'][0]['websocket_config']['auth']['request']['payload']['token'] = token
            errors = validate_snapshot(snapshot)
            self.assertTrue(errors)
            self.assertNotIn('secret', str(errors))

    def test_session_and_command_deadlines_fit_scenario_limits(self):
        for key, value in [('command_timeout_ms', 60000), ('connect_timeout_ms', 30000), ('max_session_ms', 40000)]:
            snapshot = ws_snapshot()
            snapshot['runtime_config']['timeout'] = 20
            if key != 'max_session_ms':
                snapshot['load_config']['duration'] = 180
                snapshot['steps'][0]['websocket_config']['max_session_ms'] = 120000
            snapshot['steps'][0]['websocket_config'][key] = value
            self.assertTrue(validate_snapshot(snapshot), key)

    def test_malformed_snapshot_fields_return_safe_errors(self):
        for headers in [['TOP_SECRET'], 'TOP_SECRET', 1]:
            snapshot = ws_snapshot(); snapshot['steps'][0]['headers'] = headers
            self.assertTrue(validate_snapshot(snapshot))
        for think in [{'type': 'RANDOM'}, {'type': 'FIXED', 'min': -1}, {'type': 1}]:
            snapshot = ws_snapshot(); snapshot['steps'][0]['think_time'] = think
            self.assertTrue(validate_snapshot(snapshot))

    def test_credentials_only_appear_in_auth_token_slot(self):
        for field in ['url', 'headers', 'command', 'auth_assertion']:
            snapshot = ws_snapshot()
            step = snapshot['steps'][0]
            if field == 'url':
                step['url'] = '/api/{{access_token}}'
            elif field == 'headers':
                step['headers'] = {'X-Key': '{{refresh_token}}'}
            elif field == 'command':
                step['websocket_config']['commands'][0]['request']['payload']['value'] = '{{access_token}}'
            else:
                step['websocket_config']['auth']['assertions'][0]['expected'] = '{{refresh_token}}'
            self.assertTrue(validate_snapshot(snapshot), field)

    def test_empty_commands_rejected_and_zero_disables_heartbeat(self):
        snapshot = ws_snapshot()
        snapshot['steps'][0]['websocket_config']['commands'] = []
        self.assertTrue(validate_snapshot(snapshot))
        snapshot = ws_snapshot()
        snapshot['steps'][0]['websocket_config']['heartbeat_interval_ms'] = 0
        self.assertEqual(validate_snapshot(snapshot), [])

    def test_missing_dependency_and_protected_extractions_are_rejected(self):
        for name in ['access_token', 'refresh_token', 'user_id', 'password', 'vu_id', 'request_id', '__proto__']:
            snapshot = ws_snapshot()
            snapshot['steps'][0]['websocket_config']['commands'][0]['extractors'] = [
                {'type': 'JSON_PATH', 'expr': '$.data', 'name': name}]
            self.assertTrue(validate_snapshot(snapshot), name)
        snapshot = ws_snapshot()
        snapshot['steps'][0]['websocket_config']['commands'][0]['request']['payload']['cursor'] = '{{not_yet}}'
        snapshot['steps'][0]['websocket_config']['commands'][0]['extractors'] = [
            {'type': 'JSON_PATH', 'expr': '$.data', 'name': 'not_yet'}]
        self.assertTrue(validate_snapshot(snapshot))

    def test_bounded_configuration_rejects_unknown_fields_ids_and_http_payload(self):
        variants = []
        for field, value in [('connect_timeout_ms', True), ('command_timeout_ms', '5000'),
                             ('hold_open_ms', -1), ('max_session_ms', 0), ('heartbeat_interval_ms', 1),
                             ('script', 'TOP_SECRET')]:
            config = ws_config(); config[field] = value; variants.append(config)
        config = ws_config(); config['auth']['request']['id'] = 'TOP_SECRET'; variants.append(config)
        config = ws_config(); config['commands'][0]['request']['id'] = 'TOP_SECRET'; variants.append(config)
        config = ws_config(); config['commands'] *= 65; variants.append(config)
        config = ws_config(); config['commands'][0]['request']['payload']['large'] = 'x' * 131072; variants.append(config)
        config = ws_config(); config['commands'][0]['assertions'][0]['expr'] = '$..*'; variants.append(config)
        for config in variants:
            snapshot = ws_snapshot(); snapshot['steps'][0]['websocket_config'] = config
            errors = validate_snapshot(snapshot)
            self.assertTrue(errors)
            self.assertNotIn('TOP_SECRET', str(errors))
        for field, value in [('body', 'TOP_SECRET'), ('files', [{'file_id': 1}]),
                             ('assertions', [{'type': 'STATUS_CODE', 'expected': 101}]),
                             ('extractors', [{'name': 'foo', 'expr': '$.x'}]),
                             ('source_metadata', {'source_key': 'GET /ws'}), ('source_request_id', 123)]:
            snapshot = ws_snapshot(); snapshot['steps'][0][field] = value
            self.assertTrue(validate_snapshot(snapshot), field)

    def test_public_snapshot_excludes_ws_config_even_for_legacy_stored_payloads(self):
        step = ws_step()
        step.update(websocket_config={'auth': {'secret': 'TOP_SECRET'}}, headers={'secret': 'TOP_SECRET'},
                    body='TOP_SECRET', request={'secret': 'TOP_SECRET'})
        execution = SimpleNamespace(steps_snapshot=[step], load_snapshot={'_engine': 'K6'}, summary={}, pk=None)
        result = reporter.report_steps(execution)
        self.assertNotIn('TOP_SECRET', json.dumps(result))
        self.assertEqual(result[0]['protocol'], 'WEBSOCKET')

    def test_public_command_metadata_is_safe_and_idempotent(self):
        step = ws_step()
        command = step['websocket_config']['commands'][0]
        command['request']['payload']['content'] = 'PAYLOAD_SECRET'
        command['assertions'][0].update(expr='$.RESPONSE_SECRET', expected='EXPECTED_SECRET')
        command['extractors'] = [{'type': 'JSON_PATH', 'expr': '$.EXTRACTED_SECRET', 'name': 'PRIVATE_NAME'}]
        expected = [{'index': 0, 'name': 'Read history', 'action': 'timeline.list',
                     'assertions': [{'index': 0, 'type': 'JSON_PATH'}],
                     'extractors': [{'index': 0, 'type': 'JSON_PATH'}]}]
        execution = SimpleNamespace(steps_snapshot=[step], load_snapshot={'_engine': 'K6'}, summary={}, pk=None)
        result = reporter.report_steps(execution)
        self.assertEqual(result[0].get('websocket_commands'), expected)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('PRIVATE_NAME', json.dumps(result))
        execution.steps_snapshot = result
        self.assertEqual(reporter.report_steps(execution), result)
        # Re-reading a public snapshot still strips fields added by a legacy writer.
        execution.steps_snapshot[0]['websocket_commands'][0].update(auth='AUTH_SECRET', payload='PAYLOAD_SECRET')
        execution.steps_snapshot[0]['websocket_commands'][0]['assertions'][0]['expected'] = 'EXPECTED_SECRET'
        sanitized = reporter.report_steps(execution)
        self.assertEqual(sanitized[0]['websocket_commands'], expected)
        self.assertNotIn('SECRET', json.dumps(sanitized))

    def test_public_command_metadata_drops_malformed_or_dynamic_values(self):
        safe = {'index': 0, 'name': 'Read history', 'action': 'timeline.list', 'assertions': [], 'extractors': []}
        for patch in [{'index': True}, {'index': '0'}, {'index': -1}, {'index': 64}, {'name': ['SECRET']},
                      {'name': '{{SECRET}}'}, {'name': 'x' * 201}, {'action': 'INVALID SECRET'},
                      {'action': 'auth'}, {'action': 'ping'}]:
            step = {key: value for key, value in ws_step().items() if key != 'websocket_config'}
            step['websocket_commands'] = [dict(safe, **patch)]
            execution = SimpleNamespace(steps_snapshot=[step], load_snapshot={'_engine': 'K6'}, summary={}, pk=None)
            self.assertEqual(reporter.report_steps(execution)[0].get('websocket_commands'), [], patch)
        step['websocket_commands'] = [dict(safe, assertions=[{'index': True, 'type': 'JSON_PATH'},
            {'index': 1, 'type': 'SECRET'}, {'index': 2, 'type': 'JSON_PATH', 'expected': 'SECRET'}])]
        result = reporter.report_steps(execution)
        self.assertEqual(result[0]['websocket_commands'][0]['assertions'], [{'index': 2, 'type': 'JSON_PATH'}])
        self.assertNotIn('SECRET', json.dumps(result))


class WebSocketApiTests(TestCase):
    setUp = fixtures.ApiCatalogTests.setUp
    upload = fixtures.ApiCatalogTests.upload
    project_url = fixtures.ApiCatalogTests.project_url
    imported = fixtures.ApiCatalogTests.imported

    def payload(self):
        return dict(ws_step(), scenario=self.scene.pk)

    def test_push_config_save_copy_freeze_and_public_event_metadata(self):
        from apps.perf_testing.services.websocket_steps import normalize_websocket_config
        config = normalize_websocket_config(push_config())
        response = self.client.post('/api/perf-testing/steps/',
            dict(self.payload(), websocket_config=config), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        raw = self.client.get(f'/api/perf-testing/steps/{response.data["id"]}/').data
        self.assertEqual(raw['websocket_config'], config)
        saved = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/save-steps/', {'steps': [raw]}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        clone = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/duplicate/', {}, format='json')
        self.assertEqual(clone.status_code, 201, clone.data)
        self.assertEqual(clone.data['steps'][0]['websocket_config'], config)
        with mock.patch('apps.perf_testing.engines.k6_version', return_value='fixture-version'):
            execution = executor.create_execution(self.scene, user=self.owner)
        frozen = executor._execution_snapshot(execution)
        self.assertEqual(frozen['steps'][0]['websocket_config'], config)
        self.scene.steps.update(websocket_config={})
        self.assertEqual(executor._execution_snapshot(execution), frozen)
        public = reporter.report_steps(execution)
        self.assertEqual(public[0]['websocket_commands'][0]['action'], 'quote.snapshot')
        self.assertEqual(public[0]['websocket_commands'][0]['kind'], 'event')
        self.assertNotIn('access_token', json.dumps(public))
        self.assertNotIn('sz000001', json.dumps(public))

    def test_crud_bulk_save_and_private_snapshot_roundtrip(self):
        payload = self.payload()
        response = self.client.post('/api/perf-testing/steps/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data.get('protocol'), 'WEBSOCKET')
        self.assertEqual(response.data.get('websocket_config'), ws_config())
        raw = self.client.get(f'/api/perf-testing/steps/{response.data["id"]}/').data
        result = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/save-steps/',
                                 {'steps': [raw]}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        snapshot = executor.build_snapshot(self.scene, user=self.owner)
        self.assertEqual(snapshot['steps'][0]['websocket_config'], ws_config())
        self.assertEqual(snapshot['steps'][0]['protocol'], 'WEBSOCKET')
        clone = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/duplicate/', {}, format='json')
        self.assertEqual(clone.status_code, 201, clone.data)
        self.assertEqual(clone.data['steps'][0]['protocol'], 'WEBSOCKET')
        self.assertEqual(clone.data['steps'][0]['websocket_config'], ws_config())

    def test_unknown_protocol_spoofed_source_and_http_config_rejected(self):
        for fields in [{'protocol': 'SOCKETIO'}, {'source_metadata': {'source_key': 'GET /ws'}},
                       {'protocol': 'HTTP'}, {'method': 'POST'}]:
            response = self.client.post('/api/perf-testing/steps/', dict(self.payload(), **fields), format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.scene.steps.count(), 0)

    def test_existing_controlled_http_cannot_be_converted_to_native_ws(self):
        operation = self.imported()[0]
        step = self.service.import_steps(self.scene, [operation['id']], self.owner)[0]
        metadata = deepcopy(step.source_metadata)
        response = self.client.patch(f'/api/perf-testing/steps/{step.pk}/',
            {'protocol': 'WEBSOCKET', 'websocket_config': ws_config(), 'source_request': None}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        step.refresh_from_db()
        self.assertEqual(step.source_metadata, metadata)
        self.assertNotIn('protocol', api_catalog.REQUEST_FIELDS)
        self.assertNotIn('websocket_config', api_catalog.REQUEST_FIELDS)

    def test_ws_non_k6_save_and_engine_switch_rejected(self):
        result = self.client.post('/api/perf-testing/steps/', self.payload(), format='json')
        self.assertEqual(result.status_code, 201, result.data)
        response = self.client.patch(f'/api/perf-testing/scenarios/{self.scene.pk}/', {'engine': 'BUILTIN'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.scene.engine = 'BUILTIN'; self.scene.save(update_fields=['engine'])
        response = self.client.post('/api/perf-testing/steps/', self.payload(), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        readiness = api_catalog.scenario_readiness(self.scene, executor._resolve_environment_inputs(self.scene, self.owner))
        self.assertFalse(readiness[0]['ready'])

    def test_http_defaults_preserved(self):
        response = self.client.post('/api/perf-testing/steps/', {'scenario': self.scene.pk, 'name': 'HTTP',
                                   'url': '/health'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data.get('protocol'), 'HTTP')
        self.assertEqual(response.data.get('websocket_config'), {})

    def test_frozen_private_config_is_immutable_and_public_execution_drops_payload(self):
        response = self.client.post('/api/perf-testing/steps/', self.payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        with mock.patch('apps.perf_testing.engines.k6_version', return_value='fixture-version'):
            execution = executor.create_execution(self.scene, user=self.owner)
        before = executor._execution_snapshot(execution)
        self.assertEqual(before['steps'][0]['websocket_config'], ws_config())
        self.assertNotIn('websocket_config', execution.steps_snapshot[0])
        self.assertNotIn('auth', json.dumps(execution.steps_snapshot))
        expected = [{'index': 0, 'name': 'Read history', 'action': 'timeline.list',
                     'assertions': [{'index': 0, 'type': 'JSON_PATH'}], 'extractors': []}]
        self.assertEqual(execution.steps_snapshot[0].get('websocket_commands'), expected)
        self.scene.steps.update(websocket_config={}, protocol='HTTP')
        self.assertEqual(executor._execution_snapshot(execution), before)
        self.assertEqual(reporter.report_steps(execution)[0]['protocol'], 'WEBSOCKET')
        self.assertEqual(reporter.report_steps(execution)[0]['websocket_commands'], expected)

    def test_models_have_matching_pressure_migrations(self):
        call_command('makemigrations', 'perf_testing', check=True, dry_run=True, stdout=StringIO(), verbosity=0)

    def test_invalid_non_k6_ws_cannot_start_when_preflight_is_skipped(self):
        result = self.client.post('/api/perf-testing/steps/', self.payload(), format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.scene.engine = 'BUILTIN'; self.scene.save(update_fields=['engine'])
        with mock.patch('apps.perf_testing.services.executor.spawn_execution') as spawn:
            execution, check = executor.start_execution(self.scene, self.owner, skip_preflight=True)
        self.assertIsNone(execution)
        self.assertFalse(check['passed'])
        spawn.assert_not_called()
        with self.assertRaises(ValidationError):
            executor.create_execution(self.scene, user=self.owner)
