"""Bounded OpenAPI imports, project-owned API assets and immutable source references."""
from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from django.db import transaction
from django.db.models import Max
from rest_framework.exceptions import APIException, ValidationError

from .environments import require_project_access

MAX_BYTES = 8 * 1024 * 1024
MAX_OPERATIONS = 2000
MAX_BATCH = 2000
MAX_IDENTIFIER = 2**63 - 1
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_OPERATION_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_NODES = 500000
MAX_OPERATION_NODES = 30000
METHODS = ('get', 'post', 'put', 'delete', 'patch', 'head', 'options')
REQUEST_FIELDS = ('name', 'method', 'url', 'headers', 'params', 'body_type', 'body', 'files', 'assertions', 'extractors')


class CatalogInputError(ValidationError):
    default_detail = '接口契约格式无效，请检查 OpenAPI/Swagger 文件'


class CatalogConflict(APIException):
    status_code = 409
    default_detail = '接口库版本已变化，请重新预览后导入或更新'


class ExpansionBudget:
    """Charge repeated materializations, including scalars, before allocating them.

    Six bytes per character bounds UTF-8 JSON and escaped control characters.
    Container/key overhead is deliberately conservative; this is a work budget,
    not an estimate of Python's resident memory.
    """
    def __init__(self, parent=None):
        self.parent = parent
        self.bytes_left = MAX_OPERATION_BYTES if parent else MAX_EXPANDED_BYTES
        self.nodes_left = MAX_OPERATION_NODES if parent else MAX_EXPANDED_NODES

    def node(self, value):
        size = 32
        if isinstance(value, str):
            size += 6 * len(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            size += value.bit_length() // 3 + 2
        elif isinstance(value, dict):
            size += sum(6 * len(key) + 8 for key in value)
        self.bytes_left -= size
        self.nodes_left -= 1
        if self.bytes_left < 0 or self.nodes_left < 0:
            raise CatalogInputError('契约引用展开或请求转换超过安全预算，请拆分契约或减少重复示例')
        if self.parent:
            self.parent.node(value)

    def tree(self, value, depth=0):
        self.node(value)
        if depth > 80:
            raise CatalogInputError('契约结构过大或嵌套过深')
        if isinstance(value, (dict, list)):
            for child in value.values() if isinstance(value, dict) else value:
                self.tree(child, depth + 1)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def gap(code: str, field: str, message: str) -> dict:
    return {'code': code, 'field': field, 'message': message}


def _check_tree(value: object, ancestors: frozenset = frozenset(), budget: list | None = None, depth: int = 0) -> None:
    budget = budget if budget is not None else [200000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 80:
        raise CatalogInputError('契约结构过大或嵌套过深')
    if isinstance(value, (dict, list)):
        if id(value) in ancestors:
            raise CatalogInputError('契约含循环 YAML 锚点')
        ancestors = ancestors | {id(value)}
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise CatalogInputError('契约对象的键必须是字符串')
            ref = value.get('$ref')
            if ref is not None and (not isinstance(ref, str) or not ref.startswith('#/')):
                raise CatalogInputError('只支持当前文件内的 $ref；请先合并外部引用')
            children = value.values()
        else:
            children = value
        for child in children:
            _check_tree(child, ancestors, budget, depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise CatalogInputError('契约含非 JSON 类型')


def _reference(document: dict, ref: str) -> object:
    current = document
    try:
        for token in ref[2:].split('/'):
            token = token.replace('~1', '/').replace('~0', '~')
            current = current[int(token)] if isinstance(current, list) else current[token]
        return current
    except (KeyError, IndexError, ValueError, TypeError):
        raise CatalogInputError('契约的本地 $ref 目标不存在') from None


def _resolve(value: object, document: dict, gaps: list, field: str, refs: tuple = (),
             budget: ExpansionBudget | None = None, depth: int = 0) -> object:
    budget = budget if budget is not None else ExpansionBudget()
    budget.node(value)
    if depth > 60:
        raise CatalogInputError('契约引用展开超过安全上限，请简化输入结构')
    if isinstance(value, list):
        return [_resolve(v, document, gaps, field, refs, budget, depth + 1) for v in value]
    if not isinstance(value, dict):
        return value
    if '$ref' in value:
        ref = value['$ref']
        if ref in refs:
            gaps.append(gap('recursive_ref', field, '递归输入模型需要手动准备并审查请求体'))
            return {'$ref': ref, 'x-perf-recursive': True}
        target = _reference(document, ref)
        if not isinstance(target, dict):
            raise CatalogInputError('本地 $ref 必须指向对象')
        return _resolve({**target, **{k: v for k, v in value.items() if k != '$ref'}},
                        document, gaps, field, refs + (ref,), budget, depth + 1)
    return {k: _resolve(v, document, gaps, field, refs, budget, depth + 1) for k, v in value.items()}


def _example(value: dict) -> tuple:
    if 'default' in value:
        return deepcopy(value['default']), 'default'
    if 'example' in value:
        return deepcopy(value['example']), 'example'
    examples = value.get('examples')
    if isinstance(examples, list) and examples:
        return deepcopy(examples[0]), 'example'
    if isinstance(examples, dict):
        for entry in examples.values():
            if isinstance(entry, dict) and 'value' in entry:
                return deepcopy(entry['value']), 'example'
    return None, 'missing'


def _token(field: str) -> str:
    return '{{' + re.sub(r'[^a-zA-Z0-9_]', '_', field).strip('_') + '}}'


def _sample(schema: dict, field: str, required: bool, requirements: list, gaps: list,
            budget: ExpansionBudget | None = None) -> object:
    if budget:
        budget.tree(schema)
    if not isinstance(schema, dict):
        gaps.append(gap('schema_unsupported', field, '此输入 schema 需要手动审查'))
        return None
    if schema.get('readOnly'):
        return None
    if schema.get('format') in ('binary', 'byte') or schema.get('type') == 'file':
        if required:
            requirements.append({'field': field, 'provenance': 'missing', 'kind': 'file'})
        return None
    for kind in ('oneOf', 'anyOf'):
        if kind in schema:
            gaps.append(gap('schema_choice', field, '请求模型有多个候选，请填写请求体并确认模型分支'))
    if schema.get('x-perf-recursive'):
        return None
    if 'allOf' in schema:
        merged = {}
        for child in schema['allOf']:
            sample = _sample(child, field, required, requirements, gaps, budget)
            if isinstance(sample, dict):
                merged.update(sample)
            elif sample is not None:
                gaps.append(gap('schema_unsupported', field, 'allOf 非对象组合需要手动审查'))
        rest = {k: v for k, v in schema.items() if k != 'allOf'}
        if rest:
            sample = _sample(rest, field, False, requirements, gaps, budget)
            if isinstance(sample, dict):
                merged.update(sample)
        return merged
    value, provenance = _example(schema)
    if schema.get('type') == 'array':
        items = schema.get('items')
        if not items:
            gaps.append(gap('schema_unsupported', field + '/*', '数组 items 的 schema 尚未声明或支持'))
        else:
            _sample(items, field + '/*', False, [], gaps, budget)
    is_object = schema.get('type') == 'object' or 'properties' in schema
    if is_object:
        if value is not None:
            # Examples supply values, never replace the independent required structure.
            for name, child in (schema.get('properties') or {}).items():
                child_schema = deepcopy(child)
                if isinstance(value, dict) and name in value and isinstance(child_schema, dict):
                    child_schema[provenance] = deepcopy(value[name])
                pointer = field + '/' + name.replace('~', '~0').replace('/', '~1')
                _sample(child_schema, pointer, required and name in schema.get('required', []), requirements, gaps, budget)
            if required or provenance == 'example':
                requirements.append({'field': field, 'provenance': provenance, 'suggested': deepcopy(value), 'required': required})
            return value
        result = {}
        for name, child in (schema.get('properties') or {}).items():
            pointer = field + '/' + name.replace('~', '~0').replace('/', '~1')
            child_required = required and name in schema.get('required', [])
            val = _sample(child, pointer, child_required, requirements, gaps, budget)
            if val is not None:
                result[name] = val
        if required:
            requirements.append({'field': field, 'provenance': 'structure'})
        return result
    resource = bool(re.search(r'(^|[/_.-])(id|.*_id|token|password|username|authorization)$', field, re.I))
    if required or provenance == 'example' or (resource and provenance != 'missing'):
        requirements.append({'field': field, 'provenance': provenance, 'suggested': deepcopy(value),
                             'resource': resource, 'required': required})
    return value if provenance != 'missing' else (_token(field) if required else None)


def _consumed_metadata(value: dict) -> None:
    for key in ('operationId', 'summary', 'description', 'host', 'basePath'):
        if key in value and not isinstance(value[key], str):
            raise CatalogInputError('契约的名称、描述和地址字段必须为字符串')
    for key in ('tags', 'schemes', 'consumes'):
        if key in value and (not isinstance(value[key], list) or any(not isinstance(v, str) for v in value[key])):
            raise CatalogInputError('契约的 tags、schemes、consumes 必须为字符串数组')
    if 'parameters' in value and not isinstance(value['parameters'], list):
        raise CatalogInputError('契约 parameters 必须为数组')
    if 'servers' in value and (not isinstance(value['servers'], list) or any(
            not isinstance(server, dict) or not isinstance(server.get('url'), str) for server in value['servers'])):
        raise CatalogInputError('契约 servers 必须为包含字符串 url 的对象数组')
    if 'security' in value and (not isinstance(value['security'], list) or any(
            not isinstance(item, dict) or any(not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes)
                for scopes in item.values()) for item in value['security'])):
        raise CatalogInputError('契约 security 必须为认证对象数组')


def _operation(document: dict, path: str, method: str, path_item: dict, raw: dict, order: int,
               budget: ExpansionBudget) -> dict:
    _consumed_metadata(raw)
    budget.tree(raw)
    gaps, requirements = [], []
    source_servers = raw.get('servers', path_item.get('servers', document.get('servers', [])))
    budget.tree(source_servers)
    servers = deepcopy(source_servers)
    if document.get('swagger') == '2.0':
        servers = [{'url': scheme + '://' + document.get('host', '') + document.get('basePath', '')}
            for scheme in document.get('schemes', ['https'])] if document.get('host') else []
        base_path = document.get('basePath', '')
    else:
        prefixes = {urlsplit(server.get('url', '')).path.rstrip('/') for server in servers}
        base_path = next(iter(prefixes), '') if len(prefixes) <= 1 else ''
        if len(prefixes) > 1 or any('{' in server.get('url', '') for server in servers):
            gaps.append(gap('server_path_choice', 'server', '服务器路径前缀或变量不同，请手动确认场景 URL 的完整路径'))
    if base_path and (not base_path.startswith('/') or '{' in base_path):
        gaps.append(gap('server_path_choice', 'server', '相对服务器地址需要手动确认场景 URL 的完整路径'))
        base_path = ''
    request_path = base_path.rstrip('/') + path
    params = _resolve(path_item.get('parameters', []) + raw.get('parameters', []), document, gaps, 'parameters', budget=budget)
    if not isinstance(params, list) or any(not isinstance(p, dict) or not isinstance(p.get('in'), str)
            or not isinstance(p.get('name'), str) for p in params):
        raise CatalogInputError('参数必须含 name 和 in')
    by_key = {(p['in'], p['name']): p for p in params if isinstance(p, dict) and 'in' in p and 'name' in p}
    if len(by_key) > 300:
        raise CatalogInputError('单个接口参数过多')
    params = list(by_key.values())
    declared_paths = {p['name'] for p in params if p['in'] == 'path'}
    if set(re.findall(r'\{([^{}]+)\}', path)) - declared_paths:
        gaps.append(gap('path_parameter_missing', 'path', '路径模板缺少参数声明，请补齐契约'))
    request = {'name': str(raw.get('summary') or raw.get('operationId') or method.upper() + ' ' + path)[:200],
        'method': method.upper(), 'url': request_path, 'headers': {}, 'params': {}, 'body_type': 'NONE', 'body': '',
        'files': [], 'assertions': [], 'extractors': []}
    for parameter in params:
        location, name = parameter['in'], parameter['name']
        if location in ('body', 'formData'):
            continue
        field = location + '/' + name
        schema = deepcopy(parameter.get('schema', parameter))
        for key in ('example', 'examples'):
            if key in parameter:
                schema[key] = parameter[key]
        required = location == 'path' or bool(parameter.get('required'))
        value = _sample(schema, field, required, requirements, gaps, budget)
        serialization = (schema.get('type') in ('array', 'object') or 'content' in parameter
            or parameter.get('style', 'form' if location == 'query' else 'simple') not in ('form', 'simple')
            or parameter.get('allowReserved') or parameter.get('allowEmptyValue'))
        if serialization:
            gaps.append(gap('parameter_serialization', field, '参数序列化尚未自动适配，请使用明确的标量请求参数'))
        if location == 'path':
            request['url'] = request['url'].replace('{' + name + '}', str(value) if value is not None else _token(field))
        elif location == 'query' and value is not None:
            request['params'][name] = value
        elif location == 'header' and value is not None:
            request['headers'][name] = str(value)
        elif location not in ('query', 'header'):
            gaps.append(gap('parameter_location', field, '此参数位置尚未自动适配'))
    body = _resolve(raw.get('requestBody', {}), document, gaps, 'body', budget=budget)
    if document.get('swagger') == '2.0':
        consumes = raw.get('consumes', document.get('consumes', ['application/json']))
        bodies = [p for p in params if p['in'] == 'body']
        forms = [p for p in params if p['in'] == 'formData']
        if bodies:
            body = {'required': bodies[0].get('required', False),
                'content': {ct: {'schema': bodies[0].get('schema', {})} for ct in consumes}}
        elif forms:
            ct = consumes[0] if consumes else 'multipart/form-data'
            body = {'required': any(p.get('required') for p in forms), 'content': {ct: {'schema': {
                'type': 'object', 'properties': {p['name']: p for p in forms},
                'required': [p['name'] for p in forms if p.get('required')]}}}}
    contents = body.get('content', {})
    if len(contents) > 1:
        gaps.append(gap('content_type_choice', 'body', '存在多种请求 Content-Type，请确认选用的类型'))
    content_type = next(iter(contents), '')
    if contents:
        media = contents[content_type]
        normalized_type = media_type(content_type)
        schema = deepcopy(media.get('schema', {}))
        for key in ('example', 'examples'):
            if key in media:
                schema[key] = media[key]
        data = _sample(schema, 'body', True, requirements, gaps, budget)
        budget.tree(data)
        request['headers']['Content-Type'] = content_type
        if normalized_type == 'application/json' or normalized_type.endswith('+json'):
            request['body_type'] = 'JSON'
            request['body'] = json.dumps(data, ensure_ascii=False) if data is not None else ''
        elif normalized_type in ('multipart/form-data', 'application/x-www-form-urlencoded'):
            request['body_type'] = 'FORM'
            for name, prop in schema.get('properties', {}).items():
                if prop.get('type') == 'file' or prop.get('format') in ('binary', 'byte'):
                    request['files'].append({'field': name, 'file_id': None, 'filename': '', 'content_type': ''})
                    gaps.append(gap('file_required', 'files/' + name, '请上传并选择项目内文件'))
                    if isinstance(data, dict):
                        data.pop(name, None)
            request['body'] = json.dumps(data or {}, ensure_ascii=False)
            if normalized_type == 'multipart/form-data':
                request['headers'].pop('Content-Type', None)
            if media.get('encoding'):
                gaps.append(gap('body_encoding', 'body', '自定义表单编码尚未适配'))
        elif normalized_type.startswith('text/') or normalized_type in ('application/xml',):
            request['body_type'] = 'XML' if 'xml' in normalized_type else 'RAW'
            request['body'] = str(data) if data is not None else ''
        elif schema.get('format') == 'binary' or normalized_type == 'application/octet-stream':
            request['body_type'] = 'BINARY'
            request['files'] = [{'field': 'file', 'file_id': None, 'filename': '', 'content_type': content_type}]
            gaps.append(gap('file_required', 'files/file', '请选择项目内原始文件'))
        else:
            request['body_type'] = 'RAW'
            request['body'] = str(data) if data is not None else ''
            gaps.append(gap('binary_body', 'body', '该 Content-Type 的请求编码尚未适配'))
    source_security = raw.get('security', document.get('security', []))
    budget.tree(source_security)
    security = deepcopy(source_security)
    schemes = document.get('components', {}).get('securitySchemes', document.get('securityDefinitions', {}))
    budget.tree(schemes)
    if security and not any(item == {} for item in security):
        gaps.append(gap('authentication', 'auth', '请配置契约要求的认证请求头或参数'))
    if request['body_type'] not in ('NONE', 'JSON', 'FORM', 'BINARY'):
        gaps.append(gap('engine_body_type', 'body', '当前 k6 场景执行仅支持 JSON 请求体；此接口保留在目录中待适配'))
    if len(request['url']) > 1000:
        raise CatalogInputError('请求 URL 超过 1000 字符')
    result = {'source_key': method.upper() + ' ' + path, 'operation_id': raw.get('operationId', ''),
        'path': path, 'request_path': request_path, 'servers': servers, 'source_base_path': base_path,
        'method': method.upper(), 'order': order, 'tags': deepcopy(raw.get('tags') or ['默认模块']),
        'parameters': params, 'request_body': body, 'security': security, 'security_schemes': deepcopy(schemes),
        'requirements': requirements, 'gaps': gaps, 'request': request, 'raw': deepcopy(raw)}
    budget.tree(result)
    result['content_hash'] = digest(result)
    return result


def parse_document(raw: bytes | dict) -> dict:
    try:
        if isinstance(raw, dict):
            document = raw
        else:
            if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
                raise CatalogInputError('请上传不超过 8 MB 的 UTF-8 JSON/YAML 契约')
            text = raw.decode('utf-8-sig')
            if text.lstrip().startswith(('{', '[')):
                def pairs(items):
                    result = {}
                    for key, value in items:
                        if key in result:
                            raise CatalogInputError('JSON 对象含重复键')
                        result[key] = value
                    return result
                document = json.loads(text, object_pairs_hook=pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(CatalogInputError()))
            else:
                import yaml
                class CatalogLoader(yaml.SafeLoader):
                    def __init__(self, stream):
                        super().__init__(stream)
                        self.catalog_nodes = self.catalog_aliases = self.catalog_depth = 0

                    def compose_node(self, parent, index):
                        self.catalog_nodes += 1
                        self.catalog_depth += 1
                        event = self.peek_event()
                        alias = isinstance(event, yaml.AliasEvent)
                        self.catalog_aliases += int(alias)
                        if self.catalog_nodes > 200000 or self.catalog_aliases > 10000 or self.catalog_depth > 80:
                            raise CatalogInputError('契约 YAML 节点、引用或嵌套超过安全上限')
                        if not alias and getattr(event, 'anchor', None):
                            # YAML permits reuse: aliases bind to the nearest preceding definition.
                            self.anchors.pop(event.anchor, None)
                        try:
                            return super().compose_node(parent, index)
                        finally:
                            self.catalog_depth -= 1

                    def flatten_mapping(self, node):
                        if any(key.tag == 'tag:yaml.org,2002:merge' for key, _ in node.value):
                            raise CatalogInputError('暂不支持 YAML 合并键 <<，请展开合并或使用 JSON 契约')
                        return super().flatten_mapping(node)
                document = yaml.load(text, Loader=CatalogLoader)
        _check_tree(document)
        if not isinstance(document, dict) or not isinstance(document.get('paths'), dict):
            raise CatalogInputError()
        version = str(document.get('openapi', document.get('swagger', '')))
        if version != '2.0' and not re.fullmatch(r'3\.(0|1)\.\d+', version):
            raise CatalogInputError('支持 Swagger 2.0 和 OpenAPI 3.0/3.1')
        budget = ExpansionBudget()
        budget.tree(document)
        if isinstance(raw, dict):
            document = deepcopy(document)
        _consumed_metadata({k: v for k, v in document.items() if k != 'tags'})
        info = document.get('info', {})
        if not isinstance(info, dict) or any(key in info and not isinstance(info[key], str) for key in ('title', 'version', 'description')):
            raise CatalogInputError('契约 info 的名称、版本和描述必须为字符串')
        schemes = document.get('components', {}).get('securitySchemes', document.get('securityDefinitions', {}))
        if not isinstance(schemes, dict) or any(not isinstance(scheme, dict) or any(
                key in scheme and not isinstance(scheme[key], str) for key in ('type', 'scheme', 'name', 'in'))
                for scheme in schemes.values()):
            raise CatalogInputError('契约认证方案必须为对象，类型和名称必须为字符串')
        operations = []
        for path, path_item in document['paths'].items():
            if not path.startswith('/') or len(path) > 1000 or not isinstance(path_item, dict):
                raise CatalogInputError('接口路径无效或过长')
            if '$ref' in path_item:
                path_item = _resolve(path_item, document, [], 'path', budget=budget)
            _consumed_metadata(path_item)
            for method, operation in path_item.items():
                if method.lower() == 'trace':
                    raise CatalogInputError('当前性能场景不支持 TRACE 方法')
                if method.lower() not in METHODS:
                    continue
                if not isinstance(operation, dict) or len(operations) >= MAX_OPERATIONS:
                    raise CatalogInputError('接口操作无效或超过 2000 个')
                operations.append(_operation(document, path, method.lower(), path_item, operation, len(operations), ExpansionBudget(budget)))
        if not operations:
            raise CatalogInputError('契约没有可导入的 HTTP 接口')
        budget.tree(document)
        return {'document': document, 'operations': operations,
                'content_hash': digest({'document': document, 'operation_order': [op['source_key'] for op in operations]}),
                'source_version': str(document.get('info', {}).get('version', ''))[:200]}
    except CatalogInputError:
        raise
    except Exception:
        raise CatalogInputError() from None


def latest_version(project_id: int):
    from ..models import PerfApiCatalogVersion
    return PerfApiCatalogVersion.objects.filter(project_id=project_id).order_by('-version').first()


def version_summary(version) -> dict | None:
    if version is None:
        return None
    return {'id': version.pk, 'version': version.version, 'content_hash': version.content_hash,
        'source_version': version.source_version, 'operation_count': len(version.operations), 'created_at': version.created_at}


def differences(previous: list, current: list) -> dict:
    old = {op['source_key']: op for op in previous}
    new = {op['source_key']: op for op in current}
    return {'added': [key for key in new if key not in old], 'removed': [key for key in old if key not in new],
        'changed': [key for key in new if key in old and new[key]['content_hash'] != old[key]['content_hash']],
        'unchanged': sum(new[key]['content_hash'] == old[key]['content_hash'] for key in new if key in old)}


def preview(project_id: int, parsed: dict, user) -> dict:
    require_project_access(project_id, user)
    previous = latest_version(project_id)
    return {'operation_count': len(parsed['operations']), 'content_hash': parsed['content_hash'],
        'source_version': parsed['source_version'], 'current_version': version_summary(previous),
        'diff': differences(previous.operations if previous else [], parsed['operations']),
        'gap_count': sum(len(op['gaps']) + len(request_readiness(op['request'], op)['gaps']) for op in parsed['operations'])}


def bounded_integer(value: object, name: str, minimum: int = 1) -> int:
    valid = isinstance(value, int) and not isinstance(value, bool)
    if isinstance(value, str):
        valid = 0 < len(value) <= 19 and all('0' <= c <= '9' for c in value)
    if valid:
        try:
            number = int(value)
            if minimum <= number <= MAX_IDENTIFIER:
                return number
        except (ValueError, TypeError, OverflowError):
            pass
    raise CatalogInputError(f'{name} 必须为 {minimum} 到 {MAX_IDENTIFIER} 的整数')


def expected_version(value: object) -> int:
    return bounded_integer(value, 'expected_version（首次为 0）', minimum=0)


@transaction.atomic
def import_document(project_id: int, parsed: dict, expected: object, user) -> dict:
    from apps.api_testing.models import ApiProject, ApiCollection, ApiRequest
    from ..models import PerfProject, PerfApiCatalogVersion
    project = PerfProject.objects.select_for_update().get(pk=project_id)
    require_project_access(project_id, user)
    previous = latest_version(project_id)
    if expected_version(expected) != (previous.version if previous else 0):
        raise CatalogConflict()
    result = preview(project_id, parsed, user)
    identical = bool(previous and previous.content_hash == parsed['content_hash'])
    repaired = False
    if identical:
        existing = dict(ApiRequest.objects.filter(pk__in=[op['id'] for op in previous.operations],
            collection__project_id=project.api_project_id).values_list('pk', 'collection_id')) if project.api_project_id else {}
        repaired = any(existing.get(op['id']) != op['collection'] for op in previous.operations)
        if not repaired:
            return {**result, 'changed': False, 'repaired': False, 'version': version_summary(previous)}
    if project.api_project_id is None:
        api_project = ApiProject.objects.create(name=project.name, description=project.description,
            project_type='HTTP', status=project.status, owner=project.owner)
        project.api_project = api_project
        project.save(update_fields=['api_project'])
    old = {op['source_key']: op for op in previous.operations} if previous else {}
    folders = {f.name: f for f in ApiCollection.objects.filter(project_id=project.api_project_id, parent=None)}
    operations = deepcopy(parsed['operations'])
    for op in operations:
        folder_name = str(op['tags'][0])[:200]
        if folder_name not in folders:
            folders[folder_name] = ApiCollection.objects.create(project_id=project.api_project_id,
                name=folder_name, order=len(folders))
        request = op['request']
        values = {key: deepcopy(request[key]) for key in ('name', 'method', 'url', 'headers', 'params', 'assertions')}
        kind = {'JSON': 'json', 'RAW': 'raw', 'FORM': 'form-data', 'NONE': 'none', 'XML': 'xml', 'BINARY': 'binary'}[request['body_type']]
        values.update(collection=folders[folder_name], order=op['order'], body={'type': kind, 'data': request['body']})
        old_id = old.get(op['source_key'], {}).get('id')
        api = ApiRequest.objects.filter(pk=old_id, collection__project_id=project.api_project_id).first() if old_id else None
        if api and not repaired:
            # Preserve manually maintained assertions and scripts on the original API asset.
            values.pop('assertions')
            for key, val in values.items():
                setattr(api, key, val)
            api.save(update_fields=list(values))
        elif not api:
            api = ApiRequest.objects.create(created_by=user, **values)
        op['id'] = api.pk
        op['collection'] = api.collection_id
    version = PerfApiCatalogVersion.objects.create(project=project, version=(previous.version + 1 if previous else 1),
        content_hash=parsed['content_hash'], source_version=parsed['source_version'],
        document=parsed['document'], operations=operations, created_by=user)
    return {**result, 'changed': True, 'repaired': repaired, 'version': version_summary(version)}


def source_metadata(operation: dict, version) -> dict:
    return {**deepcopy(operation), 'version_id': version.pk, 'version': version.version,
        'catalog_hash': version.content_hash, 'project_id': version.project_id, 'source_version': version.source_version}


def strict_ids(value: object, name: str) -> list:
    if not isinstance(value, list) or not value or len(value) > MAX_BATCH:
        raise CatalogInputError(f'{name} 必须为 1 到 {MAX_BATCH} 个 ID 的数组')
    if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= MAX_IDENTIFIER for v in value) or len(value) != len(set(value)):
        raise CatalogInputError(f'{name} 必须为不重复的正整数')
    return value


def api_request_data(api, operation: dict) -> tuple:
    """Keep saved API body/rules, including the legacy data/content shapes."""
    request = deepcopy(operation['request'])
    issues = []
    request.update({key: deepcopy(getattr(api, key)) for key in ('name', 'method', 'url', 'headers', 'params', 'assertions')})
    body = api.body
    if not isinstance(body, dict):
        request['body_type'] = 'RAW'
        request['body'] = json.dumps(body, ensure_ascii=False)
        issues.append(gap('source_body_format', 'body', '来源请求体不是标准对象，请手动检查转换'))
    else:
        kind = str(body.get('type', 'none')).lower()
        types = {'none': 'NONE', 'json': 'JSON', 'raw': 'RAW', 'xml': 'XML',
                 'form-data': 'FORM', 'x-www-form-urlencoded': 'FORM', 'binary': 'BINARY'}
        content = body.get('data', body.get('content'))
        if kind not in types:
            issues.append(gap('source_body_format', 'body', '来源请求体类型尚未适配，请手动检查转换'))
        request['body_type'] = types.get(kind, 'RAW')
        if kind in ('form-data', 'x-www-form-urlencoded') and isinstance(content, list):
            fields, files = {}, []
            for item in content:
                if not isinstance(item, dict) or not item.get('key'):
                    issues.append(gap('source_body_format', 'body', '来源表单含无效字段，请手动检查转换'))
                    continue
                if item.get('type') == 'file':
                    files.append({'field': item['key'], 'file_id': None, 'filename': '', 'content_type': ''})
                    issues.append(gap('file_required', 'files/' + item['key'], '请上传并选择项目内文件'))
                elif item['key'] in fields:
                    issues.append(gap('parameter_serialization', 'body', '重复表单字段尚未适配'))
                else:
                    fields[item['key']] = item.get('value', '')
            request['files'] = files
            content = fields
        request['body'] = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list, bool, int, float)) else (content if isinstance(content, str) else '')
        if request['body_type'] == 'NONE' and content not in (None, '', {}, []):
            issues.append(gap('source_body_format', 'body', '来源包含正文但未声明请求体类型'))
    if api.auth:
        issues.append(gap('source_auth', 'auth', '来源接口含独立认证设置，请显式配置性能场景认证'))
    for key in ('headers', 'params'):
        if not isinstance(request[key], dict):
            issues.append(gap('source_parameters', key, '来源参数结构需要手动检查转换'))
            request[key] = {}
    if request['body_type'] not in ('NONE', 'JSON', 'FORM', 'BINARY'):
        issues.append(gap('engine_body_type', 'body', '当前 k6 场景执行仅支持 JSON 请求体；来源正文类型待适配'))
    return request, issues


def controlled_source(api, operation: dict, version, previous: dict | None = None) -> tuple:
    """Every entry point freezes the same actual asset and unresolved conversion issues."""
    data, issues = api_request_data(api, operation)
    metadata = source_metadata(operation, version)
    metadata['request'] = deepcopy(data)
    metadata['source_asset_hash'] = digest(data)
    metadata['source_asset'] = {'body': deepcopy(api.body), 'headers': deepcopy(api.headers),
        'params': deepcopy(api.params), 'auth': deepcopy(api.auth), 'assertions': deepcopy(api.assertions)}
    if api.pre_request_script or api.post_request_script:
        issues.append(gap('source_scripts', 'scripts', '来源接口含脚本，请在场景中重建并审查提取与前置逻辑'))
        metadata['source_scripts'] = {'pre': api.pre_request_script, 'post': api.post_request_script}
    previous = previous or {}
    carried = previous.get('source_issues', [g for g in previous.get('gaps', []) if g['code'].startswith('source_')])
    metadata['source_issues'] = list({(g['code'], g['field']): deepcopy(g) for g in [*issues, *carried]}.values())
    metadata['gaps'].extend(deepcopy(metadata['source_issues']))
    if carried:
        metadata['prior_source_asset'] = deepcopy(previous.get('prior_source_asset') or previous.get('source_asset', {}))
        if previous.get('source_scripts') and 'source_scripts' not in metadata:
            metadata['source_scripts'] = deepcopy(previous['source_scripts'])
    return data, metadata


def allow_legacy_edit(instance, scene, source, user) -> bool:
    if not (instance and instance.pk and not instance.source_metadata and instance.source_request_id
            and source and source.pk == instance.source_request_id and scene.pk == instance.scenario_id):
        return False
    from .environments import is_environment_admin
    if user and is_environment_admin(user):
        return True
    if not user or not user.is_authenticated:
        return False
    if source.collection_id:
        project = source.collection.project
        return project.owner_id == user.pk or project.members.filter(pk=user.pk).exists()
    return source.created_by_id == user.pk


def validate_source(project_id: int, api, metadata: dict | None = None) -> None:
    from ..models import PerfProject
    project = PerfProject.objects.get(pk=project_id)
    if api and (not project.api_project_id or not api.collection_id
                or api.collection.project_id != project.api_project_id):
        raise CatalogInputError('接口来源不存在或不属于当前压测项目的接口库')
    if metadata and metadata.get('project_id') != project_id:
        raise CatalogInputError('场景仍引用原项目接口库，请先移除相关步骤')


@transaction.atomic
def import_steps(scenario, ids: object, user, *, as_setup: bool = False) -> list:
    from .prepared_requests import import_defaults
    from .environments import resolve_environment
    from apps.api_testing.models import ApiRequest
    from ..models import PerfScenario, PerfScenarioStep
    scene = PerfScenario.objects.select_for_update().get(pk=scenario.pk)
    require_project_access(scene.project_id, user)
    ids = strict_ids(ids, 'request_ids')
    version = latest_version(scene.project_id)
    lookup = {op['id']: op for op in version.operations} if version else {}
    if any(pk not in lookup for pk in ids):
        raise CatalogInputError('接口不存在、已从最新版本移除或不属于当前项目')
    assets = {a.pk: a for a in ApiRequest.objects.filter(pk__in=ids).select_related('collection')}
    created = []
    inherited_headers = resolve_environment(scene, user=user)['env_config'].get('headers', {})
    maximum = scene.steps.aggregate(value=Max('order'))['value']
    for offset, pk in enumerate(ids):
        api = assets.get(pk)
        if api is None:
            raise CatalogInputError('来源接口已删除，请重新导入契约')
        validate_source(scene.project_id, api)
        op = lookup[pk]
        data, metadata = controlled_source(api, op, version)
        data = import_defaults(data, op, version.document, scene.runtime_config, inherited_headers)
        created.append(PerfScenarioStep.objects.create(scenario=scene, source_request=api,
            order=(maximum + 1 if maximum is not None else 0) + offset, is_setup=as_setup,
            source_metadata=metadata, **data))
    return created


def _field_value(request: dict, meta: dict, field: str, headers: dict) -> object:
    location, _, name = field.partition('/')
    if location == 'query':
        return (request.get('params') or {}).get(name)
    if location == 'header':
        return headers.get(name.lower())
    if location == 'path':
        path = meta.get('request_path', meta.get('path', ''))
        names = re.findall(r'\{([^{}]+)\}', path)
        pattern = re.escape(path)
        for key in names:
            pattern = pattern.replace(re.escape('{' + key + '}'), '([^/?]+)')
        url = request.get('url', '')
        match = re.search(pattern + r'(?:\?|$)', url)
        return dict(zip(names, match.groups())).get(name) if match else None
    if location == 'body':
        raw = request.get('body', '')
        if not name:
            if request.get('body_type') == 'JSON':
                try:
                    return json.loads(raw)
                except (TypeError, ValueError):
                    return None
            return raw
        try:
            value = json.loads(raw)
            for part in name.split('/'):
                key = part.replace('~1', '/').replace('~0', '~')
                value = value[int(key)] if isinstance(value, list) else value[key]
            return value
        except (TypeError, ValueError, KeyError, IndexError):
            return None
    return None


def _configured(value: object, known: set) -> bool:
    if value is None or value == '' or value == '******':
        return False
    refs = re.findall(r'\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+)\}', str(value))
    return all((a or b).strip() in known for a, b in refs)


def _union_gaps(schema: dict, value: object, field: str = 'body', depth: int = 0,
                confirmed: list | None = None) -> list:
    """Validate each union independently; oneOf must match exactly one branch."""
    gaps = []
    for keyword in ('oneOf', 'anyOf'):
        if keyword not in schema:
            continue
        branches = schema[keyword]
        if not isinstance(branches, list) or not branches:
            gaps.append(gap('schema_unsupported', field, '联合模型必须声明非空的候选 schema'))
            continue
        matching = []
        for branch in branches:
            issues = _required_body_gaps(branch, value, field, depth + 1, confirmed)
            gaps.extend(issue for issue in issues if issue['code'] in ('schema_unsupported', 'recursive_ref'))
            if all(issue['code'] == 'confirm_example' for issue in issues):
                matching.append(issues)
        if not matching or (keyword == 'oneOf' and len(matching) != 1):
            gaps.append(gap('schema_choice', field, '请求体必须匹配 oneOf 的唯一分支或 anyOf 的至少一个分支'))
        else:
            gaps.extend(issue for issues in matching for issue in issues)
    return gaps


def _scalar_schema_gaps(schema: dict, value: object, field: str) -> list:
    # The JSON Schema validator handles JSON type/equality semantics (bool != int).
    # Child objects and unions are traversed separately to retain resource provenance.
    keys = ('type', 'const', 'enum', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
            'multipleOf', 'minLength', 'maxLength', 'pattern', 'minItems', 'maxItems', 'uniqueItems',
            'minProperties', 'maxProperties')
    local = {key: schema[key] for key in keys if key in schema}
    if schema.get('nullable') is True and isinstance(local.get('type'), str):
        local['type'] = [local['type'], 'null']
    if isinstance(value, str) and re.search(r'\{\{\s*[^{}]+?\s*\}\}|\$\{[^{}]+\}', value):
        # Interpolated strings cannot be checked against literal string constraints.
        for key in ('minLength', 'maxLength', 'pattern'):
            local.pop(key, None)
    try:
        Draft202012Validator.check_schema(local)
        if isinstance(value, str) and re.fullmatch(r'\{\{\s*[^{}]+?\s*\}\}|\$\{[^{}]+\}', value):
            # k6 renderTree preserves the native extracted value for a full template.
            # Its mapping is required separately; type/value remain runtime checks.
            return []
        if not Draft202012Validator(local).is_valid(value):
            return [gap('body_structure', field, '请求体字段不满足契约类型、取值或数量约束')]
    except (SchemaError, TypeError, ValueError, re.error):
        return [gap('schema_unsupported', field, '请求体字段的 schema 约束无效或尚未支持')]
    return []


def _required_body_gaps(schema: object, value: object, field: str = 'body', depth: int = 0,
                        confirmed: list | None = None) -> list:
    """Required object structure is independent of sample generation."""
    if depth > 60 or not isinstance(schema, dict) or (not schema and field != 'body'):
        return [gap('schema_unsupported', field, '数组元素或输入模型的 schema 尚未支持')]
    if schema.get('readOnly'):
        return []
    confirmed = confirmed or []
    gaps = _scalar_schema_gaps(schema, value, field)
    if field != 'body' and not any(key in schema for key in ('type', 'const', 'enum', 'properties', 'required', 'additionalProperties', 'not', 'allOf', 'oneOf', 'anyOf', '$ref', 'x-perf-recursive', 'pattern', 'minLength', 'maxLength', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minItems', 'maxItems', 'uniqueItems', 'minProperties', 'maxProperties')):
        gaps.append(gap('schema_unsupported', field, '数组元素或输入模型缺少可支持的 schema 声明'))
    if schema.get('x-perf-recursive') or '$ref' in schema:
        gaps.append(gap('recursive_ref', field, '当前转换不支持递归输入模型'))
    templated = isinstance(value, str) and re.search(r'\{\{\s*[^{}]+?\s*\}\}|\$\{[^{}]+\}', value)
    if templated and not any(key in schema for key in ('oneOf', 'anyOf', 'allOf')):
        return gaps
    if 'oneOf' in schema or 'anyOf' in schema:
        gaps.extend(_union_gaps(schema, value, field, depth, confirmed))
    if 'not' in schema:
        excluded = _required_body_gaps(schema['not'], value, field, depth + 1, confirmed)
        if any(issue['code'] in ('schema_unsupported', 'recursive_ref') for issue in excluded):
            gaps.append(gap('schema_unsupported', field, '排除条件含未支持的 schema'))
        elif all(issue['code'] == 'confirm_example' for issue in excluded):
            gaps.append(gap('body_structure', field, '请求体命中契约禁止的字段或模型组合'))
    suggested, provenance = _example(schema)
    resource = bool(re.search(r'(^|[/_.-])(id|.*_id|token|password|username|authorization)$', field, re.I))
    if (provenance == 'example' or resource and provenance == 'default') and value == suggested and field not in confirmed:
        gaps.append(gap('confirm_example', field, '契约示例或资源默认值需要确认真实可用后再执行'))
    for child in schema.get('allOf', []):
        gaps.extend(_required_body_gaps(child, value, field, depth + 1, confirmed))
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        for name in required:
            if isinstance(properties.get(name), dict) and properties[name].get('readOnly'):
                continue
            if name not in value:
                gaps.append(gap('required_value', field + '/' + name.replace('~', '~0').replace('/', '~1'), '请求体必填字段未准备'))
        for name, child in properties.items():
            if name in value:
                if isinstance(suggested, dict) and name in suggested and isinstance(child, dict):
                    child = {**child, provenance: suggested[name]}
                gaps.extend(_required_body_gaps(child, value[name], field + '/' + name.replace('~', '~0').replace('/', '~1'), depth + 1, confirmed))
        extra = set(value) - set(properties)
        additional = schema.get('additionalProperties', True)
        if extra and additional is False:
            gaps.append(gap('body_structure', field, '请求体含契约不允许的额外字段'))
        elif isinstance(additional, dict):
            for name in extra:
                gaps.extend(_required_body_gaps(additional, value[name], field + '/' + name.replace('~', '~0').replace('/', '~1'), depth + 1, confirmed))
    elif isinstance(value, list) and (schema.get('type') == 'array' or 'items' in schema):
        for index, item in enumerate(value[:2000]):
            child = schema.get('items', {})
            if isinstance(suggested, list) and index < len(suggested) and isinstance(child, dict):
                child = {**child, provenance: suggested[index]}
            gaps.extend(_required_body_gaps(child, item, field + '/' + str(index), depth + 1, confirmed))
        if len(value) > 2000:
            gaps.append(gap('body_structure', field, '请求体数组超过准备检查上限 2000 项'))
    return gaps


def media_type(value: object) -> str:
    return str(value or '').split(';', 1)[0].strip().lower()


def request_readiness(request: dict, metadata: dict, preparation: dict | None = None,
                      variables: list | None = None, environment_headers: dict | None = None,
                      *, project_id: int | None = None) -> dict:
    if request.get('protocol') == 'WEBSOCKET':
        from .websocket_steps import catalog_websocket_matches, normalize_websocket_config
        from .auth_profiles import request_headers
        gaps = []
        if metadata and not catalog_websocket_matches({**request, 'source_metadata': metadata}):
            gaps.append(gap('websocket_source', 'protocol', '来源须明确声明 WebSocket GET 升级并保持原地址'))
        try:
            normalize_websocket_config(request.get('websocket_config'))
        except ValidationError:
            gaps.append(gap('websocket_config', 'websocket_config', 'WebSocket 业务帧合同尚未就绪'))
            return {'ready': False, 'gaps': gaps}
        # Validate the actual handshake inputs against the frozen HTTP upgrade
        # contract. The engine supplies PUSH authentication, not a saved header.
        headers = request_headers(request, environment_headers or {})
        config = request.get('websocket_config') or {}
        if config.get('mode') == 'PUSH' and isinstance(config.get('auth'), dict):
            headers['Authorization'] = 'Bearer ' + str(config['auth'].get('token', ''))
        handshake = {**request, 'protocol': 'HTTP', 'headers': headers}
        gaps.extend(request_readiness(handshake, metadata, preparation, variables, {}, project_id=project_id)['gaps'])
        return {'ready': not gaps, 'gaps': gaps}
    gaps = [gap('request_container', field, '请求字段必须为对象，请修正后再执行')
        for field in ('headers', 'params') if not isinstance(request.get(field, {}), dict)]
    if request.get('protocol') == 'SSE':
        from .sse_steps import normalize_sse_config
        try:
            normalize_sse_config(request.get('sse_config'))
        except ValidationError:
            gaps.append(gap('sse_config', 'sse_config', 'SSE 事件合同尚未就绪'))
    request = {**request, **{field: {} for field in ('headers', 'params')
        if not isinstance(request.get(field, {}), dict)}}
    preparation = preparation or {}
    request_headers = request.get('headers') if isinstance(request.get('headers'), dict) else {}
    headers = {k.lower(): v for source in (environment_headers or {}, request_headers) for k, v in source.items()}
    from . import upload_files
    file_errors = upload_files.project_errors(request.get('files') or [], project_id)
    gaps.extend(gap('file_required', 'files', message) for message in file_errors)
    encoding_errors = upload_files.request_errors(request, headers)
    gaps.extend(gap('body_encoding', 'body', message) for message in encoding_errors)
    if not metadata:
        return {'ready': not gaps, 'gaps': gaps}
    known = {v['name'] for v in variables or [] if isinstance(v, dict) and v.get('name')
        and (v.get('type') == 'CSV' or v.get('value') not in (None, '', '******'))}
    known |= set(preparation.get('_known_extractors', [])) | {'vu_id', 'iteration', 'base_url', 'baseUrl', 'request_id'}
    def check_variables(value, field):
        if isinstance(value, dict):
            for key, item in value.items():
                check_variables(item, field + '/' + str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                check_variables(item, field + '/' + str(index))
        elif isinstance(value, str):
            refs = re.findall(r'\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+)\}', value)
            if any((a or b).strip() not in known for a, b in refs):
                gaps.append(gap('variable_mapping', field, '请求引用了尚未定义或尚未提取的变量'))
    for field in ('url', 'params', 'body'):
        check_variables(request.get(field), field)
    check_variables(headers, 'headers')
    confirmed = preparation.get('confirmed_fields', [])
    contents = metadata.get('request_body', {}).get('content', {})
    normalized_contents = {media_type(key): value for key, value in contents.items()}
    content_type = media_type(headers.get('content-type', next(iter(contents), '')))
    body_type = request.get('body_type', 'NONE')
    if body_type == 'NONE' and (request.get('body') or request.get('files')):
        gaps.append(gap('body_encoding', 'body', 'NONE 类型不会发送已填写的正文，请选择正确的请求体类型'))
    if metadata.get('request_body', {}).get('required') and body_type == 'NONE':
        gaps.append(gap('required_value', 'body', '契约要求发送请求体'))
    omitted_optional_body = (not metadata.get('request_body', {}).get('required')
        and body_type == 'NONE' and not request.get('body') and not request.get('files'))
    if not omitted_optional_body and contents and content_type not in normalized_contents:
        gaps.append(gap('content_type', 'headers/Content-Type', 'Content-Type 不属于当前接口契约，请选择已声明的媒体类型'))
    selected = normalized_contents.get(content_type)
    is_json_media = content_type == 'application/json' or content_type.endswith('+json')
    if not omitted_optional_body and contents and is_json_media and body_type != 'JSON':
        gaps.append(gap('body_encoding', 'body', '契约 JSON 正文需要选择 JSON 请求体类型'))
    checked_body_schema = False
    if not omitted_optional_body and body_type == 'JSON':
        if contents and not is_json_media:
            gaps.append(gap('body_encoding', 'body', 'JSON 请求体与所选 Content-Type 不匹配'))
        try:
            body = json.loads(request.get('body', ''))
            if selected is not None:
                gaps.extend(_required_body_gaps(selected.get('schema', {}), body, confirmed=confirmed))
                checked_body_schema = True
        except (ValueError, TypeError):
            gaps.append(gap('body_structure', 'body', '请求体必须为有效 JSON'))
    if not omitted_optional_body and body_type == 'FORM':
        if contents and content_type not in ('multipart/form-data', 'application/x-www-form-urlencoded'):
            gaps.append(gap('body_encoding', 'body', 'FORM 正文与所选 Content-Type 不匹配'))
        if selected is not None and not encoding_errors:
            body = upload_files.form_fields(request.get('body'), request.get('files') or [])
            schema = deepcopy(selected.get('schema', {}))
            for name, prop in schema.get('properties', {}).items():
                if prop.get('type') == 'file' or prop.get('format') in ('binary', 'byte'):
                    if any(item.get('field') == name and item.get('file_id') for item in request.get('files') or []):
                        body[name] = 'project-upload'
                    schema['properties'][name] = {'type': 'string'}
            gaps.extend(_required_body_gaps(schema, body, confirmed=confirmed))
            checked_body_schema = True
    if not omitted_optional_body and body_type == 'BINARY' and selected is not None:
        schema = selected.get('schema', {})
        if schema.get('format') != 'binary' and content_type != 'application/octet-stream':
            gaps.append(gap('body_encoding', 'body', '当前契约未声明原始文件请求体'))
    for requirement in metadata.get('requirements', []):
        field = requirement['field']
        if omitted_optional_body and field.startswith('body'):
            continue
        if requirement.get('kind') == 'file':
            continue
        value = _field_value(request, metadata, field, headers)
        if checked_body_schema and (field == 'body' or field.startswith('body/')) and value in (None, ''):
            # Presence, nullability and empty-string constraints were checked against
            # the selected schema; generated placeholders cannot override that result.
            continue
        if not requirement.get('required', True) and value is None:
            continue
        if not _configured(value, known):
            gaps.append(gap('required_value', field, '必填值未准备，或变量尚未映射'))
        elif requirement.get('provenance') == 'example' or requirement.get('resource'):
            suggested = requirement.get('suggested')
            matches = value == suggested or (not isinstance(value, (dict, list)) and str(value) == str(suggested))
            if matches and field not in confirmed:
                gaps.append(gap('confirm_example', field, '契约示例或资源默认值需要确认真实可用后再执行'))
    for issue in metadata.get('gaps', []):
        code, field = issue['code'], issue['field']
        source_issue = code.startswith('source_') or issue in metadata.get('source_issues', [])
        if omitted_optional_body and not source_issue and (field == 'body' or field.startswith(('body/', 'files/'))):
            continue
        if code == 'engine_body_type' and not encoding_errors:
            continue
        if code == 'binary_body' and body_type == 'BINARY' and not encoding_errors:
            continue
        if code == 'file_required':
            if any(item.get('field') == field[6:] and item.get('file_id') for item in request.get('files', [])):
                continue
        elif code == 'authentication':
            schemes = metadata.get('security_schemes', {})
            def satisfied(requirement):
                for name in requirement:
                    scheme = schemes.get(name, {})
                    kind = scheme.get('type')
                    if kind in ('http', 'basic', 'oauth2', 'openIdConnect'):
                        val = headers.get('authorization')
                        auth_scheme = scheme.get('scheme', 'basic' if kind == 'basic' else 'bearer')
                        if auth_scheme not in ('bearer', 'basic') or not isinstance(val, str):
                            return False
                        prefix, _, credential = val.partition(' ')
                        if prefix.lower() != auth_scheme or not credential.strip():
                            return False
                    elif kind == 'apiKey' and scheme.get('in') == 'header':
                        val = headers.get(str(scheme.get('name')).lower())
                    elif kind == 'apiKey' and scheme.get('in') == 'query':
                        val = (request.get('params') or {}).get(scheme.get('name'))
                    elif kind == 'apiKey' and scheme.get('in') == 'cookie':
                        val = (preparation.get('_auth_cookies') or {}).get(scheme.get('name'))
                    else:
                        return False
                    if not _configured(val, known):
                        return False
                return True
            if any(satisfied(req) for req in metadata.get('security', [])):
                continue
        elif code == 'content_type_choice':
            if preparation.get('body_reviewed') and _configured(request.get('body'), known):
                if content_type in normalized_contents:
                    if is_json_media and body_type == 'JSON':
                        continue
        elif code == 'server_path_choice':
            if 'server' in confirmed and request.get('url'):
                continue
        elif code == 'schema_choice' and (field == 'body' or (isinstance(field, str) and field.startswith('body/'))):
            # Sample-time gaps use wildcard paths and may refer to omitted optional
            # fields. Actual body traversal already checked each present union.
            if checked_body_schema and not source_issue:
                continue
        gaps.append(deepcopy(issue))
    unique = {(g['code'], g['field']): g for g in gaps}
    return {'ready': not unique, 'gaps': list(unique.values())}


def scenario_readiness(scenario, resolved: dict, *, load_config: dict | None = None) -> list:
    result, extracted = [], []
    effective_load = load_config if load_config is not None else (
        scenario.get_load_config() if hasattr(scenario, 'get_load_config')
        else getattr(scenario, 'load_config', {'concurrency': 1}))
    from .auth_profiles import normalize_profile, profile_steps, validate_binding, request_input_issues
    auth_issues = []
    try:
        profile = normalize_profile((scenario.runtime_config or {}).get('auth_profile'))
        if profile:
            auth_snapshot = {**resolved, 'steps': [], 'runtime_config': scenario.runtime_config,
                'load_config': effective_load}
            auth_issues = request_input_issues(profile, auth_snapshot)
            input_messages = {issue['message'] for issue in auth_issues}
            auth_issues.extend(gap('auth_binding', 'auth_profile', message) for message in
                validate_binding(profile, auth_snapshot) if message not in input_messages)
            if auth_issues:
                profile = {}
    except ValidationError:
        profile = {}
        auth_issues = [gap('auth_profile', 'auth_profile', '每用户认证配置无效，请检查认证字段')]
    if profile:
        extracted.extend(rule['name'] for step in profile_steps(profile)
            if step['auth_phase'] == 'login' for rule in step['extractors'])
    from . import websocket_steps, auth_profiles
    ws_snapshot = {**resolved, 'runtime_config': scenario.runtime_config, 'engine': getattr(scenario, 'engine', 'K6'),
                   'load_config': effective_load}
    known = set(extracted) | auth_profiles.RESERVED | {item.get('name') for item in resolved.get('variables') or []}
    protected = auth_profiles.stable_identity_names(ws_snapshot)
    steps = list(scenario.steps.select_related('source_request__collection').order_by('order', 'id'))
    from . import reminder_recovery
    recovery_config, recovery_error = {}, ''
    if (scenario.runtime_config or {}).get('resource_recovery'):
        try:
            recovery_steps = [{**{key: getattr(step, key) for key in REQUEST_FIELDS},
                'id': step.pk, 'protocol': step.protocol, 'enabled': step.enabled,
                'is_setup': step.is_setup, 'execution_policy': step.execution_policy} for step in steps]
            recovery_config, _, _, _ = reminder_recovery._binding({**ws_snapshot, 'steps': recovery_steps})
        except (reminder_recovery.RecoveryError, KeyError, TypeError, ValueError):
            recovery_error = '提醒恢复组绑定无效，请检查四步顺序、身份及一次执行策略'
    steps = [step for step in steps if step.is_setup] + [step for step in steps if not step.is_setup]
    for step in steps:
        if not step.enabled:
            continue
        protocol = getattr(step, 'protocol', 'HTTP')
        sse_errors = []
        if protocol == 'SSE':
            from .sse_steps import validate_sse_step
            before = set(known)
            request = {field: getattr(step, field) for field in REQUEST_FIELDS}
            request.update(protocol=protocol, sse_config=step.sse_config, source_metadata=step.source_metadata,
                           source_request_id=step.source_request_id)
            sse_errors = validate_sse_step(request, ws_snapshot, known, protected)
            extracted.extend(known - before)
        if protocol not in ('HTTP', 'SSE'):
            if protocol == 'WEBSOCKET':
                validate_source(scenario.project_id, step.source_request, step.source_metadata)
                before = set(known)
                request = {field: getattr(step, field) for field in REQUEST_FIELDS}
                request.update(protocol=protocol, websocket_config=step.websocket_config,
                    source_request_id=step.source_request_id, source_metadata=step.source_metadata)
                errors = websocket_steps.validate_websocket_step(request, ws_snapshot, known, protected)
                extracted.extend(known - before)
            else:
                errors = ['步骤协议仅支持 HTTP 或 WEBSOCKET']
            gaps = [gap('websocket_config', 'websocket_config', message) for message in errors]
            if protocol == 'WEBSOCKET':
                gaps.extend(request_readiness(request, step.source_metadata,
                    {**step.preparation, '_known_extractors': extracted}, resolved.get('variables'),
                    resolved.get('env_config', {}).get('headers'), project_id=scenario.project_id)['gaps'])
            gaps.extend(deepcopy(auth_issues))
            result.append({'step_id': step.pk, 'ready': not gaps, 'gaps': gaps})
            continue
        if not allow_legacy_edit(step, scenario, step.source_request, scenario.created_by):
            validate_source(scenario.project_id, step.source_request, step.source_metadata)
        request = {field: getattr(step, field) for field in REQUEST_FIELDS}
        if protocol == 'SSE':
            request.update(protocol=protocol, sse_config=step.sse_config)
        environment_headers = resolved.get('env_config', {}).get('headers')
        if profile:
            environment_headers = {key: value for key, value in (environment_headers or {}).items()
                                   if key.lower() not in ('authorization', 'cookie')}
            request['headers'] = {key: value for key, value in (request.get('headers') or {}).items()
                                  if key.lower() not in ('authorization', 'cookie')}
        if profile and profile['transport'] == 'BEARER':
            request['headers']['Authorization'] = 'Bearer {{' + profile['access_token_variable'] + '}}'
        preparation = {**step.preparation, '_known_extractors': extracted, '_auth_cookies': {}}
        if recovery_config and step.pk == recovery_config['receipt_step_id']:
            preparation['_known_extractors'] = [*extracted, 'rr_put_digest']
        if recovery_config and step.pk == recovery_config['delete_step_id']:
            request = reminder_recovery.project_cleanup_request(request)
        if profile and profile['transport'] == 'COOKIE':
            # Only a validated LOGIN template or configured per-account STATIC variable satisfies this.
            if profile['mode'] == 'LOGIN':
                preparation['_auth_cookies'] = {profile['cookie_name']: '{{vu_id}}'}
            else:
                preparation['_auth_cookies'] = {profile['cookie_name']: '{{' + profile['cookie_variable'] + '}}'}
        readiness = request_readiness(request, step.source_metadata, preparation,
            resolved.get('variables'), environment_headers, project_id=getattr(scenario, 'project_id', None))
        if recovery_error:
            readiness['gaps'].append(gap('resource_recovery', 'resource_recovery', recovery_error))
            readiness['ready'] = False
        if sse_errors:
            readiness['gaps'].extend(gap('sse_config', 'sse_config', message) for message in sse_errors)
            readiness['ready'] = False
        if auth_issues:
            readiness['gaps'].extend(deepcopy(auth_issues))
            readiness['ready'] = False
        result.append({'step_id': step.pk, **readiness})
        extracted.extend(item.get('name') for item in step.extractors if item.get('name'))
        known.update(extracted)
    return result


def scene_differences(scenario) -> list:
    latest = latest_version(scenario.project_id)
    lookup = {op['source_key']: op for op in latest.operations} if latest else {}
    result = []
    for step in scenario.steps.all().order_by('order', 'id'):
        old = step.source_metadata
        if not old:
            continue
        new = lookup.get(old.get('source_key'))
        fields = []
        if new:
            for field in REQUEST_FIELDS:
                before, after = old['request'].get(field), new['request'].get(field)
                if before != after:
                    fields.append({'field': field, 'before': before, 'after': after,
                        'customized': getattr(step, field) != before})
        result.append({'step_id': step.pk, 'source_key': old.get('source_key'), 'from_version': old.get('version'),
            'to_version': latest.version if latest else None, 'removed': new is None,
            'changed': new is None or new['content_hash'] != old.get('content_hash'), 'fields': fields})
    return result


def _merge_custom(current: object, old: object, new: object) -> object:
    if current == old:
        return deepcopy(new)
    if isinstance(current, dict) and isinstance(old, dict) and isinstance(new, dict):
        result = deepcopy(current)
        for key in set(old) | set(new):
            if key not in old and key not in current:
                result[key] = deepcopy(new[key])
            elif key in current and key in old and current[key] == old[key]:
                if key in new:
                    result[key] = deepcopy(new[key])
                else:
                    result.pop(key)
        return result
    return deepcopy(current)


@transaction.atomic
def update_steps(scenario, ids: object, expected: object, user) -> int:
    from apps.api_testing.models import ApiRequest
    from ..models import PerfScenario, PerfProject
    scene = PerfScenario.objects.select_for_update().get(pk=scenario.pk)
    PerfProject.objects.select_for_update().get(pk=scene.project_id)
    require_project_access(scene.project_id, user)
    expected = expected_version(expected)
    version = latest_version(scene.project_id)
    if not version or version.version != expected:
        raise CatalogConflict()
    ids = strict_ids(ids, 'step_ids')
    steps = {s.pk: s for s in scene.steps.select_for_update().filter(pk__in=ids)}
    if len(steps) != len(ids):
        raise CatalogInputError('步骤不存在或不属于当前场景')
    operations = {op['source_key']: op for op in version.operations}
    for pk in ids:
        step = steps[pk]
        old = step.source_metadata
        new = operations.get(old.get('source_key'))
        if not new:
            raise CatalogInputError('来源接口已移除或没有受控来源，无法更新')
        validate_source(scene.project_id, step.source_request, old)
        source = ApiRequest.objects.select_related('collection').filter(pk=new['id']).first()
        if source is None:
            raise CatalogInputError('来源接口已删除，请重新导入契约')
        validate_source(scene.project_id, source)
        data, metadata = controlled_source(source, new, version, old)
        for field in REQUEST_FIELDS:
            if field in ('assertions', 'extractors'):
                continue
            setattr(step, field, _merge_custom(getattr(step, field), old['request'].get(field), data.get(field)))
        step.source_metadata = metadata
        step.source_request = source
        step.preparation = {}
        step.save()
    return len(steps)
