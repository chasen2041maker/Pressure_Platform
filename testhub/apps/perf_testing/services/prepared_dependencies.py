"""单层已准备接口依赖；只解析配置，不发送请求或改变身份绑定。"""
from copy import deepcopy
import re

from . import api_catalog as catalog, auth_profiles

MAX_SETUP_STEPS = 8
REFERENCE_FIELDS = {'source_key', 'revision', 'definition_hash', 'extractors'}
STEP_PREPARATION_FIELDS = {'confirmed_fields', 'body_reviewed'}
SETUP_SOURCE_MARKER = '_prepared_setup'


def request_extractors(request):
    if request.get('protocol') == 'WEBSOCKET':
        config = request.get('websocket_config') or {}
        return [rule for frame in [config.get('auth') or {}, *config.get('commands', [])]
                for rule in frame.get('extractors', [])]
    if request.get('protocol') == 'SSE':
        return [rule for event in (request.get('sse_config') or {}).get('rules', []) for rule in event.get('extractors', [])]
    return request.get('extractors', [])


def validate_references(value):
    if not isinstance(value, list) or len(value) > MAX_SETUP_STEPS:
        raise catalog.CatalogInputError('setup_steps 必须为最多 8 项的单层前置数组')
    seen = set()
    for ref in value:
        if (not isinstance(ref, dict) or set(ref) != REFERENCE_FIELDS
                or not isinstance(ref['source_key'], str) or not 1 <= len(ref['source_key']) <= 512
                or type(ref['revision']) is not int or ref['revision'] < 1
                or not isinstance(ref['definition_hash'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', ref['definition_hash'])):
            raise catalog.CatalogInputError('前置必须包含准确的来源键、修订、定义哈希和提取规则')
        extractors = ref['extractors']
        if not isinstance(extractors, list) or not 1 <= len(extractors) <= 16:
            raise catalog.CatalogInputError('每个前置必须包含 1 至 16 条输出提取规则')
        _validate_extractors(extractors)
        identity = catalog.digest(ref)
        if identity in seen:
            raise catalog.CatalogInputError('同一接口不能重复声明相同前置')
        seen.add(identity)
    return deepcopy(value)


def _validate_extractors(extractors):
    names = set()
    for rule in extractors:
        if (not isinstance(rule, dict) or set(rule) != {'name', 'type', 'expr'}
                or rule.get('type') != 'JSON_PATH' or not isinstance(rule.get('name'), str)
                or not auth_profiles.NAME.fullmatch(rule['name'])
                or not isinstance(rule.get('expr'), str) or not auth_profiles.PATH.fullmatch(rule['expr'])
                or rule['expr'] == '$' or rule['name'] in names):
            raise catalog.CatalogInputError('前置输出只支持唯一变量名和基础 JSON_PATH 路径')
        names.add(rule['name'])
    return names


def _protected_names(row, variables):
    from . import prepared_requests as prepared
    if variables is None:
        project, _, _, _ = prepared._current_state(row.project_id)
        variables = prepared.memo_get(('dependency_variables', row.project_id, row.context_fingerprint),
            lambda: prepared._context(project, prepared._config(project)[1], project.owner)[2])
    names = {v['name'] for v in variables}
    names |= auth_profiles.RESERVED | auth_profiles.IDENTITY_NAMES
    names |= {'token', 'access_token', 'refresh_token', 'device_id'}
    runtime = row.context.get('runtime_config') or {}
    profile = runtime.get('auth_profile') or {}
    names.update(value for key, value in profile.items() if key.endswith('_variable') and isinstance(value, str))
    if runtime.get('account_identity_variable'):
        names.add(runtime['account_identity_variable'])
    return names


def _setup_copy(row, reference):
    from . import prepared_requests as prepared
    extractors = deepcopy(request_extractors(row.request))
    _validate_extractors(extractors)
    for rule in reference['extractors']:
        existing = next((item for item in extractors if item['name'] == rule['name']), None)
        if existing is not None and existing != rule:
            raise catalog.CatalogInputError('前置不能覆盖原有提取规则')
        if existing is None:
            if row.request.get('protocol') in ('SSE', 'WEBSOCKET'):
                raise catalog.CatalogInputError('流式前置只能复用合同中已声明的帧内提取规则')
            extractors.append(deepcopy(rule))
    key = catalog.digest({'project_id': row.project_id, 'context': row.context_fingerprint, 'reference': reference})
    kwargs = prepared.step_kwargs(row)
    kwargs.update(is_setup=True, extractors=[] if row.request.get('protocol') in ('SSE', 'WEBSOCKET') else extractors)
    # source_metadata is read-only in normal step editing; source sync discards this binding.
    kwargs['source_metadata'][SETUP_SOURCE_MARKER] = {'key': key, **deepcopy(reference)}
    return {'key': key, 'row': row, 'kwargs': kwargs, 'reference': deepcopy(reference)}


def _same_original_producer(step, row):
    from . import prepared_requests as prepared
    marker = step.source_metadata.get(SETUP_SOURCE_MARKER)
    if not isinstance(marker, dict) or set(marker) != REFERENCE_FIELDS | {'key'}:
        return False
    reference = {key: marker[key] for key in REFERENCE_FIELDS}
    try:
        validate_references([reference])
        if (reference['source_key'] != row.source_key or reference['revision'] != row.revision
                or reference['definition_hash'] != prepared.definition_hash(row)):
            return False
        expected = _setup_copy(row, reference)['kwargs']
        return {key: deepcopy(getattr(step, key)) for key in expected} == expected
    except catalog.CatalogInputError:
        return False


def resolve_setup_steps(row, *, variables=None):
    """Return exact frozen copies. Invalid/stale references never become known variables."""
    from ..models import PerfPreparedRequest
    from . import prepared_requests as prepared
    references = validate_references(row.preparation.get('setup_steps', []))
    if not references:
        return []
    protected = _protected_names(row, variables)
    occupied = {rule.get('name') for rule in request_extractors(row.request)}
    result = []
    for ref in references:
        if ref['source_key'] == row.source_key:
            raise catalog.CatalogInputError('接口不能依赖自己')
        dependency = PerfPreparedRequest.objects.filter(project_id=row.project_id, source_key=ref['source_key']).first()
        if dependency is None:
            raise catalog.CatalogConflict('前置模板不存在或不属于当前项目')
        if dependency.preparation.get('setup_steps'):
            raise catalog.CatalogInputError('仅支持一层前置，前置接口不能再声明依赖')
        if (dependency.revision != ref['revision'] or prepared.definition_hash(dependency) != ref['definition_hash']
                or dependency.context != row.context or dependency.context_fingerprint != row.context_fingerprint):
            raise catalog.CatalogConflict('前置修订、定义或公共绑定已变化，请重新审查依赖')
        prepared.validate_current(dependency)
        request = dependency.request
        if dependency.gaps or dependency.status == 'blocked' or not request.get('enabled') or request.get('is_setup'):
            raise catalog.CatalogInputError('前置接口必须已完成配置并启用')
        if request.get('protocol') == 'SSE':
            from .sse_steps import normalize_sse_config
            normalize_sse_config(request.get('sse_config'))
        elif request.get('protocol') == 'WEBSOCKET':
            from .pool_verification import _business_assertions
            if not _business_assertions(request):
                raise catalog.CatalogInputError('WebSocket 前置每个业务帧必须有断言')
        elif not prepared._reviewed_business_assertions(request.get('assertions', [])):
            raise catalog.CatalogInputError('前置接口必须包含成功状态和业务断言')
        if request.get('files') or request.get('body_type') not in ('NONE', 'JSON'):
            raise catalog.CatalogInputError('前置接口只支持当前 K6 的 NONE / JSON 请求')
        dependency_copy = _setup_copy(dependency, ref)
        outputs = _validate_extractors(request_extractors(dependency_copy['kwargs']))
        if outputs & (protected | occupied):
            raise catalog.CatalogInputError('前置输出不能覆盖身份、环境、内置变量或其他提取变量')
        occupied |= outputs
        result.append(dependency_copy)
    return result


def expanded_setup_steps(rows):
    """Share only identical producers; never resolve same-name outputs by last write."""
    result = {}
    producers = {}
    business_outputs = {}
    for row in rows:
        for rule in request_extractors(row.request):
            business_outputs.setdefault(rule.get('name'), []).append((row, rule))
    for row in rows:
        for dependency in resolve_setup_steps(row):
            key = dependency['key']
            for rule in request_extractors(dependency['kwargs']):
                name = rule['name']
                from .prepared_requests import definition_hash
                same_business_producer = all(other.pk == dependency['row'].pk
                    and definition_hash(other) == dependency['reference']['definition_hash'] and rule == original
                    for other, original in business_outputs.get(name, []))
                if not same_business_producer or name in producers and producers[name] != key:
                    raise catalog.CatalogConflict('所选接口的前置输出存在冲突')
                producers[name] = key
            result.setdefault(key, dependency)
    return list(result.values())


def public_dependencies(row, resolved):
    from .websocket_steps import request_has_writes
    steps = [{'key': item['key'], 'source_key': item['row'].source_key, 'revision': item['row'].revision,
              'definition_hash': item['reference']['definition_hash'],
              'method': item['kwargs']['method'], 'path': item['row'].source_metadata.get('path', ''),
              'outputs': [rule['name'] for rule in request_extractors(item['kwargs'])]}
             for item in resolved]
    methods = list(dict.fromkeys([item['method'] for item in steps] + [row.request['method']]))
    return {'setup_steps': steps, 'verification_methods': methods,
            'has_writes': any(request_has_writes(request) for request in [row.request, *[item['kwargs'] for item in resolved]])}


def missing_import_setups(scene, dependencies, business_rows=()):
    """Deduplicate exact stored copies; any existing conflicting producer blocks import."""
    existing = list(scene.steps.all())
    for row in business_rows:
        outputs = {rule.get('name') for rule in request_extractors(row.request)}
        for step in existing:
            if (step.is_setup and outputs & {rule.get('name') for rule in request_extractors({'protocol': step.protocol, 'sse_config': step.sse_config, 'websocket_config': step.websocket_config, 'extractors': step.extractors})}
                    and not _same_original_producer(step, row)):
                raise catalog.CatalogConflict('新增业务接口会覆盖场景已有前置输出，请先审查冲突')
    missing = []
    for dependency in dependencies:
        kwargs = dependency['kwargs']
        outputs = {rule['name'] for rule in request_extractors(kwargs)}
        matched = False
        for step in existing:
            overlaps = outputs & {rule.get('name') for rule in request_extractors({'protocol': step.protocol, 'sse_config': step.sse_config, 'websocket_config': step.websocket_config, 'extractors': step.extractors})}
            if not overlaps:
                continue
            actual = {key: deepcopy(getattr(step, key)) for key in kwargs}
            if actual == kwargs and step.is_setup and not matched:
                matched = True
            elif not step.is_setup:
                from .prepared_requests import step_kwargs
                original = step_kwargs(dependency['row'])
                if actual != original:
                    raise catalog.CatalogConflict('场景业务步骤会覆盖前置输出，请先审查冲突')
            else:
                raise catalog.CatalogConflict('场景已有不同的前置输出，不能覆盖或重复执行')
        if not matched:
            missing.append(dependency)
    return missing
