from copy import deepcopy
from django.test import SimpleTestCase, TestCase
from unittest import mock
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from rest_framework.exceptions import ValidationError
from apps.perf_testing.services.sse_steps import normalize_sse_config
from apps.perf_testing.engines.k6_engine import validate_snapshot
from .test_websocket_steps import ws_snapshot
from apps.perf_testing.services.k6_sse_metrics import SSEMetrics
from apps.perf_testing.services import executor, reporter, prepared_requests
from . import test_api_catalog as fixtures
from . import test_prepared_requests as prepared_fixtures
from apps.perf_testing import models


def condition(expr, expected):
    return {'type': 'JSON_PATH', 'expr': expr, 'operator': 'eq', 'expected': expected}


def sse_config():
    return {'version': 1, 'total_ms': 1000, 'idle_ms': 300, 'rules': [
        {'name': 'result', 'event': 'message', 'min_events': 1, 'max_events': 1,
         'assertions': [condition('$.ok', True)],
         'extractors': [{'name': 'stream_value', 'type': 'JSON_PATH', 'expr': '$.value'}]},
        {'name': 'done', 'event': 'message', 'data': '[DONE]', 'terminal': True, 'after': ['result']}]}


def sse_snapshot():
    value = ws_snapshot()
    value['steps'] = [{'id': 1, 'protocol': 'SSE', 'name': 'Stream', 'method': 'POST',
                      'url': '/stream', 'sse_config': sse_config()}]
    return value


class SSEConfigTests(SimpleTestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for shared frontend presets')
    def test_frontend_presets_normalize_in_backend(self):
        module = Path(__file__).resolve().parents[3] / 'frontend/src/views/performance-testing/sseStepForm.mjs'
        script = f'import {{sseTemplate}} from {json.dumps(module.as_uri())}; console.log(JSON.stringify(["CHAT","MESSAGE_SPEECH","CONTENT_SPEECH"].map(kind=>sseTemplate(kind))));'
        result = subprocess.run(['node', '--input-type=module', '-e', script], check=True, capture_output=True, text=True)
        for preset in json.loads(result.stdout):
            self.assertEqual(normalize_sse_config(preset), preset)

    def test_contract_defaults_and_snapshot_outputs(self):
        config = normalize_sse_config(sse_config())
        self.assertEqual(config['max_events'], 256)
        self.assertFalse(config['rules'][0]['terminal'])
        snapshot = sse_snapshot()
        snapshot['steps'].append({'id': 2, 'url': '/{{stream_value}}'})
        self.assertEqual(validate_snapshot(snapshot), [])

    def test_invalid_configs_fail_without_reflecting_content(self):
        for change in [lambda c: c.update(idle_ms=2000), lambda c: c.update(unknown='secret-body'),
                       lambda c: c['rules'][1].update(after=[]),
                       lambda c: c['rules'][0].update(after=['done']),
                       lambda c: c['rules'][0].update(min_events=True),
                       lambda c: c['rules'][0]['assertions'][0].update(operator={}),
                       lambda c: c['rules'][0]['assertions'][0].update(operator=[]),
                       lambda c: c['rules'][0]['extractors'][0].update(name='__proto__')]:
            config = sse_config(); change(config)
            with self.assertRaises(ValidationError) as caught:
                normalize_sse_config(config)
            self.assertNotIn('secret-body', str(caught.exception))

    def test_wrong_engine_unknown_refs_and_protected_outputs_fail(self):
        for change in [lambda s: s.update(engine='BUILTIN'),
                       lambda s: s['steps'][0].update(files=[{'file_id': 3}]),
                       lambda s: s['steps'][0].update(params={'nested': ['not', 'scalar']}),
                       lambda s: s['steps'][0].update(url='https://other.invalid/stream'),
                       lambda s: s['steps'][0]['sse_config']['rules'][0]['assertions'][0].update(expected='{{missing}}'),
                       lambda s: s['steps'][0]['sse_config']['rules'][0]['extractors'][0].update(name='access_token')]:
            snapshot = sse_snapshot(); change(snapshot)
            self.assertTrue(validate_snapshot(snapshot))

    def test_incomplete_stream_keeps_first_event_and_private_fields_are_dropped(self):
        metrics = SSEMetrics(sse_snapshot()['steps'])
        metrics.start(0, 1)
        metrics.consume({'kind': 'sse_first_event', 'elapsed_ms': 5, 'data': 'PRIVATE'}, 0, 1)
        result = metrics.snapshot()
        self.assertEqual(result['streams']['incomplete'], 1)
        self.assertEqual(result['first_event']['count'], 1)
        self.assertEqual(result['completion']['count'], 0)
        self.assertEqual(result['stream_metrics'][0]['step_id'], 1)
        self.assertNotIn('PRIVATE', str(result))

    def test_public_snapshot_strips_event_contract_and_is_idempotent(self):
        step = sse_snapshot()['steps'][0]
        step['sse_config']['rules'][0]['assertions'][0]['expected'] = 'PRIVATE_EXPECTED'
        value = SimpleNamespace(steps_snapshot=[step], load_snapshot={'_engine': 'K6'}, summary={}, pk=None)
        public = reporter.report_steps(value)
        self.assertNotIn('PRIVATE', json.dumps(public))
        self.assertNotIn('sse_config', public[0])
        value.steps_snapshot = public
        self.assertEqual(reporter.report_steps(value), public)

    def test_rejected_runtime_options_do_not_count_an_http_attempt(self):
        from apps.perf_testing.engines.k6_engine import K6Engine
        engine = K6Engine(sse_snapshot())
        engine._consume_event({'kind': 'request_started', 'step': 0, 'vu': 1})
        engine._consume_event({'kind': 'sse_result', 'step': 0, 'vu': 1, 'started': False,
                               'ok': False, 'closed': True, 'reason': 'invalid_options'})
        self.assertEqual(engine._http_started, 0)
        self.assertEqual(engine._business_started, 0)
        self.assertEqual(engine._sse.snapshot()['streams']['started'], 0)
        self.assertEqual(engine._runtime_failures, 1)

    def test_html_attributes_safe_failure_to_frozen_stream_step(self):
        from . import test_k6_report
        helper = test_k6_report.K6ReportTests()
        execution = helper.execution()
        execution.steps_snapshot = [dict(sse_snapshot()['steps'][0], name='Frozen stream')]
        metrics = SSEMetrics(execution.steps_snapshot)
        metrics.start(0, 1)
        metrics.consume({'kind': 'sse_first_event', 'elapsed_ms': 2}, 0, 1)
        metrics.consume({'kind': 'sse_result', 'started': True, 'ok': False, 'closed': True,
                         'reason': 'event_error', 'phase': 'business', 'elapsed_ms': 4}, 0, 1)
        execution.summary['sse'] = metrics.snapshot()
        execution.summary['sse']['stream_metrics'][0]['errors'].append({'phase': 'PRIVATE', 'reason': 'PRIVATE', 'count': 1})
        document = helper.render(execution)
        self.assertIn('逐流失败阶段与原因', document)
        self.assertIn('business / event_error / 1', document)
        self.assertIn('transport / protocol_error / 1', document)
        self.assertNotIn('PRIVATE', document)

    def test_no_json_literal_or_literal_only_terminal_bypass(self):
        for config in [dict(sse_config(), rules=[{'name': 'done', 'event': 'message', 'data': '{"ok":true}', 'terminal': True}]),
                       dict(sse_config(), rules=[{'name': 'first', 'event': 'message', 'data': 'hello'},
                            {'name': 'done', 'event': 'message', 'data': '[DONE]', 'after': ['first'], 'terminal': True}]),
                       dict(sse_config(), rules=[{'name': 'bad', 'event': 'message', 'after': [{}]}])]:
            with self.assertRaises(ValidationError):
                normalize_sse_config(config)


class SSEApiTests(TestCase):
    setUp = fixtures.ApiCatalogTests.setUp
    upload = fixtures.ApiCatalogTests.upload
    project_url = fixtures.ApiCatalogTests.project_url
    configure = prepared_fixtures.PreparedRequestTests.configure
    prepare = prepared_fixtures.PreparedRequestTests.prepare
    edit_url = prepared_fixtures.PreparedRequestTests.edit_url
    save_edit = prepared_fixtures.PreparedRequestTests.save_edit

    def test_crud_copy_bulk_and_immutable_private_snapshot(self):
        payload = dict(sse_snapshot()['steps'][0], scenario=self.scene.pk)
        response = self.client.post('/api/perf-testing/steps/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        raw = self.client.get(f'/api/perf-testing/steps/{response.data["id"]}/').data
        config = normalize_sse_config(sse_config())
        self.assertEqual(raw['sse_config'], config)
        response = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/save-steps/', {'steps': [raw]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        clone = self.client.post(f'/api/perf-testing/scenarios/{self.scene.pk}/duplicate/', {}, format='json')
        self.assertEqual(clone.status_code, 201, clone.data)
        self.assertEqual(clone.data['steps'][0]['sse_config'], config)
        with mock.patch('apps.perf_testing.engines.k6_version', return_value='synthetic-candidate'):
            execution = executor.create_execution(self.scene, user=self.owner)
        frozen = executor._execution_snapshot(execution)
        self.scene.steps.update(sse_config={})
        self.assertEqual(executor._execution_snapshot(execution), frozen)
        self.assertEqual(frozen['steps'][0]['sse_config'], config)
        self.assertNotIn('sse_config', reporter.report_steps(execution)[0])

    def test_prepared_sse_save_reload_and_source_body_validation(self):
        document = {'openapi': '3.0.3', 'info': {'title': 'Stream fixture', 'version': '1'}, 'paths': {
            '/stream': {'post': {'responses': {'200': {'description': 'Stream', 'content': {'text/event-stream': {'schema': {'type': 'string'}}}}}}}}}
        response = self.upload(document)
        self.assertEqual(response.status_code, 201, response.data)
        env = self.configure(token_variable='stream_token')
        env.variables = [{'name': 'stream_token', 'type': 'CONSTANT', 'value': 'FAKE_TOKEN'}]
        env.save()
        self.assertEqual(self.prepare().status_code, 200)
        row = models.PerfPreparedRequest.objects.get(source_key='POST /stream')
        response = self.save_edit(row.source_metadata['id'], row.revision,
            {'protocol': 'SSE', 'sse_config': sse_config(), 'assertions': [], 'extractors': []})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['request']['protocol'], 'SSE')
        self.assertEqual(response.data['prepared']['status'], 'unverified', response.data['prepared'])
        row.refresh_from_db()
        old_hash = prepared_requests.definition_hash(row)
        saved = self.client.get(self.edit_url(row.source_metadata['id'])).data
        same = self.save_edit(row.source_metadata['id'], row.revision, saved['request'], saved['preparation'])
        self.assertEqual(same.status_code, 200, same.data)
        self.assertEqual(same.data['prepared']['revision'], row.revision)
        self.assertEqual(prepared_requests.step_kwargs(row)['sse_config'], normalize_sse_config(sse_config()))
        changed = deepcopy(saved['request']); changed['sse_config']['idle_ms'] = 200
        updated = self.save_edit(row.source_metadata['id'], row.revision, changed, saved['preparation'])
        self.assertEqual(updated.status_code, 200, updated.data)
        row.refresh_from_db()
        self.assertNotEqual(prepared_requests.definition_hash(row), old_hash)
        from apps.perf_testing.services import prepared_dependencies
        reference = dict(source_key=row.source_key, revision=row.revision, definition_hash=prepared_requests.definition_hash(row),
            extractors=[{'name': 'stream_value', 'type': 'JSON_PATH', 'expr': '$.value'}])
        consumer = SimpleNamespace(project_id=self.project.pk, source_key='GET /consumer', request={},
            preparation={'setup_steps': [reference]}, context=row.context, context_fingerprint=row.context_fingerprint)
        dependencies = prepared_dependencies.resolve_setup_steps(consumer, variables=[{'name': 'stream_token'}])
        self.assertEqual(dependencies[0]['kwargs']['protocol'], 'SSE')
        self.assertEqual(dependencies[0]['kwargs']['extractors'], [])
        self.assertEqual(prepared_dependencies.request_extractors(dependencies[0]['kwargs']), reference['extractors'])
