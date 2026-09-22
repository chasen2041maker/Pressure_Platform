"""Project interface pool: derive safe defaults, retain gaps, and reuse verified snapshots."""
from copy import deepcopy
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

from django.http import Http404

from django.db import transaction
from django.db.models import Max
from rest_framework.exceptions import ValidationError

from . import api_catalog as catalog, environments, account_pools
from .auth_profiles import normalize_profile
from .prepared_dependencies import (resolve_setup_steps, expanded_setup_steps, public_dependencies,
                                    missing_import_setups, STEP_PREPARATION_FIELDS)
from .prepared_dependencies import request_extractors
from ..models import (PerfProject, PerfScenario, PerfScenarioStep, PerfPreparedRequest,
                      PerfEnvironment, PerfAccountPoolVersion)
from apps.api_testing.models import ApiRequest

DEFAULT_CONFIG = dict(environment=None, global_environment=None, account_pool_version=None,
                      account_pool_group='', token_variable='token', identity_variable='user_id')
CONTEXT_FIELDS = ('environment', 'global_environment', 'account_pool_version', 'account_pool_group',
                  'variables', 'env_config', 'runtime_config')
STATUSES = {'unprepared', 'blocked', 'unverified', 'passed', 'failed', 'stale'}
_STATES = ContextVar('prepared_validation_states', default=None)
EDITABLE_FIELDS = (*catalog.REQUEST_FIELDS, 'think_time', 'weight', 'protocol', 'sse_config', 'websocket_config')
PREPARATION_FIELDS = {'confirmed_fields', 'body_reviewed', 'setup_steps', 'resource_recovery'}
EDIT_MARKER = '_edited_fields'
CONFIRM_MARKER = '_edited_preparation'
MASK = '******'


@contextmanager
def validation_scope(*, fresh=False):
    if _STATES.get() is not None and not fresh:
        yield
        return
    token = _STATES.set({})
    try:
        yield
    finally:
        _STATES.reset(token)


def memo_get(key, factory):
    cache = _STATES.get()
    if cache is None:
        return factory()
    scoped_key = ('memo', key)
    if scoped_key not in cache:
        cache[scoped_key] = factory()
    return cache[scoped_key]


def validation_cached(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        if _STATES.get() is not None:
            return func(*args, **kwargs)
        token = _STATES.set({})
        try:
            return func(*args, **kwargs)
        finally:
            _STATES.reset(token)
    return wrapped


def _current_state(project_id):
    cache = _STATES.get()
    if cache is not None and project_id in cache:
        return cache[project_id]
    project = PerfProject.objects.get(pk=project_id)
    version = catalog.latest_version(project_id)
    operations = {op['source_key']: op for op in version.operations} if version else {}
    assets = ApiRequest.objects.select_related('collection').in_bulk([op['id'] for op in operations.values()])
    try:
        context, _, variables = _context(project, _config(project)[1], project.owner)
        if cache is not None:
            cache[('memo', ('dependency_variables', project.pk, catalog.digest(context)))] = variables
    except ValidationError:
        context = None
    state = (project, operations, assets, context)
    if cache is not None:
        cache[project_id] = state
    return state


def _remember_state(project, version, context, variables, assets):
    # Partial preparation/editor reads must still validate prerequisites outside that selection.
    operations = {op['source_key']: op for op in version.operations} if version else {}
    complete = dict(assets)
    missing = {op['id'] for op in operations.values()} - set(complete)
    if missing:
        complete.update(ApiRequest.objects.select_related('collection').in_bulk(missing))
    cache = _STATES.get()
    cache[project.pk] = (project, operations, complete, context)
    cache[('memo', ('dependency_variables', project.pk, catalog.digest(context)))] = variables


@validation_cached
def public_summaries(project, operations):
    rows = {row.source_key: row for row in project.prepared_requests.all()}
    descriptions = dict(ApiRequest.objects.filter(pk__in=[op['id'] for op in operations],
        collection__project_id=project.api_project_id).values_list('pk', 'description'))
    return {op['source_key']: public_summary(rows.get(op['source_key']), op,
        description=descriptions.get(op['id'], '')) for op in operations}


def preparation_note(description):
    marker = re.search(r'(?m)^\[压测准备备注\][ \t]*\r?$', description or '')
    if not marker:
        return ''
    note = description[marker.end():]
    end = re.search(r'(?m)^\[/压测准备备注\][ \t]*\r?$', note)
    return note[:end.start()].strip()[:4000] if end else ''


def _revision(value, expected):
    if type(value) is not int or value != expected:
        raise catalog.CatalogConflict('配置或接口版本已变化，请刷新后重试')


def _config(project):
    stored = project.pool_config or {}
    return stored.get('revision', 0), {**DEFAULT_CONFIG, **stored.get('config', {})}


def config_response(project, user):
    environments.require_project_access(project.pk, user)
    revision, config = _config(project)
    envs = environments.accessible_environments(user).filter(
        project_id__in=[project.pk]) | environments.accessible_environments(user).filter(scope='GLOBAL')
    pools = PerfAccountPoolVersion.objects.filter(pool__project=project).select_related('pool')
    return {'revision': revision, 'config': config, 'choices': {
        'environments': list(envs.values('id', 'name', 'scope')),
        'account_pool_versions': [{'id': v.pk, 'version': v.version, 'pool_id': v.pool_id,
            'pool_name': v.pool.name, 'row_count': v.row_count, 'field_names': list(v.field_mapping),
            'groups': [{'id': g['id'], 'count': g['count']} for g in v.groups]} for v in pools]}}


def _context(project, config, user):
    envs = PerfEnvironment.objects.in_bulk([config[k] for k in ('environment', 'global_environment') if config[k]])
    if any(config[k] and config[k] not in envs for k in ('environment', 'global_environment')):
        raise ValidationError('引用环境已删除，请重新选择')
    environments.validate_selection(project.pk, envs.get(config['environment']), envs.get(config['global_environment']), user)
    pool = PerfAccountPoolVersion.objects.filter(pk=config['account_pool_version']).select_related('pool').first()
    if config['account_pool_version'] and pool is None:
        raise ValidationError('账号池版本已删除，请重新选择')
    scene = PerfScenario(project=project, created_by=user, engine='K6',
        environment_id=config['environment'], global_environment_id=config['global_environment'])
    resolved = environments.resolve_environment(scene, user=user)
    account_pools.validate_binding(project.pk, 'K6', pool, config['account_pool_group'], resolved['variables'], user)
    if pool:
        # Verify private file integrity without returning or persisting any account rows.
        account_pools.read_version(pool)
    variables = deepcopy(resolved['variables'])
    if pool:
        variables.extend({'name': name, 'type': 'CSV'} for name in pool.field_mapping)
    names = {v['name'] for v in variables}
    runtime = {}
    if config['token_variable'] and config['token_variable'] in names:
        runtime['auth_profile'] = normalize_profile({'mode': 'STATIC', 'transport': 'BEARER',
            'access_token_variable': config['token_variable']})
    if pool and config['identity_variable']:
        if config['identity_variable'] not in pool.field_mapping:
            raise ValidationError('身份变量不在所选账号池映射中')
        runtime['account_identity_variable'] = config['identity_variable']
    context = {k: config[k] for k in ('environment', 'global_environment', 'account_pool_version', 'account_pool_group')}
    context.update(variables=[], env_config={}, runtime_config=runtime,
        config_revision=_config(project)[0], environment_fingerprints={str(k): {
            'version': v.version, 'hash': environments.content_hash(v)} for k, v in envs.items()},
        pool_hash=pool.content_hash if pool else None)
    return context, {**resolved, 'runtime_config': runtime}, variables


@transaction.atomic
def save_config(project_id, payload, user):
    project = PerfProject.objects.select_for_update().get(pk=project_id)
    environments.require_project_access(project.pk, user)
    if not isinstance(payload, dict) or set(payload) != {'expected_config_revision', 'config'}:
        raise catalog.CatalogInputError('请提交完整配置与当前配置版本')
    revision, old = _config(project)
    _revision(payload['expected_config_revision'], revision)
    config = payload['config']
    if not isinstance(config, dict) or set(config) != set(DEFAULT_CONFIG):
        raise catalog.CatalogInputError('公共配置仅支持环境、账号池引用与变量名')
    for key in ('environment', 'global_environment', 'account_pool_version'):
        if config[key] is not None and (type(config[key]) is not int or config[key] < 1):
            raise catalog.CatalogInputError('配置引用必须为正整数或空')
    for key in ('token_variable', 'identity_variable'):
        if not isinstance(config[key], str) or (config[key] and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', config[key])):
            raise catalog.CatalogInputError('变量名称格式无效')
    if not isinstance(config['account_pool_group'], str) or len(config['account_pool_group']) > 64:
        raise catalog.CatalogInputError('账号池分组无效')
    _context(project, config, user)
    if revision == 0 or config != old:
        project.pool_config = {'revision': revision + 1, 'config': deepcopy(config)}
        project.save(update_fields=['pool_config'])
    return config_response(project, user)


def _asset_hash(asset, operation):
    return memo_get(('source_asset', asset.pk, operation['content_hash']), lambda: _asset_digest(asset, operation))


def _asset_digest(asset, operation):
    data, _ = catalog.api_request_data(asset, operation)
    return catalog.digest({'request': data, 'body': asset.body, 'auth': asset.auth,
        'pre': asset.pre_request_script, 'post': asset.post_request_script})


def _validate_asset(project, asset):
    if not project.api_project_id or not asset.collection_id or asset.collection.project_id != project.api_project_id:
        raise catalog.CatalogConflict('来源接口已移出当前项目，请重新导入契约')


def _success_assertions(operation, document, headers=None):
    response_gaps = []
    try:
        responses = catalog._resolve(operation.get('raw', {}).get('responses', {}), document,
            response_gaps, 'response', budget=catalog.ExpansionBudget(parent=catalog.ExpansionBudget()))
    except catalog.CatalogInputError:
        return [], [catalog.gap('response_schema', 'response', '成功响应契约无法安全解析')]
    if not isinstance(responses, dict):
        return [], [catalog.gap('response_schema', 'response', '成功响应契约需要审查')]
    if '304' in responses and (headers is None or any(str(key).lower() in ('if-none-match', 'if-modified-since') for key in headers)):
        return [], [catalog.gap('response_schema', 'response', '条件请求可能返回304，请明确成功条件')]
    success = [(str(code), response) for code, response in responses.items() if re.fullmatch(r'2\d\d', str(code))]
    if len(success) != 1:
        return [], [catalog.gap('response_schema', 'response', '需要唯一明确的成功响应状态与业务断言')]
    code, response = success[0]
    rules = [{'type': 'STATUS_CODE', 'expected': int(code)}]
    if operation.get('method', '').upper() == 'HEAD' or code in ('204', '205'):
        return rules, response_gaps
    if not isinstance(response, dict) or not isinstance(response.get('content', {}), dict):
        return rules, [catalog.gap('response_schema', 'response', '成功响应结构需要审查')]
    contents = response.get('content', {})
    if len(contents) > 1:
        return rules, [catalog.gap('response_schema', 'response', '成功响应存在多种媒体类型，请明确返回格式')]
    schema = {}
    if contents:
        media_type, media = next(iter(contents.items()))
        if not isinstance(media, dict):
            return rules, [catalog.gap('response_schema', 'response', '成功响应结构需要审查')]
        normalized = catalog.media_type(media_type)
        if normalized == 'application/json' or normalized.endswith('+json'):
            schema = media.get('schema', {})
    elif 'schema' in response:
        produces = operation.get('raw', {}).get('produces', document.get('produces', []))
        if produces == ['application/json']:
            schema = response['schema']

    def constants(node, path, depth=0):
        if not isinstance(node, dict) or depth > 8:
            return
        if node.get('writeOnly') or node.get('nullable') or isinstance(node.get('type'), list) and 'null' in node['type']:
            return
        if any(key in node for key in ('oneOf', 'anyOf', 'not', 'x-perf-recursive')):
            response_gaps.append(catalog.gap('response_schema', 'response', '成功响应存在未支持的联合或递归 schema'))
            return
        required = node.get('required', [])
        properties = node.get('properties', {})
        if not isinstance(required, list) or any(not isinstance(name, str) for name in required) or not isinstance(properties, dict):
            response_gaps.append(catalog.gap('response_schema', 'response', '成功响应结构需要审查'))
            return
        for name, prop in properties.items():
            if name not in required or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) or not isinstance(prop, dict):
                continue
            if prop.get('writeOnly') or prop.get('nullable') or isinstance(prop.get('type'), list) and 'null' in prop['type']:
                continue
            target = path + '.' + name
            enum = prop.get('enum')
            value = prop['const'] if 'const' in prop else enum[0] if isinstance(enum, list) and len(enum) == 1 else None
            if value == '':
                response_gaps.append(catalog.gap('response_schema', 'response', '空字符串业务断言需要手工审查'))
            elif value is not None and isinstance(value, (str, int, float, bool)):
                rules.append({'type': 'JSON_PATH', 'expr': target, 'expected': value, 'operator': 'eq'})
            constants(prop, target, depth + 1)
        branches = node.get('allOf', [])
        if isinstance(branches, list):
            for branch in branches:
                constants(branch, path, depth + 1)
    constants(schema, '$')
    return rules, response_gaps


def _derive(asset, operation, version, context, resolved, variables):
    request, metadata = catalog.controlled_source(asset, operation, version)
    # Omit unresolved optional query/header suggestions instead of inventing resource identifiers.
    for requirement in metadata.get('requirements', []):
        if not requirement.get('required', True):
            location, _, name = requirement['field'].partition('/')
            if location in ('query', 'header') and (requirement.get('provenance') == 'example' or requirement.get('resource')):
                container = request['params' if location == 'query' else 'headers']
                if container.get(name) == requirement.get('suggested'):
                    container.pop(name, None)
    generated, response_gaps = _success_assertions(operation, version.document,
        _effective_headers(resolved['env_config'].get('headers', {}), request.get('headers')))
    original = request.get('assertions', [])
    if not isinstance(original, list) or len(original) > 1000 or any(not isinstance(rule, dict) for rule in original):
        response_gaps.append(catalog.gap('assertion_format', 'assertions', '原接口断言结构需要修正'))
        original = []
    request['assertions'] = original or generated
    known = {item['name'] for item in variables} | {'vu_id', 'iteration', 'base_url', 'baseUrl', 'request_id'}
    for parameter in metadata.get('parameters', []):
        if parameter.get('in') == 'header' and parameter.get('required') and parameter.get('name', '').lower() == 'idempotency-key':
            name = next((key for key in request['headers'] if key.lower() == 'idempotency-key'), parameter['name'])
            if not catalog._configured(request['headers'].get(name), known):
                request['headers'][name] = '{{request_id}}'
    profile = context['runtime_config'].get('auth_profile')
    if profile and operation.get('security') and not any(item == {} for item in operation['security']) and not any(key.lower() == 'authorization' for key in request['headers']):
        schemes = operation.get('security_schemes', {})
        if any(all(schemes.get(name, {}).get('type') == 'http' and
                   schemes[name].get('scheme', '').lower() == 'bearer' for name in requirement)
               for requirement in operation['security']):
            request['headers']['Authorization'] = 'Bearer {{' + profile['access_token_variable'] + '}}'
    gaps = _request_gaps(request, metadata, {}, operation, version, resolved, variables, response_gaps)
    request.update(enabled=True, is_setup=False, think_time={}, weight=1)
    return request, metadata, gaps


def _effective_headers(inherited, request_headers):
    if inherited is None:
        return None
    return {str(key).lower(): value for headers in (inherited, request_headers or {}) for key, value in headers.items()}


def import_defaults(request, operation, document, runtime, inherited_headers=None):
    """补齐新导入的空项，不改变来源参数、人工断言或准备/验证证据。"""
    if not request.get('assertions'):
        request['assertions'], _ = _success_assertions(operation, document,
            _effective_headers(inherited_headers, request.get('headers')))
        for rule in request['assertions']:
            if rule['type'] == 'JSON_PATH':
                rule['json_path'] = rule.pop('expr')
    profile = (runtime or {}).get('auth_profile') or {}
    security = operation.get('security') or []
    schemes = operation.get('security_schemes') or {}
    if (profile.get('transport') == 'BEARER' and profile.get('access_token_variable')
            and security and not any(item == {} for item in security)
            and not any(key.lower() == 'authorization' for key in request['headers'])
            and any(all(schemes.get(name, {}).get('type') == 'http'
                        and schemes[name].get('scheme', '').lower() == 'bearer' for name in requirement)
                    for requirement in security)):
        request['headers']['Authorization'] = 'Bearer {{' + profile['access_token_variable'] + '}}'
    return request


def _public_preparation(preparation):
    return {key: deepcopy(value) for key, value in preparation.items() if key in PREPARATION_FIELDS}


def _request_gaps(request, metadata, preparation, operation, version, resolved, variables, response_gaps=None,
                  setup_outputs=()):
    if response_gaps is None:
        _, response_gaps = _success_assertions(operation, version.document,
            _effective_headers(resolved['env_config'].get('headers', {}), request.get('headers')))
    if 'assertions' in preparation.get(EDIT_MARKER, []) and _reviewed_business_assertions(request.get('assertions', [])):
        response_gaps = []
    from .prepared_recovery import readiness_request
    readiness = catalog.request_readiness(readiness_request(request, preparation), metadata,
        {**_public_preparation(preparation), '_known_extractors': list(setup_outputs)}, variables,
        resolved['env_config'].get('headers'), project_id=version.project_id)
    if request.get('protocol') in ('SSE', 'WEBSOCKET'):
        from .sse_steps import validate_sse_step
        from .websocket_steps import validate_websocket_step
        from . import auth_profiles
        snapshot = {**resolved, 'engine': 'K6', 'variables': variables, 'load_config': {'duration': 300}}
        known = {item['name'] for item in variables} | set(setup_outputs) | auth_profiles.RESERVED
        validate = validate_sse_step if request['protocol'] == 'SSE' else validate_websocket_step
        errors = validate({**request, 'source_metadata': metadata}, snapshot, known,
                          auth_profiles.stable_identity_names(snapshot))
        field = 'sse_config' if request['protocol'] == 'SSE' else 'websocket_config'
        readiness['gaps'].extend(catalog.gap(field, field, error) for error in errors)
        response_gaps = []
    gaps = readiness['gaps'] + response_gaps
    if not resolved['env_config'].get('base_url') and request['url'].startswith('/'):
        gaps.append(catalog.gap('base_url', 'environment', '请选择包含目标基础地址的公共环境'))
    return list({(gap['code'], gap['field']): gap for gap in gaps}.values())


def valid_contains_assertion(rule):
    return (isinstance(rule, dict) and str(rule.get('type', '')).upper() == 'CONTAINS'
            and isinstance(rule.get('expected'), str) and bool(rule['expected'].strip())
            and rule.get('operator') in (None, '', 'eq', 'equals', '=='))


def _reviewed_business_assertions(rules):
    return (any(rule.get('type') == 'STATUS_CODE' and re.fullmatch(r'2\d\d', str(rule.get('expected'))) for rule in rules)
            and any(valid_contains_assertion(rule) or (rule.get('type') == 'JSON_PATH' and rule.get('expected') not in (None, '')
                    and not isinstance(rule.get('expected'), (dict, list))
                    and re.fullmatch(r'\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])+', str(rule.get('expr') or rule.get('json_path') or '')))
                    for rule in rules))


def _variable_reference(value):
    return isinstance(value, str) and bool(re.fullmatch(
        r'(?:(?:Bearer|Basic)\s+)?(?:\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}|\$\{[A-Za-z_][A-Za-z0-9_]*\})', value, re.I))


def _mask_tree(value, key=''):
    if environments.sensitive_header(key) and value not in (None, '') and not _variable_reference(value):
        return MASK
    if isinstance(value, dict):
        return {name: _mask_tree(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_mask_tree(item) for item in value]
    return deepcopy(value)


def _public_request(request):
    result = {key: deepcopy(request[key]) for key in EDITABLE_FIELDS if key in request}
    result.setdefault('protocol', 'HTTP')
    result.setdefault('sse_config', {})
    result.setdefault('websocket_config', {})
    result['websocket_config'] = _mask_tree(result['websocket_config'])
    if _private_url(result.get('url', '')):
        result['url'] = MASK
    for field in ('headers', 'params'):
        result[field] = _mask_tree(result.get(field, {}))
    body = result.get('body', '')
    if body:
        try:
            parsed = json.loads(body)
            masked = _mask_tree(parsed)
            if masked != parsed:
                result['body'] = json.dumps(masked, ensure_ascii=False)
        except (ValueError, TypeError):
            result['body'] = MASK
    for assertion in result.get('assertions', []):
        if environments.sensitive_header(str(assertion.get('expr') or assertion.get('json_path') or '')):
            assertion['expected'] = _mask_tree(assertion.get('expected'), 'token')
    return result


def _private_url(value):
    try:
        parts = urlsplit(value)
        return (parts.username is not None or parts.password is not None
                or any(environments.sensitive_header(key) and item and not _variable_reference(item)
                       for key, item in parse_qsl(parts.query, keep_blank_values=True)))
    except (ValueError, TypeError):
        return True


def _assertion_identity(rule):
    if not isinstance(rule, dict):
        return None
    expr, path = rule.get('expr'), rule.get('json_path')
    if expr and path and str(expr).strip() != str(path).strip():
        raise catalog.CatalogInputError('断言路径字段互相冲突')
    return str(rule.get('type', '')).upper(), str(expr or path or '').strip()


def _restore_assertions(rules, old, public):
    if not isinstance(rules, list):
        return deepcopy(rules)
    restored = []
    for rule in rules:
        matches = [index for index, previous in enumerate(old)
                   if _assertion_identity(previous) == _assertion_identity(rule)]
        index = matches[0] if len(matches) == 1 else None
        restored.append(_restore_tree(rule, old[index] if index is not None else None,
                                      public[index] if index is not None else None))
    return restored


def _restore_tree(value, old, public):
    if value == MASK:
        if public != MASK or old is None or old == MASK:
            raise catalog.CatalogInputError('脱敏占位没有可保留的原值，请重新填写或使用变量')
        return deepcopy(old)
    if isinstance(value, dict):
        return {key: _restore_tree(item, old.get(key) if isinstance(old, dict) else None,
                                  public.get(key) if isinstance(public, dict) else None) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_tree(item, old[index] if isinstance(old, list) and index < len(old) else None,
                              public[index] if isinstance(public, list) and index < len(public) else None)
                for index, item in enumerate(value)]
    return deepcopy(value)


def _restore_request(patch, old):
    public = _public_request(old)
    if 'url' in patch and patch['url'] != MASK and _private_url(patch['url']):
        raise catalog.CatalogInputError('URL 不可包含用户名密码或敏感查询字面值，请使用已配置变量')
    result = _restore_tree({key: value for key, value in patch.items() if key not in ('body', 'assertions')}, old, public)
    if 'assertions' in patch:
        result['assertions'] = _restore_assertions(patch['assertions'], old.get('assertions', []), public.get('assertions', []))
    if 'body' in patch:
        body = patch['body']
        if body == MASK:
            result['body'] = _restore_tree(body, old.get('body'), public.get('body'))
        else:
            try:
                raw = json.loads(body)
            except (ValueError, TypeError):
                result['body'] = body
            else:
                try:
                    previous, shown = json.loads(old.get('body', '')), json.loads(public.get('body', ''))
                    had_json = True
                except (ValueError, TypeError):
                    previous, shown = None, None
                    had_json = False
                restored = _restore_tree(raw, previous, shown)
                result['body'] = old['body'] if had_json and catalog.digest(restored) == catalog.digest(previous) else json.dumps(restored, ensure_ascii=False)
    return result


def _validate_edit(project, asset, metadata, request, preparation, user):
    from ..serializers import PerfScenarioStepSerializer
    catalog._check_tree(request)
    try:
        if len(json.dumps(request, allow_nan=False).encode()) > catalog.MAX_OPERATION_BYTES:
            raise ValueError()
    except (ValueError, TypeError):
        raise catalog.CatalogInputError('请求配置过大或包含无效值') from None
    scene = PerfScenario(project=project, engine='K6', created_by=user)
    instance = PerfScenarioStep(scenario=scene, source_request=asset, source_metadata=metadata, **request)
    validation = {key: deepcopy(request[key]) for key in EDITABLE_FIELDS if key in request}
    validation['preparation'] = {key: deepcopy(value) for key, value in preparation.items()
                                 if key in STEP_PREPARATION_FIELDS}
    # The editor supports the engine's expr alias without rewriting unchanged saved rules.
    for rule in validation.get('assertions', []):
        if isinstance(rule, dict) and str(rule.get('type', '')).upper() == 'CONTAINS' and not valid_contains_assertion(rule):
            raise catalog.CatalogInputError('CONTAINS 断言需要非空字符串期望值，且不能使用其他运算符')
        if isinstance(rule, dict) and str(rule.get('type', '')).upper() == 'JSON_PATH' and isinstance(rule.get('expected'), (dict, list)):
            raise catalog.CatalogInputError('JSON_PATH 断言期望值必须是标量，不能使用对象或数组')
        if isinstance(rule, dict) and str(rule.get('type', '')).upper() == 'JSON_PATH' and rule.get('expr'):
            if rule.get('json_path') and rule['json_path'] != rule['expr']:
                raise catalog.CatalogInputError('断言路径字段互相冲突')
            rule['json_path'] = rule['expr']
    serializer = PerfScenarioStepSerializer(instance=instance, data=validation, partial=True,
        context={'request': SimpleNamespace(user=user)})
    serializer.is_valid(raise_exception=True)
    result = deepcopy(request)
    result.update({key: value for key, value in serializer.validated_data.items() if key not in ('assertions', 'preparation')})
    result['headers'] = environments.validate_headers(result['headers'], allow_masks=False)
    checked = serializer.validated_data['preparation']
    if 'setup_steps' in preparation:
        from .prepared_dependencies import validate_references
        checked['setup_steps'] = validate_references(preparation['setup_steps'])
    if 'resource_recovery' in preparation:
        from .prepared_recovery import declaration
        checked['resource_recovery'] = declaration(preparation['resource_recovery'], result)
    return result, checked


def _editor_source(project, request_id):
    version = catalog.latest_version(project.pk)
    operation = next((op for op in version.operations if op['id'] == request_id), None) if version else None
    if operation is None:
        raise Http404()
    asset = ApiRequest.objects.select_related('collection').filter(pk=request_id).first()
    if asset is None:
        raise catalog.CatalogConflict('来源接口已删除，请刷新接口库')
    _validate_asset(project, asset)
    return version, operation, asset


@validation_cached
def editable_request(project_id, request_id, user):
    project = PerfProject.objects.get(pk=project_id)
    environments.require_project_access(project.pk, user)
    version, operation, asset = _editor_source(project, request_id)
    context, resolved, variables = _context(project, _config(project)[1], user)
    _remember_state(project, version, context, variables, {asset.pk: asset})
    row = PerfPreparedRequest.objects.filter(project=project, source_key=operation['source_key']).first()
    if row:
        request, preparation = row.request, row.preparation
    else:
        request, _, _ = _derive(asset, operation, version, context, resolved, variables)
        preparation = {}
    outputs = set()
    if row:
        try:
            outputs = {rule['name'] for item in resolve_setup_steps(row, variables=variables)
                       for rule in request_extractors(item['kwargs'])}
        except (catalog.CatalogConflict, ValidationError):
            pass
    return {'catalog_version': version.version, 'prepared': public_summary(row, operation, description=asset.description),
        'request': _public_request(request), 'preparation': _public_preparation(preparation),
        'auth_access_token_variable': ((context.get('runtime_config') or {}).get('auth_profile') or {}).get('access_token_variable'),
        'operation': deepcopy(operation),
        'known_variable_names': sorted({item['name'] for item in variables} | outputs
            | {'vu_id', 'iteration', 'base_url', 'baseUrl', 'request_id'})}


@transaction.atomic
@validation_cached
def save_edit(project_id, request_id, payload, user):
    project = PerfProject.objects.select_for_update().get(pk=project_id)
    environments.require_project_access(project.pk, user)
    if not isinstance(payload, dict) or set(payload) != {'expected_catalog_version', 'expected_revision', 'request', 'preparation'}:
        raise catalog.CatalogInputError('请提交请求配置、准备确认与当前版本')
    version, operation, asset = _editor_source(project, request_id)
    _revision(payload['expected_catalog_version'], version.version)
    row = PerfPreparedRequest.objects.select_for_update().filter(project=project, source_key=operation['source_key']).first()
    _revision(payload['expected_revision'], row.revision if row else 0)
    patch, preparation = payload['request'], payload['preparation']
    if not isinstance(patch, dict) or set(patch) - set(EDITABLE_FIELDS):
        raise catalog.CatalogInputError('请求包含不允许编辑的字段')
    if not isinstance(preparation, dict) or set(preparation) - PREPARATION_FIELDS:
        raise catalog.CatalogInputError('准备确认字段无效')
    # Older editors must not silently erase a reviewed dependency while editing request fields.
    for key in ('setup_steps', 'resource_recovery'):
        if row and key not in preparation and key in row.preparation:
            preparation = {**preparation, key: deepcopy(row.preparation[key])}
    context, resolved, variables = _context(project, _config(project)[1], user)
    _remember_state(project, version, context, variables, {asset.pk: asset})
    derived, metadata, _ = _derive(asset, operation, version, context, resolved, variables)
    previous = row.request if row else derived
    patch = {key: value for key, value in patch.items()
             if not (key not in previous and ((key == 'protocol' and value == 'HTTP') or (key in ('sse_config', 'websocket_config') and value == {})))}
    request = {**deepcopy(previous), **_restore_request(patch, previous)}
    if request['method'] != operation['method']:
        raise catalog.CatalogInputError('请求方法必须与来源契约一致')
    request, preparation = _validate_edit(project, asset, metadata, request, preparation, user)
    edited = set((row.preparation if row else {}).get(EDIT_MARKER, []))
    edited.update(key for key in patch if catalog.digest(request[key]) != catalog.digest(previous.get(key)))
    prior_preparation = _public_preparation(row.preparation) if row else {}
    internal = deepcopy(preparation)
    if edited:
        internal[EDIT_MARKER] = sorted(edited)
    if (row and row.preparation.get(CONFIRM_MARKER)) or preparation != prior_preparation:
        internal[CONFIRM_MARKER] = True
    candidate = SimpleNamespace(project_id=project.pk, source_key=operation['source_key'],
        request=request, preparation=internal, context=context, context_fingerprint=catalog.digest(context))
    dependencies = resolve_setup_steps(candidate, variables=variables)
    outputs = [rule['name'] for item in dependencies for rule in request_extractors(item['kwargs'])]
    gaps = _request_gaps(request, metadata, internal, operation, version, resolved, variables, setup_outputs=outputs)
    values = dict(request=request, preparation=internal, source_metadata=metadata,
        source_catalog_version=version.version, source_hash=operation['content_hash'], source_asset_hash=_asset_hash(asset, operation),
        context=context, context_fingerprint=catalog.digest(context), gaps=gaps, status='blocked' if gaps else 'unverified')
    if row is None:
        row = PerfPreparedRequest.objects.create(project=project, source_key=operation['source_key'], created_by=user, **values)
    elif (row.source_metadata.get('id') != metadata['id'] or any(catalog.digest(getattr(row, key)) != catalog.digest(value) for key, value in values.items()
          if key not in ('source_catalog_version', 'source_metadata'))):
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
        row.save()
    return editable_request(project.pk, request_id, user)


@transaction.atomic
@validation_cached
def prepare(project_id, payload, user):
    project = PerfProject.objects.select_for_update().get(pk=project_id)
    environments.require_project_access(project.pk, user)
    if not isinstance(payload, dict) or set(payload) - {'expected_catalog_version', 'expected_config_revision', 'request_ids'}:
        raise catalog.CatalogInputError('批量准备参数无效')
    version = catalog.latest_version(project.pk)
    _revision(payload.get('expected_catalog_version'), version.version if version else 0)
    revision, config = _config(project)
    _revision(payload.get('expected_config_revision'), revision)
    operations = version.operations if version else []
    ids = catalog.strict_ids(payload['request_ids'], 'request_ids') if 'request_ids' in payload else [op['id'] for op in operations]
    lookup = {op['id']: op for op in operations}
    if any(pk not in lookup for pk in ids):
        raise catalog.CatalogInputError('接口不存在或不属于当前接口库')
    context, resolved, variables = _context(project, config, user)
    fingerprint = catalog.digest(context)
    assets = ApiRequest.objects.select_related('collection').in_bulk(ids)
    if set(assets) != set(ids):
        raise catalog.CatalogConflict('来源接口已删除，请重新导入契约')
    _remember_state(project, version, context, variables, assets)
    results = []
    counts = {'new_count': 0, 'updated_count': 0, 'unchanged_count': 0, 'user_preserved_count': 0}
    for pk in ids:
        operation, asset = lookup[pk], assets[pk]
        _validate_asset(project, asset)
        request, metadata, gaps = _derive(asset, operation, version, context, resolved, variables)
        row = PerfPreparedRequest.objects.select_for_update().filter(project=project, source_key=operation['source_key']).first()
        preparation = {}
        if row and (row.preparation.get(EDIT_MARKER) or row.preparation.get(CONFIRM_MARKER)):
            if (row.source_hash != operation['content_hash'] or row.source_asset_hash != _asset_hash(asset, operation)
                    or row.source_metadata.get('id') != metadata['id']):
                raise catalog.CatalogConflict('有手工配置的接口来源已变化，请打开接口详情审查并保存，原配置已保留')
            for key in row.preparation.get(EDIT_MARKER, []):
                if key in EDITABLE_FIELDS:
                    request[key] = deepcopy(row.request[key])
            preparation = deepcopy(row.preparation)
            candidate = SimpleNamespace(project_id=project.pk, source_key=operation['source_key'],
                request=request, preparation=preparation, context=context, context_fingerprint=fingerprint)
            dependencies = resolve_setup_steps(candidate, variables=variables)
            outputs = [rule['name'] for item in dependencies for rule in request_extractors(item['kwargs'])]
            gaps = _request_gaps(request, metadata, preparation, operation, version, resolved, variables,
                                 setup_outputs=outputs)
            counts['user_preserved_count'] += 1
        values = dict(source_catalog_version=version.version, source_hash=operation['content_hash'],
            source_asset_hash=_asset_hash(asset, operation), source_metadata=metadata, request=request,
            preparation=preparation, context=context, context_fingerprint=fingerprint,
            status='blocked' if gaps else 'unverified', gaps=gaps)
        if row is None:
            row = PerfPreparedRequest.objects.create(project=project, source_key=operation['source_key'], created_by=user, **values)
            counts['new_count'] += 1
        elif (row.source_metadata.get('id') != metadata['id'] or
              any(catalog.digest(getattr(row, key)) != catalog.digest(value) for key, value in values.items()
                  if key not in ('source_catalog_version', 'source_metadata'))):
            for key, value in values.items():
                setattr(row, key, deepcopy(value))
            row.revision += 1
            row.save()
            counts['updated_count'] += 1
        else:
            counts['unchanged_count'] += 1
        results.append(public_summary(row, operation, description=asset.description))
    return {'catalog_version': version.version if version else 0, 'config_revision': revision,
        'prepared_count': len(results), 'blocked_count': sum(r['status'] == 'blocked' for r in results),
        'ready_count': sum(r['status'] not in ('blocked', 'stale') for r in results), **counts, 'results': results}


def definition_hash(row):
    payload = {'revision': row.revision, 'request': row.request, 'preparation': row.preparation,
        'metadata': row.source_metadata, 'context': row.context, 'source_hash': row.source_hash,
        'source_asset_hash': row.source_asset_hash}
    if row.request.get('files'):
        from .upload_files import resolve_files
        try:
            payload['upload_files'] = resolve_files(row.request['files'], row.project_id, {})
        except ValidationError:
            payload['upload_files'] = {'unavailable': True}
    return catalog.digest(payload)


def validate_current(row, *, require_passed=False):
    project, operations, assets, context = _current_state(row.project_id)
    operation = operations.get(row.source_key)
    asset = assets.get(operation['id']) if operation else None
    if (not operation or not asset or operation['id'] != row.source_metadata.get('id') or
            operation['content_hash'] != row.source_hash or _asset_hash(asset, operation) != row.source_asset_hash):
        raise catalog.CatalogConflict('来源接口已变化，请重新补齐配置并验证')
    _validate_asset(project, asset)
    if context is None:
        raise catalog.CatalogConflict('公共绑定已删除或不可用，请重新配置')
    if context != row.context or catalog.digest(context) != row.context_fingerprint:
        raise catalog.CatalogConflict('公共配置或环境已变化，请重新补齐并验证')
    resolve_setup_steps(row)
    if require_passed:
        from .pool_verification import verification_summary
        if row.gaps or verification_summary(row).get('status') != 'passed':
            raise catalog.CatalogConflict('仅可复用当前配置已验证通过的接口')


@validation_cached
def public_summary(row, operation=None, *, description=None):
    if description is None and row is not None:
        project, operations, assets, _ = _current_state(row.project_id)
        current = operations.get(row.source_key)
        asset = assets.get(current['id']) if current else None
        if asset and asset.collection_id and asset.collection.project_id == project.api_project_id:
            description = asset.description
    note = preparation_note(description)
    if row is None:
        return {'id': None, 'request_id': operation['id'], 'source_key': operation['source_key'],
            'revision': 0, 'config_revision': 0, 'status': 'unprepared', 'protocol': 'HTTP', 'gaps': [], 'last_evidence': None,
            'preparation_note': note, 'definition_hash': None, 'setup_steps': [],
            'verification_methods': [operation['method']],
            'has_writes': operation['method'] not in ('GET', 'HEAD', 'OPTIONS')}
    status, evidence = row.status, None
    gaps = deepcopy(row.gaps)
    closure = {'setup_steps': [], 'verification_methods': [row.request['method']],
               'has_writes': row.request['method'] not in ('GET', 'HEAD', 'OPTIONS')}
    try:
        validate_current(row)
        closure = public_dependencies(row, resolve_setup_steps(row))
    except (catalog.CatalogConflict, ValidationError):
        status = 'stale'
        gaps = [catalog.gap('stale', 'source', '接口来源或公共绑定已变化，请重新补齐配置')]
    else:
        if not gaps:
            from .pool_verification import verification_summary
            verification = verification_summary(row)
            status, evidence = verification['status'], verification.get('last_evidence')
    return {'id': row.pk, 'request_id': row.source_metadata['id'], 'source_key': row.source_key,
        'revision': row.revision, 'config_revision': row.context['config_revision'], 'status': status,
        'protocol': row.request.get('protocol', 'HTTP'),
        'gaps': gaps, 'last_evidence': evidence, 'preparation_note': note,
        'definition_hash': definition_hash(row), **closure}


def scenario_kwargs(row):
    result = {key: deepcopy(row.context[key]) for key in CONTEXT_FIELDS}
    for key in ('environment', 'global_environment', 'account_pool_version'):
        result[key + '_id'] = result.pop(key)
    return {'engine': 'K6', **result}


def step_kwargs(row):
    return {**deepcopy(row.request), 'source_request_id': row.source_metadata['id'],
        'source_metadata': deepcopy(row.source_metadata), 'preparation': {
            key: deepcopy(value) for key, value in row.preparation.items() if key in STEP_PREPARATION_FIELDS}}


@transaction.atomic
@validation_cached
def import_steps(scenario, payload, user):
    PerfProject.objects.select_for_update().get(pk=scenario.project_id)
    scene = PerfScenario.objects.select_for_update().get(pk=scenario.pk)
    environments.require_project_access(scene.project_id, user)
    if scene.engine != 'K6':
        raise catalog.CatalogConflict('已验证接口池仅支持 K6 场景')
    runtime = scene.runtime_config or {}
    if runtime.get('proxy') or runtime.get('script_ref') not in (None, '', {}, {'mode': 'scenario'}):
        raise catalog.CatalogConflict('当前场景配置了代理或独立脚本，请使用匹配接口池配置的场景')
    version = catalog.latest_version(scene.project_id)
    _revision(payload.get('expected_catalog_version'), version.version if version else 0)
    ids = catalog.strict_ids(payload.get('request_ids'), 'request_ids')
    operations = {op['id']: op for op in version.operations} if version else {}
    if any(pk not in operations for pk in ids):
        raise catalog.CatalogConflict('接口已从当前接口库移除')
    revisions = payload.get('expected_prepared_revisions')
    keys = {operations[pk]['source_key'] for pk in ids}
    if not isinstance(revisions, dict) or set(revisions) != keys:
        raise catalog.CatalogConflict('请刷新并选择当前已验证接口')
    rows = {r.source_key: r for r in PerfPreparedRequest.objects.select_for_update().filter(project_id=scene.project_id, source_key__in=keys)}
    chosen = []
    for pk in ids:
        row = rows.get(operations[pk]['source_key'])
        if row is None:
            raise catalog.CatalogConflict('选择中包含尚未准备的接口，请选择原始契约模式或先准备验证')
        _revision(revisions[row.source_key], row.revision)
        validate_current(row, require_passed=True)
        chosen.append(row)
    dependencies = expanded_setup_steps(chosen)
    setups = missing_import_setups(scene, dependencies, chosen)
    from . import prepared_recovery
    recovery_group = prepared_recovery.group(chosen)
    target = scenario_kwargs(chosen[0])
    if any(scenario_kwargs(row) != target for row in chosen):
        raise catalog.CatalogConflict('所选接口的公共绑定不一致')
    actual = {key: deepcopy(getattr(scene, key)) for key in target}
    # Runtime operational defaults are not authentication bindings and remain owned by the scene.
    actual['runtime_config'] = {key: val for key, val in actual['runtime_config'].items()
                                if key in ('auth_profile', 'account_identity_variable')}
    actual['env_config'] = {key: value for key, value in actual['env_config'].items()
        if not (key == 'base_url' and value == '' or key == 'headers' and value == {})}
    empty = all(actual[key] in (None, '', {}, []) for key in actual if key != 'engine') and not scene.steps.exists()
    applied = actual != target
    if applied and not empty:
        raise catalog.CatalogConflict('当前场景已有不同公共配置，不能自动覆盖；请使用空白场景或匹配绑定')
    if applied:
        for key, value in target.items():
            if key == 'runtime_config':
                value = {**(scene.runtime_config or {}), **value}
            setattr(scene, key, value)
        scene.save()
    maximum = scene.steps.aggregate(value=Max('order'))['value']
    if recovery_group:
        created, _ = prepared_recovery.create_steps(scene, chosen, user,
            order=maximum + 1 if maximum is not None else 0)
        return created, applied
    kwargs = [item['kwargs'] for item in setups] + [step_kwargs(row) for row in chosen]
    created = [PerfScenarioStep.objects.create(scenario=scene, order=(maximum + 1 if maximum is not None else 0) + offset,
        **item) for offset, item in enumerate(kwargs)]
    return created, applied
