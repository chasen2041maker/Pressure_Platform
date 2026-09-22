"""Single-process k6 adapter for fixed-VU, ordered HTTP scenarios.

Only the documented subset is accepted. Request events contain no payloads,
headers or rendered URLs; this validation adapter is not a capacity benchmark
of the load generator itself.
"""
import csv
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid

from .base import BaseEngine, EngineError
from .k6_process import WindowsJob
from . import k6_docker
from ..services.metrics import MetricsCollector
from ..services.k6_throughput import CompletionBuckets, RATE_NOTICE, valid_timestamp
from ..services import auth_profiles
from ..services import k6_thresholds
from ..services.k6_native_vu import NativeVUCollector, VERSION as NATIVE_VU_VERSION, SOURCE as NATIVE_VU_SOURCE
from ..services.k6_websocket_metrics import WebSocketMetrics, WS_ERRORS
from ..services.k6_sse_metrics import SSEMetrics
from rest_framework.exceptions import ValidationError

ADAPTER_VERSION = '0.12.1'
EVENT_PREFIX = 'TESTHUB_K6_EVENT '
_ROOT = Path(__file__).resolve().parents[3]
DEBUG_LIMIT = 100
DEBUG_RESPONSE_LIMIT = 2048


def _debug_response(value: dict, is_auth: bool) -> dict:
    if is_auth:
        return {'state': 'auth_omitted'}
    states = {'json', 'unavailable', 'non_json_omitted', 'binary_omitted', 'oversize_omitted', 'invalid_json'}
    if not isinstance(value, dict) or value.get('state') not in states:
        return {'state': 'unavailable'}
    result = {'state': value['state'], 'truncated': value.get('truncated') is True}
    if value['state'] != 'json':
        return result
    budget = [100]

    def shape(item: object, depth: int = 0) -> object:
        budget[0] -= 1
        if budget[0] < 0 or depth > 5:
            result['truncated'] = True
            return '[omitted]'
        if item is None:
            return None
        if isinstance(item, list):
            result['truncated'] |= len(item) > 10
            return [shape(child, depth + 1) for child in item[:10]]
        if isinstance(item, dict):
            result['truncated'] |= len(item) > 20
            safe_keys = {'code', 'status', 'message', 'data', 'items', 'id', 'success', 'error', 'errors', 'result', 'total'}
            return {key if key in safe_keys else f'field_{i + 1}': shape(child, depth + 1)
                    for i, (key, child) in enumerate(list(item.items())[:20])}
        return '******'

    preview = json.dumps(shape(value.get('body')), ensure_ascii=True, separators=(',', ':'))
    result['truncated'] |= len(preview) > DEBUG_RESPONSE_LIMIT
    result['preview'] = preview[:DEBUG_RESPONSE_LIMIT]
    return result


def _debug_rules(value: object, count: int) -> list[dict]:
    results = {'passed', 'mismatch', 'invalid_json', 'missing', 'skipped', 'invalid_type', 'empty', 'invalid_expiry'}
    supplied = {row.get('index'): row.get('result') for row in value[:32]
                if isinstance(row, dict) and type(row.get('index')) is int
                and row.get('result') in results} if isinstance(value, list) else {}
    return [{'index': index, 'result': supplied.get(index, 'skipped')} for index in range(min(count, 32))]


def _debug_auth_outputs(value: object, step: dict, profile: dict) -> list[dict]:
    if not step.get('auth_phase') or not isinstance(value, list):
        return []
    roles = {}
    if profile.get('transport') == 'BEARER':
        roles['access_token'] = (profile.get('access_token_variable'), {'invalid_type', 'empty'})
    elif profile.get('transport') == 'COOKIE':
        roles['cookie'] = (None, {'cookie_missing'})
    if profile.get('expires_in_variable'):
        roles['expires_in'] = (profile['expires_in_variable'], {'invalid_expiry'})
    results = {}
    for row in value[:3]:
        if not isinstance(row, dict) or not isinstance(row.get('role'), str) or row['role'] not in roles:
            continue
        variable, reasons = roles[row['role']]
        if not isinstance(row.get('result'), str) or row['result'] not in reasons:
            continue
        index = next((i for i, rule in enumerate(step.get('extractors') or [])
                      if variable and rule.get('name') == variable), None)
        results[row['role']] = {'role': row['role'], 'index': index, 'result': row['result']}
    return list(results.values())
_PATH_RE = re.compile(r'^\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$')
_PLACEHOLDER = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+)\}')
_IDENTITY_NAMES = {'user_id', 'username', 'user_name', 'account', 'phone',
                   'mobile', 'email', 'token', 'load_token', 'account_id'}
_TRANSPORT_CATEGORIES = {
    1212: ('connection_refused', 'ConnectionRefused'),
    1220: ('connection_reset', 'ConnectionReset'),
    1211: ('connect_timeout', 'ConnectTimeout'),
    1050: ('request_timeout', 'RequestTimeout'),
    1201: ('broken_pipe', 'BrokenPipe'),
    1101: ('dns_not_found', 'DNSNotFound'),
    1020: ('invalid_url', 'InvalidURL'),
    1110: ('blocked_target', 'BlockedTarget'),
    1111: ('blocked_target', 'BlockedTarget'),
}


def _safe_error_code(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) \
            and math.isfinite(value) and value == int(value) and 0 <= value <= 99999:
        return int(value)
    return 0


def _error_category(error, code, status):
    """Derive fixed categories locally; never trust child-provided error text."""
    if error == 'TransportError' or status == 0:
        return _TRANSPORT_CATEGORIES.get(code, ('transport_error', 'TransportError'))
    return {
        'HTTPFailed': ('http_error', 'HTTPFailed'),
        'AssertionFailed': ('assertion_failed', 'AssertionFailed'),
        'ExtractionFailed': ('extraction_failed', 'ExtractionFailed'),
    }.get(error, ('request_failed', 'RequestFailed'))


def _write_private_json(path, payload):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)


def _binary():
    candidate = os.environ.get('K6_BIN') or shutil.which('k6')
    return str(candidate or '')


def is_available():
    try:
        if k6_docker.runner_mode() == 'DOCKER':
            k6_docker.resolve_config()
            return True
    except EngineError:
        return False
    return bool(_binary() and os.path.isfile(_binary()))


def get_version():
    try:
        if k6_docker.runner_mode() == 'DOCKER':
            return k6_docker.fingerprint(k6_docker.resolve_config())
    except EngineError:
        return ''
    if not is_available():
        return ''
    try:
        result = subprocess.run([_binary(), 'version'], capture_output=True,
                                text=True, timeout=5,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.stdout.strip()[:200] if result.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def _positive_number(value, integer=False):
    try:
        number = float(value)
        return math.isfinite(number) and number > 0 and (not integer or number.is_integer())
    except (TypeError, ValueError):
        return False


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def validate_snapshot(snapshot):
    """Return actionable Chinese errors without exposing credentials."""
    from ..services.execution_policy import validate_policies
    errors = validate_policies(snapshot)
    from ..services import reminder_recovery
    errors.extend(reminder_recovery.validate_snapshot(snapshot))
    load = snapshot.get('load_config') or {}
    runtime = snapshot.get('runtime_config') or {}
    try:
        auth = auth_profiles.normalize_profile(runtime.get('auth_profile'))
    except ValidationError:
        return ['每用户认证配置无效，请检查 runtime_config.auth_profile']
    errors.extend(auth_profiles.validate_binding(auth, snapshot))
    if load.get('model', 'CONCURRENCY') != 'CONCURRENCY':
        errors.append('k6 验证版仅支持固定并发 CONCURRENCY')
    for key in ('ramp_up', 'max_requests'):
        if load.get(key):
            errors.append(f'k6 验证版不支持 {key}，请设为 0')
    if not _positive_number(load.get('concurrency', 1), integer=True):
        errors.append('并发用户数必须是正整数')
    users = int(load.get('concurrency', 1)) if _positive_number(load.get('concurrency', 1), True) else 1
    if not _positive_number(load.get('duration', 60)) or float(load.get('duration', 60)) < 1:
        errors.append('k6 持续时间或轮次测试的最长时间必须至少为 1 秒')
    rounds = load.get('iterations_per_vu', 0)
    if rounds not in (0, None, '') and not _positive_number(rounds, True):
        errors.append('每用户轮次必须是非负整数')
    if str(runtime.get('worker_processes') or 1) != '1':
        errors.append('k6 验证版仅允许一个本地引擎进程')
    if runtime.get('follow_redirects'):
        errors.append('k6 验证版禁止自动重定向，以保持请求计数与身份范围准确')
    if not _positive_number(runtime.get('timeout', 30)):
        errors.append('请求超时必须大于 0')
    if (snapshot.get('script_ref') or {}).get('mode') == 'script':
        errors.append('k6 验证版只支持页面场景，不支持上传任意脚本')
    variables = snapshot.get('variables') or []
    global_headers = (snapshot.get('env_config') or {}).get('headers') or {}
    if not isinstance(global_headers, dict):
        errors.append('环境全局请求头必须为键值对象')
        global_headers = {}
    known = {'base_url', 'baseUrl', 'vu_id', 'iteration', 'request_id'}
    if runtime.get('resource_recovery'):
        known.update(reminder_recovery.NAMES)
    used_names = set()
    csv_variables = []
    for variable in variables:
        name = variable.get('name') or ''
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            errors.append('变量名必须由英文字母、数字和下划线组成，且不能以数字开头')
        if name in used_names or name in {'vu_id', 'iteration', 'request_id'}:
            errors.append('变量名重复或覆盖 k6 内置变量')
        used_names.add(name); known.add(name)
        kind = (variable.get('type') or 'CONSTANT').upper()
        if kind not in ('CONSTANT', 'CSV'):
            errors.append('k6 验证版变量只支持 CONSTANT 和 CSV')
        if kind == 'CSV':
            csv_variables.append(variable)
            file_id = str(variable.get('data_file_id') or variable.get('file_id') or '')
            data = (snapshot.get('csv_data') or {}).get(file_id) or {}
            rows = data.get('rows') or []
            column = variable.get('column')
            if len(rows) < users:
                errors.append(f'账号数据行数不足：并发 {users}，可用 {len(rows)}；禁止循环复用账号')
            if not column or any(column not in row for row in rows[:users]):
                errors.append('CSV 变量必须指定存在的列')
    account_pool = snapshot.get('account_pool')
    identity_name = runtime.get('account_identity_variable')
    identities = ([{'data_file_id': account_pool['data_key'], 'column': account_pool['identity_column']}]
                  if account_pool else
                  [v for v in csv_variables if v.get('name') == identity_name] if identity_name else
                  [v for v in csv_variables if v.get('name', '').lower() in _IDENTITY_NAMES])
    if auth:
        identities = auth_profiles.identity_variables(snapshot)
        if account_pool:
            identities += [{'data_file_id': account_pool['data_key'], 'column': account_pool['identity_column']}]
    protected_identity_names = (auth_profiles.stable_identity_names(snapshot) if auth else
                                {v.get('name') for v in identities if v.get('name')})
    if account_pool:
        pool_rows = ((snapshot.get('csv_data') or {}).get(account_pool['data_key']) or {}).get('rows') or []
        if len(pool_rows) < users:
            errors.append(f'账号池有效身份不足：并发 {users}，可用 {len(pool_rows)}；禁止循环复用账号')
    if csv_variables and not identities:
        errors.append('账号池需要 username/user_id/token 等身份列，或指定 account_identity_variable')
    for variable in identities:
        file_id = str(variable.get('data_file_id') or variable.get('file_id') or '')
        rows = ((snapshot.get('csv_data') or {}).get(file_id) or {}).get('rows') or []
        values = [str(row.get(variable.get('column'), '')).strip() for row in rows[:users]]
        if any(not value for value in values):
            errors.append('账号身份列存在空值')
        if len(set(values)) != len(values):
            errors.append('账号身份或 Token 存在重复，无法保证每个用户独立身份')
    from ..services.upload_files import frozen_errors
    errors.extend(frozen_errors(snapshot))
    steps = [s for s in snapshot.get('steps', []) if s.get('enabled', True)]
    try:
        k6_thresholds.validate_config(snapshot.get('sla_config') or {}, steps)
    except ValidationError as exc:
        errors.append(f'SLA 配置无效：{exc.detail}')
    if not any(not s.get('is_setup') for s in steps):
        errors.append('场景没有启用的业务步骤')
    ids = [str(s['id']) for s in steps if s.get('id') is not None]
    if len(ids) != len(set(ids)):
        errors.append('步骤 ID 必须唯一，避免请求统计合并')
    auth_steps = auth_profiles.profile_steps(auth)
    ordered = ([s for s in auth_steps if s['auth_phase'] == 'login']
               + [s for s in auth_steps if s['auth_phase'] == 'refresh']
               + [s for s in steps if s.get('is_setup')] + [s for s in steps if not s.get('is_setup')])
    for step in ordered:
        if (step.get('weight') or 1) != 1:
            errors.append('k6 验证版按步骤顺序执行，不支持步骤权重')
        protocol = step.get('protocol', 'HTTP')
        if protocol not in ('HTTP', 'WEBSOCKET', 'SSE'):
            errors.append('步骤协议仅支持 HTTP、WEBSOCKET 或 SSE')
            continue
        if protocol == 'SSE':
            from ..services.sse_steps import validate_sse_step
            errors.extend(validate_sse_step(step, snapshot, known, protected_identity_names))
            continue
        if step.get('sse_config'):
            errors.append('非 SSE 步骤不能配置 SSE 事件合同')
        if protocol == 'WEBSOCKET':
            from ..services.websocket_steps import validate_websocket_step
            errors.extend(validate_websocket_step(step, snapshot, known, protected_identity_names))
            continue
        if step.get('websocket_config'):
            errors.append('HTTP 步骤不能配置 WebSocket 会话')
        body_type = (step.get('body_type') or 'NONE').upper()
        from ..services import upload_files
        errors.extend(upload_files.request_errors(step, auth_profiles.request_headers(step, global_headers, auth_enabled=bool(auth))))
        if body_type == 'JSON':
            try:
                json.loads(step.get('body') or '')
            except (ValueError, TypeError):
                errors.append('JSON 请求体必须是有效 JSON；变量应放在 JSON 字符串内')
        if (step.get('method') or 'GET').upper() not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'):
            errors.append('k6 验证版只支持标准 HTTP 请求方法')
        if not step.get('url'):
            errors.append('请求地址不能为空')
        elif not step['url'].startswith(('http://', 'https://', '{{base', '${base')) and not (snapshot.get('env_config') or {}).get('base_url'):
            errors.append('相对请求地址需要环境 base_url')
        think = step.get('think_time') or {}
        if not isinstance(think, dict) or (think.get('type') or '').upper() not in ('', 'NONE', 'FIXED'):
            errors.append('k6 验证版思考时间只支持 FIXED')
        elif think.get('min') not in (None, '', 0) and not _positive_number(think.get('min')):
            errors.append('思考时间必须是非负数')
        for key in ('headers', 'params'):
            if not isinstance(step.get(key) or {}, dict):
                errors.append(f'请求 {key} 必须为键值对象')
        rendered_fields = {k: step.get(k) for k in ('url', 'params', 'body')}
        rendered_fields['headers'] = auth_profiles.request_headers(step, global_headers, auth_enabled=bool(auth))
        for text in _strings(rendered_fields):
            for found in _PLACEHOLDER.finditer(text):
                if (found.group(1) or found.group(2)).strip() not in known:
                    errors.append('请求引用了尚未定义或尚未提取的变量')
        for rule in step.get('assertions') or []:
            kind = (rule.get('type') or '').upper().replace('JSONPATH', 'JSON_PATH')
            if kind not in ('STATUS_CODE', 'JSON_PATH', 'CONTAINS'):
                errors.append('k6 验证版断言仅支持 STATUS_CODE / JSON_PATH 相等判断和 CONTAINS 文本包含')
            if rule.get('operator') not in (None, '', 'eq', 'equals', '=='):
                errors.append('k6 验证版只支持相等断言')
            if kind == 'STATUS_CODE' and not str(rule.get('expected', '')).isdigit():
                errors.append('状态码断言的期望值必须是整数')
            if kind == 'JSON_PATH' and not _PATH_RE.fullmatch(rule.get('expr') or rule.get('json_path') or ''):
                errors.append('JSONPath 仅支持 $.field.child 和 [数字]，不支持通配符、过滤或正则')
            if kind == 'JSON_PATH' and isinstance(rule.get('expected'), (dict, list)):
                errors.append('JSONPath 断言期望值仅支持标量，不支持对象或数组')
            if kind == 'CONTAINS' and (not isinstance(rule.get('expected'), str) or not rule['expected'].strip()):
                errors.append('CONTAINS 断言期望值必须是非空字符串')
        for rule in step.get('extractors') or []:
            kind = (rule.get('type') or 'JSON_PATH').upper().replace('JSONPATH', 'JSON_PATH')
            if kind != 'JSON_PATH' or not _PATH_RE.fullmatch(rule.get('expr') or rule.get('json_path') or ''):
                errors.append('k6 验证版提取器仅支持基础 JSONPath 路径')
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', rule.get('name') or ''):
                errors.append('提取变量名无效')
            if rule.get('name') in {'vu_id', 'iteration', 'base_url', 'baseUrl', 'request_id'} or rule.get('name') in protected_identity_names:
                errors.append('提取器不能覆盖账号身份或内置变量')
            known.add(rule.get('name'))
    return list(dict.fromkeys(errors))


class K6Engine(BaseEngine):
    name = 'K6'

    def __init__(self, snapshot, on_sample=None, on_log=None, raw_csv_path=None, work_dir=None):
        super().__init__(snapshot, on_sample, on_log)
        private_root = Path(os.environ.get('PERF_PRIVATE_ROOT') or _ROOT.parent / 'runtime' / 'private')
        self.work_dir = Path(work_dir or private_root / 'k6_runs' / uuid.uuid4().hex)
        self.raw_csv_path = raw_csv_path
        self.steps = [s for s in snapshot.get('steps', []) if s.get('enabled', True)]
        self.auth_profile = auth_profiles.normalize_profile((snapshot.get('runtime_config') or {}).get('auth_profile'))
        self.steps += auth_profiles.profile_steps(self.auth_profile, snapshot)
        self._has_websocket = any(step.get('protocol') == 'WEBSOCKET' for step in self.steps)
        self._has_sse = any(step.get('protocol') == 'SSE' for step in self.steps)
        self._has_stream = self._has_websocket or self._has_sse
        self._sse = SSEMetrics(self.steps)
        self._websocket = WebSocketMetrics(self.steps)
        self.collector = MetricsCollector()
        self.completion_buckets = CompletionBuckets()
        self._sample_seq = 0
        self.process = None
        self._start_ts = None
        self._end_ts = None
        self._last_sample = None
        self._raw_fh = None
        self._raw_writer = None
        self._raw_rows = 0
        self._http_total = 0
        self._http_started = 0
        self._business_started = 0
        self._completed_iterations = 0
        self._executor_iterations = 0
        self._idle_iterations = 0
        from ..services.execution_policy import PolicyMetrics
        self._policy_metrics = PolicyMetrics(self.steps, snapshot.get('load_config') or {})
        load = snapshot.get('load_config') or {}
        self._debug_enabled = load.get('_purpose') == 'debug' and load.get('concurrency') == 1 and load.get('iterations_per_vu') == 1
        self._debug_details = []
        self._debug_truncated = False
        self._setup_failed_vus = set()
        self._auth_failed_vus = set()
        self._auth_counts = {phase: {'started': 0, 'completed': 0, 'success': 0, 'failed': 0}
                             for phase in ('login', 'refresh')}
        self._active_vus = set()
        self._vu_ids = set()
        self._exit_code = None
        self._stop_reason = ''
        self._runtime_failures = 0
        self._proc_probe = None
        self._cpu_sample_count = 0
        self._peak_k6_cpu = None
        self._process_guard = None
        self._request_error_groups = {}
        self._runner_mode = k6_docker.runner_mode()
        self._docker_guard = None
        self._docker_sample_serial = 0
        self._native_vu = None
        self._scenario_start_ms = None
        self._last_request_completed_ms = None
        self._setup_incomplete_vus = set()
        self._websocket_deadline = None

    def prepare(self):
        errors = validate_snapshot(self.snapshot)
        if errors:
            raise EngineError('；'.join(errors))
        docker_config = k6_docker.resolve_config() if self._runner_mode == 'DOCKER' else None
        if not docker_config and not is_available():
            raise EngineError('未找到 k6 可执行文件，请设置 K6_BIN')
        if docker_config and self.snapshot.get('k6_version') and self.snapshot['k6_version'] != k6_docker.fingerprint(docker_config):
            raise EngineError('Docker runner 依赖与冻结版本不一致，请重新创建任务')
        runtime = self.snapshot.get('runtime_config') or {}
        if docker_config and (runtime.get('proxy') or runtime.get('use_system_proxy')):
            raise EngineError('Docker runner 验证版不支持系统代理或自定义代理')
        self.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        data_path = self.work_dir / 'scenario.private.json'
        # Keep only execution config in the per-VU object. A SharedArray with one
        # giant snapshot element would deserialize the entire account pool per VU.
        payload = {key: self.snapshot.get(key) for key in (
            'load_config', 'env_config', 'runtime_config', 'variables')}
        # 契约溯源只用于预检和证据核对；复制到每个 VU 会放大大型场景的内存。
        # 原始快照和 self.steps 保留全部字段，只有引擎运行输入去掉非执行信息。
        payload.update(steps=[{key: value for key, value in step.items()
                              if key not in ('source_metadata', 'preparation')}
                             for step in self.steps], csv_files={})
        from ..services.execution_policy import compile_groups
        payload['execution_groups'] = compile_groups(self.steps, (self.snapshot.get('env_config') or {}).get('headers'),
                                                     auth_enabled=bool(self.auth_profile))
        from ..services.websocket_steps import normalize_websocket_config
        from ..services.sse_steps import normalize_sse_config
        for step in payload['steps']:
            if step.get('protocol') == 'WEBSOCKET':
                step['websocket_config'] = normalize_websocket_config(step.get('websocket_config'))
            if step.get('protocol') == 'SSE':
                step['sse_config'] = normalize_sse_config(step.get('sse_config'))
        if self.auth_profile:
            payload['runtime_config'] = dict(payload.get('runtime_config') or {}, auth_profile=self.auth_profile)
        for variable in self.snapshot.get('variables') or []:
            if (variable.get('type') or 'CONSTANT').upper() != 'CSV':
                continue
            file_id = str(variable.get('data_file_id') or variable.get('file_id'))
            if file_id in payload['csv_files']:
                continue
            rows_path = self.work_dir / f'csv-{len(payload["csv_files"]):04d}.private.json'
            rows = (self.snapshot.get('csv_data') or {})[file_id]['rows']
            _write_private_json(rows_path, rows)
            payload['csv_files'][file_id] = '/run/' + rows_path.name if docker_config else str(rows_path.resolve())
        from ..services.upload_files import decode_blob
        payload['upload_files'] = {}
        for digest, blob in (self.snapshot.get('upload_data') or {}).items():
            file_path = self.work_dir / f'upload-{len(payload["upload_files"]):04d}.private.bin'
            with os.fdopen(os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as stream:
                stream.write(decode_blob(digest, blob))
            payload['upload_files'][digest] = '/run/' + file_path.name if docker_config else str(file_path.resolve())
        _write_private_json(data_path, payload)
        shutil.copyfile(Path(__file__).with_name('k6_script.js'), self.work_dir / 'scenario.js')
        shutil.copyfile(Path(__file__).with_name('k6_websocket.js'), self.work_dir / 'k6_websocket.js')
        shutil.copyfile(Path(__file__).with_name('k6_sse.js'), self.work_dir / 'k6_sse.js')
        shutil.copyfile(Path(__file__).with_name('k6_reminder_recovery.js'), self.work_dir / 'k6_reminder_recovery.js')
        (self.work_dir / 'k6_sse_runtime.js').write_text(
            "import sse from 'k6/x/testhub-sse'; export default sse;\n" if any(step.get('protocol') == 'SSE' for step in self.steps)
            else "export default {version: ''};\n", encoding='utf-8', newline='\n')
        shutil.copyfile(Path(__file__).with_name('k6_execution_policy.js'), self.work_dir / 'k6_execution_policy.js')
        if docker_config:
            shutil.copyfile(docker_config['binary'], self.work_dir / 'k6')
            with (self.work_dir / 'k6').open('rb') as copied_binary:
                copied_hash = hashlib.file_digest(copied_binary, 'sha256').hexdigest()
            if copied_hash != docker_config['binary_sha256']:
                raise EngineError('Docker runner 二进制与冻结哈希不一致，请重新创建任务')
            os.chmod(self.work_dir / 'k6', 0o700)
            entrypoint = Path(__file__).with_name('k6_container_entrypoint.sh').read_text(encoding='utf-8')
            (self.work_dir / 'container-entrypoint.sh').write_text(entrypoint, encoding='utf-8', newline='\n')
            self._docker_guard = k6_docker.DockerGuard(docker_config, self.work_dir,
                                                      self.snapshot.get('execution_id'))
        self._open_raw_writer()
        self.log('k6 场景已准备：固定身份、顺序业务步骤，凭据存于私有运行目录')

    def _open_raw_writer(self):
        if not self.raw_csv_path or self._raw_fh:
            return
        Path(self.raw_csv_path).parent.mkdir(parents=True, exist_ok=True)
        self._raw_fh = gzip.open(self.raw_csv_path, 'wt', encoding='utf-8', newline='')
        self._raw_writer = csv.writer(self._raw_fh)
        self._raw_writer.writerow(['timestamp_ms', 'elapsed_ms', 'step', 'method', 'url',
                                  'status_code', 'success', 'sent_bytes', 'recv_bytes', 'error',
                                  'is_setup', 'vu_id', 'error_code', 'error_category']
                                 + (['phase'] if self.auth_profile else [])
                                 + (['protocol', 'latency_kind'] if self._has_stream else []))

    def _close_raw_writer(self):
        if self._raw_fh:
            self._raw_fh.close()
        self._raw_fh = None
        self._raw_writer = None

    def _consume_event(self, event):
        kind = event.get('kind')
        vu = int(event.get('vu') or 0)
        if kind in ('group_started', 'group_completed', 'policy_skips'):
            self._policy_metrics.consume(event, vu)
        elif kind == 'executor_iteration':
            self._executor_iterations += 1
            if event.get('idle') is True:
                self._idle_iterations += 1
        elif kind == 'business_idle':
            self._active_vus.discard(vu)
        elif kind == 'business_active':
            self._active_vus.add(vu)
            self.collector.max_concurrency = max(self.collector.max_concurrency, len(self._active_vus))
        elif kind in ('ws_stage', 'ws_connection', 'ws_diagnostic', 'ws_fatal'):
            index = event.get('step')
            if type(index) is not int or not 0 <= index < len(self.steps) or self.steps[index].get('protocol') != 'WEBSOCKET':
                return
            if kind == 'ws_fatal':
                raise EngineError('WebSocket 对端未及时关闭连接，已中止本次发压并清理运行器；未观察关闭的连接保持未知')
            if kind == 'ws_diagnostic':
                self._consume_websocket_diagnostic(event, index)
            else:
                self._websocket.consume(event, index, vu)
        elif kind in ('sse_first_event', 'sse_result'):
            index = event.get('step')
            if type(index) is not int or not 0 <= index < len(self.steps) or self.steps[index].get('protocol') != 'SSE':
                return
            if kind == 'sse_result' and event.get('started') is not True and (index, vu) in self._sse.pending:
                self._http_started -= 1
                if not self.steps[index].get('is_setup'):
                    self._business_started -= 1
            self._sse.consume(event, index, vu)
            if kind == 'sse_result' and event.get('started') is not True:
                self._runtime_failures += 1
        elif kind == 'debug_truncated':
            if self._debug_enabled:
                self._debug_truncated = True
        elif kind == 'diagnostic':
            if not self._debug_enabled:
                return
            index = event.get('step')
            if type(index) is not int or not 0 <= index < len(self.steps):
                return
            if len(self._debug_details) >= DEBUG_LIMIT:
                self._debug_truncated = True
                return
            step = self.steps[index]
            outcomes = {'passed', 'not_sent', 'transport_failed', 'http_failed', 'assertion_failed', 'extraction_failed'}
            status = event.get('status')
            assertions, extractors = step.get('assertions') or [], step.get('extractors') or []
            self._debug_details.append({
                'step_id': step.get('id') or index + 1,
                'phase': step.get('auth_phase') or ('setup' if step.get('is_setup') else 'business'),
                'outcome': event.get('outcome') if event.get('outcome') in outcomes else 'not_sent',
                'status': status if type(status) is int and 0 <= status <= 599 else 0,
                'assertions': _debug_rules(event.get('assertions'), len(assertions)),
                'extractors': _debug_rules(event.get('extractors'), len(extractors)),
                'auth_outputs': _debug_auth_outputs(event.get('auth_outputs'), step, self.auth_profile),
                'rules_truncated': len(assertions) > 32 or len(extractors) > 32,
                'response': _debug_response(event.get('response'), bool(step.get('auth_phase'))),
            })
        elif kind == 'vu_start':
            start = event.get('scenario_start_ms')
            if type(start) is int and start > 0:
                if self._scenario_start_ms is not None and self._scenario_start_ms != start:
                    raise EngineError('k6 发压窗口起点不一致，不能判定执行成功')
                self._scenario_start_ms = start
                if self._has_stream and self._websocket_deadline is None:
                    load = self.snapshot.get('load_config') or {}
                    duration = float(load.get('duration') or 60)
                    cleanup = 2 if int(load.get('iterations_per_vu') or 0) else float((self.snapshot.get('runtime_config') or {}).get('timeout') or 30) + 7
                    remaining = max((start + duration * 1000 - time.time() * 1000) / 1000, 0)
                    self._websocket_deadline = time.monotonic() + remaining + cleanup
            self._vu_ids.add(vu)
            if not self._policy_metrics.enabled:
                self._active_vus.add(vu)
            self.collector.max_concurrency = max(self.collector.max_concurrency, len(self._active_vus))
        elif kind == 'vu_done':
            self._active_vus.discard(vu)
        elif kind == 'setup_failed':
            self._setup_failed_vus.add(vu); self._active_vus.discard(vu)
        elif kind == 'load_expired':
            if event.get('setup_complete') is not True:
                self._setup_incomplete_vus.add(vu)
        elif kind == 'auth_failed':
            self._auth_failed_vus.add(vu); self._active_vus.discard(vu)
        elif kind == 'runtime_error':
            self._runtime_failures += 1
        elif kind == 'iteration':
            self._completed_iterations += 1
        elif kind == 'request_started':
            index = int(event.get('step', -1))
            if 0 <= index < len(self.steps):
                if self.steps[index].get('protocol') == 'WEBSOCKET':
                    self._websocket.stages['sessions']['started'] += 1
                else:
                    self._http_started += 1
                    if self.steps[index].get('protocol') == 'SSE':
                        self._sse.start(index, vu)
                phase = self.steps[index].get('auth_phase')
                if phase in self._auth_counts:
                    self._auth_counts[phase]['started'] += 1
                if not self.steps[index].get('is_setup') and not self.steps[index].get('auth_phase'):
                    self._business_started += 1
        elif kind == 'request':
            index = int(event.get('step', -1))
            if index < 0 or index >= len(self.steps):
                return
            step = self.steps[index]
            websocket = step.get('protocol') == 'WEBSOCKET'
            completed = event.get('timestamp_ms')
            if type(completed) is int and completed > 0:
                self._last_request_completed_ms = max(self._last_request_completed_ms or 0, completed)
            name = step.get('name') or f'步骤 {index + 1}'
            method = 'WEBSOCKET' if websocket else (step.get('method') or 'GET').upper()
            # Even literal secrets embedded in URLs cannot enter report artifacts.
            label = f'step:{step.get("id") or index + 1}'
            elapsed = k6_thresholds.finite(event.get('elapsed_ms'))
            if elapsed is None or elapsed < 0:
                raise EngineError('k6 请求耗时事件无效，已保留此前的有效采样')
            ok = event.get('ok') is True
            if websocket:
                self._websocket.stages['sessions']['completed'] += 1
                self._websocket.stages['sessions']['success' if ok else 'failed'] += 1
            else:
                self._http_total += 1
            phase = step.get('auth_phase')
            if phase in self._auth_counts:
                self._auth_counts[phase]['completed'] += 1
                self._auth_counts[phase]['success' if ok else 'failed'] += 1
            allowed_errors = WS_ERRORS if websocket else ('HTTPFailed', 'AssertionFailed', 'ExtractionFailed', 'TransportError', 'SSEFailed')
            error = event.get('error') if event.get('error') in allowed_errors else ''
            code = _safe_error_code(event.get('error_code'))
            status = int(event.get('status') or 0)
            category, error_type = ('none', None) if ok else _error_category(error, code, status)
            if websocket and not ok:
                category, error_type = error or 'WSSessionFailed', error or 'WSSessionFailed'
            if step.get('protocol') == 'SSE' and not ok:
                category, error_type = 'SSEFailed', 'SSEFailed'
            if not ok:
                group = self._request_error_groups.setdefault((code, category, status), {
                    'error_code': code, 'category': category, 'status_code': status,
                    'count': 0, 'business_count': 0, 'setup_count': 0})
                group['count'] += 1
                group['setup_count' if step.get('is_setup') or step.get('auth_phase') else 'business_count'] += 1
            metric_key = f'id:{step["id"]}' if step.get('id') is not None else f'index:{index}'
            self.collector.record(metric_key, elapsed, ok, error_type=error_type,
                                  error_message=category if not ok else '', method=method, url=label,
                                  counted=not step.get('is_setup') and not step.get('auth_phase'))
            if not step.get('is_setup') and not step.get('auth_phase'):
                self.completion_buckets.record(event.get('timestamp_ms'))
            self.collector.steps[metric_key].name = name
            if self._raw_writer:
                self._raw_writer.writerow([int(event['timestamp_ms']) if valid_timestamp(event.get('timestamp_ms')) else '', round(elapsed, 3),
                    name, method, label, status, int(ok), 0, 0, error,
                    int(bool(step.get('is_setup') or step.get('auth_phase'))), vu, code, category]
                    + ([phase or ('setup' if step.get('is_setup') else 'business')] if self.auth_profile else [])
                    + ([step.get('protocol', 'HTTP'), 'session' if websocket else 'stream' if step.get('protocol') == 'SSE' else 'request'] if self._has_stream else []))
                self._raw_rows += 1

    def _consume_websocket_diagnostic(self, event: dict, index: int) -> None:
        if not self._debug_enabled:
            return
        if len(self._debug_details) >= DEBUG_LIMIT:
            self._debug_truncated = True
            return
        step = self.steps[index]
        stage = event.get('stage')
        if stage not in ('auth', 'command', 'event', 'heartbeat'):
            return
        command = event.get('command')
        definitions = (step.get('websocket_config') or {}).get('commands') or []
        definition = {}
        if stage in ('command', 'event'):
            if type(command) is not int or not 0 <= command < len(definitions):
                return
            definition = definitions[command]
        self._debug_details.append({'step_id': step.get('id') or index + 1, 'protocol': 'WEBSOCKET',
            'phase': 'setup' if step.get('is_setup') else 'business', 'stage': stage,
            'command_index': command if stage in ('command', 'event') else None,
            'outcome': 'passed' if event.get('outcome') == 'passed' else 'failed',
            'assertions': _debug_rules(event.get('assertions'), len(definition.get('assertions') or [])),
            'extractors': _debug_rules(event.get('extractors'), len(definition.get('extractors') or [])),
            'response': {'state': 'auth_omitted' if stage == 'auth' else 'body_omitted'}})

    def _step_metrics(self, duration: float) -> list[dict]:
        result = []
        for index, step in enumerate(self.steps):
            step_id = step.get('id') if step.get('id') is not None else f'legacy:{index + 1}'
            metric_key = f'id:{step["id"]}' if step.get('id') is not None else f'index:{index}'
            metric = self.collector.steps.get(metric_key)
            if metric is not None:
                result.append(dict(metric.to_stat(duration), step_id=step_id,
                                   protocol=step.get('protocol', 'HTTP'),
                                   latency_kind='session' if step.get('protocol') == 'WEBSOCKET' else 'stream' if step.get('protocol') == 'SSE' else 'request',
                                   phase=step.get('auth_phase') or ('setup' if step.get('is_setup') else 'business')))
        return result

    def _emit_sample(self, force=False):
        now = time.monotonic()
        interval = max(float((self.snapshot.get('runtime_config') or {}).get('sample_interval') or 1), 0.1)
        if not self._start_ts:
            return
        since = now - (self._last_sample or self._start_ts)
        if not force and since < interval:
            return
        elapsed = now - self._start_ts
        # The collector window still supplies latency/errors, never K6 throughput.
        self.collector.peak_tps = 0.0
        window_count = self.collector._win_total
        sample = self.collector.take_window(1)
        throughput = self.completion_buckets.snapshot(include_buckets=False)
        sample.update(tps=throughput['latest_rps'], throughput=throughput)
        self._sample_seq += 1
        sample.update(sample_seq=self._sample_seq, window_count=window_count,
                      business_total=self.collector.total, failed_requests=self.collector.failed,
                      success_requests=self.collector.success, completed_iterations=self._completed_iterations,
                      executor_iterations=self._executor_iterations, idle_iterations=self._idle_iterations)
        if self._policy_metrics.enabled:
            sample['execution_policy'] = self._policy_metrics.snapshot()
        if not window_count:
            for key in ('avg_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'error_rate'):
                sample[key] = None
        self.collector.peak_tps = throughput['peak_rps']
        step_metrics = self._step_metrics(elapsed)
        sample.update(elapsed_seconds=elapsed, engine_finished=self._end_ts is not None,
                      sla_metrics=dict(self.collector.build_summary(elapsed),
                                       business_total=self.collector.total, step_metrics=step_metrics,
                                       metric_duration_seconds=elapsed))
        sample.update(ts_offset=int(round(elapsed)), active_users=len(self._active_vus),
                      cpu_percent=None, cpu_sampled=False, memory_mb=0.0,
                      steps=[dict(row, name=row['step_name']) for row in step_metrics], http_total=self._http_total,
                      http_started=self._http_started, business_started=self._business_started,
                      http_incomplete=max(self._http_started - self._http_total, 0),
                      business_incomplete=max(self._business_started - self.collector.total, 0))
        sample['native_vu_observations'] = self._native_vu.drain() if self._native_vu else []
        if self._has_websocket:
            sample['websocket'] = self._websocket.snapshot(elapsed, self._end_ts is not None)
        if self._has_sse:
            sample['sse'] = self._sse.snapshot()
        if self._docker_guard:
            docker_sample = self._docker_guard.sample()
            if docker_sample and docker_sample[0] != self._docker_sample_serial:
                self._docker_sample_serial, cpu, memory = docker_sample
                sample.update(cpu_percent=round(cpu, 1), cpu_sampled=True, memory_mb=round(memory, 1))
                self._cpu_sample_count += 1
                self._peak_k6_cpu = max(self._peak_k6_cpu or 0, sample['cpu_percent'])
        elif self._proc_probe:
            try:
                cpu = float(self._proc_probe.cpu_percent())
                if math.isfinite(cpu) and cpu >= 0:
                    sample['cpu_percent'] = round(cpu, 1)
                    sample['cpu_sampled'] = True
                    self._cpu_sample_count += 1
                    self._peak_k6_cpu = max(self._peak_k6_cpu or 0, sample['cpu_percent'])
            except Exception:
                pass
            try:
                sample['memory_mb'] = round(self._proc_probe.memory_info().rss / 1024 ** 2, 1)
            except Exception:
                pass
        self._last_sample = now
        self.on_sample(sample)

    def run(self):
        if self._stopping:
            return
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('K6_')}
        runtime = self.snapshot.get('runtime_config') or {}
        if not runtime.get('use_system_proxy'):
            for key in list(env):
                if key.upper() in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'):
                    env.pop(key)
        if runtime.get('proxy'):
            env.update(HTTP_PROXY=runtime['proxy'], HTTPS_PROXY=runtime['proxy'])
        env.update(K6_NO_USAGE_REPORT='true', K6_TESTHUB_CONFIG=str(self.work_dir / 'scenario.private.json'))
        for key in ('TMP', 'TEMP', 'TMPDIR'):
            env[key] = str(self.work_dir)
        command = [_binary(), 'run', '--quiet', '--log-format=raw', '--log-output=stdout',
                   str(self.work_dir / 'scenario.js')]
        self._start_ts = time.monotonic()
        messages = queue.Queue(maxsize=10000)
        try:
            if self._docker_guard:
                command = self._docker_guard.prepare_container()
            if os.name == 'nt':
                self._process_guard = WindowsJob()
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', bufsize=1, cwd=self.work_dir, env=env,
                creationflags=self._process_guard.creation_flags if self._process_guard else 0)
            if self._process_guard:
                try:
                    self._process_guard.attach_and_resume(self.process)
                except OSError as exc:
                    raise EngineError('无法建立 k6 子进程崩溃清理保护，已阻止执行') from exc
            if self._stopping:
                self.stop()
            elif self._docker_guard:
                load = self.snapshot.get('load_config') or {}
                self._native_vu = NativeVUCollector(self._docker_guard.read_native_status,
                    execution_id=self._docker_guard.execution_id, runner_instance=self._docker_guard.owner,
                    expected_vus=int(load.get('concurrency') or 1), origin=self._start_ts,
                    configured_duration=float(load.get('duration') or 60))
                self._native_vu.start()
            try:
                if self._docker_guard:
                    raise ImportError('Docker CLI is not the load generator')
                import psutil
                probe = psutil.Process(self.process.pid)
                probe.cpu_percent()  # Baseline only; not an observed CPU sample.
                self._proc_probe = probe
            except Exception:
                self._proc_probe = None

            def reader():
                try:
                    for line in self.process.stdout:
                        # Discard k6's own diagnostics: URLs/headers may contain secrets.
                        if line.startswith(EVENT_PREFIX):
                            messages.put(line[len(EVENT_PREFIX):])
                finally:
                    messages.put(None)

            output_thread = threading.Thread(target=reader, daemon=True)
            output_thread.start()
            while True:
                try:
                    message = messages.get(timeout=0.1)
                    if message is None:
                        break
                    self._consume_event(json.loads(message))
                except queue.Empty:
                    pass
                self._emit_sample()
                if self._has_stream:
                    load = self.snapshot.get('load_config') or {}
                    startup_limit = self._start_ts + float(load.get('duration') or 60) + float(runtime.get('timeout') or 30) + 15
                    if time.monotonic() > (self._websocket_deadline or startup_limit):
                        raise EngineError('WebSocket 运行器超过截止清理时间，已强制清理本次运行器；在途步骤和未观察关闭的连接保持未完成或未知')
                if self._native_vu and self._native_vu.summary()['collector_failure']:
                    raise EngineError('原生活动 VU 采集完整性失败，已停止并保留收到的样本')
            self._exit_code = self.process.wait(timeout=10)
            output_thread.join(timeout=2)
            if self._exit_code and not self._stopping:
                raise EngineError(f'k6 异常退出（退出码 {self._exit_code}），已保留收到的请求统计')
            if self._runtime_failures and not self._stopping:
                raise EngineError('k6 运行时变量或请求配置失败，已保留已有统计；请检查场景关联规则')
            if self._setup_failed_vus and not self._stopping:
                raise EngineError(f'{len(self._setup_failed_vus)} 个用户前置登录或提取失败，未发送这些用户的业务请求')
            if self._setup_incomplete_vus and not self._stopping:
                raise EngineError(f'{len(self._setup_incomplete_vus)} 个用户在发压到期时尚未完成全部前置步骤')
            if self._auth_failed_vus and not self._stopping:
                raise EngineError('k6 运行时变量或请求配置失败，已保留已有统计；请检查场景关联规则')
            if not self.collector.total and not self._stopping:
                raise EngineError('未产生任何已完成的业务请求，不能判定压测成功；请检查超时、时长和业务配置')
            rounds = int((self.snapshot.get('load_config') or {}).get('iterations_per_vu') or 0)
            expected = rounds * int((self.snapshot.get('load_config') or {}).get('concurrency') or 1)
            if rounds and self._executor_iterations != expected and not self._stopping:
                raise EngineError('未完成指定的用户轮次，可能达到最长时间；请查看已获得的数据')
            if self._policy_metrics.incomplete() and not self._stopping:
                raise EngineError('执行组缺少终态，已保留实际请求与未完成组数据')
        finally:
            native_stopped = True
            try:
                if self._native_vu:
                    native_stopped = self._native_vu.stop()
                if self._docker_guard:
                    self._docker_guard.close()
            finally:
                if self._process_guard:
                    self._process_guard.close()
                if self.process and self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=10)
                if self.process and self.process.stdout:
                    self.process.stdout.close()
                self._end_ts = time.monotonic()
                self._active_vus.clear()
                try:
                    self._emit_sample(force=True)
                finally:
                    self._close_raw_writer()
            if not native_stopped:
                raise EngineError('原生活动 VU 采集器未能及时停止，已执行本次 runner 清理')
        # EOF and final stop can race a failure; do not mask an earlier run error.
        if self._native_vu and self._native_vu.summary()['collector_failure']:
            raise EngineError('原生活动 VU 采集完整性失败，已停止并保留收到的样本')

    def stop(self, graceful=True):
        self._stopping = True
        self._stop_reason = '用户停止，保留已收到的请求结果'
        native_stopped = self._native_vu.stop() if self._native_vu else True
        if self._docker_guard:
            self._docker_guard.close()
            if not native_stopped:
                raise EngineError('原生活动 VU 采集器未能及时停止，已执行本次 runner 清理')
            return
        process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except (OSError, ProcessLookupError):
                pass

    def collect(self):
        duration = max((self._end_ts or time.monotonic()) - self._start_ts, 0.001) if self._start_ts else 0
        summary = self.collector.build_summary(duration)
        summary['throughput'] = self.completion_buckets.snapshot()
        summary['peak_tps'] = summary['throughput']['peak_rps']
        step_metrics = self._step_metrics(duration)
        summary['step_metrics'] = step_metrics
        summary['metric_duration_seconds'] = duration
        load = self.snapshot.get('load_config') or {}
        if not int(load.get('iterations_per_vu') or 0):
            seconds = float(load.get('duration') or 60)
            deadline = self._scenario_start_ms + seconds * 1000 if self._scenario_start_ms is not None else None
            summary['timed_load'] = {
                'configured_seconds': seconds,
                'drain_limit_seconds': float((self.snapshot.get('runtime_config') or {}).get('timeout') or 30) + 5,
                'scenario_start_utc_ms': self._scenario_start_ms,
                'admission_deadline_utc_ms': deadline,
                'last_completion_utc_ms': self._last_request_completed_ms,
                'observed_drain_seconds': max(0, (self._last_request_completed_ms - deadline) / 1000)
                    if deadline is not None and self._last_request_completed_ms is not None else None,
                'engine_seconds': duration,
                'notice': '配置时长包含前置和认证；到期停止发起新 HTTP 请求、SSE 流和 WS 会话。已接纳 WS 会话可在原会话上限及全局收尾期限内完成认证和命令，实际关闭后统计结果。引擎总时长和原生 VU 观测包含启动、收尾，不证明全时段持续业务并发。',
            }
        summary['native_vu'] = self._native_vu.summary() if self._native_vu else {
            'version': NATIVE_VU_VERSION, 'source': NATIVE_VU_SOURCE, 'available': False,
            'reason': 'not_started' if self._runner_mode == 'DOCKER' else 'unsupported_runner',
            'sustained_concurrency_verified': None, 'persistence_verified': None}
        summary.update(http_total=self._http_total, business_total=self.collector.total,
            runner_mode=self._runner_mode,
            runner_metadata=self._docker_guard.metadata() if self._docker_guard else {'mode': 'NATIVE'},
            peak_load_gen_cpu=self._peak_k6_cpu, cpu_sample_count=self._cpu_sample_count,
            load_generator_capacity_verified=False, data_trustworthy=None,
            http_started=self._http_started, business_started=self._business_started,
            http_incomplete=max(self._http_started - self._http_total, 0),
            business_incomplete=max(self._business_started - self.collector.total, 0),
            business_rps=summary['tps'], completed_iterations=self._completed_iterations,
            executor_iterations=self._executor_iterations, idle_iterations=self._idle_iterations,
            distinct_vus=len(self._vu_ids), setup_failed_vus=len(self._setup_failed_vus),
            setup_incomplete_vus=len(self._setup_incomplete_vus),
            auth_failed_vus=len(self._auth_failed_vus),
            auth_requests={phase: dict(counts, incomplete=max(counts['started'] - counts['completed'], 0))
                           for phase, counts in self._auth_counts.items()},
            k6_exit_code=self._exit_code, adapter_version=ADAPTER_VERSION,
            request_error_groups=sorted(self._request_error_groups.values(), key=lambda group: -group['count']),
            metric_semantics={
                'process_ownership': 'Windows 子进程先挂起、加入关闭即终止的 Job Object 后恢复运行；worker 强杀后子进程退出' if os.name == 'nt' else '非 Windows 平台尚未验证 worker 崩溃时的子进程清理',
                'cpu_percent': '仅 k6 进程 CPU，多核时可能超过 100%；不是系统总 CPU，也不包含 Python 采集进程。首个基线不计入有效采样',
                'cpu_sample_count': '成功读取的 k6 进程 CPU 样本数；0 表示峰值未知，并非占用为 0',
                'data_trustworthy': '尚未标定发压器容量；较低的进程 CPU 不足以证明容量或整个结果可信',
                'total_requests': '业务 HTTP 请求数，排除前置、登录及刷新',
                'http_total': '所有已完成并收到采样的 HTTP 请求，包含前置、登录及刷新',
                'http_started': '开始调用 HTTP 客户端的请求尝试数，包含前置、登录及刷新；不表示服务器已接收',
                'business_started': '业务 HTTP 请求尝试数，排除前置、登录及刷新；渲染失败不计入',
                'http_incomplete': '已开始 HTTP 尝试但未收到完成事件的数量，停止/超时可能截断在途请求',
                'business_incomplete': '已开始但未收到完成事件的业务请求数量，不混入已完成请求失败率',
                'tps': '业务请求数 / 引擎运行秒数（业务 RPS），不是事务 TPS',
                'peak_tps': RATE_NOTICE,
                'completed_iterations': '本轮按冻结策略完成全部应执行业务步骤的非空轮次；可包含请求失败，不包含依赖阻断',
                'executor_iterations': '执行器完成的轮次，包含无可执行业务的空轮次；不计入业务 TPS',
                'idle_iterations': '没有执行任何业务步骤的轮次，不计入业务完成轮次或活跃用户',
                'error_rate': '失败业务请求 / 业务请求 × 100，每请求只计一次失败',
                'request_error_groups': '全部失败请求按数值 k6 错误码、固定安全分类和 HTTP 状态分组；分别统计业务与前置请求，不保留原始错误文本',
                'account_data': '每个 CSV 池独立按行 SharedArray；每 VU 只读取自己的一行一次，多列与多轮保持同一行',
                'response_time': '每次请求墙钟耗时，单位毫秒；包含连接等待',
                'percentiles': '平台固定桶直方图估算，直接合并全部请求样本；未平均窗口分位数。误差受桶宽和样本分布影响，不保证固定相对误差',
                'sla': '平台累计已完成请求事件判定，非原生 k6 thresholds；全局排除前置/认证，接口使用稳定步骤 ID；延迟后同一规则持续超限触发停止',
                'bytes': '当前适配器未统计字节数，0 表示未采集',
                'raw_url': '仅保存步骤编号，避免 URL 中的凭据进入报告',
                'think_time': '与网页输入一致，think_time.min 的单位是毫秒',
                'collector': '逐请求安全事件流，适用于功能验证；发压器极限尚未标定',
                'stopped': '停止或异常时在途请求可能未产生事件，已完成样本保留',
            })
        if self._policy_metrics.enabled:
            summary['execution_policy'] = self._policy_metrics.snapshot()
        if self._debug_enabled:
            summary['debug_details'] = {'version': 1, 'limit': DEBUG_LIMIT, 'response_limit': DEBUG_RESPONSE_LIMIT,
                                        'truncated': self._debug_truncated, 'steps': self._debug_details}
        if self._has_websocket:
            summary['websocket'] = self._websocket.snapshot(duration, self._end_ts is not None)
            summary['metric_semantics'].update(
                total_requests='已完成业务步骤：每次 HTTP 请求或整个 WebSocket 会话各计一次，排除前置及登录刷新',
                business_started='已开始业务步骤数：HTTP 请求尝试或 WebSocket 会话尝试',
                tps='已完成业务步骤 / 引擎运行秒数；不是 WebSocket 消息 QPS',
                response_time='HTTP 为请求耗时；WebSocket 为含握手、认证、命令、保活和关闭的会话总耗时；命令 RTT 单列',
                websocket='握手 101 不代表认证或命令通过；命令严格匹配回执 id 和布尔 ok=true。连接数来自实际打开/关闭事件，截断未观察关闭时 current 未知',
                sla='WebSocket 步骤 SLA 使用会话总耗时；首版不支持命令级 SLA。全局混合统计按业务步骤计数')
        if self._has_sse:
            summary['sse'] = self._sse.snapshot()
            summary['metric_semantics'].update(
                sse='一次 SSE 流计一个 HTTP 请求；200 和首事件均不代表业务成功，必须满足事件合同并观察成功终态。首事件与完整流耗时分开统计；未完成流不计成功。',
                sse_bytes='SSE 消费的原始事件字节数，最多含用于检测超限的额外 1 字节；不含 HTTP 头。',
                response_time='HTTP 为请求耗时，SSE 为完整流或失败截止耗时，WebSocket 为会话耗时；SSE 首事件延迟单列。')
        if self._docker_guard:
            summary['metric_semantics'].update(
                process_ownership='每任务唯一 owner/execution 标签；正常退出确认移除本容器；worker 心跳序列停止 5 秒后容器看门狗终止 k6',
                cpu_percent='Docker stats 的整个发压容器 CPU，包含 k6 和轻量看门狗，多核可能超过 100%；不是 Docker CLI 或系统总 CPU',
                cpu_sample_count='成功读取且未重复计算的 Docker stats 样本数；0 表示未知',
                memory_mb='Docker stats 发压容器内存，不是 Windows docker.exe 内存')
        return {'summary': summary, 'request_stats': step_metrics,
                'duration': round(duration, 3), 'stop_reason': self._stop_reason, 'raw_rows': self._raw_rows}
