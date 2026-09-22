"""Local Docker runner: immutable dependencies and ownership-scoped cleanup."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from .base import EngineError
from .k6_process import WindowsJob
from ..services.k6_native_vu import MAX_RESPONSE_BYTES, POLICY, NativeVUError

OWNER_LABEL = 'io.testhub.k6.owner'
EXECUTION_LABEL = 'io.testhub.k6.execution'


class DockerRunnerError(EngineError):
    pass


def runner_mode():
    mode = os.environ.get('K6_RUNNER', 'NATIVE').upper()
    if mode not in ('NATIVE', 'DOCKER'):
        raise DockerRunnerError('K6_RUNNER 只支持 NATIVE 或 DOCKER')
    return mode


def _run(command, timeout=15):
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError) as exc:
        raise DockerRunnerError('Docker 命令执行失败或超时；请检查本地 Docker 服务') from exc


def fingerprint(config):
    frozen = {k: config[k] for k in ('image_id', 'network', 'network_id', 'cpus', 'memory',
                                    'binary_sha256', 'binary_version')}
    return 'k6-docker:' + hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()


_version_cache = {}


def resolve_config():
    docker = os.environ.get('K6_DOCKER_BIN') or shutil.which('docker')
    binary = Path(os.environ.get('K6_LINUX_BIN') or Path(__file__).resolve().parents[4] / 'runtime/bin/k6-linux')
    network = os.environ.get('K6_DOCKER_NETWORK', '')
    cpus = os.environ.get('K6_DOCKER_CPUS', '8')
    memory = os.environ.get('K6_DOCKER_MEMORY', '2g').lower()
    if not docker or not binary.is_file() or not network:
        raise DockerRunnerError('Docker runner 需要本地 Docker、K6_LINUX_BIN 和 K6_DOCKER_NETWORK')
    if not re.fullmatch(r'\d+(?:\.\d+)?', cpus) or not 0 < float(cpus) <= 8:
        raise DockerRunnerError('Docker runner CPU 限额必须大于 0 且不超过 8')
    if not re.fullmatch(r'[1-9]\d*[mg]', memory):
        raise DockerRunnerError('Docker runner 内存限额需要 m/g 单位')
    memory_mb = int(memory[:-1]) * (1024 if memory[-1] == 'g' else 1)
    if memory_mb > 8192:
        raise DockerRunnerError('Docker runner 内存限额不能超过 8 GiB')
    image = os.environ.get('K6_DOCKER_IMAGE', 'alpine:3.22')
    image_result = _run([docker, 'image', 'inspect', image, '--format', '{{.Id}}'])
    network_result = _run([docker, 'network', 'inspect', network, '--format', '{{.Id}}'])
    if image_result.returncode or network_result.returncode:
        raise DockerRunnerError('Docker runner 镜像或网络不存在；不会自动拉取镜像')
    image_id = image_result.stdout.strip(); network_id = network_result.stdout.strip()
    with binary.open('rb') as source:
        binary_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    cache_key = (image_id, binary_hash)
    if cache_key not in _version_cache:
        name = 'testhub-k6-version-' + uuid.uuid4().hex
        try:
            version = _run([docker, 'run', '--rm', '--pull=never', '--name', name,
                '--network=none', '--read-only', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--cpus=1', '--memory=256m',
                '--mount', f'type=bind,source={binary.resolve()},target=/k6,readonly',
                image_id, '/k6', 'version'])
            if version.returncode:
                raise DockerRunnerError('Linux k6 二进制无法在本地镜像运行')
            _version_cache[cache_key] = version.stdout.strip()[:200]
        finally:
            _run([docker, 'rm', '-f', name])
    return dict(docker=str(docker), binary=str(binary.resolve()), image_id=image_id,
                network=network, network_id=network_id, cpus=cpus, memory=memory,
                binary_sha256=binary_hash, binary_version=_version_cache[cache_key])


class DockerGuard:
    def __init__(self, config, work_dir, execution_id=None):
        self.config = config
        self.work_dir = Path(work_dir).resolve()
        self.owner = uuid.uuid4().hex
        self.name = 'testhub-k6-' + self.owner
        self.execution_id = int(execution_id or 0)
        self.created = False
        self._closed = False
        self._lock = threading.RLock()
        self._done = threading.Event()
        self._threads = []
        self._sample = None
        self._sample_serial = 0
        self.container_id = None
        self._native_identity_verified = False

    def metadata(self):
        return dict(mode='DOCKER', container_name=self.name, owner=self.owner,
                    execution_id=self.execution_id, fingerprint=fingerprint(self.config),
                    **{k: self.config[k] for k in ('image_id', 'network', 'network_id', 'cpus',
                        'memory', 'binary_version', 'binary_sha256')})

    def create_command(self):
        return [self.config['docker'], 'create', '--rm', '--pull=never', '--name', self.name,
            '--label', OWNER_LABEL + '=' + self.owner,
            '--label', EXECUTION_LABEL + '=' + str(self.execution_id),
            '--network', self.config['network_id'], '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--cpus=' + self.config['cpus'],
            '--memory=' + self.config['memory'], '--memory-swap=' + self.config['memory'],
            '--pids-limit=256', '--stop-timeout=2',
            '--mount', f'type=bind,source={self.work_dir},target=/run,readonly',
            '--env', 'K6_NO_USAGE_REPORT=true', '--env', 'K6_TESTHUB_CONFIG=/run/scenario.private.json',
            self.config['image_id'], '/bin/sh', '/run/container-entrypoint.sh']

    def _command(self, args, *, timeout=15):
        return _run([self.config['docker']] + args, timeout=timeout)

    def _inspect(self, *, timeout=15):
        target = self.container_id or self.name
        result = self._command(['inspect', target], timeout=timeout)
        if result.returncode:
            missing = r'(?:error:\s*|error response from daemon:\s*)?no such (?:object|container):\s*' + re.escape(target)
            if re.fullmatch(missing, result.stderr.strip(), re.IGNORECASE):
                return None
            raise DockerRunnerError('无法确认本次 Docker 容器状态')
        try:
            return json.loads(result.stdout)[0]
        except (ValueError, IndexError, TypeError) as exc:
            raise DockerRunnerError('Docker 容器状态格式无效') from exc

    def _owned(self, *, timeout=15):
        state = self._inspect(timeout=timeout)
        if state and state.get('Config', {}).get('Labels', {}).get(OWNER_LABEL) != self.owner:
            raise DockerRunnerError('Docker 容器归属不匹配，拒绝操作')
        if state and self.container_id and (state.get('Id') != self.container_id or
                state.get('Config', {}).get('Labels', {}).get(EXECUTION_LABEL) != str(self.execution_id)):
            raise DockerRunnerError('Docker 容器执行身份不匹配，拒绝操作')
        return state

    def prepare_container(self):
        with self._lock:
            if self._closed:
                raise DockerRunnerError('Docker runner 已停止，不能重新启动')
            (self.work_dir / 'heartbeat').write_text('0', encoding='ascii')
            result = _run(self.create_command())
            # A CLI timeout may occur after daemon creation; close() inspects by owner.
            self.created = True
            if result.returncode:
                raise DockerRunnerError('Docker runner 容器创建失败')
            ident = result.stdout.strip()
            if not re.fullmatch('[a-f0-9]{64}', ident):
                raise DockerRunnerError('Docker runner 容器 ID 无效')
            self.container_id = ident
            if self._owned() is None:
                raise DockerRunnerError('Docker runner 容器身份无法确认')
            self._native_identity_verified = True
            self._threads = [threading.Thread(target=self._heartbeat, daemon=True),
                             threading.Thread(target=self._stats, daemon=True)]
            for thread in self._threads: thread.start()
        return [self.config['docker'], 'start', '--attach', self.container_id]

    def read_native_status(self, cancel: threading.Event) -> bytes:
        if not self._native_identity_verified or not self.container_id or self._closed:
            raise NativeVUError('ownership_mismatch')
        command = [self.config['docker'], 'exec', self.container_id, '/bin/busybox', 'wget',
                   '-q', '-Y', 'off', '-T', '1', '-O', '-', 'http://127.0.0.1:6565/v1/status']
        job = None
        process = None
        try:
            job = WindowsJob() if os.name == 'nt' else None
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=job.creation_flags if job else 0)
            if job:
                job.attach_and_resume(process)
        except (OSError, subprocess.SubprocessError) as exc:
            if job:
                job.close()
            if process:
                process.kill()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
                process.stdout.close()
            raise NativeVUError('transport_error') from exc
        finished = threading.Event()
        received = []

        def read_response():
            try:
                received.append(process.stdout.read(MAX_RESPONSE_BYTES + 1))
            except (OSError, ValueError):
                received.append(None)
            finally:
                finished.set()

        reader = threading.Thread(target=read_response, name='k6-native-response', daemon=True)
        reader.start()
        deadline = time.monotonic() + POLICY['exec_timeout_ms']/1000
        try:
            while True:
                if cancel.is_set():
                    raise NativeVUError('transport_error')
                if time.monotonic() >= deadline:
                    raise NativeVUError('timeout')
                if finished.wait(0.01):
                    if not received or received[0] is None:
                        raise NativeVUError('transport_error')
                    if len(received[0]) > MAX_RESPONSE_BYTES:
                        raise NativeVUError('oversize')
                    if process.poll() is not None:
                        if process.returncode:
                            raise NativeVUError('not_ready')
                        return received[0]
                    cancel.wait(0.01)
        finally:
            if job:
                job.close()
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
            reader.join(timeout=0.5)
            if not reader.is_alive():
                process.stdout.close()

    def _heartbeat(self):
        sequence = 0
        while not self._done.wait(0.5):
            sequence += 1
            try:
                (self.work_dir / 'heartbeat').write_text(str(sequence), encoding='ascii')
            except OSError:
                return  # Watchdog fails closed when private storage disappears.

    def _stats(self):
        while not self._done.wait(1):
            try:
                result = self._command(['stats', '--no-stream', '--format', '{{json .}}', self.name])
                if result.returncode or not result.stdout.strip(): continue
                data = json.loads(result.stdout)
                cpu = float(data['CPUPerc'].rstrip('%'))
                memory = data['MemUsage'].split('/')[0].strip()
                match = re.fullmatch(r'([0-9.]+)([A-Za-z]+)', memory)
                factors = {'B': 1 / 1024**2, 'KiB': 1/1024, 'MiB': 1, 'GiB': 1024,
                           'kB': 1000/1024**2, 'MB': 1000**2/1024**2, 'GB': 1000**3/1024**2}
                if not math.isfinite(cpu) or cpu < 0 or not match: continue
                memory_mb = float(match[1]) * factors[match[2]]
                self._sample_serial += 1
                self._sample = (self._sample_serial, cpu, memory_mb)
            except (DockerRunnerError, ValueError, KeyError, TypeError):
                pass

    def sample(self):
        return self._sample

    def close(self):
        with self._lock:
            if self._closed: return
            deadline = time.monotonic() + 8
            target = self.container_id or self.name

            def remaining():
                value = deadline - time.monotonic()
                if value <= 0:
                    raise DockerRunnerError('本次 Docker 容器尚未清理完成')
                return value

            def cleanup_command(args):
                result = self._command(args, timeout=remaining())
                if result.returncode:
                    reference = re.escape(target)
                    transient = (r'(?:error:\s*|error response from daemon:\s*)?(?:'
                        r'no such (?:object|container):\s*' + reference + '|'
                        r'removal of container ' + reference + r' is already in progress)')
                    if not re.fullmatch(transient, result.stderr.strip(), re.IGNORECASE):
                        raise DockerRunnerError('本次 Docker 容器清理命令失败；请检查 Docker 服务和权限')

            try:
                state = self._owned(timeout=remaining())
                if state:
                    if state.get('State', {}).get('Running'):
                        cleanup_command(['stop', '--timeout', '2', target])
                    state = self._owned(timeout=remaining())
                    if state:
                        cleanup_command(['rm', '-f', target])
                    # --rm cleanup is asynchronous; every observation must retain ownership.
                    while self._owned(timeout=remaining()) is not None:
                        time.sleep(min(0.1, remaining()))
                self._closed = True
            finally:
                self._done.set()
