"""Strict imports, private immutable versions and explicit VU account bindings."""
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import uuid

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from .environments import require_project_access

MAX_BYTES = 20 * 1024 * 1024
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')
RESERVED = {'vu_id', 'iteration', 'base_url', 'baseUrl', 'request_id', '__proto__', 'prototype', 'constructor'}


def invalid(code: str, row: int = 0, field: str | int = 'file') -> None:
    raise ValidationError({'errors': [{'row': row, 'field': field, 'code': code,
                                      'message': '账号池数据无效，请按行号和字段修正后重新导入'}]})


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hmac.new(settings.SECRET_KEY.encode(), encoded, hashlib.sha256).hexdigest()


def validate_csv_quotes(text: str) -> None:
    state, line = 'start', 1
    for char in text:
        if state == 'quoted':
            if char == '"':
                state = 'closed'
        elif state == 'closed' and char == '"':
            state = 'quoted'
        elif char in ',\r\n':
            state = 'start'
        elif state == 'closed' or (state == 'unquoted' and char == '"'):
            invalid('invalid_csv_quote', line)
        elif char == '"':
            state = 'quoted'
        else:
            state = 'unquoted'
        if char == '\n':
            line += 1
    if state == 'quoted':
        invalid('unclosed_csv_quote', line)


def parse_accounts(raw: bytes, file_format: str, identity_column: str,
                   field_mapping: dict, group_column: str = '', *, inspect: bool = False) -> dict:
    if not isinstance(identity_column, str) or not isinstance(group_column, str):
        invalid('invalid_column_selection', field='identity_column_or_group_column')
    if not raw or len(raw) > MAX_BYTES:
        invalid('empty_or_too_large')
    try:
        text = raw.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError:
        invalid('invalid_utf8')
    rows, line_numbers = [], []
    if file_format == 'csv':
        validate_csv_quotes(text)
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        try:
            columns = next(reader)
            if len(columns) != len(set(columns)):
                invalid('duplicate_header', 1)
            for values in reader:
                line = reader.line_num
                if not values or not any(value.strip() for value in values):
                    invalid('empty_row', line)
                if len(values) != len(columns):
                    invalid('irregular_row', line)
                if values == columns:
                    invalid('repeated_header_row', line)
                rows.append(dict(zip(columns, values)))
                line_numbers.append(line)
        except (csv.Error, StopIteration):
            invalid('invalid_csv', reader.line_num)
    elif file_format == 'json':
        def pairs(items: list) -> dict:
            result = {}
            for key, value in items:
                if key in result:
                    invalid('duplicate_key')
                result[key] = value
            return result
        try:
            rows = json.loads(text, object_pairs_hook=pairs,
                              parse_constant=lambda _: invalid('invalid_number'))
        except (ValueError, RecursionError):
            invalid('invalid_json')
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            invalid('expected_nonempty_object_array')
        columns = list(rows[0])
        line_numbers = list(range(1, len(rows) + 1))
    else:
        invalid('format_must_be_csv_or_json')
    if not rows:
        invalid('no_accounts')
    if not columns or any(not column.strip() or column != column.strip() or len(column) > 128
                          or any(ord(char) < 32 for char in column) or column in RESERVED for column in columns):
        invalid('invalid_header', 1)
    if not inspect and identity_column not in columns:
        invalid('missing_identity_column', field='identity_column')
    if not isinstance(field_mapping, dict) or (not inspect and not field_mapping):
        invalid('mapping_required', field='field_mapping')
    if any(not isinstance(name, str) or not NAME.fullmatch(name) or name in RESERVED
           or not isinstance(column, str) or column not in columns for name, column in field_mapping.items()):
        invalid('invalid_mapping', field='field_mapping')
    if len(set(field_mapping.values())) != len(field_mapping):
        invalid('duplicate_mapping', field='field_mapping')
    if group_column and group_column not in columns:
        invalid('missing_group_column', field='group_column')
    identities = set()
    for row, line in zip(rows, line_numbers):
        if not isinstance(row, dict) or set(row) != set(columns):
            invalid('missing_or_extra_columns', line)
        for index, column in enumerate(columns, 1):
            value = row[column]
            if isinstance(value, bool) or not isinstance(value, (str, int, float)) or (isinstance(value, float) and not math.isfinite(value)):
                invalid('invalid_value', line, index)
            value = str(value)
            if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
                invalid('empty_or_control_value', line, index)
            row[column] = value
        if not inspect:
            identity = row[identity_column].strip()
            if identity in identities:
                invalid('duplicate_identity', line, columns.index(identity_column) + 1)
            identities.add(identity)
    groups = []
    if group_column:
        counts = Counter(row[group_column] for row in rows)
        groups = [{'id': fingerprint(value), 'label': f'分组 {index}', 'count': count}
                  for index, (value, count) in enumerate(counts.items(), 1)]
    return {'rows': rows, 'columns': columns, 'identity_column': identity_column,
            'field_mapping': field_mapping, 'group_column': group_column, 'groups': groups}


def version_path(version) -> Path:
    from .executor import _private_root
    root = _private_root() / 'account-pools'
    path = (root / version.private_file).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValidationError('账号池私密文件位置无效')
    return path


def read_version(version) -> dict:
    try:
        payload = json.loads(version_path(version).read_text(encoding='utf-8'))
        if fingerprint(payload) != version.content_hash:
            raise ValueError
        return payload
    except (OSError, ValueError, TypeError, RecursionError):
        raise ValidationError('账号池版本缺失或已损坏，请重新导入新版本') from None


def import_version(*, project_id: int, name: str, upload, identity_column: str,
                   field_mapping: dict, user, group_column: str = '', pool=None):
    from ..models import PerfAccountPoolVersion, PerfDataFile, PerfProject
    require_project_access(project_id, user)
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        invalid('invalid_name', field='name')
    if upload is None:
        invalid('file_required')
    if upload.size > MAX_BYTES:
        invalid('too_large')
    parsed = parse_accounts(upload.read(MAX_BYTES + 1), Path(upload.name).suffix.lower().lstrip('.'),
                            identity_column, field_mapping, group_column)
    path, created = None, False
    try:
        with transaction.atomic():
            PerfProject.objects.select_for_update().get(pk=project_id)
            if pool is None:
                pool = PerfDataFile.objects.create(project_id=project_id, name=name.strip(), file_type='ACCOUNT',
                                                   file='', uploaded_by=user)
            else:
                pool = PerfDataFile.objects.select_for_update().get(pk=pool.pk, project_id=project_id, file_type='ACCOUNT')
            last = pool.account_versions.order_by('-version').first()
            version = PerfAccountPoolVersion(pool=pool, version=(last.version + 1 if last else 1),
                private_file=uuid.uuid4().hex + '.json', columns=parsed['columns'],
                identity_column=identity_column, field_mapping=field_mapping, group_column=group_column,
                groups=parsed['groups'], row_count=len(parsed['rows']), content_hash=fingerprint(parsed), created_by=user)
            path = version_path(version)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(parsed, output, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            version.save()
            return version
    except Exception:
        if created and path and path.exists():
            path.unlink()
        raise


def metadata(version, group: str = '') -> dict:
    count = version.row_count
    if group:
        selected = next((item for item in version.groups if item['id'] == group), None)
        if selected is None:
            raise ValidationError('账号池分组不存在，请重新选择')
        count = selected['count']
    return {'version_id': version.pk, 'pool_id': version.pool_id, 'version': version.version,
            'content_hash': version.content_hash, 'identity_column': version.identity_column,
            'field_mapping': deepcopy(version.field_mapping), 'group': group,
            'effective_row_count': count, 'row_count': version.row_count}


def validate_binding(project_id: int, engine: str, version, group: str = '', variables: list | None = None,
                     user=None) -> None:
    if version is None:
        if group:
            raise ValidationError('选择账号池分组前必须选择版本')
        return
    if user is not None:
        require_project_access(project_id, user)
    if version.pool.project_id != project_id:
        raise ValidationError('账号池版本不属于当前项目')
    if engine != 'K6':
        raise ValidationError('账号池版本仅支持 K6，请先解除账号池选择')
    metadata(version, group)
    names = {item.get('name') for item in variables or []}
    if names.intersection(version.field_mapping):
        raise ValidationError('账号池映射与场景或环境变量重名，请明确修改映射或变量')


def resolve_accounts(scenario, resolved: dict, user=None) -> dict:
    version = getattr(scenario, 'account_pool_version', None)
    group = getattr(scenario, 'account_pool_group', '')
    validate_binding(scenario.project_id, scenario.engine, version, group, resolved['variables'],
                     user if user is not None else scenario.created_by)
    if version is None:
        return resolved
    payload = read_version(version)
    rows = payload['rows']
    if group:
        rows = [row for row in rows if fingerprint(row[version.group_column]) == group]
    key = f'account-version-{version.pk}'
    resolved['account_pool'] = dict(metadata(version, group), data_key=key)
    resolved['csv_data'][key] = {'rows': rows, 'columns': version.columns}
    resolved['variables'].extend({'name': name, 'column': column, 'type': 'CSV', 'data_file_id': key,
                                  'secret': True, 'source': 'ACCOUNT_POOL'}
                                 for name, column in version.field_mapping.items())
    return resolved


def cleanup_version_file(sender, instance, **kwargs) -> None:
    path = version_path(instance)
    def remove() -> None:
        path.unlink(missing_ok=True)
    transaction.on_commit(remove)
