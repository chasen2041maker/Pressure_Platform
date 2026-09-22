"""Project-owned uploads remain immutable and become real HTTP request bytes."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from http.server import ThreadingHTTPServer
from email.parser import BytesParser
from email.policy import default

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from apps.perf_testing.engines import k6_engine
from apps.perf_testing.services import executor
from apps.perf_testing import models
from . import test_api_catalog as fixtures
from .test_k6_engine import TEST_ROOT, TestHandler


CONTENT = b'RIFF\x00\x01private-upload-bytes\xffWAVE'


def upload_snapshot(base='http://127.0.0.1:1', binary=False):
    digest = hashlib.sha256(CONTENT).hexdigest()
    return {'engine': 'K6', 'load_config': {'model': 'CONCURRENCY', 'concurrency': 2,
                'duration': 8, 'iterations_per_vu': 2},
        'runtime_config': {'timeout': 2}, 'env_config': {'base_url': base},
        'variables': [], 'csv_data': {}, 'upload_data': {digest: {
            'size': len(CONTENT), 'sha256': digest,
            'base64': base64.b64encode(CONTENT).decode('ascii')}},
        'steps': [{'id': 1, 'name': 'upload', 'method': 'PUT' if binary else 'POST', 'url': '/file',
            'body_type': 'BINARY' if binary else 'FORM',
            'body': '' if binary else '{"language":"zh","user":"{{vu_id}}","empty":""}',
            'files': [{'field': 'file', 'file_id': 1, 'sha256': digest, 'size': len(CONTENT),
                       'filename': 'sample.wav', 'content_type': 'audio/wav'}],
            'assertions': [{'type': 'JSON_PATH', 'expr': '$.ok', 'expected': True}],
            'execution_policy': {'group_id': 'upload', 'vu_start': 2, 'vu_end': 2,
                'max_runs_per_vu': 1, 'min_interval_ms': 0}}]}


class UploadSnapshotTests(unittest.TestCase):
    def test_frozen_form_and_binary_are_accepted(self):
        for binary in (False, True):
            self.assertEqual(k6_engine.validate_snapshot(upload_snapshot(binary=binary)), [])

    def test_forged_hash_missing_blob_and_file_paths_fail_closed(self):
        for mutate in (lambda s: s['upload_data'].clear(),
                       lambda s: s['upload_data'][next(iter(s['upload_data']))].update(base64='Zm9yZ2Vk'),
                       lambda s: s['steps'][0]['files'][0].update(path='C:/private/secret')):
            s = upload_snapshot(); mutate(s)
            self.assertTrue(k6_engine.validate_snapshot(s))

    def test_form_cannot_hide_nested_or_colliding_fields(self):
        for body in ('[]', '{"file":"shadow"}', '{"nested":{"x":1}}'):
            s = upload_snapshot(); s['steps'][0]['body'] = body
            self.assertTrue(k6_engine.validate_snapshot(s))

    def test_malformed_file_descriptors_and_oversize_bytes_fail_as_validation(self):
        for value in ({'path': '/arbitrary'}, ['bad'], [{'field': 'file', 'sha256': {}}]):
            s = upload_snapshot(); s['steps'][0]['files'] = value
            self.assertTrue(k6_engine.validate_snapshot(s))
        with mock.patch('apps.perf_testing.services.upload_files.MAX_BYTES', 8):
            self.assertTrue(k6_engine.validate_snapshot(upload_snapshot()))

    def test_prepare_materializes_bytes_without_inline_runtime_data(self):
        s = upload_snapshot(); original = deepcopy(s)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder, \
                mock.patch.object(k6_engine, 'is_available', return_value=True):
            engine = k6_engine.K6Engine(s, work_dir=folder); engine.prepare()
            payload = json.loads((Path(folder) / 'scenario.private.json').read_text())
            self.assertNotIn('upload_data', payload)
            path = Path(next(iter(payload['upload_files'].values())))
            self.assertEqual(path.read_bytes(), CONTENT)
            self.assertEqual(s, original)


class UploadProjectTests(TestCase):
    setUp = fixtures.ApiCatalogTests.setUp
    project_url = fixtures.ApiCatalogTests.project_url
    upload = fixtures.ApiCatalogTests.upload
    def upload_file(self, project=None):
        return models.PerfDataFile.objects.create(project=project or self.project,
            name='sample.wav', file_type='UPLOAD', uploaded_by=self.owner,
            file=SimpleUploadedFile('sample.wav', CONTENT, 'audio/wav'),
            meta={'content_type': 'audio/wav'})

    def test_snapshot_contains_frozen_hash_and_bytes_after_source_is_changed(self):
        file = self.upload_file()
        models.PerfScenarioStep.objects.create(scenario=self.scene, name='upload', url='/upload',
            method='POST', body_type='FORM', body='{"language":"zh"}',
            files=[{'field': 'file', 'file_id': file.pk}])
        snapshot = executor.build_snapshot(self.scene)
        descriptor = snapshot['steps'][0]['files'][0]
        self.assertNotIn('path', descriptor)
        Path(file.file.path).write_bytes(b'changed')
        self.assertEqual(base64.b64decode(snapshot['upload_data'][descriptor['sha256']]['base64']), CONTENT)
        self.assertEqual(k6_engine.validate_snapshot(snapshot), [])
        from apps.perf_testing.services.k6_execution import save_snapshot, load_snapshot
        with tempfile.TemporaryDirectory() as folder:
            save_snapshot(folder, 17, snapshot)
            Path(file.file.path).unlink()
            frozen = load_snapshot(folder, 17)
            self.assertEqual(k6_engine.validate_snapshot(frozen), [])
            with mock.patch.object(k6_engine, 'is_available', return_value=True):
                engine = k6_engine.K6Engine(frozen, work_dir=Path(folder) / 'run'); engine.prepare()
            files = list((Path(folder) / 'run').glob('upload-*.private.bin'))
            self.assertEqual(files[0].read_bytes(), CONTENT)

    def test_missing_foreign_and_outside_media_files_are_blocked(self):
        from rest_framework.exceptions import ValidationError
        own = self.upload_file(); foreign = self.upload_file(self.other)
        step = models.PerfScenarioStep.objects.create(scenario=self.scene, name='upload', url='/upload',
            method='POST', body_type='FORM', body='{}')
        for file_id in (foreign.pk, 999999):
            step.files = [{'field': 'file', 'file_id': file_id}]; step.save()
            with self.assertRaises(ValidationError): executor.build_snapshot(self.scene)
        step.files = [{'field': 'file', 'file_id': own.pk}]; step.save()
        Path(own.file.path).unlink()
        with self.assertRaises(ValidationError): executor.build_snapshot(self.scene)
        models.PerfDataFile.objects.filter(pk=own.pk).update(file='../outside.wav')
        with self.assertRaises(ValidationError): executor.build_snapshot(self.scene)

    def test_catalog_multipart_requires_real_owned_file_and_scalar_fields(self):
        file = self.upload_file()
        metadata = {'request_body': {'required': True, 'content': {'multipart/form-data': {'schema': {
            'type': 'object', 'required': ['file', 'language'], 'properties': {
                'file': {'type': 'string', 'format': 'binary'}, 'language': {'type': 'string'}}}}}},
            'gaps': [{'code': 'engine_body_type', 'field': 'body', 'message': 'legacy unsupported'},
                     {'code': 'file_required', 'field': 'files/file', 'message': 'choose file'}]}
        request = {'url': '/file', 'body_type': 'FORM', 'body': '{"language":"zh"}',
                   'files': [{'field': 'file', 'file_id': file.pk}]}
        ready = self.service.request_readiness(request, metadata, project_id=self.project.pk)
        self.assertTrue(ready['ready'], ready)
        request['body'] = '{}'
        self.assertFalse(self.service.request_readiness(request, metadata, project_id=self.project.pk)['ready'])
        request['body'] = '{"language":"zh"}'
        Path(file.file.path).unlink()
        self.assertFalse(self.service.request_readiness(request, metadata, project_id=self.project.pk)['ready'])

    def test_normal_catalog_file_edit_save_reload_and_snapshot(self):
        from .test_prepared_requests import PreparedRequestTests
        schema = {'type': 'object', 'required': ['file', 'language'], 'properties': {
            'file': {'type': 'string', 'format': 'binary'}, 'language': {'type': 'string'}}}
        doc = {'openapi': '3.0.3', 'info': {'title': 'Upload fixture', 'version': '1'}, 'paths': {
            '/file': {'post': {'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': schema}}},
                'responses': {'200': {'description': 'ok', 'content': {'application/json': {'schema': {
                    'type': 'object', 'properties': {'code': {'type': 'string', 'enum': ['OK']}}}}}}}}}}}
        self.assertEqual(self.upload(doc).status_code, 201)
        PreparedRequestTests.configure(self)
        response = self.client.post(self.project_url('api-pool/prepare'),
            {'expected_catalog_version': 1, 'expected_config_revision': 1}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        row = models.PerfPreparedRequest.objects.get(project=self.project)
        self.assertEqual(row.status, 'blocked')
        url = self.project_url('api-pool/requests/' + str(row.source_metadata['id']))
        loaded = self.client.get(url).data
        file = self.upload_file()
        loaded['request'].update(body='{"language":"zh"}', files=[{'field': 'file', 'file_id': file.pk}])
        response = self.client.put(url, {'expected_catalog_version': 1, 'expected_revision': row.revision,
            'request': loaded['request'], 'preparation': loaded['preparation']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['prepared']['status'], 'unverified', response.data['prepared']['gaps'])
        reopened = self.client.get(url)
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.data['request']['files'][0]['file_id'], file.pk)
        Path(file.file.path).unlink()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_serializer_rejects_paths_header_injection_and_cross_project_patch(self):
        from apps.perf_testing.serializers import PerfScenarioStepSerializer
        file = self.upload_file()
        base = {'scenario': self.scene.pk, 'name': 'upload', 'url': '/file', 'body_type': 'FORM', 'body': '{}'}
        for item in ({'field': 'file', 'file_id': file.pk, 'path': 'C:/private'},
                     {'field': 'file', 'file_id': file.pk, 'filename': 'x\r\nInjected'},
                     {'field': 'file', 'file_id': file.pk, 'content_type': 'audio/wav\r\nInjected'}):
            serializer = PerfScenarioStepSerializer(data=dict(base, files=[item]))
            self.assertFalse(serializer.is_valid(), serializer.errors)
        step = models.PerfScenarioStep.objects.create(scenario=self.scene, name='file', url='/file',
            files=[{'field': 'file', 'file_id': file.pk}])
        other = models.PerfScenario.objects.create(project=self.other, created_by=self.outside,
            name='Other', engine='K6')
        serializer = PerfScenarioStepSerializer(step, data={'scenario': other.pk}, partial=True)
        self.assertFalse(serializer.is_valid(), serializer.errors)

    def test_source_file_change_invalidates_prior_verification_definition(self):
        from apps.perf_testing.services.prepared_requests import definition_hash
        file = self.upload_file()
        row = SimpleNamespace(project_id=self.project.pk, revision=1,
            request={'files': [{'field': 'file', 'file_id': file.pk}]}, preparation={},
            source_metadata={}, context={}, source_hash='', source_asset_hash='')
        before = definition_hash(row)
        Path(file.file.path).write_bytes(b'new-file-version')
        self.assertNotEqual(definition_hash(row), before)
        Path(file.file.path).unlink()
        self.assertNotEqual(definition_hash(row), before)
        row.request['files'][0]['file_id'] = None
        self.assertIsInstance(definition_hash(row), str)


@unittest.skipUnless(k6_engine.is_available(), 'native k6 required')
class UploadNativeTests(unittest.TestCase):
    def test_object_valued_form_variable_never_sends_a_request(self):
        from apps.perf_testing.engines.base import EngineError
        seen = []
        class Handler(TestHandler):
            def do_POST(self):
                seen.append(True); self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200); self.send_header('Content-Length', '11'); self.end_headers(); self.wfile.write(b'{"ok":true}')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            data = upload_snapshot(f'http://127.0.0.1:{server.server_port}')
            data['variables'] = [{'name': 'nested', 'type': 'CONSTANT', 'value': {'not': 'scalar'}}]
            data['steps'][0]['body'] = '{"language":"{{nested}}"}'
            with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
                engine = k6_engine.K6Engine(data, work_dir=folder); engine.prepare()
                with self.assertRaises(EngineError): engine.run()
                self.assertEqual(engine.collect()['summary']['http_started'], 0)
                self.assertEqual(seen, [])
        finally: server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_multipart_and_raw_bytes_with_bounded_attempts(self):
        for binary, reject in ((False, False), (True, False), (False, True)):
            seen = []
            class Handler(TestHandler):
                def do_POST(self):
                    raw = self.rfile.read(int(self.headers['Content-Length']))
                    if binary:
                        ok = raw == CONTENT and self.headers['Content-Type'] == 'audio/wav'
                    else:
                        parsed = BytesParser(policy=default).parsebytes(
                            ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + raw)
                        parts = {part.get_param('name', header='content-disposition'): part for part in parsed.iter_parts()}
                        ok = (parts['file'].get_payload(decode=True) == CONTENT
                            and parts['file'].get_filename() == 'sample.wav'
                            and parts['language'].get_payload(decode=True) == b'zh'
                            and parts['user'].get_payload(decode=True) == b'2'
                            and parts['empty'].get_payload(decode=True) == b'')
                    seen.append(ok); body = json.dumps({'ok': ok}).encode()
                    self.send_response(503 if reject else 200); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
                do_PUT = do_POST
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
                    engine = k6_engine.K6Engine(upload_snapshot(f'http://127.0.0.1:{server.server_port}', binary), work_dir=folder)
                    engine.prepare(); engine.run(); result = engine.collect()['summary']
                    self.assertEqual(seen, [True]); self.assertEqual(result['http_total'], 1)
                    self.assertEqual(result['business_total'], 1); self.assertEqual(result['failed_requests'], int(reject))
                    self.assertNotIn(base64.b64encode(CONTENT).decode(), json.dumps(result))
                    self.assertNotIn('sample.wav', json.dumps(result))
            finally: server.shutdown(); server.server_close(); thread.join(timeout=2)
