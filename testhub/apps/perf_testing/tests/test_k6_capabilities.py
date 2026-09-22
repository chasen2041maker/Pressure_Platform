"""Version-bound capability responses; all runner probes are replaced, no traffic."""
import copy
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import tempfile
from unittest import mock

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.perf_testing import engines
from apps.perf_testing.engines import k6_docker, k6_engine
from apps.perf_testing.tests.test_k6_engine import snapshot
from apps.perf_testing.views import EngineStatusView
from apps.perf_testing.services import k6_capabilities as capabilities


VERSION = '2.2.1-0.20260915101307-0ae3c2989e5b'
COMMIT = '0ae3c2989e5b7d24e5d18080aa72aa968ad5015f'
DOCKER = dict(binary_version=f'k6 v{VERSION} (go1.25.1, linux/amd64)',
              binary_sha256='a' * 64, image_id='sha256:' + 'b' * 64,
              network='private-network', network_id='c' * 64, cpus='8', memory='2g',
              docker='private-path', binary='private-binary-path', password='secret-marker')
LINUX_SSE_SHA = '87a37de604f3c40829597faf91eee24915db55dc4d923be35a3798d368b17e64'
WINDOWS_SSE_SHA = '996ef029c8f5d7f237ef5be76aa359c9c2e8ea3e11326017d4c18451cd4b266a'
def custom_version(name='k6', system='linux'):
    return f'{name} v(devel) (go1.26.5, {system}/amd64)\nExtensions:\n  pressure.local/bounded-sse , k6/x/testhub-sse [js]'


class K6CapabilityTests(SimpleTestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict('os.environ', {'K6_RUNNER': 'DOCKER'}).start()
        self.resolve = mock.patch.object(k6_docker, 'resolve_config', return_value=DOCKER).start()
        mock.patch.object(engines, 'k6_available', return_value=True).start()
        mock.patch.object(engines, 'k6_version', return_value=k6_docker.fingerprint(DOCKER)).start()
        mock.patch.object(engines, 'locust_available', return_value=False).start()
        mock.patch.object(engines, 'locust_version', return_value='').start()
        mock.patch.object(engines, 'jmeter_available', return_value=False).start()
        mock.patch.object(engines, 'jmeter_version', return_value='').start()
        mock.patch.object(EngineStatusView, '_websocket_available', return_value=False).start()
        mock.patch.object(engines, '_status_cache', {'ts': 0.0, 'data': None}).start()

    def api(self):
        request = APIRequestFactory().get('/api/perf-testing/engines/status/')
        force_authenticate(request, user=mock.Mock(is_authenticated=True))
        return EngineStatusView.as_view()(request)

    def service(self, available=True, version=None):
        name = 'apps.perf_testing.services.k6_capabilities'
        self.assertIsNotNone(importlib.util.find_spec(name), 'capability service must exist')
        module = __import__(name, fromlist=['build_capabilities'])
        return module.build_capabilities(available=available,
                                         version=version or k6_docker.fingerprint(DOCKER))

    def test_api_adds_capabilities_without_changing_existing_engine_contract(self):
        response = self.api()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'engines', 'websocket', 'limits'})
        statuses = {item['name']: item for item in response.data['engines']}
        self.assertEqual(set(statuses), {'K6', 'BUILTIN', 'LOCUST', 'JMETER'})
        self.assertIn('capabilities', statuses['K6'])
        for name in ('BUILTIN', 'LOCUST', 'JMETER'):
            self.assertEqual(set(statuses[name]), {'name', 'label', 'available', 'version', 'description'})
        self.assertEqual(statuses['K6']['version'], k6_docker.fingerprint(DOCKER))
        capabilities = statuses['K6']['capabilities']
        self.assertEqual(capabilities['verification']['state'], 'matched')
        self.assertEqual(capabilities['runtime']['binary_version'], VERSION)
        self.assertEqual(capabilities['runtime']['source_commit'], COMMIT)
        self.assertEqual(capabilities['runtime']['image_id'], DOCKER['image_id'])
        self.assertEqual(capabilities['runtime']['adapter_version'], '0.12.1')
        raw = json.dumps(response.data)
        for secret in ('secret-marker', 'private-path', 'private-binary-path', 'private-network'):
            self.assertNotIn(secret, raw)

    def test_capability_contract_separates_form_script_and_runtime(self):
        result = self.service()
        rows = {row['id']: row for row in result['items']}
        self.assertEqual(len(rows), len(result['items']))
        for row in rows.values():
            for key in ('official_name', 'source', 'entry', 'config_path', 'execution_path',
                        'report_path', 'evidence', 'form_status', 'script_status', 'enabled', 'reason_code'):
                self.assertIn(key, row)
            self.assertIn(row['form_status'], ('accepted', 'planned', 'dependency', 'absent'))
            self.assertEqual(row['script_status'], 'absent' if row['form_status'] == 'absent'
                             else 'dependency' if row['form_status'] == 'dependency' else 'planned')
            self.assertIn(COMMIT, row['source'])
        for key in ('debug', 'constant-vus', 'per-vu-iterations', 'http-basic', 'csv-identity',
                    'assertions-basic', 'per-vu-setup', 'stop', 'report', 'websocket', 'reminder-recovery'):
            self.assertTrue(rows[key]['enabled'], key)
        for key in ('shared-iterations', 'ramping-vus', 'constant-arrival-rate',
                    'ramping-arrival-rate', 'native-script', 'proxy', 'grpc'):
            self.assertFalse(rows[key]['enabled'], key)
        self.assertEqual(rows['externally-controlled']['form_status'], 'absent')
        for key in ('browser', 'cloud', 'extensions'):
            self.assertEqual(rows[key]['form_status'], 'dependency')
            self.assertFalse(rows[key]['enabled'])

    def test_current_acceptance_is_limited_to_real_snapshot_contract(self):
        self.assertEqual(k6_engine.validate_snapshot(snapshot()), [])
        for kind, key, value in [('load_config', 'model', 'RPS'), ('load_config', 'ramp_up', 3),
                                 ('script_ref', 'mode', 'script'), ('runtime_config', 'follow_redirects', True)]:
            candidate = copy.deepcopy(snapshot())
            candidate.setdefault(kind, {})[key] = value
            self.assertTrue(k6_engine.validate_snapshot(candidate))

    def test_unknown_or_different_binary_does_not_borrow_baseline_commit(self):
        for value in ('k6 v2.3.0 (linux/amd64)', 'unknown', 'k6 v' + VERSION + '-modified'):
            with self.subTest(value=value):
                config = {**DOCKER, 'binary_version': value}
                self.resolve.return_value = config
                result = self.service(version=k6_docker.fingerprint(config))
                self.assertNotEqual(result['verification']['state'], 'matched')
                self.assertIsNone(result['runtime']['source_commit'])
                self.assertFalse(any(row['enabled'] for row in result['items']))
                self.assertTrue(result['verification']['differences'])
                self.assertFalse(any(row['form_status'] == 'accepted' for row in result['items']))

    def test_changed_adapter_requires_new_acceptance(self):
        for adapter in ('0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.8.1', 'unverified-test-adapter'):
            with self.subTest(adapter=adapter), mock.patch.object(k6_engine, 'ADAPTER_VERSION', adapter):
                result = self.service()
                self.assertEqual(result['verification']['state'], 'mismatch')
                self.assertEqual(result['reason_code'], 'version_unverified')
                self.assertIn('adapter_version', [x['field'] for x in result['verification']['differences']])
                self.assertFalse(any(row['enabled'] for row in result['items']))

    def test_reviewed_0120_baseline_matches_actual_engine(self):
        self.assertEqual(k6_engine.ADAPTER_VERSION, '0.12.1')
        result = self.service()
        self.assertEqual(result['baseline']['adapter_version'], '0.12.1')
        self.assertEqual(result['runtime']['adapter_version'], '0.12.1')
        self.assertEqual(result['matrix_revision'], '2026-09-21.1')
        self.assertEqual(result['reason_code'], 'ready')
        self.assertEqual(result['verification'], {'state': 'matched', 'differences': []})

    def test_fingerprint_race_fails_closed(self):
        result = self.service(version='k6-docker:' + 'd' * 64)
        self.assertEqual(result['verification']['state'], 'unverified')
        self.assertEqual(result['reason_code'], 'runner_changed')
        self.assertFalse(any(row['enabled'] for row in result['items']))

    def test_unavailable_runner_has_safe_reason_and_no_500(self):
        for error in (k6_docker.DockerRunnerError('secret-marker'), OSError('secret-marker')):
            with self.subTest(error=type(error).__name__):
                self.resolve.side_effect = error
                result = self.service(available=False)
                self.assertEqual(result['reason_code'], 'runner_unavailable')
                self.assertFalse(result['available'])
                self.assertFalse(any(row['enabled'] for row in result['items']))
                self.assertNotIn('secret-marker', json.dumps(result))
        self.resolve.side_effect = k6_docker.DockerRunnerError('secret-marker')
        response = self.api()
        self.assertEqual(response.status_code, 200)
        self.assertIn('capabilities', response.data['engines'][0])
        self.assertFalse(response.data['engines'][0]['capabilities']['available'])

    def test_native_identity_is_version_bound_and_proxy_is_not_claimed_accepted(self):
        with mock.patch.dict('os.environ', {'K6_RUNNER': 'NATIVE'}):
            result = self.service(version=f'k6 v{VERSION} (go1.25.1, windows/amd64)')
        self.resolve.assert_not_called()
        self.assertEqual(result['verification']['state'], 'matched')
        self.assertIsNone(result['runtime']['image_id'])
        proxy = next(row for row in result['items'] if row['id'] == 'proxy')
        self.assertFalse(proxy['enabled'])
        self.assertEqual(proxy['reason_code'], 'not_validated')

    def test_native_windows_executable_name_accepts_only_the_pinned_version(self):
        version = f'k6.exe v{VERSION} (commit/0ae3c2989e, go1.26.5, windows/amd64)'
        with mock.patch.dict('os.environ', {'K6_RUNNER': 'NATIVE'}):
            result = self.service(version=version)
        self.assertEqual(result['verification'], {'state': 'matched', 'differences': []})
        self.assertEqual(result['runtime']['binary_version'], VERSION)
        self.assertEqual(result['runtime']['source_commit'], COMMIT)
        self.assertTrue(next(row for row in result['items'] if row['id'] == 'debug')['enabled'])

    def test_native_version_parser_rejects_other_names_versions_and_multiline_output(self):
        values = (
            f'xk6.exe v{VERSION}', f'k6Xexe v{VERSION}', f'k6.exe.bak v{VERSION}',
            f'prefix k6.exe v{VERSION}', f'k6.exe v{VERSION} trailing',
            f'k6.exe v{VERSION}\n(go1.26.5, windows/amd64)',
            f'k6 v{VERSION}\n(go1.26.5, linux/amd64)',
            f'k6.exe v{VERSION} (go1.26.5,\nwindows/amd64)',
            f'k6.exe v{VERSION}\n', 'k6.exe v2.3.0', f'k6.exe v{VERSION}-modified',
        )
        with mock.patch.dict('os.environ', {'K6_RUNNER': 'NATIVE'}):
            for value in values:
                with self.subTest(value=value):
                    result = self.service(version=value)
                    self.assertNotEqual(result['verification']['state'], 'matched')
                    self.assertIsNone(result['runtime']['source_commit'])
                    self.assertFalse(any(row['enabled'] for row in result['items']))

    def test_websocket_acceptance_is_explicitly_limited_to_manual_correlated_json_sessions(self):
        result = self.service()
        row = next(row for row in result['items'] if row['id'] == 'websocket')
        self.assertTrue(row['enabled'])
        self.assertEqual(row['script_status'], 'planned')
        self.assertEqual(row['official_name'], 'k6/websockets (bounded JSON command and push sessions)')
        self.assertIn('手工', row['entry'])
        self.assertIn('steps[].websocket_config', row['config_path'])
        self.assertIn('二进制', row['config_path'])
        self.assertIn('自动重连', row['config_path'])
        self.assertIn('SSE', row['config_path'])
        self.assertIn('test_k6_websocket_integration', row['evidence'])

    def test_status_cache_does_not_reprobe_runner_per_request(self):
        self.assertEqual(self.api().status_code, 200)
        self.assertEqual(self.api().status_code, 200)
        self.resolve.assert_called_once()

    def test_reviewed_docker_sse_is_sha_and_extension_bound_without_version_impersonation(self):
        config={**DOCKER,'binary_version':custom_version(),'binary_sha256':LINUX_SSE_SHA}
        self.resolve.return_value=config
        result=self.service(version=k6_docker.fingerprint(config))
        self.assertEqual(result['verification'],{'state':'matched','differences':[]})
        self.assertEqual(result['runtime']['binary_version'],'(devel)')
        self.assertEqual(result['runtime']['source_commit'],COMMIT)
        self.assertEqual(result['runtime']['binary_sha256'],LINUX_SSE_SHA)
        self.assertTrue(next(row for row in result['items'] if row['id']=='sse')['enabled'])
        self.assertTrue(next(row for row in result['items'] if row['id']=='http-basic')['enabled'])

    def test_custom_sse_rejects_unknown_hash_missing_or_extra_extensions_wrong_platform_and_race(self):
        valid={**DOCKER,'binary_version':custom_version(),'binary_sha256':LINUX_SSE_SHA}
        for change in [{'binary_sha256':'d'*64},{'binary_sha256':WINDOWS_SSE_SHA},
                       {'binary_version':custom_version().split('\n')[0]},
                       {'binary_version':custom_version()+'\n  other/module [js]'},
                       {'binary_version':custom_version(system='windows')},
                       {'binary_version':custom_version().replace('go1.26.5','go1.25.1')}]:
            config={**valid,**change};self.resolve.return_value=config
            result=self.service(version=k6_docker.fingerprint(config))
            self.assertFalse(any(row['enabled'] for row in result['items']),change)
            self.assertIsNone(result['runtime']['source_commit'])
        self.resolve.return_value=valid
        self.assertEqual(self.service(version='k6-docker:'+'f'*64)['reason_code'],'runner_changed')

    def test_plain_baseline_retains_http_but_does_not_claim_sse_module(self):
        result=self.service()
        row=next(row for row in result['items'] if row['id']=='sse')
        self.assertFalse(row['enabled'])
        self.assertEqual(row['reason_code'],'runtime_extension_required')
        self.assertTrue(next(row for row in result['items'] if row['id']=='http-basic')['enabled'])

    def test_native_custom_binary_is_bound_to_actual_basename_and_hash(self):
        version=custom_version('renamed-reviewed.exe','windows')
        with mock.patch.dict(os.environ,{'K6_RUNNER':'NATIVE'}),mock.patch.object(capabilities,'_native_identity',return_value=(WINDOWS_SSE_SHA,'renamed-reviewed.exe')):
            result=self.service(version=version)
            self.assertEqual(result['verification']['state'],'matched')
            self.assertEqual(result['runtime']['binary_version'],'(devel)')
            self.assertTrue(next(row for row in result['items'] if row['id']=='sse')['enabled'])
            self.assertFalse(any(row['enabled'] for row in self.service(version=custom_version('another.exe','windows'))['items']))
        with mock.patch.dict(os.environ,{'K6_RUNNER':'NATIVE'}),mock.patch.object(capabilities,'_native_identity',return_value=('f'*64,'renamed-reviewed.exe')):
            result=self.service(version=version)
            self.assertFalse(any(row['enabled'] for row in result['items']))
            self.assertIsNone(result['runtime']['source_commit'])
        self.resolve.assert_not_called()

    def test_native_hash_cache_is_bounded_and_invalidated_by_file_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'reviewed.exe';path.write_bytes(b'synthetic-review-binary')
            version=custom_version(path.name,'windows')
            with mock.patch.object(k6_engine,'_binary',return_value=str(path)),mock.patch.object(k6_engine,'get_version',return_value=version) as probe:
                first=capabilities._native_identity(version)
                self.assertEqual(first,(hashlib.sha256(path.read_bytes()).hexdigest(),path.name))
                self.assertEqual(capabilities._native_identity(version),first)
                self.assertEqual(probe.call_count,1)
                path.write_bytes(b'changed-synthetic-review-binary')
                self.assertNotEqual(capabilities._native_identity(version),first)
                self.assertEqual(probe.call_count,2)
                with mock.patch.object(capabilities,'_MAX_NATIVE_BYTES',1),self.assertRaises(ValueError):
                    capabilities._native_identity(version)

    def test_native_identity_cache_expires_and_retains_at_most_eight_files(self):
        with tempfile.TemporaryDirectory() as folder,mock.patch.object(capabilities,'_native_cache',capabilities.OrderedDict()),mock.patch.object(capabilities.time,'monotonic',return_value=10) as clock:
            path=Path(folder)/'first.exe';path.write_bytes(b'cache-fixture')
            version=custom_version(path.name,'windows')
            with mock.patch.object(k6_engine,'_binary',return_value=str(path)),mock.patch.object(k6_engine,'get_version',return_value=version) as probe:
                capabilities._native_identity(version);clock.return_value=26;capabilities._native_identity(version)
                self.assertEqual(probe.call_count,2)
            for number in range(9):
                path=Path(folder)/f'fixture-{number}.exe';path.write_bytes(b'cache-fixture')
                version=custom_version(path.name,'windows')
                with mock.patch.object(k6_engine,'_binary',return_value=str(path)),mock.patch.object(k6_engine,'get_version',return_value=version):
                    capabilities._native_identity(version)
            self.assertEqual(len(capabilities._native_cache),8)

    def test_native_version_and_file_replacement_races_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'reviewed.exe';path.write_bytes(b'synthetic-review-binary')
            version=custom_version(path.name,'windows')
            def replace_during_probe():
                path.write_bytes(b'replaced-during-version-probe')
                return version
            for probe in [lambda:custom_version('other.exe','windows'),replace_during_probe]:
                with mock.patch.dict(os.environ,{'K6_RUNNER':'NATIVE'}),mock.patch.object(k6_engine,'_binary',return_value=str(path)),mock.patch.object(k6_engine,'get_version',side_effect=probe):
                    result=self.service(version=version)
                self.assertEqual(result['reason_code'],'runner_changed')
                self.assertFalse(any(row['enabled'] for row in result['items']))
