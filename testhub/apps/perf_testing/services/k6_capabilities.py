"""Reviewed adapter capabilities for one k6 source revision, without source scans."""
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import re
import stat
import threading
import time

from ..engines import k6_docker, k6_engine
from ..engines.base import EngineError


BASELINE_VERSION = '2.2.1-0.20260915101307-0ae3c2989e5b'
BASELINE_COMMIT = '0ae3c2989e5b7d24e5d18080aa72aa968ad5015f'
BASELINE_ADAPTER = '0.12.1'
SOURCE_ROOT = f'https://github.com/grafana/k6/blob/{BASELINE_COMMIT}/'
MATRIX_REVISION = '2026-09-21.1'
REVIEWED_SSE_BINARIES = {
    'linux': '87a37de604f3c40829597faf91eee24915db55dc4d923be35a3798d368b17e64',
    'windows': '996ef029c8f5d7f237ef5be76aa359c9c2e8ea3e11326017d4c18451cd4b266a',
}
_CUSTOM_VERSION = re.compile(
    r'(?P<name>[^\r\n/\\]{1,128}) v\(devel\) \(go1\.26\.5, (?P<platform>linux|windows)/amd64\)\n'
    r'Extensions:\n  pressure\.local/bounded-sse , k6/x/testhub-sse \[js\]')
_MAX_NATIVE_BYTES = 256 * 1024 * 1024
_native_cache = OrderedDict()
_native_lock = threading.Lock()
REASONS = {
    'ready': '当前版本已接入；执行前仍需检查场景配置。',
    'not_implemented': '平台尚未接入此能力。',
    'runner_unavailable': 'k6 运行环境不可用，请检查引擎安装及运行环境配置。',
    'runner_changed': '运行环境刚刚发生变化，请刷新能力状态后重试。',
    'version_unverified': '当前引擎或适配器版本尚未核对，请完成版本验证后再运行。',
    'not_in_version': '固定版本没有此功能。',
    'optional_dependency': '需要额外运行环境或外部服务，尚未完成平台接入。',
    'docker_proxy_unsupported': '当前 Docker 运行环境不支持代理。',
    'not_validated': '原生运行环境有代理传递路径，尚未验收，暂不开放。',
    'runtime_extension_required': 'SSE 需要已验收且 SHA 匹配的 testhub-sse/1 运行时。',
}


class _RunnerChanged(ValueError):
    pass


def _signature(info):
    # Windows path stat and fd stat expose different legacy ctime semantics.
    created_or_changed = getattr(info, 'st_birthtime_ns', info.st_ctime_ns) if os.name == 'nt' else info.st_ctime_ns
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_size, info.st_mtime_ns, created_or_changed)


def _native_identity(version):
    """Bind version output to bounded file bytes; cache only an unchanged file identity."""
    path = Path(k6_engine._binary()).absolute()
    if path.is_symlink():
        raise ValueError('Native identity requires a regular file')
    path = path.resolve(strict=True)
    with _native_lock:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_NATIVE_BYTES:
            raise ValueError('Native binary size or type invalid')
        signature = _signature(info)
        key = (str(path), signature, version)
        cached = _native_cache.get(key)
        if cached and time.monotonic() - cached[0] < 15:
            _native_cache.move_to_end(key)
            return cached[1], path.name
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            if _signature(os.fstat(stream.fileno())) != signature:
                raise _RunnerChanged()
            remaining = info.st_size
            while remaining:
                block = stream.read(min(remaining, 1024 * 1024))
                if not block:
                    raise _RunnerChanged()
                checksum.update(block); remaining -= len(block)
            if stream.read(1) or _signature(os.fstat(stream.fileno())) != signature:
                raise _RunnerChanged()
        if (k6_engine.get_version() != version or Path(k6_engine._binary()).resolve(strict=True) != path
                or _signature(path.stat()) != signature):
            raise _RunnerChanged()
        digest = checksum.hexdigest()
        _native_cache[key] = (time.monotonic(), digest)
        _native_cache.move_to_end(key)
        while len(_native_cache) > 8:
            _native_cache.popitem(last=False)
        return digest, path.name


def _capability(key: str, official: str, entry: str, source: str, config: str,
                status: str = 'planned', evidence: str = '待开发；无运行验收') -> dict:
    return {
        'id': key, 'official_name': official, 'entry': entry,
        'source': (SOURCE_ROOT.replace('/blob/', '/tree/') if source == 'lib/executor'
                   else SOURCE_ROOT) + source, 'config_path': config,
        'execution_path': 'apps/perf_testing/engines/k6_engine.py + k6_script.js'
                          if status == 'accepted' else '尚未接入',
        'report_path': '执行监控 / 执行报告 / 原始 CSV'
                       if status == 'accepted' else '尚未接入',
        'evidence': evidence, 'form_status': status,
        'script_status': status if status in ('absent', 'dependency') else 'planned',
    }


CAPABILITIES = (
    _capability('debug', 'per-vu-iterations (bounded debug)', '调试（1 用户 / 1 轮）',
                'lib/executor/per_vu_iterations.go', 'debug → 固定 1 VU / 1 轮 / 最长 60 秒',
                'accepted', 'test_k6_debug'),
    _capability('constant-vus', 'constant-vus', '固定并发 / 按时长',
                'lib/executor/constant_vus.go', 'load_config.concurrency,duration; iterations_per_vu=0',
                'accepted', 'test_k6_timed_drain.TimedDrainTests; deployment/test-runner/k6_timed_drain.test.mjs'),
    _capability('per-vu-iterations', 'per-vu-iterations', '固定并发 / 每用户轮数',
                'lib/executor/per_vu_iterations.go', 'load_config.concurrency,iterations_per_vu,duration',
                'accepted', 'test_k6_debug'),
    _capability('shared-iterations', 'shared-iterations', '共享总轮数（待开发）',
                'lib/executor/shared_iterations.go', '尚未接入；上游 scenarios.*.iterations'),
    _capability('ramping-vus', 'ramping-vus', '阶梯 / 尖峰负载（待开发）',
                'lib/executor/ramping_vus.go', '尚未接入；上游 scenarios.*.stages'),
    _capability('constant-arrival-rate', 'constant-arrival-rate', '固定迭代到达率（待开发）',
                'lib/executor/constant_arrival_rate.go', '尚未接入；上游 scenarios.*.rate,timeUnit'),
    _capability('ramping-arrival-rate', 'ramping-arrival-rate', '阶段迭代到达率（待开发）',
                'lib/executor/ramping_arrival_rate.go', '尚未接入；上游 scenarios.*.stages,timeUnit'),
    _capability('externally-controlled', 'externally-controlled', '外部动态 VU 执行器',
                'lib/executor', '该版本未注册', 'absent', '固定源码执行器注册清单：无此项'),
    _capability('http-basic', 'k6/http.request (NONE / JSON)', '顺序 HTTP 请求',
                'js/modules/k6/http/request.go', 'steps[].method,url,headers,params,body_type,body',
                'accepted', 'test_k6_engine.AdapterContractTests'),
    _capability('csv-identity', 'k6/data.SharedArray (adapter CSV identity)', 'CSV 独立用户',
                'internal/js/modules/k6/data/data.go', 'variables[].type=CSV,data_file_id,column',
                'accepted', 'test_k6_engine; test_k6_worker'),
    _capability('assertions-basic', 'Response.status / Response.json / Response.body', '状态码 / 基础 JSONPath / 文本包含',
                'js/modules/k6/http/response.go', 'steps[].assertions,extractors',
                'accepted', 'test_k6_engine.AdapterContractTests; deployment/test-runner/k6_contains.test.mjs'),
    _capability('per-vu-setup', 'VU-local state (adapter login once per VU)', '每用户前置登录',
                'internal/js/runner.go', 'steps[].is_setup=true',
                'accepted', 'test_k6_engine; test_k6_debug'),
    _capability('bounded-execution', 'VU-local declarative attempt budgets', '同场景有界专项执行',
                'internal/js/runner.go', 'steps[].execution_policy；连续组、固定 VU 范围、次数上限及最小间隔',
                'accepted', 'test_execution_policy; test_execution_policy_api; deployment/test-runner/k6_execution_policy.test.mjs；本地隔离验收，不代表线上发布或业务容量'),
    _capability('stop', 'run / process termination', '运行 / 停止',
                'internal/cmd/run.go', 'scenarios/:id/execute; executions/:id/stop',
                'accepted', 'test_k6_stop_race'),
    _capability('reminder-recovery', 'adapter durable HTTP resource recovery', '提醒条件写入与独立恢复',
                'js/modules/k6/http/request.go', 'runtime_config.resource_recovery；目标服务必须启用提醒恢复v1',
                'accepted', 'test_reminder_recovery; test_reminder_recovery_db; test_reminder_recovery_native；隔离身份与强停恢复验证，不代表真实业务或1000容量'),
    _capability('report', 'console / adapter request events', '每接口报告 / 原始 CSV',
                'internal/js/console.go', '执行监控 / 执行报告',
                'accepted', 'test_k6_report; test_k6_engine'),
    _capability('native-script', 'JavaScript / TypeScript / lifecycle', '原生脚本（待开发）',
                'internal/js/bundle.go', '尚未接入；不能通过 script_ref 或 JSON 参数启用'),
    _capability('multi-scenario', 'options.scenarios / http.batch', '多场景 / 权重 / 并行（待开发）',
                'lib/options.go', '尚未接入'),
    _capability('native-metrics', 'Counter / Gauge / Rate / Trend / thresholds', '原生指标与阈值（待开发）',
                'internal/js/modules/k6/metrics/metrics.go', '尚未接入；平台断言不等于原生 checks/thresholds'),
    _capability('http-files', 'k6/http.file / binary request body', '项目文件上传 / FORM / BINARY',
                'js/modules/k6/http/file.go', 'steps[].body_type=FORM|BINARY; steps[].files[].file_id；项目归属与不可变私有文件快照',
                'accepted', 'test_k6_uploads；隔离原生k6 multipart/BINARY字节及失败配额验证；不代表真实业务或1000容量'),
    _capability('http-advanced', 'HTTP redirects / TLS / DNS', '高级 HTTP（待开发）',
                'lib/options.go', '尚未完整接入；已接入子集见矩阵'),
    _capability('proxy', 'HTTP_PROXY / HTTPS_PROXY', 'HTTP 代理',
                'internal/js/runner.go#L204', 'runtime_config.proxy（Docker 禁止；原生待验收）'),
    _capability('websocket', 'k6/websockets (bounded JSON command and push sessions)', '手工 WebSocket JSON 会话与推送',
                'internal/js/modules/k6/websockets/websockets.go',
                'steps[].protocol=WEBSOCKET; steps[].websocket_config；首帧认证、id/ok 回执，或 PUSH Bearer 握手及类型事件断言；不含二进制、自动重连或 SSE',
                'accepted', '本地配置、原生 k6 与页面回归：test_websocket_steps; test_k6_websocket_integration; '
                'test_k6_websocket_metrics; deployment/test-runner/k6_websocket.test.mjs；未代表线上发布或真实客服负载验收'),
    _capability('sse', 'k6/x/testhub-sse (owned bounded extension)', '有界 SSE 事件流',
                'ext/ext.go', 'steps[].protocol=SSE; steps[].sse_config；同源 Bearer、明确业务终态、事件断言与提取、字节和时间边界',
                'accepted', 'deployment/runtime/bounded-sse；test_sse_steps; test_k6_sse_integration; '
                'test_k6_sse_contract.mjs; test_sse_diagnostics；严格空白字符串与安全失败位置，固定 SHA 的合成混合协议验收，不代表真实业务或1000并发容量'),
    _capability('grpc', 'k6/net/grpc', 'gRPC（待开发）',
                'internal/js/modules/k6/grpc/grpc.go', '尚未接入'),
    _capability('browser', 'k6/browser', '浏览器与混合压测',
                'internal/js/modules/k6/browser/browser/module.go', '待接入 Chromium runner', 'dependency'),
    _capability('cloud', 'Grafana Cloud k6', 'Cloud 托管（可选外部服务）',
                'internal/cmd/cloud.go', '待接入外部账号与服务', 'dependency'),
    _capability('extensions', 'k6/x/* / output / secret-source extensions', '社区扩展',
                'ext/ext.go', '待登记和验收各扩展及其版本', 'dependency'),
)


def build_capabilities(*, available: bool, version: str) -> dict:
    """Called inside engine_status's 15-second cache; only public runner metadata escapes."""
    runtime = {'mode': None, 'binary_version': None, 'source_commit': None,
               'adapter_version': k6_engine.ADAPTER_VERSION, 'binary_sha256': None,
               'image_id': None, 'fingerprint': None, 'runtime_kind': None,
               'sse_api_version': None}
    reason = 'ready'
    raw_version = version
    basename = None
    try:
        runtime['mode'] = k6_docker.runner_mode()
        if not available:
            reason = 'runner_unavailable'
        elif runtime['mode'] == 'DOCKER':
            config = k6_docker.resolve_config()
            if k6_docker.fingerprint(config) != version:
                reason = 'runner_changed'
                raw_version = ''
            else:
                raw_version = config['binary_version']
                runtime.update({key: config[key] for key in ('binary_sha256', 'image_id')})
                runtime['fingerprint'] = version
                basename = 'k6'
        elif _CUSTOM_VERSION.fullmatch(raw_version or ''):
            runtime['binary_sha256'], basename = _native_identity(raw_version)
    except _RunnerChanged:
        reason = 'runner_changed'
        raw_version = ''
    except (EngineError, OSError, ValueError, KeyError):
        available = False
        reason = 'runner_unavailable'
        raw_version = ''

    match = re.fullmatch(r'k6(?:\.exe)? v([0-9][A-Za-z0-9.+-]*)(?:[ \t]+\([^\r\n]*\))?', raw_version or '')
    custom = _CUSTOM_VERSION.fullmatch(raw_version or '')
    trusted_custom = bool(custom and custom['name'] == basename
                          and (runtime['mode'] != 'DOCKER' or custom['platform'] == 'linux')
                          and runtime['binary_sha256'] == REVIEWED_SSE_BINARIES[custom['platform']])
    runtime['binary_version'] = match.group(1) if match else '(devel)' if custom else None
    binary_verified = trusted_custom or runtime['binary_version'] == BASELINE_VERSION
    if binary_verified:
        runtime['runtime_kind'] = 'reviewed-extension' if trusted_custom else 'upstream'
    if trusted_custom:
        runtime['sse_api_version'] = 'testhub-sse/1'
    differences = [
        {'field': key, 'expected': expected, 'actual': runtime[key]}
        for key, expected in (('binary_version', '(devel)' if trusted_custom else BASELINE_VERSION), ('adapter_version', BASELINE_ADAPTER))
        if runtime[key] != expected
    ]
    if reason == 'ready' and differences:
        reason = 'version_unverified'
    matched = reason == 'ready'
    if binary_verified and reason not in ('runner_changed', 'runner_unavailable'):
        runtime['source_commit'] = BASELINE_COMMIT
    items = []
    for item in CAPABILITIES:
        row = dict(item)
        code = {'accepted': 'ready', 'planned': 'not_implemented',
                'absent': 'not_in_version', 'dependency': 'optional_dependency'}[row['form_status']]
        if row['id'] == 'proxy':
            code = 'docker_proxy_unsupported' if runtime['mode'] == 'DOCKER' else 'not_validated'
        if row['id'] == 'sse' and not trusted_custom:
            code = 'runtime_extension_required'
            row.update(form_status='planned', script_status='planned')
        if not matched:
            code = reason
            row.update(form_status='planned', script_status='planned')
        row.update(enabled=matched and row['form_status'] == 'accepted',
                   reason_code=code, reason=REASONS[code])
        items.append(row)
    return {
        'schema_version': 1, 'matrix_revision': MATRIX_REVISION,
        'baseline': {'binary_version': BASELINE_VERSION, 'source_commit': BASELINE_COMMIT,
                     'adapter_version': BASELINE_ADAPTER, 'source': SOURCE_ROOT,
                     'sse_runtime': {'binary_version': '(devel)', 'module': 'k6/x/testhub-sse',
                                     'api_version': 'testhub-sse/1', 'sha256_by_platform': dict(REVIEWED_SSE_BINARIES)}},
        'runtime': runtime, 'available': bool(available) and reason != 'runner_changed',
        'verification': {'state': 'matched' if matched else 'mismatch' if differences and (match or custom)
                         else 'unverified', 'differences': differences},
        'reason_code': reason, 'reason': REASONS[reason], 'items': items,
    }
