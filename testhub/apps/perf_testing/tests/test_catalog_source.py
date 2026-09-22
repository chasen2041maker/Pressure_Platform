"""URL imports use disposable DBs and a local synthetic HTTP server only."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.perf_testing import models
from apps.perf_testing.services import api_catalog, catalog_source
from . import test_api_catalog as catalog_tests

contract = catalog_tests.contract


class SourceHTTPTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                cls.seen.append((self.path,self.headers.get('Authorization'),self.headers.get('Cookie')))
                if self.path=='/slow-headers':
                    try:
                        for byte in b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}':
                            self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.02)
                    except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
                    return
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','/spec.json');self.end_headers();return
                if self.path=='/outside':
                    self.send_response(302);self.send_header('Location','http://127.0.0.1:1/spec.json');self.end_headers();return
                if self.path=='/loop':
                    self.send_response(302);self.send_header('Location','/loop');self.end_headers();return
                self.send_response(200)
                if self.path=='/huge':self.send_header('Content-Length',str(api_catalog.MAX_BYTES+1))
                self.end_headers()
                body=json.dumps(contract()).encode()
                if self.path=='/swagger/':body=b'<html><script>SwaggerUIBundle({dom_id: "#ui", url: "../spec.json"})</script></html>'
                if self.path=='/dynamic':body=b'<html><script>SwaggerUIBundle({url: config.url})</script></html>'
                if self.path=='/ambiguous':body=b'<html><script>SwaggerUIBundle({url:"/a",url:"/b"})</script></html>'
                if self.path=='/html-outside':body=b'<html><script>SwaggerUIBundle({url:"http://127.0.0.1:1/spec.json"})</script></html>'
                if self.path=='/slow':time.sleep(.2)
                if self.path=='/over-body':body=b'x'*129
                try:self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.origin=f'http://127.0.0.1:{cls.server.server_port}'
        cls.options=override_settings(PERF_CATALOG_ALLOWED_ORIGINS=[cls.origin]);cls.options.enable()

    @classmethod
    def tearDownClass(cls):
        cls.options.disable();cls.server.shutdown();cls.server.server_close();cls.thread.join(2)
        super().tearDownClass()

    def test_direct_page_and_same_origin_redirect_without_auth_or_proxy(self):
        with mock.patch.dict('os.environ',{'HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1'}):
            for path in ('/spec.json','/swagger/#/','/redirect'):
                parsed,source,fingerprint=catalog_source.fetch(self.origin+path)
                self.assertEqual(len(parsed['operations']),3)
                self.assertEqual(source['document_url'],self.origin+'/spec.json')
                self.assertNotIn('#',source['url'])
                self.assertEqual(len(fingerprint),64)
        self.assertTrue(all(auth is None and cookie is None for _,auth,cookie in self.seen))

    def test_invalid_origin_credentials_query_and_controls_never_fetch(self):
        before=len(self.seen)
        for url in ('file:///tmp/a','http://127.0.0.1:1/a',self.origin+'/a?token=x',
                    self.origin.replace('http://','http://user:secret@')+'/a',self.origin+'/a\\b',
                    self.origin+'/a\nX: injected',self.origin+'/a?'):
            with self.subTest(url=url),self.assertRaises(api_catalog.CatalogInputError):catalog_source.fetch(url)
        with override_settings(PERF_CATALOG_ALLOWED_ORIGINS=[]),self.assertRaises(api_catalog.CatalogInputError):
            catalog_source.fetch(self.origin+'/spec.json')
        self.assertEqual(len(self.seen),before)

    def test_redirect_limits_dynamic_html_and_cross_origin_document_fail(self):
        for path in ('/outside','/loop','/dynamic','/ambiguous','/html-outside','/huge'):
            with self.subTest(path=path),self.assertRaises(api_catalog.CatalogInputError):catalog_source.fetch(self.origin+path)
        with mock.patch.object(api_catalog,'MAX_BYTES',128),self.assertRaises(api_catalog.CatalogInputError):
            catalog_source.fetch(self.origin+'/over-body')
        with mock.patch.object(catalog_source,'TOTAL_TIMEOUT',.05),self.assertRaises(api_catalog.CatalogInputError):
            catalog_source.fetch(self.origin+'/slow')
        started=time.monotonic()
        with mock.patch.object(catalog_source,'TOTAL_TIMEOUT',.1),self.assertRaises(api_catalog.CatalogInputError):
            catalog_source.fetch(self.origin+'/slow-headers')
        self.assertLess(time.monotonic()-started,.8)


@override_settings(PERF_CATALOG_ALLOWED_ORIGINS=['http://catalog.invalid'])
class CatalogSourceTests(TestCase):
    setUp=catalog_tests.ApiCatalogTests.setUp
    project_url=catalog_tests.ApiCatalogTests.project_url
    upload=catalog_tests.ApiCatalogTests.upload

    def fetched(self,doc=None,url='http://catalog.invalid/swagger/'):
        doc=doc or contract()
        return api_catalog.parse_document(doc),{'url':url,'document_url':'http://catalog.invalid/spec.yaml',
            'checked_at':'2026-09-16T00:00:00+00:00'},api_catalog.digest(doc)

    def preview(self,url='http://catalog.invalid/swagger/#/'):
        return self.client.post(self.project_url('api-catalog/preview'),{'source_url':url},format='multipart')

    def confirm(self,preview,version=0,**extra):
        return self.client.post(self.project_url('api-catalog/import'),{'source_url':preview.data['source']['url'],
            'preview_token':preview.data['preview_token'],'expected_version':version,**extra},format='multipart')

    def test_preview_import_refresh_no_change_and_file_clears_readonly_source(self):
        with mock.patch.object(catalog_source,'fetch',return_value=self.fetched()):
            preview=self.preview();self.assertEqual(preview.status_code,200,preview.data)
            self.project.refresh_from_db();self.assertEqual(self.project.catalog_source,{})
            self.assertFalse(models.PerfApiCatalogVersion.objects.exists())
            imported=self.confirm(preview);self.assertEqual(imported.status_code,201,imported.data)
            self.assertEqual(self.client.get(self.project_url()).data['source'],preview.data['source'])
            ops=self.client.get(self.project_url()).data['results']
            api_catalog.import_steps(self.scene,[ops[0]['id']],self.owner)
            before=models.PerfScenarioStep.objects.values().get()
            second=self.preview();self.assertEqual(self.confirm(second,1).status_code,200)
        changed=contract();changed['paths']['/new']={'get':{'responses':{'200':{'description':'ok'}}}}
        with mock.patch.object(catalog_source,'fetch',return_value=self.fetched(changed)):
            preview=self.preview();self.assertEqual(len(preview.data['diff']['added']),1)
            self.assertEqual(self.confirm(preview,1).status_code,201)
        self.assertEqual(models.PerfScenarioStep.objects.values().get(),before)
        self.assertEqual(self.upload(version=2).status_code,201)
        self.project.refresh_from_db();self.assertEqual(self.project.catalog_source,{})
        self.client.patch(f'/api/perf-testing/projects/{self.project.pk}/',{'catalog_source':{'url':'forged'}},format='json')
        self.project.refresh_from_db();self.assertEqual(self.project.catalog_source,{})

    def test_preview_token_binding_expiry_and_changed_document_leave_existing_data_untouched(self):
        with mock.patch.object(catalog_source,'fetch',return_value=self.fetched()):
            preview=self.preview();self.assertEqual(self.confirm(preview).status_code,201)
            preview=self.preview()
        version=api_catalog.latest_version(self.project.pk)
        api_catalog.import_steps(self.scene,[version.operations[0]['id']],self.owner)
        old_step=models.PerfScenarioStep.objects.values().get()
        self.project.refresh_from_db();old=deepcopy(self.project.catalog_source)
        before=list(models.PerfApiCatalogVersion.objects.values())
        changed=contract();changed['info']['version']='changed'
        for result in (self.fetched(changed),self.fetched(url='http://catalog.invalid/elsewhere')):
            with mock.patch.object(catalog_source,'fetch',return_value=result):
                self.assertEqual(self.confirm(preview,1).status_code,409)
        different_document=self.fetched();different_document[1]['document_url']='http://catalog.invalid/other.yaml'
        with mock.patch.object(catalog_source,'fetch',return_value=different_document):
            self.assertEqual(self.confirm(preview,1).status_code,409)
        with mock.patch.object(catalog_source,'fetch',side_effect=api_catalog.CatalogInputError('读取失败')):
            self.assertEqual(self.confirm(preview,1).status_code,400)
        with mock.patch.object(catalog_source,'fetch',return_value=self.fetched()) as fetch:
            for extra in ({'expected_version':0},{'preview_token':'invalid'},{'source_url':'http://catalog.invalid/other'}):
                self.assertEqual(self.confirm(preview,1,**extra).status_code,409)
            with mock.patch('django.core.signing.time.time',return_value=time.time()+601):
                self.assertEqual(self.confirm(preview,1).status_code,409)
            another=models.PerfProject.objects.create(name='Same owner other project',owner=self.owner)
            result=self.client.post(self.project_url('api-catalog/import',another),{
                'source_url':preview.data['source']['url'],'preview_token':preview.data['preview_token'],
                'expected_version':1},format='multipart')
            self.assertEqual(result.status_code,409)
            self.client.force_authenticate(self.member)
            self.assertEqual(self.confirm(preview,1).status_code,409)
            fetch.assert_not_called()
        self.project.refresh_from_db();self.assertEqual(self.project.catalog_source,old)
        self.assertEqual(list(models.PerfApiCatalogVersion.objects.values()),before)
        self.assertEqual(models.PerfScenarioStep.objects.values().get(),old_step)

    def test_permission_and_exclusive_inputs_precede_fetch_and_invalid_import_never_writes(self):
        with mock.patch.object(catalog_source,'fetch') as fetch:
            self.client.force_authenticate(self.outside)
            self.assertIn(self.preview().status_code,(403,404))
            self.client.force_authenticate(self.owner)
            result=self.client.post(self.project_url('api-catalog/preview'),{'source_url':'http://catalog.invalid/spec',
                'file':SimpleUploadedFile('spec.json',json.dumps(contract()).encode())},format='multipart')
            self.assertEqual(result.status_code,400)
            self.assertEqual(self.client.post(self.project_url('api-catalog/import'),{
                'source_url':'http://catalog.invalid/spec','expected_version':0},format='multipart').status_code,409)
            fetch.assert_not_called()
        self.assertFalse(models.PerfApiCatalogVersion.objects.exists())

    def test_two_previews_cannot_replace_more_recent_source_at_same_catalog_version(self):
        with mock.patch.object(catalog_source,'fetch',return_value=self.fetched()):
            first=self.preview();self.assertEqual(self.confirm(first).status_code,201)
            stale=self.preview();latest=self.preview()
        newer=self.fetched();newer[1]['checked_at']='2026-09-16T00:01:00+00:00'
        with mock.patch.object(catalog_source,'fetch',return_value=newer):
            self.assertEqual(self.confirm(latest,1).status_code,200)
            self.assertEqual(self.confirm(stale,1).status_code,409)
        self.assertEqual(models.PerfApiCatalogVersion.objects.count(),1)
        self.project.refresh_from_db();self.assertEqual(self.project.catalog_source,newer[1])


class CatalogYamlTests(SimpleTestCase):
    def test_yaml_merge_expansion_is_rejected_before_flattening(self):
        lines=['openapi: 3.0.3', "paths: {/health: {get: {responses: {'200': {description: ok}}}}}",
               'x0: &a0 {key: 1}']
        for n in range(1,11):lines.append(f'x{n}: &a{n} {{<<: [*a{n-1}, *a{n-1}, *a{n-1}]}}')
        with self.assertRaisesRegex(api_catalog.CatalogInputError,'合并'):
            api_catalog.parse_document('\n'.join(lines).encode())

    def test_repeated_anchor_uses_nearest_prior_definition_without_changing_previous_alias(self):
        parsed=api_catalog.parse_document(b'''openapi: 3.0.3
info: {title: fixture, version: '1'}
x-first: &same {type: integer, default: 0}
x-before: *same
x-second: &same {type: boolean, default: false}
x-after: *same
paths: {/health: {get: {responses: {'200': {description: ok}}}}}
''')
        doc=parsed['document']
        self.assertEqual(doc['x-before'],{'type':'integer','default':0})
        self.assertEqual(doc['x-after'],{'type':'boolean','default':False})
        self.assertIs(type(doc['x-before']['default']),int)
        self.assertIs(type(doc['x-after']['default']),bool)

    def test_cycle_depth_alias_expansion_and_unsafe_tags_still_rejected(self):
        for tail in ('x: &a [*a]', 'x: !!python/object/apply:os.system ["never"]',
                     'x: '+'['*90+'0'+']'*90):
            with self.subTest(tail=tail[:30]),self.assertRaises(api_catalog.CatalogInputError):
                api_catalog.parse_document(('openapi: 3.0.3\npaths: {}\n'+tail).encode())
        aliases='x: &a 1\ny: ['+','.join('*a' for _ in range(10001))+']'
        with self.assertRaises(api_catalog.CatalogInputError):
            api_catalog.parse_document(('openapi: 3.0.3\npaths: {}\n'+aliases).encode())
