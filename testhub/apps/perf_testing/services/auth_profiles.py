"""Explicit per-VU authentication templates stored in the existing runtime JSON."""
from copy import deepcopy
import json
import re
from urllib.parse import urlsplit

from rest_framework.exceptions import ValidationError

MASK = '******'
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')
PATH = re.compile(r'\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*\Z')
RESERVED = {'base_url', 'baseUrl', 'vu_id', 'iteration', 'request_id', '__proto__', 'prototype', 'constructor'}
IDENTITY_NAMES = {'user_id', 'username', 'user_name', 'account', 'phone', 'mobile', 'email', 'account_id'}
REFERENCES = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+)\}')
FIELDS = {'mode', 'transport', 'access_token_variable', 'refresh_token_variable',
          'expires_in_variable', 'cookie_name', 'cookie_variable', 'login', 'refresh',
          'max_attempts', 'retry_delay_ms', 'expiry_skew_seconds', 'refresh_on_status'}


def invalid(field: str) -> None:
    raise ValidationError({'auth_profile': {field: '认证配置无效，请检查类型、范围和变量映射'}})


def has_dynamic_json_keys(value: object) -> bool:
    if isinstance(value, dict):
        return any(REFERENCES.search(key) or has_dynamic_json_keys(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_dynamic_json_keys(item) for item in value)
    return False


def normalize_profile(value: dict | None, previous: dict | None = None) -> dict:
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict) or set(value) - FIELDS:
        invalid('profile')
    profile, old = deepcopy(value), previous or {}
    if profile.get('mode') not in ('LOGIN', 'STATIC'):
        invalid('mode')
    profile.setdefault('transport', 'BEARER')
    if profile['transport'] not in ('BEARER', 'COOKIE'):
        invalid('transport')
    for field, default, maximum in [('max_attempts', 1, 3), ('retry_delay_ms', 0, 5000),
                                     ('expiry_skew_seconds', 0, 300)]:
        number = profile.setdefault(field, default)
        if type(number) is not int or not (1 if field == 'max_attempts' else 0) <= number <= maximum:
            invalid(field)
    statuses = profile.setdefault('refresh_on_status', [401])
    if not isinstance(statuses, list) or 401 not in statuses or any(type(s) is not int or s not in (401, 403) for s in statuses):
        invalid('refresh_on_status')
    for field in ('access_token_variable', 'refresh_token_variable', 'expires_in_variable', 'cookie_variable'):
        if field in profile and (not isinstance(profile[field], str) or not NAME.fullmatch(profile[field])
                                 or profile[field] in RESERVED):
            invalid(field)
    if profile['transport'] == 'BEARER' and not profile.get('access_token_variable'):
        invalid('access_token_variable')
    if profile['transport'] == 'COOKIE':
        if not isinstance(profile.get('cookie_name'), str) or not NAME.fullmatch(profile['cookie_name']):
            invalid('cookie_name')
        if profile['mode'] == 'STATIC' and not profile.get('cookie_variable'):
            invalid('cookie_variable')
    for phase in ('login', 'refresh'):
        step = profile.get(phase)
        if step is None:
            if phase == 'login' and profile['mode'] == 'LOGIN':
                invalid(phase)
            continue
        if phase == 'login' and profile['mode'] == 'STATIC':
            invalid(phase)
        if not isinstance(step, dict) or set(step) - {'method', 'url', 'body_type', 'body', 'headers', 'assertions', 'extractors'}:
            invalid(phase)
        step.setdefault('method', 'POST')
        step.setdefault('body_type', 'NONE')
        step.setdefault('body', '')
        if step['body'] == MASK:
            step['body'] = (old.get(phase) or {}).get('body', MASK)
            if step['body'] == MASK:
                invalid(phase + '.body')
        url = step.get('url')
        if (not isinstance(url, str) or not url.startswith('/') or url.startswith('//')
                or any(c.isspace() for c in url) or any(c in url for c in '?#{\\') or '${' in url):
            invalid(phase + '.url')
        if step['method'] not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            invalid(phase + '.method')
        if step['body_type'] not in ('NONE', 'JSON') or not isinstance(step['body'], str):
            invalid(phase + '.body')
        if step['body_type'] == 'NONE' and step['body']:
            invalid(phase + '.body')
        if step['body_type'] == 'JSON':
            try:
                body = json.loads(step['body'])
            except (ValueError, TypeError):
                invalid(phase + '.body')
            if has_dynamic_json_keys(body):
                raise ValidationError({'auth_profile': {phase + '.body':
                    '认证请求 JSON 字段名不支持变量；请使用固定字段名，并将变量放在字段值中'}})
        headers = step.setdefault('headers', {})
        if not isinstance(headers, dict):
            invalid(phase + '.headers')
        for name, item in headers.items():
            if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                invalid(phase + '.headers')
            if item == MASK:
                item = ((old.get(phase) or {}).get('headers') or {}).get(name, MASK)
                if item == MASK:
                    invalid(phase + '.headers')
                headers[name] = item
            if not isinstance(item, str) or '\r' in item or '\n' in item:
                invalid(phase + '.headers')
        for field in ('extractors', 'assertions'):
            rules = step.setdefault(field, [])
            if not isinstance(rules, list) or any(not isinstance(r, dict) for r in rules):
                invalid(phase + '.' + field)
            for rule in rules:
                allowed = {'name', 'type', 'expr', 'json_path'} if field == 'extractors' else {'type', 'expr', 'json_path', 'operator', 'expected'}
                if set(rule) - allowed:
                    invalid(phase + '.' + field)
                if field == 'assertions' and rule.get('expected') == MASK:
                    previous_rules = (old.get(phase) or {}).get('assertions') or []
                    previous_rule = next((r for r in previous_rules if r.get('type') == rule.get('type')
                        and (r.get('expr') or r.get('json_path')) == (rule.get('expr') or rule.get('json_path'))), {})
                    rule['expected'] = previous_rule.get('expected', MASK)
                    if rule['expected'] == MASK:
                        invalid(phase + '.assertions')
                kind = str(rule.get('type', 'JSON_PATH')).upper().replace('JSONPATH', 'JSON_PATH')
                if kind not in (('JSON_PATH',) if field == 'extractors' else ('JSON_PATH', 'STATUS_CODE')):
                    invalid(phase + '.' + field)
                rule['type'] = kind
                if kind == 'JSON_PATH' and not PATH.fullmatch(str(rule.get('expr') or rule.get('json_path') or '')):
                    invalid(phase + '.' + field)
                if field == 'extractors' and (not isinstance(rule.get('name'), str)
                        or not NAME.fullmatch(rule['name']) or rule['name'] in RESERVED):
                    invalid(phase + '.extractors')
                if field == 'assertions' and rule.get('operator') not in (None, '', 'eq', 'equals', '=='):
                    invalid(phase + '.assertions')
                if field == 'assertions' and (('expected' not in rule)
                        or (kind == 'STATUS_CODE' and not str(rule['expected']).isdigit())):
                    invalid(phase + '.assertions')
        outputs = [r['name'] for r in step['extractors']]
        if len(outputs) != len(set(outputs)):
            invalid(phase + '.extractors')
        required = [profile.get(field) for field in ('expires_in_variable', 'refresh_token_variable')]
        if profile['transport'] == 'BEARER':
            required.append(profile['access_token_variable'])
        if any(name and name not in outputs for name in required):
            invalid(phase + '.extractors')
    if profile.get('refresh') and not profile.get('refresh_token_variable'):
        invalid('refresh_token_variable')
    if profile.get('expires_in_variable') and not profile.get('refresh'):
        invalid('refresh')
    return profile


def public_profile(value: dict) -> dict:
    result = deepcopy(value)
    for phase in ('login', 'refresh'):
        step = result.get(phase)
        if isinstance(step, dict):
            if step.get('body'):
                step['body'] = MASK
            step['headers'] = {key: MASK for key in step.get('headers', {})}
            # Assertion expected values may also be credentials or personal identifiers.
            for rule in step.get('assertions', []):
                if 'expected' in rule and str(rule.get('type', '')).upper() != 'STATUS_CODE':
                    rule['expected'] = MASK
    return result


def profile_steps(profile: dict, snapshot: dict | None = None) -> list[dict]:
    steps = []
    for phase in ('login', 'refresh'):
        if profile.get(phase):
            step = dict(deepcopy(profile[phase]), id='auth:' + phase,
                        name='认证登录' if phase == 'login' else '认证刷新',
                        is_setup=True, auth_phase=phase, enabled=True)
            if snapshot is not None:
                step['auth_input_variables'] = request_binding_variables(profile, phase, snapshot)
            steps.append(step)
    return steps


def references(value: object) -> set[str]:
    if isinstance(value, str):
        return {(brace or dollar).strip() for brace, dollar in REFERENCES.findall(value)}
    if isinstance(value, dict):
        return set().union(*(references(key) | references(item) for key, item in value.items())) if value else set()
    if isinstance(value, list):
        return set().union(*(references(item) for item in value)) if value else set()
    return set()


def template_headers(headers: dict) -> dict:
    """Header names are insensitive; variable names inside them are not."""
    return {str(key) if references(key) else str(key).lower(): value for key, value in headers.items()}


def request_headers(step: dict, global_headers: dict | None = None, *, auth_enabled: bool = False) -> dict:
    """Only effective header values are rendered, after per-step overrides."""
    headers = template_headers(global_headers) if isinstance(global_headers, dict) else {}
    step_headers = step.get('headers') or {}
    step_headers = template_headers(step_headers) if isinstance(step_headers, dict) else {}
    headers.update(step_headers)
    websocket = step.get('protocol') == 'WEBSOCKET'
    if auth_enabled or websocket:
        for name in ('authorization', 'cookie'):
            headers.pop(name, None)
            if not websocket and step.get('auth_phase') and name in step_headers:
                headers[name] = step_headers[name]
    return headers


def identity_variables(snapshot: dict) -> list[dict]:
    csv_variables = [v for v in snapshot.get('variables') or [] if str(v.get('type', '')).upper() == 'CSV']
    pool = snapshot.get('account_pool') or {}
    if pool:
        csv_variables = [v for v in csv_variables
                         if str(v.get('data_file_id') or v.get('file_id')) == str(pool.get('data_key'))]
    selected = (snapshot.get('runtime_config') or {}).get('account_identity_variable')
    if selected:
        return [v for v in csv_variables if v.get('name') == selected]
    if pool:
        return [v for v in csv_variables if v.get('column') == pool.get('identity_column')]
    principals = [v for v in csv_variables if str(v.get('name', '')).lower() in IDENTITY_NAMES]
    return principals or [v for v in csv_variables if str(v.get('name', '')).lower() in {'token', 'load_token'}]


def stable_identity_names(snapshot: dict) -> set[str]:
    """Protect selected principal and pool identity, including aliases of their source columns."""
    sources = {(str(v.get('data_file_id') or v.get('file_id')), v.get('column'))
               for v in identity_variables(snapshot)}
    pool = snapshot.get('account_pool') or {}
    if pool:
        sources.add((str(pool.get('data_key')), pool.get('identity_column')))
    return {v['name'] for v in snapshot.get('variables') or []
            if str(v.get('type', '')).upper() == 'CSV' and v.get('name')
            and (str(v.get('data_file_id') or v.get('file_id')), v.get('column')) in sources}


def assigned_values(snapshot: dict, variable: dict) -> list[str]:
    try:
        users = max(int((snapshot.get('load_config') or {}).get('concurrency', 1)), 1)
    except (ValueError, TypeError):
        users = 1
    key = str(variable.get('data_file_id') or variable.get('file_id'))
    rows = ((snapshot.get('csv_data') or {}).get(key) or {}).get('rows') or []
    if len(rows) < users:
        return []
    column = variable.get('column')
    return [str(row[column]).strip() if column in row and row[column] is not None else '' for row in rows[:users]]


def request_binding_variables(profile: dict, phase: str, snapshot: dict) -> list[str]:
    """Bind supported credential value slots, never keys, assertions or incidental references."""
    selected = identity_variables(snapshot) if phase == 'login' else []
    names = {v['name'] for v in selected} if phase == 'login' else {profile['refresh_token_variable']}
    aliases = (IDENTITY_NAMES | {'principal', 'identity'} | names
               | {v.get('column', '') for v in selected}) if phase == 'login' else names | {'refresh_token'}
    normalized_aliases = {re.sub(r'[^a-z0-9]', '', str(name).lower()) for name in aliases if name}
    step = profile[phase]
    slots = []

    def body_slots(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if not references(key) and re.sub(r'[^a-z0-9]', '', key.lower()) in normalized_aliases:
                    slots.append((item, ''))
                else:
                    body_slots(item)
        elif isinstance(value, list):
            for item in value:
                body_slots(item)

    if step.get('body_type') == 'JSON':
        body = json.loads(step['body'])
        if has_dynamic_json_keys(body):
            return []
        body_slots(body)
    for key, value in (step.get('headers') or {}).items():
        key = key.lower()
        alias = re.sub(r'[^a-z0-9]', '', key.removeprefix('x-'))
        if key in ('authorization', 'cookie') or alias in normalized_aliases:
            slots.append((value, key))
    bound = set()
    for value, header in slots:
        if not isinstance(value, str):
            return []
        value = value.strip()
        if header == 'authorization':
            value = re.sub(r'^[A-Za-z][A-Za-z0-9_-]*\s+', '', value, count=1)
        elif header == 'cookie':
            value = re.sub(r'^[!#$%&\'*+.^_`|~0-9A-Za-z-]+=\s*', '', value, count=1)
        match = REFERENCES.fullmatch(value)
        name = (match.group(1) or match.group(2)).strip() if match else None
        if name not in names:
            return []
        bound.add(name)
    return sorted(bound)


def request_input_issues(profile: dict, snapshot: dict) -> list[dict]:
    """Same dependency graph for preflight and catalog; diagnostics contain names, never values."""
    known = {'base_url', 'baseUrl', 'vu_id', 'iteration', 'request_id'}
    for variable in snapshot.get('variables') or []:
        if str(variable.get('type', '')).upper() == 'CSV':
            values = assigned_values(snapshot, variable)
            available = bool(values) and all(values)
        else:
            available = variable.get('value') not in (None, '', MASK)
        if available:
            known.add(variable.get('name'))
    issues = []
    global_headers = (snapshot.get('env_config') or {}).get('headers') or {}
    public_headers = {key: value for key, value in
        template_headers(global_headers if isinstance(global_headers, dict) else {}).items()
        if str(key).lower() not in ('authorization', 'cookie')}
    for phase in ('login', 'refresh'):
        step = profile.get(phase)
        if not step:
            continue
        if not request_binding_variables(profile, phase, snapshot):
            role = '所选独立账号身份' if phase == 'login' else '当前用户刷新凭据'
            issues.append({'code': 'auth_input_binding', 'field': f'auth_profile.{phase}',
                'message': f'认证{phase}必须将{role}变量绑定到 JSON 凭据字段值或明确认证头值；'
                           '支持身份/刷新变量名、映射列名、principal/refresh_token 字段及对应 X- 头、Authorization/Cookie，'
                           '键名、断言或无关字段引用不能代替绑定'})
        fields = {'url': step.get('url'), 'body': step.get('body'),
                  'headers': {**public_headers, **template_headers(step.get('headers') or {})}}
        for field, value in fields.items():
            for name in sorted(references(value) - known):
                issues.append({'code': 'auth_variable_mapping', 'field': f'auth_profile.{phase}.{field}',
                    'variable': name, 'message': f'认证{phase}的{field}缺少可用变量映射：{name}'})
        known.update(rule['name'] for rule in step.get('extractors') or [])
    return issues


def validate_binding(profile: dict, snapshot: dict) -> list[str]:
    """Validate identity ownership without returning variable values or responses."""
    if not profile:
        return []
    errors = [issue['message'] for issue in request_input_issues(profile, snapshot)]
    variables = snapshot.get('variables') or []
    csv_names = {v.get('name') for v in variables if str(v.get('type', '')).upper() == 'CSV'}
    if not csv_names:
        errors.append('每用户认证必须绑定账号池或 CSV 身份，禁止使用环境共享凭据')
    base = (snapshot.get('env_config') or {}).get('base_url', '')
    def origin(url: str) -> tuple | None:
        try:
            parts = urlsplit(url)
            if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
                    or parts.password is not None or '%' in parts.netloc or '\\' in url):
                return None
            return (parts.scheme.lower(), parts.hostname.lower(), parts.port or (443 if parts.scheme == 'https' else 80))
        except (ValueError, TypeError):
            return None
    if not origin(base):
        errors.append('每用户认证需要有效 HTTP(S) 环境 base_url，禁止地址内嵌认证信息')
    if profile['mode'] == 'STATIC':
        name = profile.get('access_token_variable') if profile['transport'] == 'BEARER' else profile.get('cookie_variable')
        required = [name] + ([profile['refresh_token_variable']] if profile.get('refresh') else [])
        if any(name not in csv_names for name in required):
            errors.append('预存认证必须引用每个账号自己的 CSV Token/Cookie 与刷新凭据')
        for name in required:
            variable = next((v for v in variables if v.get('name') == name and v.get('name') in csv_names), None)
            if variable:
                rows = ((snapshot.get('csv_data') or {}).get(str(variable.get('data_file_id') or variable.get('file_id'))) or {}).get('rows') or []
                users = (snapshot.get('load_config') or {}).get('concurrency', 1)
                try:
                    values = [str(row.get(variable.get('column'), '')).strip() for row in rows[:int(users)]]
                except (ValueError, TypeError):
                    values = []
                if not values or any(not val for val in values) or len(values) != len(set(values)):
                    errors.append('预存认证 Token/Cookie 或刷新凭据为空或重复，不能保证独立身份')
    else:
        request_refs = set(request_binding_variables(profile, 'login', snapshot))
        selected = identity_variables(snapshot)
        valid_identities = set()
        for variable in selected:
            values = assigned_values(snapshot, variable)
            if values and all(values) and len(values) == len(set(values)):
                valid_identities.add(variable['name'])
            else:
                errors.append('所选登录身份列缺失、为空或重复，不能保证独立身份')
        if not request_refs.intersection(valid_identities):
            errors.append('登录请求模板必须引用所选的独立账号身份变量，密码或断言引用不能代替身份绑定')
    outputs = {r['name'] for s in profile_steps(profile) for r in s['extractors']}
    rotating = {profile.get(field) for field in ('access_token_variable', 'refresh_token_variable', 'cookie_variable')}
    stable_names = stable_identity_names(snapshot)
    protected_inputs = {v.get('name') for v in variables}
    if profile['mode'] == 'STATIC':
        if profile.get('refresh') and rotating.intersection(stable_names):
            errors.append('稳定身份变量及其映射别名不能同时作为轮换 Token/Cookie，请使用独立凭据变量')
        protected_inputs -= rotating - stable_names
    if outputs.intersection(protected_inputs):
        errors.append('认证提取变量不能覆盖账号身份、环境或场景输入变量；预存认证只允许轮换指定凭据')
    for step in snapshot.get('steps') or []:
        url = step.get('url', '').replace('{{base_url}}', base).replace('${base_url}', base).replace('{{baseUrl}}', base).replace('${baseUrl}', base)
        if url.startswith(('http://', 'https://')) and (not origin(url) or origin(url) != origin(base)):
            errors.append('每用户认证的业务步骤必须使用环境同一服务地址')
        auth_outputs = {r['name'] for s in profile_steps(profile) for r in s['extractors']}
        auth_outputs.update(profile.get(field) for field in ('access_token_variable', 'refresh_token_variable', 'cookie_variable', 'expires_in_variable') if profile.get(field))
        if auth_outputs.intersection(r.get('name') for r in step.get('extractors') or []):
            errors.append('业务或前置提取器不能覆盖认证上下文变量')
    return errors
