"""Explicit environment selection, scoped access and secret-safe static overlays."""
from copy import deepcopy
import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import Q, QuerySet
from rest_framework.exceptions import PermissionDenied, ValidationError

from .variables import SECRET_MASK


def is_environment_admin(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def accessible_projects(user) -> QuerySet:
    from ..models import PerfProject
    query = PerfProject.objects.all()
    if not user or not user.is_authenticated:
        return query.none()
    if is_environment_admin(user):
        return query
    return query.filter(Q(owner=user) | Q(members=user)).distinct()


def require_project_access(project_id: int, user) -> None:
    if not accessible_projects(user).filter(pk=project_id).exists():
        raise PermissionDenied('无权访问该压测项目')


def accessible_environments(user) -> QuerySet:
    from ..models import PerfEnvironment
    query = PerfEnvironment.objects.select_related('project', 'created_by')
    if not user or not user.is_authenticated:
        return query.none()
    if is_environment_admin(user):
        return query
    return query.filter(Q(scope='GLOBAL', project__isnull=True)
                        | Q(scope='PROJECT', project__in=accessible_projects(user)))


def require_environment_write(environment, user) -> None:
    if environment.scope == 'GLOBAL':
        if not is_environment_admin(user):
            raise PermissionDenied('仅管理员可以维护全局环境')
    else:
        require_project_access(environment.project_id, user)


def validate_selection(project_id: int, environment, global_environment, user=None) -> None:
    if user is not None:
        require_project_access(project_id, user)
    for env, scope in ((global_environment, 'GLOBAL'), (environment, 'PROJECT')):
        if env is None:
            continue
        if env.scope != scope or (scope == 'GLOBAL' and env.project_id is not None):
            raise ValidationError('环境作用域与选择字段不匹配')
        if scope == 'PROJECT' and env.project_id != project_id:
            raise ValidationError('环境不属于当前场景项目')


def validate_engine_selection(engine: str, environment, global_environment, overrides=None) -> None:
    if engine != 'K6' and (environment or global_environment or overrides):
        raise ValidationError('持久环境引用和本次运行环境覆盖仅支持 K6；切换引擎前请清除环境选择')


def sensitive_header(name: str) -> bool:
    normalized = name.lower().replace('_', '-').replace('-', '')
    return any(part in normalized for part in ('authorization', 'cookie', 'token', 'apikey',
                                               'secret', 'password', 'credential'))


def merge_headers(*layers: dict) -> dict:
    merged = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            merged[key.lower()] = (key, deepcopy(value))
    return dict(merged.values())


def validate_headers(value: dict, old: dict | None = None, allow_masks: bool = True) -> dict:
    if not isinstance(value, dict):
        raise ValidationError('公共请求头必须是对象')
    old_map = {key.lower(): val for key, val in (old or {}).items()}
    result, seen = {}, set()
    for name, val in value.items():
        if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise ValidationError('请求头名称非法')
        key = name.lower()
        if key in seen:
            raise ValidationError('请求头名称不能重复（忽略大小写）')
        seen.add(key)
        if not isinstance(val, str) or '\r' in val or '\n' in val:
            raise ValidationError('请求头值必须是单行字符串')
        if val == SECRET_MASK:
            if not allow_masks or not sensitive_header(name) or key not in old_map or old_map[key] == SECRET_MASK:
                raise ValidationError('脱敏请求头没有可保留的原值，请重新填写')
            val = old_map[key]
        result[name] = val
    return result


def validate_environment_variables(value: list, old: list | None = None,
                                   allow_masks: bool = True, *, strict_names: bool = True) -> list:
    from ..serializers import validate_variables
    if not isinstance(value, list):
        raise ValidationError('变量列表必须是数组')
    old_map = {item.get('name'): item for item in old or []}
    result = deepcopy(value)
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            raise ValidationError('变量格式或名称非法')
        item['name'] = item['name'].strip()
        # Values must never be included in validation error messages.
        if strict_names and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', item['name']):
            raise ValidationError('变量名称仅允许字母、数字和下划线，且不能以数字开头')
        if 'secret' in item and not isinstance(item['secret'], bool):
            raise ValidationError('secret 必须是布尔值')
        if not isinstance(item.get('type', 'CONSTANT'), str):
            raise ValidationError('变量类型非法')
        if item.get('type', '').upper() == 'CSV':
            file_id = item.get('data_file_id')
            if isinstance(file_id, bool) or not isinstance(file_id, (int, str)) or not str(file_id).isdigit():
                raise ValidationError('CSV 变量必须选择有效的数据文件 ID')
            if int(file_id) < 1:
                raise ValidationError('CSV 变量必须选择有效的数据文件 ID')
            item['data_file_id'] = int(file_id)
        previous = old_map.get(item['name']) or {}
        for key in ('value', 'values', 'options'):
            if item.get(key) == SECRET_MASK:
                if (not allow_masks or not item.get('secret') or not previous.get('secret')
                        or key not in previous or previous[key] == SECRET_MASK):
                    raise ValidationError('脱敏变量没有可保留的原值，请重新填写')
                item[key] = deepcopy(previous[key])
    try:
        return validate_variables(result)
    except (ValidationError, TypeError, ValueError, AttributeError):
        raise ValidationError('环境变量配置非法，请检查类型、重复名称和必填参数') from None


def validate_config(value: dict, old: dict | None = None, *, allow_masks: bool = True,
                    strict: bool = False) -> dict:
    if not isinstance(value, dict):
        raise ValidationError('环境覆盖必须是对象')
    if strict and set(value) - {'base_url', 'headers', 'verify_ssl', 'variables'}:
        raise ValidationError('环境覆盖仅支持 base_url、headers、verify_ssl、variables')
    result, old = deepcopy(value), old or {}
    if 'base_url' in result:
        base = result['base_url']
        if not isinstance(base, str):
            raise ValidationError('环境基址必须是字符串')
        base = base.strip().rstrip('/')
        if base:
            try:
                parts = urlsplit(base)
                if (parts.scheme not in ('http', 'https') or not parts.hostname
                        or parts.username is not None or parts.password is not None
                        or parts.query or parts.fragment or any(c.isspace() for c in base)):
                    raise ValueError
                parts.port
            except ValueError:
                raise ValidationError('环境基址必须是 HTTP(S) 地址，不能包含认证信息、查询参数或片段') from None
        result['base_url'] = base
    if 'verify_ssl' in result and not isinstance(result['verify_ssl'], bool):
        raise ValidationError('verify_ssl 必须是布尔值')
    if 'headers' in result:
        result['headers'] = validate_headers(result['headers'], old.get('headers'), allow_masks)
    if 'variables' in result:
        result['variables'] = validate_environment_variables(result['variables'], old.get('variables'), allow_masks)
    return result


def mask_config(config: dict) -> dict:
    result = deepcopy(config)
    if 'headers' in result:
        result['headers'] = {key: SECRET_MASK if sensitive_header(key) else value
                             for key, value in (result['headers'] or {}).items()}
    if 'variables' in result:
        for item in result['variables'] or []:
            if item.get('secret'):
                for field in ('value', 'values', 'options'):
                    if field in item:
                        item[field] = SECRET_MASK
    return result


def content_hash(environment) -> str:
    content = {key: getattr(environment, key) for key in
               ('name', 'scope', 'project_id', 'base_url', 'headers', 'variables', 'verify_ssl')}
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hmac.new(settings.SECRET_KEY.encode(), encoded, hashlib.sha256).hexdigest()


def environment_metadata(environment) -> dict:
    return {key: getattr(environment, key) for key in
            ('id', 'name', 'scope', 'project_id', 'version', 'content_hash')}


def resolve_environment(scenario, user=None, overrides: dict | None = None) -> dict:
    """Read selected IDs once. Blank base URLs inherit; explicit False still overrides."""
    from ..models import PerfEnvironment
    selected = {'environment': getattr(scenario, 'environment_id', None),
                'global_environment': getattr(scenario, 'global_environment_id', None)}
    validate_engine_selection(scenario.engine, selected['environment'], selected['global_environment'], overrides)
    refs = PerfEnvironment.objects.in_bulk([value for value in selected.values() if value])
    if any(value and value not in refs for value in selected.values()):
        raise ValidationError('引用的环境不存在，请重新选择')
    project_env, global_env = refs.get(selected['environment']), refs.get(selected['global_environment'])
    # Internal scheduler calls inherit the scenario creator's current access to references.
    actor = user
    if actor is None and refs:
        actor = scenario.created_by
    validate_selection(scenario.project_id, project_env, global_env, actor)
    layers, sources = [], []
    for env in (global_env, project_env):
        if env:
            layers.append({'base_url': env.base_url, 'headers': env.headers,
                           'verify_ssl': env.verify_ssl, 'variables': env.variables})
            sources.append(environment_metadata(env))
    layers.append(deepcopy(scenario.env_config or {}))
    layers.append({'variables': deepcopy(scenario.variables or [])})
    if overrides is not None:
        layers.append(validate_config(overrides, allow_masks=False, strict=True))
    config, variables = {}, {}
    for layer in layers:
        for key, value in layer.items():
            if key == 'variables':
                for item in value or []:
                    variables[item['name']] = deepcopy(item)
            elif key == 'headers':
                config['headers'] = merge_headers(config.get('headers'), value)
            elif key == 'base_url':
                if value:
                    config[key] = value
            else:
                config[key] = deepcopy(value)
    # Preserve the old empty inline shape when nothing has been selected or overridden.
    if not refs and overrides is None:
        config = deepcopy(scenario.env_config or {})
    return {'env_config': config, 'variables': list(variables.values()),
            'environment_sources': sources}


def public_resolution(resolved: dict) -> dict:
    return {'env_config': mask_config(resolved['env_config']),
            'variables': mask_config({'variables': resolved['variables']})['variables'],
            'sources': deepcopy(resolved['environment_sources']),
            'precedence': ['global_environment', 'environment', 'scenario', 'environment_overrides']}
