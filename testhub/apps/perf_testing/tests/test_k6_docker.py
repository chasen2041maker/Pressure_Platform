"""Docker runner contracts; real tests use a disposable controlled HTTP fixture."""
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from .test_k6_engine import ROOT, TEST_ROOT, TestHandler, snapshot


class DockerRunnerTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('apps.perf_testing.engines.k6_docker'),
                             'Docker runner must own its container lifecycle')
        from apps.perf_testing.engines import k6_docker
        return k6_docker

    def config(self):
        return dict(binary='binary', docker='docker', image_id='sha256:abc',
                    network='fixture', network_id='network1', cpus='8', memory='2g',
                    binary_sha256='binary1', binary_version='k6 v1')

    def test_fingerprint_changes_for_every_execution_dependency(self):
        m = self.module(); config = self.config()
        original = m.fingerprint(config)
        for key in ('image_id', 'network_id', 'cpus', 'memory', 'binary_sha256', 'binary_version'):
            changed = dict(config); changed[key] += '-changed'
            self.assertNotEqual(m.fingerprint(changed), original, key)

    def test_eight_gib_runner_limit_is_supported_and_still_bounded(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            binary = Path(work) / 'binary'; binary.write_bytes(b'fixture')
            digest = hashlib.sha256(b'fixture').hexdigest()
            environment = {'K6_DOCKER_BIN': 'docker', 'K6_LINUX_BIN': str(binary),
                           'K6_DOCKER_NETWORK': 'fixture', 'K6_DOCKER_CPUS': '8'}
            with mock.patch.dict(os.environ, environment), \
                    mock.patch.object(m, '_version_cache', {('sha256:fixture', digest): 'k6 v1'}), \
                    mock.patch.object(m, '_run', return_value=subprocess.CompletedProcess(
                        [], 0, 'sha256:fixture\n', '')) as command:
                for memory in ('8g', '8192m', '2g', '512m'):
                    with self.subTest(memory=memory), mock.patch.dict(os.environ, {'K6_DOCKER_MEMORY': memory}):
                        config = m.resolve_config()
                        self.assertEqual(config['memory'], memory)
                        docker_command = m.DockerGuard(config, Path(work)).create_command()
                        self.assertIn('--memory=' + memory, docker_command)
                        self.assertIn('--memory-swap=' + memory, docker_command)
                for memory in ('9g', '8193m', '0g', '-1g', 'unlimited'):
                    with self.subTest(memory=memory), mock.patch.dict(os.environ, {'K6_DOCKER_MEMORY': memory}):
                        command.reset_mock()
                        with self.assertRaises(m.DockerRunnerError):
                            m.resolve_config()
                        command.assert_not_called()

    def test_prepare_uses_private_linux_paths_without_rewriting_target(self):
        m = self.module()
        from apps.perf_testing.engines import k6_engine
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            binary = Path(work) / 'binary'; binary.write_bytes(b'fixture')
            config = self.config(); config['binary'] = str(binary)
            config['binary_sha256'] = hashlib.sha256(b'fixture').hexdigest()
            run = Path(work) / 'run'
            with mock.patch.dict(os.environ, {'K6_RUNNER': 'DOCKER'}), \
                    mock.patch.object(m, 'resolve_config', return_value=config):
                e = k6_engine.K6Engine(snapshot(), work_dir=run)
                e.prepare()
            payload = json.loads((run / 'scenario.private.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['csv_files']['1'], '/run/csv-0000.private.json')
            self.assertEqual(payload['env_config']['base_url'], 'http://127.0.0.1:1')
            self.assertTrue((run / 'k6').exists())
            self.assertEqual(e.collect()['summary']['runner_mode'], 'DOCKER')

    def test_command_limits_mount_and_container_ownership(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            guard = m.DockerGuard(self.config(), Path(work), execution_id=37)
            cmd = guard.create_command()
            rendered = ' '.join(cmd)
            self.assertIn('io.testhub.k6.execution=37', rendered)
            self.assertIn('io.testhub.k6.owner=' + guard.owner, rendered)
            self.assertIn('--read-only', cmd)
            self.assertIn('--pull=never', cmd)
            self.assertIn('--cap-drop=ALL', cmd)
            self.assertIn('--security-opt=no-new-privileges', cmd)
            self.assertIn('--cpus=8', cmd)
            self.assertIn('--memory=2g', cmd)
            mounts = [cmd[i+1] for i,x in enumerate(cmd) if x == '--mount']
            self.assertEqual(len(mounts), 1)
            self.assertTrue(mounts[0].endswith(',target=/run,readonly'))
            self.assertNotIn('docker.sock', rendered)
            self.assertNotIn('--privileged', cmd)

    def test_cleanup_refuses_to_signal_a_container_with_another_owner(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            guard = m.DockerGuard(self.config(), Path(work))
            guard.created = True
            with mock.patch.object(guard, '_inspect', return_value={'Config': {'Labels': {'io.testhub.k6.owner': 'other'}}, 'State': {'Running': True}}), \
                    mock.patch.object(guard, '_command') as command:
                with self.assertRaises(m.DockerRunnerError): guard.close()
                command.assert_not_called()

    def test_rejects_changed_frozen_runner_before_creating_container(self):
        m = self.module()
        from apps.perf_testing.engines import k6_engine
        s = snapshot(); s['k6_version'] = 'k6-docker:previous'
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work, \
                mock.patch.dict(os.environ, {'K6_RUNNER': 'DOCKER'}), \
                mock.patch.object(m, 'resolve_config', return_value=self.config()):
            with self.assertRaisesRegex(k6_engine.EngineError, '冻结版本'):
                k6_engine.K6Engine(s, work_dir=work).prepare()
            self.assertFalse((Path(work) / 'scenario.private.json').exists())

    def test_auto_removed_container_is_success_but_daemon_failure_is_not(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            guard = m.DockerGuard(self.config(), Path(work))
            for error in (f'error: no such object: {guard.name}',
                          f'Error response from daemon: No such container: {guard.name}'):
                missing = subprocess.CompletedProcess([], 1, '[]', error)
                with mock.patch.object(guard, '_command', return_value=missing):
                    self.assertIsNone(guard._inspect())
            for error in ('cannot connect to daemon',
                          'dial unix /var/run/docker.sock: connect: no such file or directory',
                          'error: no such object: another-container'):
                unavailable = subprocess.CompletedProcess([], 1, '', error)
                with mock.patch.object(guard, '_command', return_value=unavailable):
                    with self.assertRaises(m.DockerRunnerError, msg=error): guard._inspect()

    def test_changed_binary_between_fingerprint_and_copy_is_rejected(self):
        m = self.module()
        from apps.perf_testing.engines import k6_engine
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            binary = Path(work) / 'binary'; binary.write_bytes(b'changed-after-resolution')
            config = self.config(); config['binary'] = str(binary)
            config['binary_sha256'] = hashlib.sha256(b'original-frozen-content').hexdigest()
            s = snapshot(); s['k6_version'] = m.fingerprint(config)
            with mock.patch.dict(os.environ, {'K6_RUNNER': 'DOCKER'}), \
                    mock.patch.object(m, 'resolve_config', return_value=config), \
                    mock.patch.object(m, 'DockerGuard') as guard:
                with self.assertRaisesRegex(k6_engine.EngineError, '二进制.*冻结'):
                    k6_engine.K6Engine(s, work_dir=Path(work) / 'run').prepare()
                guard.assert_not_called()

    def cleanup_fixture(self, module, work):
        guard = module.DockerGuard(self.config(), Path(work), execution_id=37)
        guard.container_id = 'a' * 64
        state = {'Id': guard.container_id, 'Config': {'Labels': {
            module.OWNER_LABEL: guard.owner, module.EXECUTION_LABEL: '37'}}, 'State': {'Running': False}}
        return guard, state

    def test_cleanup_waits_for_owned_auto_removal_after_pending_rm(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            guard, state = self.cleanup_fixture(m, work)
            running = json.loads(json.dumps(state)); running['State']['Running'] = True
            pending = subprocess.CompletedProcess([], 1, '',
                f'Error response from daemon: removal of container {guard.container_id} is already in progress')
            with mock.patch.object(guard, '_inspect', side_effect=[running, state, state, None]) as inspect, \
                    mock.patch.object(guard, '_command', side_effect=[subprocess.CompletedProcess([], 0, '', ''), pending]) as command, \
                    mock.patch.object(m.time, 'sleep'):
                guard.close()
            self.assertTrue(guard._closed)
            self.assertEqual(inspect.call_count, 4)
            self.assertEqual([call.args[0] for call in command.call_args_list], [
                ['stop', '--timeout', '2', guard.container_id], ['rm', '-f', guard.container_id]])
            self.assertTrue(all(0 < call.kwargs['timeout'] <= 8 for call in command.call_args_list))

    def test_cleanup_exact_not_found_still_requires_inspect_confirmation(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            guard, state = self.cleanup_fixture(m, work)
            missing = subprocess.CompletedProcess([], 1, '', f'Error: No such container: {guard.container_id}')
            with mock.patch.object(guard, '_inspect', side_effect=[state, state, state, None]) as inspect, \
                    mock.patch.object(guard, '_command', return_value=missing), mock.patch.object(m.time, 'sleep'):
                guard.close()
            self.assertTrue(guard._closed)
            self.assertEqual(inspect.call_count, 4)

    def test_exit_143_requires_an_observed_user_stop(self):
        from apps.perf_testing.engines import k6_engine
        for user_stop in (False, True):
            with self.subTest(user_stop=user_stop), tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
                engine = k6_engine.K6Engine(snapshot(rounds=0), work_dir=work)
                engine.collector.total = engine.collector.success = 1
                guard = mock.Mock(execution_id=37, owner='a' * 32)
                guard.sample.return_value = None; guard.prepare_container.return_value = ['mock-docker-start']
                engine._docker_guard = guard
                process = mock.Mock(stdout=io.StringIO(''))
                process.poll.return_value = 143
                def finished(**kwargs):
                    if user_stop: engine.stop()
                    return 143
                process.wait.side_effect = finished
                collector = mock.Mock()
                collector.summary.return_value = {'collector_failure': None}
                collector.drain.return_value = []
                with mock.patch.object(k6_engine, 'NativeVUCollector', return_value=collector), \
                        mock.patch.object(k6_engine, 'WindowsJob') as job, \
                        mock.patch.object(k6_engine.subprocess, 'Popen', return_value=process):
                    job.return_value.creation_flags = 0
                    if user_stop: engine.run()
                    else:
                        with self.assertRaisesRegex(k6_engine.EngineError, '143'): engine.run()
                self.assertEqual(engine._exit_code, 143)

    def test_cleanup_rejects_persistent_remnant_after_bounded_poll(self):
        m = self.module()
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            guard, state = self.cleanup_fixture(m, work)
            tick = [0.0]
            with mock.patch.object(guard, '_inspect', return_value=state) as inspect, \
                    mock.patch.object(guard, '_command', return_value=subprocess.CompletedProcess([], 0, '', '')), \
                    mock.patch.object(m.time, 'monotonic', side_effect=lambda: tick[0]), \
                    mock.patch.object(m.time, 'sleep', side_effect=lambda delay: tick.__setitem__(0, tick[0] + delay)):
                with self.assertRaisesRegex(m.DockerRunnerError, '尚未清理完成'):
                    guard.close()
            self.assertFalse(guard._closed)
            self.assertGreaterEqual(tick[0], 8)
            self.assertLessEqual(tick[0], 8.001)
            self.assertGreater(inspect.call_count, 3)

    def test_cleanup_never_treats_permission_unknown_or_other_target_error_as_pending(self):
        m = self.module()
        for stderr in ('permission denied', 'daemon unavailable',
                       'Error response from daemon: removal of container another-container is already in progress',
                       'error: no such container: another-container'):
            with self.subTest(stderr=stderr), tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
                guard, state = self.cleanup_fixture(m, work)
                with mock.patch.object(guard, '_inspect', side_effect=[state, state, None]), \
                        mock.patch.object(guard, '_command', return_value=subprocess.CompletedProcess([], 1, '', stderr)):
                    with self.assertRaisesRegex(m.DockerRunnerError, '清理命令失败'):
                        guard.close()
                self.assertFalse(guard._closed)

    def test_cleanup_rechecks_identity_during_poll(self):
        m = self.module()
        for change in ('owner', 'execution', 'container'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
                guard, state = self.cleanup_fixture(m, work)
                changed = json.loads(json.dumps(state))
                if change == 'owner': changed['Config']['Labels'][m.OWNER_LABEL] = 'other'
                elif change == 'execution': changed['Config']['Labels'][m.EXECUTION_LABEL] = '38'
                else: changed['Id'] = 'b' * 64
                with mock.patch.object(guard, '_inspect', side_effect=[state, state, state, changed]), \
                        mock.patch.object(guard, '_command', return_value=subprocess.CompletedProcess([], 0, '', '')) as command, \
                        mock.patch.object(m.time, 'sleep'):
                    with self.assertRaisesRegex(m.DockerRunnerError, '不匹配'):
                        guard.close()
                self.assertFalse(guard._closed)
                command.assert_called_once()

    def test_docker_samples_never_count_cli_cpu_or_duplicate_stats(self):
        from apps.perf_testing.engines import k6_engine
        with mock.patch.dict(os.environ, {'K6_RUNNER': 'DOCKER'}):
            engine = k6_engine.K6Engine(snapshot())
        engine._docker_guard = mock.Mock()
        engine._docker_guard.sample.return_value = (1, 234.5, 120.0)
        engine._proc_probe = mock.Mock()
        engine._start_ts = time.monotonic() - 2
        samples = []; engine.on_sample = samples.append
        engine._emit_sample(force=True); engine._emit_sample(force=True)
        self.assertEqual(engine._cpu_sample_count, 1)
        self.assertEqual(engine._peak_k6_cpu, 234.5)
        engine._proc_probe.cpu_percent.assert_not_called()
        self.assertTrue(samples[0]['cpu_sampled'])
        self.assertFalse(samples[1]['cpu_sampled'])


@unittest.skipUnless(os.environ.get('K6_DOCKER_TESTS') == '1', 'Opt-in real Docker fixture tests')
class RealDockerTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'K6_RUNNER': 'DOCKER'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.received = threading.Event()
        received = self.received
        class Handler(TestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                received.set()
                body = b'{"code":0}'
                self.send_response(200); self.send_header('Content-Length', len(body))
                self.end_headers(); self.wfile.write(body)
        self.server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def scenario(self, rounds=0):
        s = snapshot(users=1, rounds=rounds)
        s['env_config']['base_url'] = f'http://host.docker.internal:{self.server.server_port}'
        s['load_config']['duration'] = 60
        s['steps'] = [{'name': 'fixture', 'url': '/ok', 'method': 'GET',
                       'think_time': {'type': 'FIXED', 'min': 100}}]
        return s

    def test_normal_completion_collects_business_and_removes_container(self):
        from apps.perf_testing.engines.k6_engine import K6Engine
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            engine = K6Engine(self.scenario(rounds=2), work_dir=work)
            engine.prepare(); engine.run()
            self.assertEqual(engine.collect()['summary']['business_total'], 2)
            self.assertEqual(engine.collect()['summary']['business_started'], 2)
            self.assertIsNone(engine._proc_probe)
            self.assertIsNone(engine._docker_guard._inspect())

    def test_attach_failure_removes_created_container_without_any_http(self):
        from apps.perf_testing.engines import k6_engine
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            engine = k6_engine.K6Engine(self.scenario(), work_dir=work); engine.prepare()
            original = subprocess.Popen
            def fail_attach(command, *args, **kwargs):
                if 'start' in command and '--attach' in command:
                    raise OSError('controlled attach failure')
                return original(command, *args, **kwargs)
            with mock.patch.object(k6_engine.subprocess, 'Popen', side_effect=fail_attach):
                with self.assertRaises(OSError): engine.run()
            self.assertIsNone(engine._docker_guard._inspect())
            self.assertFalse(self.received.is_set())

    def test_normal_stop_ends_owned_container_and_preserves_requests(self):
        from apps.perf_testing.engines.k6_engine import K6Engine
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            engine = K6Engine(self.scenario(), work_dir=work); engine.prepare()
            failures = []
            def run():
                try: engine.run()
                except Exception as exc: failures.append(type(exc).__name__)
            worker = threading.Thread(target=run); worker.start()
            try:
                self.assertTrue(self.received.wait(15), 'Docker did not reach controlled fixture')
                time.sleep(0.3)
                engine.stop(); worker.join(15)
                self.assertFalse(worker.is_alive())
                self.assertFalse(failures)
                self.assertGreater(engine.collect()['summary']['business_total'], 0)
                self.assertIsNone(engine._docker_guard._inspect())
            finally:
                engine.stop(); worker.join(15)

    def test_worker_hard_kill_ends_real_container_via_heartbeat(self):
        from apps.perf_testing.engines import k6_docker
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as work:
            work = Path(work)
            (work / 'snapshot.json').write_text(json.dumps(self.scenario()), encoding='utf-8')
            worker_code = '''
import json, sys
from pathlib import Path
from apps.perf_testing.engines.k6_engine import K6Engine
root=Path(sys.argv[1])
engine=K6Engine(json.loads((root/'snapshot.json').read_text(encoding='utf-8')),work_dir=root/'run')
engine.prepare()
(root/'owner.json').write_text(json.dumps(engine._docker_guard.metadata()),encoding='utf-8')
engine.run()
'''
            worker = subprocess.Popen([sys.executable, '-c', worker_code, str(work)], cwd=ROOT,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            guard = None
            try:
                self.assertTrue(self.received.wait(20), 'Worker did not reach controlled fixture')
                metadata = json.loads((work / 'owner.json').read_text(encoding='utf-8'))
                guard = k6_docker.DockerGuard(k6_docker.resolve_config(), work/'run')
                guard.name = metadata['container_name']; guard.owner = metadata['owner']
                self.assertTrue(guard._owned()['State']['Running'])
                worker.kill(); worker.wait(timeout=5)
                started = time.monotonic()
                while time.monotonic() - started < 12 and guard._owned() is not None:
                    time.sleep(0.3)
                self.assertIsNone(guard._owned(), 'Heartbeat watchdog left its container running')
                self.assertLess(time.monotonic() - started, 12)
            finally:
                if worker.poll() is None: worker.kill(); worker.wait(timeout=5)
                if guard: guard.close()


if __name__ == '__main__': unittest.main()
