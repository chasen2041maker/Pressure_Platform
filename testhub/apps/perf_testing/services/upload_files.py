"""Project upload validation and immutable private k6 file inputs."""
import base64
import hashlib
import json
import math
import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from rest_framework.exceptions import ValidationError

MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 16
FILE_ERROR = '上传文件不存在、不可读取、超出大小限制或不属于当前项目'
_FIELD = re.compile(r'^[A-Za-z_][A-Za-z0-9_.\[\]-]{0,127}$')
_MIME = re.compile(r'^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$')


def normalize_files(value: object) -> list:
    if value in (None, ''):
        return []
    if not isinstance(value, list) or len(value) > MAX_FILES:
        raise ValidationError('文件字段必须为数组，最多 16 项')
    result, fields = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {'field', 'file_id', 'filename', 'content_type'}:
            raise ValidationError('文件只能引用项目内 file_id，不能指定路径或地址')
        field = item.get('field')
        if not isinstance(field, str) or not _FIELD.fullmatch(field) or field in fields:
            raise ValidationError('文件字段名格式无效或重复')
        file_id = item.get('file_id')
        if file_id is not None and (type(file_id) is not int or file_id < 1):
            raise ValidationError('file_id 必须为正整数')
        filename = item.get('filename') or ''
        content_type = item.get('content_type') or ''
        if (not isinstance(filename, str) or len(filename) > 200
                or any(char in filename for char in '/\\"\r\n\x00')
                or re.search(r'[\x00-\x1f\x7f]|\{\{|\$\{', filename)):
            raise ValidationError('上传文件名必须为不含路径和控制字符的固定名称')
        if not isinstance(content_type, str) or len(content_type) > 100 or (content_type and not _MIME.fullmatch(content_type)):
            raise ValidationError('上传文件 Content-Type 必须为有效媒体类型')
        fields.add(field)
        result.append(dict(field=field, file_id=file_id, filename=filename, content_type=content_type))
    return result


def form_fields(body: object, files: list) -> dict:
    try:
        fields = json.loads(body or '{}')
    except (TypeError, ValueError) as exc:
        raise ValidationError('FORM 正文必须为 JSON 对象，字段值仅支持字符串、数字或布尔值') from exc
    names = {item.get('field') for item in files}
    if (not isinstance(fields, dict) or len(fields) > 200
            or any(not _FIELD.fullmatch(key) or key in names
                or type(value) not in (str, int, float, bool)
                or isinstance(value, float) and not math.isfinite(value)
                for key, value in fields.items())):
        raise ValidationError('FORM 字段必须为标量，字段名不得重复或与文件字段冲突')
    return fields


def request_errors(step: dict, headers: dict | None = None) -> list:
    body_type = (step.get('body_type') or 'NONE').upper()
    files = step.get('files') or []
    errors = []
    if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
        return ['文件字段必须是对象数组']
    try:
        normalize_files([{key: value for key, value in item.items() if key not in ('sha256', 'size')} for item in files])
    except ValidationError as exc:
        return [str(value) for value in exc.detail]
    if body_type not in ('NONE', 'JSON', 'FORM', 'BINARY'):
        errors.append('k6 请求体支持 NONE / JSON / FORM / BINARY')
    if files and body_type not in ('FORM', 'BINARY'):
        errors.append('上传文件需要选择 FORM 或 BINARY 请求体')
    if body_type == 'FORM':
        try:
            form_fields(step.get('body'), files)
        except ValidationError as exc:
            errors.extend(str(item) for item in exc.detail)
        header = next((str(value).lower() for key, value in (headers or {}).items() if key.lower() == 'content-type'), '')
        if files and header and header != 'multipart/form-data':
            errors.append('FORM 文件请求的 Content-Type 应留空或为 multipart/form-data；边界由引擎生成')
        if not files and header and header != 'application/x-www-form-urlencoded':
            errors.append('无文件 FORM 使用 application/x-www-form-urlencoded')
    if body_type == 'BINARY' and (len(files) != 1 or step.get('body')):
        errors.append('BINARY 请求必须选择一个文件，文本正文必须为空')
    if body_type == 'NONE' and step.get('body'):
        errors.append('NONE 类型不能配置请求体，请选择正确类型')
    return errors


def resolve_files(items: list, project_id: int, blobs: dict) -> list:
    from ..models import PerfDataFile
    resolved = []
    for item in normalize_files(items):
        if not item['file_id']:
            raise ValidationError('文件字段尚未选择项目内上传文件')
        record = PerfDataFile.objects.filter(pk=item['file_id'], project_id=project_id, file_type='UPLOAD').first()
        try:
            if not record or not record.file:
                raise ValueError('missing')
            root = Path(settings.MEDIA_ROOT).resolve()
            path = Path(record.file.path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('outside media')
            with path.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
            if not raw or len(raw) > MAX_BYTES:
                raise ValueError('size')
        except (OSError, ValueError, NotImplementedError, SuspiciousFileOperation) as exc:
            raise ValidationError(FILE_ERROR) from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest not in blobs:
            if len(raw) + sum(value['size'] for value in blobs.values()) > MAX_BYTES:
                raise ValidationError('单场景上传文件总大小不能超过 20MB')
            blobs[digest] = dict(size=len(raw), sha256=digest, base64=base64.b64encode(raw).decode('ascii'))
        descriptor = dict(item, filename=item['filename'] or path.name,
            content_type=item['content_type'] or (record.meta or {}).get('content_type') or 'application/octet-stream')
        # Stored metadata receives the same header-injection protections as user input.
        descriptor = normalize_files([descriptor])[0]
        resolved.append(dict(descriptor, sha256=digest, size=len(raw)))
    return resolved


def project_errors(items: list, project_id: int | None) -> list:
    if not items:
        return []
    if not project_id:
        return ['请选择当前项目内可读取的上传文件']
    try:
        resolve_files(items, project_id, {})
    except ValidationError as exc:
        return [str(value) for value in exc.detail]
    return []


def decode_blob(digest: str, blob: object) -> bytes:
    try:
        if not isinstance(blob, dict) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('invalid')
        encoded = blob.get('base64')
        if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
            raise ValueError('size')
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_BYTES or len(raw) != blob.get('size') or blob.get('sha256') != digest or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('hash')
        return raw
    except (TypeError, ValueError) as exc:
        raise ValidationError('冻结上传文件缺失或哈希校验失败，请重新创建执行') from exc


def frozen_errors(snapshot: dict) -> list:
    try:
        blobs = snapshot.get('upload_data') or {}
        if not isinstance(blobs, dict):
            raise ValidationError('冻结上传文件格式无效')
        size = sum(len(decode_blob(digest, blob)) for digest, blob in blobs.items())
        if size > MAX_BYTES:
            raise ValidationError('单场景上传文件总大小不能超过 20MB')
        for step in snapshot.get('steps') or []:
            if not step.get('enabled', True):
                continue
            for item in step.get('files') or []:
                if not isinstance(item, dict) or set(item) - {'field', 'file_id', 'filename', 'content_type', 'sha256', 'size'}:
                    raise ValidationError('冻结上传文件禁止读取实时路径或地址')
                normalize_files([{key: value for key, value in item.items() if key not in ('sha256', 'size')}])
                digest = item.get('sha256')
                blob = blobs.get(digest) if isinstance(digest, str) else None
                if not blob or item.get('size') != blob.get('size'):
                    raise ValidationError('冻结上传文件缺失或哈希校验失败，请重新创建执行')
    except ValidationError as exc:
        return [str(value) for value in exc.detail]
    return []
