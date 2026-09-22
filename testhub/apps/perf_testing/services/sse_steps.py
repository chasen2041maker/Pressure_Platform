"""Bounded declarative event contracts, independent of any business API schema."""
from copy import deepcopy
import json
import math
import re

from rest_framework.exceptions import ValidationError
from . import auth_profiles
from .websocket_steps import _safe_json, websocket_url

LIMITS = {'total_ms': (1, 300000, 30000), 'idle_ms': (1, 300000, 10000),
          'max_event_bytes': (1, 1048576, 262144), 'max_total_bytes': (1, 16777216, 4194304),
          'max_events': (1, 4096, 256)}
OPERATORS = {'eq', 'ne', 'exists', 'nonempty', 'blank', 'positive_int', 'nonnegative_int', 'base64', 'url'}
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,47}')


def invalid(field='config'):
    raise ValidationError({'sse_config': {field: 'SSE 配置无效，请检查有界事件规则、终态和变量映射'}})


def conditions(value, prior, *, count_allowed=True):
    if not isinstance(value, list) or len(value) > 32:
        invalid('assertions')
    for rule in value:
        if (not isinstance(rule, dict) or set(rule) - {'type', 'expr', 'operator', 'expected', 'expected_count'}
                or rule.get('type') != 'JSON_PATH' or not isinstance(rule.get('expr'), str)
                or not auth_profiles.PATH.fullmatch(rule['expr']) or rule['expr'] == '$'):
            invalid('assertions')
        operator = rule.setdefault('operator', 'eq')
        if not isinstance(operator, str) or operator not in OPERATORS:
            invalid('assertions')
        if operator in ('eq', 'ne'):
            if ('expected' in rule) == ('expected_count' in rule):
                invalid('assertions')
            if isinstance(rule.get('expected'), (dict, list)):
                invalid('assertions')
            if 'expected_count' in rule and (not count_allowed or not isinstance(rule['expected_count'], str) or rule['expected_count'] not in prior):
                invalid('assertions')
        elif 'expected' in rule or 'expected_count' in rule:
            invalid('assertions')
    return value


def normalize_sse_config(value):
    if (not isinstance(value, dict) or set(value) - {'version', 'rules', 'assertions', 'error_conditions', *LIMITS}
            or type(value.get('version')) is not int or value['version'] != 1):
        invalid()
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 131072 or not _safe_json(value):
            invalid()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        invalid()
    result = deepcopy(value)
    for key, (low, high, default) in LIMITS.items():
        number = result.setdefault(key, default)
        if type(number) is not int or not low <= number <= high:
            invalid(key)
    if result['idle_ms'] > result['total_ms']:
        invalid('idle_ms')
    conditions(result.setdefault('assertions', []), set(), count_allowed=False)
    conditions(result.setdefault('error_conditions', []), set(), count_allowed=False)
    rules = result.get('rules')
    if not isinstance(rules, list) or not 1 <= len(rules) <= 16:
        invalid('rules')
    prior, outputs, terminal, asserted = set(), set(), False, set()
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) - {'name', 'event', 'data', 'match', 'assertions', 'extractors',
                'min_events', 'max_events', 'after', 'without', 'terminal', 'sequence'}
                or not isinstance(rule.get('name'), str) or not NAME.fullmatch(rule['name']) or rule['name'] in prior
                or not isinstance(rule.get('event'), str) or not NAME.fullmatch(rule['event']) or rule['event'] == 'error'):
            invalid('rules')
        for key, default in [('min_events', 0), ('max_events', result['max_events'])]:
            number = rule.setdefault(key, default)
            if type(number) is not int or not 0 <= number <= result['max_events']:
                invalid('rules.count')
        if not rule['max_events'] or rule['min_events'] > rule['max_events']:
            invalid('rules.count')
        if type(rule.setdefault('terminal', False)) is not bool:
            invalid('rules.terminal')
        after = rule.setdefault('after', [])
        if (not isinstance(after, list) or any(not isinstance(name, str) for name in after)
                or len(after) != len(set(after)) or any(name not in prior for name in after)):
            invalid('rules.after')
        without = rule.setdefault('without', [])
        if (not isinstance(without, list) or any(not isinstance(name, str) for name in without)
                or len(without) != len(set(without)) or any(name not in prior or name in after for name in without)):
            invalid('rules.without')
        conditions(rule.setdefault('match', []), prior)
        conditions(rule.setdefault('assertions', []), prior)
        if 'data' in rule:
            if not isinstance(rule['data'], str) or not 1 <= len(rule['data']) <= 256 or rule['match'] or rule['assertions'] or rule.get('sequence'):
                invalid('rules.data')
            try:
                json.loads(rule['data'])
            except (ValueError, TypeError):
                pass
            else:
                invalid('rules.data')
        if rule['terminal']:
            terminal = True
            if not rule['assertions'] and not (set(after) & asserted):
                invalid('rules.terminal')
        sequence = rule.get('sequence')
        if sequence is not None and (not isinstance(sequence, dict) or set(sequence) != {'expr', 'start'}
                or not isinstance(sequence['expr'], str) or not auth_profiles.PATH.fullmatch(sequence['expr'])
                or type(sequence['start']) is not int or not 0 <= sequence['start'] <= 1000000):
            invalid('rules.sequence')
        extractors = rule.setdefault('extractors', [])
        if not isinstance(extractors, list) or len(extractors) > 16 or ('data' in rule and extractors):
            invalid('rules.extractors')
        for item in extractors:
            if (not isinstance(item, dict) or set(item) != {'name', 'type', 'expr'} or item['type'] != 'JSON_PATH'
                    or not isinstance(item['expr'], str) or not auth_profiles.PATH.fullmatch(item['expr']) or item['expr'] == '$'
                    or not isinstance(item['name'], str) or not auth_profiles.NAME.fullmatch(item['name'])
                    or item['name'] in auth_profiles.RESERVED | outputs):
                invalid('rules.extractors')
            outputs.add(item['name'])
        prior.add(rule['name'])
        if rule['assertions'] or set(after) & asserted:
            asserted.add(rule['name'])
    if not terminal:
        invalid('rules.terminal')
    return result


def declares_sse(metadata):
    raw = metadata.get('raw') or {}
    for status, response in (raw.get('responses') or {}).items():
        if str(status).startswith('2') and isinstance(response, dict):
            if any(str(media).split(';')[0].strip().lower() == 'text/event-stream' for media in response.get('content', {})):
                return True
    return 'text/event-stream' in raw.get('produces', [])


def output_names(step):
    config = step.get('sse_config') or {}
    return {rule['name'] for event in config.get('rules', []) for rule in event.get('extractors', [])}


def public_sse_step(step):
    from .reporter import safe_request_path
    result = {key: deepcopy(step[key]) for key in ('id', 'order', 'name', 'protocol', 'method', 'enabled', 'is_setup') if key in step}
    result['request_path'] = safe_request_path(step.get('request_path') or step.get('url'))
    return result


def validate_sse_step(step, snapshot, known, protected):
    errors = []
    try:
        config = normalize_sse_config(step.get('sse_config'))
    except ValidationError:
        return ['SSE 配置无效，请检查事件、业务终态和有界参数']
    if snapshot.get('engine', 'K6') != 'K6':
        errors.append('SSE 仅支持 K6')
    if (step.get('method', 'GET') not in ('GET', 'POST') or step.get('body_type', 'NONE') not in ('NONE', 'JSON')
            or any(step.get(key) for key in ('files', 'assertions', 'extractors', 'websocket_config'))
            or step.get('method', 'GET') == 'GET' and step.get('body_type', 'NONE') != 'NONE'):
        errors.append('SSE 仅支持 GET/POST 与 NONE/JSON 请求体，事件断言和提取须放入 SSE 配置')
    if step.get('body_type', 'NONE') == 'NONE' and step.get('body'):
        errors.append('SSE NONE 类型不能包含未发送的正文')
    if any(not isinstance(step.get(key) or {}, dict) for key in ('headers', 'params')):
        return errors + ['SSE 请求头和参数必须为对象']
    if any(isinstance(value, (dict, list)) for value in (step.get('params') or {}).values()):
        errors.append('SSE 查询参数只支持标量值')
    think = step.get('think_time') or {}
    try:
        if (not isinstance(think, dict) or think.get('type', '').upper() not in ('', 'NONE', 'FIXED')
                or isinstance(think.get('min'), bool) or not math.isfinite(float(think.get('min') or 0))
                or float(think.get('min') or 0) < 0):
            errors.append('SSE 思考时间只支持非负 FIXED 值')
    except (ValueError, TypeError, AttributeError):
        errors.append('SSE 思考时间配置无效')
    metadata = step.get('source_metadata') or {}
    if (metadata or step.get('source_request_id') or step.get('source_request')) and not declares_sse(metadata):
        errors.append('来源契约未声明 SSE 成功响应')
    if step.get('body_type') == 'JSON':
        try:
            json.loads(step.get('body') or '')
        except (ValueError, TypeError):
            errors.append('SSE JSON 请求体无效')
    try:
        websocket_url(step.get('url'), (snapshot.get('env_config') or {}).get('base_url'))
    except ValidationError:
        errors.append('SSE 必须使用环境同一 HTTP(S) 服务，禁止地址内嵌凭据')
    runtime = snapshot.get('runtime_config') or {}
    try:
        profile = auth_profiles.normalize_profile(runtime.get('auth_profile'))
    except ValidationError:
        profile = {}
    if not profile or profile.get('transport') != 'BEARER' or profile.get('mode') not in ('STATIC', 'LOGIN'):
        errors.append('SSE 必须使用每用户 STATIC/BEARER 或 LOGIN/BEARER 认证')
    try:
        timeout = float(runtime.get('timeout', 30)) * 1000
        duration = float((snapshot.get('load_config') or {}).get('duration', 60)) * 1000
        if not math.isfinite(timeout) or not math.isfinite(duration) or config['total_ms'] > min(timeout, duration):
            errors.append('SSE 总时长不能超过场景请求超时或持续时间')
    except (ValueError, TypeError):
        errors.append('SSE 场景时间配置无效')
    if runtime.get('proxy') or runtime.get('use_system_proxy'):
        errors.append('SSE 当前不支持代理')
    headers = auth_profiles.request_headers(step, (snapshot.get('env_config') or {}).get('headers'), auth_enabled=bool(profile))
    scope = set(known)
    outside = {key: step.get(key) for key in ('url', 'params', 'body')}
    outside.update(headers=headers, assertions=config['assertions'], error_conditions=config['error_conditions'])
    if auth_profiles.references(outside) - scope:
        errors.append('SSE 请求或全局断言引用了未知变量')
    credentials = {profile.get(key) for key in ('access_token_variable', 'refresh_token_variable', 'cookie_variable')}
    credentials.update(key for key in known if re.search(r'token|password|secret|credential|cookie', str(key), re.I))
    if auth_profiles.references({**outside, 'contract': config}) & credentials:
        errors.append('SSE 凭据只能由每用户认证注入请求头')
    blocked = set(protected) | set(known) | auth_profiles.RESERVED | credentials
    outputs = set()
    for event in config['rules']:
        if auth_profiles.references({'match': event['match'], 'assertions': event['assertions']}) - scope:
            errors.append('SSE 事件引用了尚未定义或提取的变量')
        for rule in event['extractors']:
            if rule['name'] in blocked:
                errors.append('SSE 提取器不能覆盖账号、认证、输入或已有变量')
            scope.add(rule['name']); outputs.add(rule['name'])
    if not errors:
        known.update(outputs)
    return list(dict.fromkeys(errors))
