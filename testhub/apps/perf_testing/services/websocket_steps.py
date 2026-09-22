"""Declarative native WebSocket steps, including explicit catalog upgrades."""
from copy import deepcopy
import json
import math
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from rest_framework.exceptions import ValidationError

from . import auth_profiles

MAX_CONFIG_BYTES = 128 * 1024
MAX_COMMANDS = 64
TIMER_LIMITS = {
    'connect_timeout_ms': (100, 30000, 5000),
    'command_timeout_ms': (100, 60000, 5000),
    'hold_open_ms': (0, 300000, 0),
    'max_session_ms': (1000, 600000, 15000),
    'heartbeat_interval_ms': (1000, 60000, 25000),
}
SENSITIVE_QUERY = re.compile(r'token|auth|cookie|password|secret|credential|session', re.I)
PUBLIC_STEP_FIELDS = ('id', 'order', 'name', 'protocol', 'method', 'enabled', 'is_setup', 'request_path')


def declares_websocket(metadata: dict) -> bool:
    raw = metadata.get('raw') if isinstance(metadata, dict) else None
    return (isinstance(raw, dict) and metadata.get('method') == 'GET'
            and raw.get('x-websocket') is True and isinstance(raw.get('responses'), dict)
            and isinstance(raw['responses'].get('101'), dict))


def catalog_websocket_matches(step: dict) -> bool:
    metadata = step.get('source_metadata') or {}
    return (declares_websocket(metadata) and step.get('method', 'GET') == 'GET'
            and step.get('url') == (metadata.get('request') or {}).get('url')
            and (not step.get('source_request_id') or step['source_request_id'] == metadata.get('id')))


def request_has_writes(request: dict) -> bool:
    # HTTP GET only describes the upgrade. Application frames can still mutate data.
    return (request.get('method') not in ('GET', 'HEAD', 'OPTIONS') or
            request.get('protocol') == 'WEBSOCKET' and any(command.get('request')
                for command in (request.get('websocket_config') or {}).get('commands', [])))


def invalid(field: str) -> None:
    raise ValidationError({'websocket_config': {field: 'WebSocket 配置无效，请检查字段类型、范围和变量映射'}})


def _safe_json(value: object) -> bool:
    if isinstance(value, dict):
        return all(isinstance(key, str) and key not in {'__proto__', 'prototype', 'constructor'}
                   and not auth_profiles.references(key) and _safe_json(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_safe_json(item) for item in value)
    return value is None or isinstance(value, (str, bool, int, float))


def _normalize_frame(value: object, field: str, *, authentication: bool, push: bool = False) -> dict:
    allowed = {'request', 'assertions', 'extractors'} | (set() if authentication else {'name'})
    if push:
        allowed |= {'event_type', 'event_path', 'error_types'}
    if not isinstance(value, dict) or set(value) - allowed:
        invalid(field)
    result = deepcopy(value)
    if not authentication and (not isinstance(result.get('name'), str)
                               or not result['name'].strip() or len(result['name']) > 200):
        invalid(field + '.name')
    request = result.get('request')
    if push:
        event_type = result.get('event_type')
        event_path = result.setdefault('event_path', '$.type')
        errors = result.setdefault('error_types', [])
        if (not isinstance(event_type, str) or not re.fullmatch(r'[a-z][a-z0-9_.]{0,95}', event_type)
                or not isinstance(event_path, str) or not auth_profiles.PATH.fullmatch(event_path)
                or not isinstance(errors, list) or len(errors) > 16
                or any(not isinstance(item, str) or not re.fullmatch(r'[a-z][a-z0-9_.]{0,95}', item)
                       or item == event_type for item in errors)
                or ('request' in result and (not isinstance(request, dict) or not request))):
            invalid(field)
    elif (not isinstance(request, dict) or set(request) != {'version', 'action', 'payload'}
            or type(request.get('version')) is not int or request['version'] != 1
            or not isinstance(request.get('action'), str)
            or not re.fullmatch(r'[a-z][a-z0-9_.]{0,63}', request['action'])
            or not isinstance(request.get('payload'), dict)):
        invalid(field + '.request')
    if push:
        if not result.get('assertions'):
            invalid(field + '.assertions')
    elif authentication:
        if request['action'] != 'auth' or set(request['payload']) != {'token'}:
            invalid(field + '.request')
    elif request['action'] in ('auth', 'ping'):
        invalid(field + '.request')
    for kind in ('assertions', 'extractors'):
        rules = result.setdefault(kind, [])
        if not isinstance(rules, list) or len(rules) > 64:
            invalid(field + '.' + kind)
        outputs = set()
        for rule in rules:
            fields = ({'name', 'type', 'expr'} if kind == 'extractors'
                      else {'type', 'expr', 'expected', 'operator'})
            if (not isinstance(rule, dict) or set(rule) - fields or rule.get('type') != 'JSON_PATH'
                    or not isinstance(rule.get('expr'), str) or not auth_profiles.PATH.fullmatch(rule['expr'])):
                invalid(field + '.' + kind)
            if kind == 'extractors':
                name = rule.get('name')
                if (not isinstance(name, str) or not auth_profiles.NAME.fullmatch(name)
                        or name in auth_profiles.RESERVED or name in outputs):
                    invalid(field + '.extractors')
                outputs.add(name)
            elif ('expected' not in rule or isinstance(rule['expected'], (dict, list))
                  or rule.get('operator') not in (None, '', 'eq', 'equals', '==')):
                invalid(field + '.assertions')
    return result


def normalize_websocket_config(value: object) -> dict:
    """Validate bounded JSON and canonical defaults without reflecting supplied values."""
    if (not isinstance(value, dict) or set(value) - ({'version', 'mode', 'auth', 'commands'} | set(TIMER_LIMITS))
            or type(value.get('version')) is not int or value['version'] != 1):
        invalid('config')
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_CONFIG_BYTES:
            invalid('size')
        if not _safe_json(value):
            invalid('config')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        invalid('config')
    result = deepcopy(value)
    if result.get('mode', 'REQUEST_REPLY') not in ('REQUEST_REPLY', 'PUSH'):
        invalid('mode')
    push = result.get('mode') == 'PUSH'
    for name, (minimum, maximum, default) in TIMER_LIMITS.items():
        timer = result.setdefault(name, 0 if push and name == 'heartbeat_interval_ms' else default)
        if type(timer) is not int or (not minimum <= timer <= maximum and not (name == 'heartbeat_interval_ms' and timer == 0)):
            invalid(name)
    if (max(result['connect_timeout_ms'], result['command_timeout_ms']) > result['max_session_ms']
            or result['hold_open_ms'] >= result['max_session_ms']):
        invalid('max_session_ms')
    if push:
        auth = result.get('auth')
        if (not isinstance(auth, dict) or set(auth) != {'type', 'token'} or auth.get('type') != 'BEARER'
                or not isinstance(auth.get('token'), str) or result['heartbeat_interval_ms'] != 0):
            invalid('auth')
    else:
        result['auth'] = _normalize_frame(result.get('auth'), 'auth', authentication=True)
    commands = result.get('commands')
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_COMMANDS:
        invalid('commands')
    result['commands'] = [_normalize_frame(item, 'commands', authentication=False, push=push) for item in commands]
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_CONFIG_BYTES:
        invalid('size')
    return result


def websocket_url(url: str, base_url: str) -> str:
    """Map HTTP origins to WS without userinfo, auth query, cross-origin or downgrade."""
    try:
        if not isinstance(url, str) or not isinstance(base_url, str):
            invalid('url')
        base = urlsplit(base_url)
        if (base.scheme not in ('http', 'https') or not base.hostname or base.username is not None
                or base.password is not None or '%' in base.netloc or '\\' in base_url):
            invalid('url')
        expected_scheme = 'wss' if base.scheme == 'https' else 'ws'
        for prefix in ('{{base_url}}', '${base_url}', '{{baseUrl}}', '${baseUrl}'):
            if url.startswith(prefix):
                url = base_url.rstrip('/') + url[len(prefix):]
                break
        if not url or url.startswith('//') or '\\' in url or any(ord(c) < 32 for c in url):
            invalid('url')
        if not urlsplit(url).scheme:
            url = base_url.rstrip('/') + '/' + url.lstrip('/')
        parts = urlsplit(url)
        scheme = {'http': 'ws', 'https': 'wss'}.get(parts.scheme, parts.scheme)
        if (scheme != expected_scheme or not parts.hostname or parts.username is not None
                or parts.password is not None or '%' in parts.netloc or auth_profiles.references(parts.netloc)
                or parts.fragment or parts.hostname.lower() != base.hostname.lower()
                or (parts.port or (443 if scheme == 'wss' else 80)) !=
                   (base.port or (443 if base.scheme == 'https' else 80))):
            invalid('url')
        if any(SENSITIVE_QUERY.search(key) or auth_profiles.references(value)
               for key, value in parse_qsl(parts.query, keep_blank_values=True)):
            invalid('url')
        return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ''))
    except (ValueError, TypeError, UnicodeError):
        invalid('url')


def validate_websocket_step(step: dict, snapshot: dict, known: set, protected: set) -> list[str]:
    """Return safe errors; add validated command outputs to known for following steps."""
    errors = []
    if snapshot.get('engine', 'K6') != 'K6':
        errors.append('WebSocket 步骤仅支持 K6')
    if ((step.get('source_request') or step.get('source_request_id') or step.get('source_metadata'))
            and not catalog_websocket_matches(step)):
        errors.append('WebSocket 来源必须明确声明 GET 升级，且保持原目录地址与身份')
    if ((step.get('method') or 'GET') != 'GET' or (step.get('body_type') or 'NONE') != 'NONE'
            or any(step.get(field) for field in ('body', 'files', 'assertions', 'extractors', 'params'))):
        errors.append('WebSocket 使用 GET 握手；HTTP 请求体、参数、文件、断言和提取器必须为空')
    headers = step.get('headers') or {}
    if not isinstance(headers, dict) or any(str(key).lower() in ('authorization', 'cookie') for key in headers):
        errors.append('WebSocket 握手请求头不能覆盖账号认证')
        headers = {}
    think = step.get('think_time') or {}
    if (not isinstance(think, dict) or not isinstance(think.get('type', ''), str)
            or (think.get('type') or '').upper() not in ('', 'NONE', 'FIXED')):
        errors.append('WebSocket 思考时间只支持 FIXED')
    elif think.get('min') not in (None, '', 0):
        try:
            valid = not isinstance(think['min'], bool) and math.isfinite(float(think['min'])) and float(think['min']) >= 0
        except (ValueError, TypeError):
            valid = False
        if not valid:
            errors.append('WebSocket 思考时间必须是非负数')
    try:
        config = normalize_websocket_config(step.get('websocket_config'))
    except ValidationError:
        return errors + ['WebSocket 配置无效，请检查字段类型、范围和变量映射']
    try:
        timeout = float((snapshot.get('runtime_config') or {}).get('timeout', 30)) * 1000
        duration = float((snapshot.get('load_config') or {}).get('duration', 60)) * 1000
        if not math.isfinite(timeout) or max(config['connect_timeout_ms'], config['command_timeout_ms']) > timeout:
            errors.append('WebSocket 连接和命令超时不能超过场景请求超时')
        if not math.isfinite(duration) or config['max_session_ms'] > duration:
            errors.append('WebSocket 会话最长时间不能超过场景持续时间')
    except (ValueError, TypeError):
        errors.append('WebSocket 场景超时或持续时间无效')
    try:
        websocket_url(step.get('url'), (snapshot.get('env_config') or {}).get('base_url'))
    except ValidationError:
        errors.append('WebSocket 必须使用环境同一服务的 WS(S) 地址，禁止降级或地址内嵌凭据')
    try:
        profile = auth_profiles.normalize_profile((snapshot.get('runtime_config') or {}).get('auth_profile'))
    except ValidationError:
        profile = {}
    if not profile or profile.get('transport') != 'BEARER' or profile.get('mode') not in ('STATIC', 'LOGIN'):
        errors.append('WebSocket 必须使用每用户 STATIC/BEARER 或 LOGIN/BEARER 认证')
    access_name = profile.get('access_token_variable')
    push = config.get('mode') == 'PUSH'
    token = config['auth']['token'] if push else config['auth']['request']['payload']['token']
    if token != '{{' + str(access_name) + '}}':
        errors.append('WebSocket auth.token 必须完整引用全局认证的 access_token_variable')
    scope = set(known)
    protected_names = set(protected) | auth_profiles.RESERVED | {v.get('name') for v in snapshot.get('variables') or []}
    protected_names.update(profile.get(key) for key in
        ('access_token_variable', 'refresh_token_variable', 'expires_in_variable', 'cookie_variable'))
    protected_names.update(r['name'] for s in auth_profiles.profile_steps(profile) for r in s['extractors'])
    credential_names = {profile.get(key) for key in ('access_token_variable', 'refresh_token_variable', 'cookie_variable')}
    credential_names.update(name for name in known if isinstance(name, str)
                            and re.search(r'token|password|secret|credential|cookie', name, re.I))
    global_headers = (snapshot.get('env_config') or {}).get('headers') or {}
    if not isinstance(global_headers, dict):
        errors.append('WebSocket 环境请求头必须为对象')
        global_headers = {}
    effective_headers = auth_profiles.request_headers({'protocol': 'WEBSOCKET', 'headers': headers}, global_headers)
    outside_auth = {'url': step.get('url'), 'headers': effective_headers,
                    'commands': config['commands'], 'auth_assertions': config['auth'].get('assertions', [])}
    if auth_profiles.references(outside_auth) & credential_names:
        errors.append('WebSocket 凭据变量只能用于 auth.token，不能写入地址、请求头或业务帧')
    if auth_profiles.references({'url': step.get('url'), 'headers': effective_headers}) - scope:
        errors.append('WebSocket 握手引用了尚未定义的变量')
    outputs = set()
    for frame in ([] if push else [config['auth']]) + config['commands']:
        if auth_profiles.references({'request': frame.get('request'), 'assertions': frame['assertions']}) - scope:
            errors.append('WebSocket 帧引用了尚未定义或尚未提取的变量')
        for rule in frame['extractors']:
            name = rule['name']
            if name in protected_names or name in scope:
                errors.append('WebSocket 提取器不能覆盖账号、认证、输入或已有变量')
            scope.add(name)
            outputs.add(name)
    if not errors:
        known.update(outputs)
    return list(dict.fromkeys(errors))


def _public_rule_metadata(rules: object, *, frozen: bool) -> list[dict]:
    if not isinstance(rules, list) or len(rules) > 64:
        return []
    result, seen = [], set()
    for position, rule in enumerate(rules):
        if not isinstance(rule, dict) or rule.get('type') != 'JSON_PATH':
            continue
        index = rule.get('index') if frozen else position
        if type(index) is not int or not 0 <= index < 64 or index in seen:
            continue
        seen.add(index)
        result.append({'index': index, 'type': 'JSON_PATH'})
    return result


def _public_command_metadata(step: dict) -> list[dict]:
    frozen = 'websocket_config' not in step
    config = step.get('websocket_config')
    commands = (step.get('websocket_commands') if frozen else
                config.get('commands') if isinstance(config, dict) else None)
    if not isinstance(commands, list) or len(commands) > MAX_COMMANDS:
        return []
    result, seen = [], set()
    for position, command in enumerate(commands):
        if not isinstance(command, dict):
            continue
        index = command.get('index') if frozen else position
        name = command.get('name')
        request = command.get('request')
        action = (command.get('action') if frozen else command.get('event_type')
                  or (request.get('action') if isinstance(request, dict) else None))
        if (type(index) is not int or not 0 <= index < MAX_COMMANDS or index in seen
                or not isinstance(name, str) or not name.strip() or len(name) > 200
                or auth_profiles.references(name) or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or not isinstance(action, str) or not re.fullmatch(r'[a-z][a-z0-9_.]{0,95}', action)
                or action in ('auth', 'ping')):
            continue
        seen.add(index)
        result.append({'index': index, 'name': name, 'action': action,
                       'assertions': _public_rule_metadata(command.get('assertions'), frozen=frozen),
                       'extractors': _public_rule_metadata(command.get('extractors'), frozen=frozen)})
        is_event = command.get('kind') == 'event' if frozen else bool(command.get('event_type'))
        if is_event:
            result[-1]['kind'] = 'event'
    return result


def public_websocket_step(step: dict) -> dict:
    """Freeze/resanitize labels, actions and rule indexes/types; omit auth and all payloads."""
    from .reporter import safe_request_path
    result = {key: deepcopy(step[key]) for key in PUBLIC_STEP_FIELDS if key in step}
    url = step.get('request_path') or step.get('url') or ''
    if isinstance(url, str):
        url = re.sub(r'^ws:', 'http:', re.sub(r'^wss:', 'https:', url))
    result['request_path'] = safe_request_path(url)
    result['websocket_commands'] = _public_command_metadata(step)
    return result
