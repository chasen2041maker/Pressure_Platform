"""Bounded, allowlisted contract retrieval and user-bound preview confirmation."""
import ast
import hashlib
import http.client
import re
import socket
import threading
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from . import api_catalog
from .environments import require_project_access

TOTAL_TIMEOUT = 12.0
SOCKET_TIMEOUT = 4.0
MAX_REDIRECTS = 3
TOKEN_SALT = 'perf.catalog-source.preview.v1'


def _invalid(message: str) -> None:
    raise api_catalog.CatalogInputError(message)


def normalize_url(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or not value or len(value)>4096 or any(ord(c)<32 or ord(c)==127 for c in value) or '\\' in value:
        _invalid('请输入有效的 HTTP/HTTPS 契约地址')
    value=value.strip().split('#',1)[0]
    if '?' in value:
        _invalid('契约地址暂不支持查询参数，请使用不含凭据的固定地址')
    try:
        parts=urlsplit(value)
        if parts.scheme not in ('http','https') or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError
        host=parts.hostname.lower()
        if '%' in host or not host.isascii() or any(c.isspace() for c in host):raise ValueError
        port=parts.port if parts.port is not None else (443 if parts.scheme=='https' else 80)
        if not 1<=port<=65535:raise ValueError
        authority=('['+host+']' if ':' in host else host)+((':'+str(port)) if port!=(443 if parts.scheme=='https' else 80) else '')
    except ValueError:
        _invalid('契约地址不支持凭据、无效主机或端口')
    origin=parts.scheme+'://'+authority
    return urlunsplit((parts.scheme,authority,parts.path or '/', '', '')),origin


def _allowed(value: object, origin: str | None = None) -> tuple[str, str]:
    url,found=normalize_url(value)
    configured=getattr(settings,'PERF_CATALOG_ALLOWED_ORIGINS',[])
    allowed=set()
    for item in configured if isinstance(configured,(list,tuple)) else []:
        normalized,host=normalize_url(item)
        if urlsplit(normalized).path!='/':_invalid('契约来源白名单配置无效')
        allowed.add(host)
    if found not in allowed or (origin is not None and origin!=found):
        _invalid('该契约来源未获允许；页面、文档与重定向必须在同一已允许来源')
    return url,found


def _get(url: str, origin: str, deadline: float) -> tuple[bytes,str]:
    for hop in range(MAX_REDIRECTS+1):
        url,_=_allowed(url,origin)
        remaining=deadline-time.monotonic()
        if remaining<=0:_invalid('读取契约超时，请稍后重试')
        parts=urlsplit(url)
        cls=http.client.HTTPSConnection if parts.scheme=='https' else http.client.HTTPConnection
        connection=cls(parts.hostname,parts.port,timeout=min(SOCKET_TIMEOUT,remaining))
        timer=None;response=None
        try:
            connection.connect()
            transport=connection.sock
            def cancel() -> None:
                try:transport.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            timer=threading.Timer(max(0,deadline-time.monotonic()),cancel)
            timer.daemon=True;timer.start()
            connection.request('GET',quote(parts.path,safe="/%:@!$&'()*+,;=-._~"),headers={
                'Accept':'application/json, application/yaml, text/yaml, text/html',
                'Accept-Encoding':'identity','Connection':'close'})
            response=connection.getresponse()
            if response.status in (301,302,303,307,308):
                location=response.getheader('Location')
                if not location or hop==MAX_REDIRECTS:_invalid('契约重定向次数过多或目标无效')
                url,_=_allowed(urljoin(url,location),origin)
                continue
            if response.status!=200:_invalid('契约来源未返回成功响应')
            if response.getheader('Content-Encoding','identity').lower()!='identity':
                _invalid('契约来源需返回未压缩的 JSON/YAML 或 Swagger 页面')
            length=response.getheader('Content-Length')
            if length is not None and (not length.isdigit() or int(length)>api_catalog.MAX_BYTES):
                _invalid('契约响应超过 8 MB 或长度无效')
            data=bytearray()
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:_invalid('读取契约超时，请稍后重试')
                transport.settimeout(min(SOCKET_TIMEOUT,remaining))
                chunk=response.read1(min(65536,api_catalog.MAX_BYTES+1-len(data)))
                if not chunk:break
                data.extend(chunk)
                if len(data)>api_catalog.MAX_BYTES:_invalid('契约响应超过 8 MB')
                if response.isclosed():break
            if length is not None and len(data)!=int(length):_invalid('契约响应不完整，请重新预览')
            if time.monotonic()>deadline:_invalid('读取契约超时，请稍后重试')
            return bytes(data),url
        except (OSError,http.client.HTTPException,UnicodeError,ValueError):
            _invalid('无法读取契约来源或读取超时，请检查地址后重试')
        finally:
            if timer is not None:timer.cancel()
            if response is not None:response.close()
            connection.close()
    _invalid('契约重定向次数过多')


def _document_url(page: bytes, page_url: str, origin: str) -> str:
    try:text=page.decode('utf-8-sig')
    except UnicodeError:_invalid('Swagger 页面必须为 UTF-8')
    # Tokenize literals/comments; remote JavaScript is never executed or imported.
    pattern=r'''/\*[\s\S]*?\*/|//[^\r\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[A-Za-z_$][\w$]*|[^\s]'''
    tokens=[]
    for match in re.finditer(pattern,text):
        token=match.group()
        if token.startswith(('//','/*')):continue
        tokens.append(token)
        if len(tokens)>100000:_invalid('Swagger 页面配置过大，请直接提供 JSON/YAML 地址')
    urls=[];calls=0
    for index,token in enumerate(tokens):
        if token!='SwaggerUIBundle' or tokens[index+1:index+3]!=['(','{']:continue
        calls+=1;depth=1;cursor=index+3
        while cursor<len(tokens) and depth:
            current=tokens[cursor]
            if depth==1 and current in ('url','"url"',"'url'") and tokens[cursor+1:cursor+2]==[':']:
                value=tokens[cursor+2] if cursor+2<len(tokens) else ''
                if not value.startswith(('"',"'")) or tokens[cursor+3:cursor+4] not in ([','],['}']):
                    _invalid('Swagger 使用动态配置，请直接提供 JSON/YAML 地址')
                try:urls.append(ast.literal_eval(value))
                except (ValueError,SyntaxError):_invalid('Swagger 文档地址格式不受支持')
            if current in ('{','[','('):depth+=1
            if current in ('}',']',')'):depth-=1
            cursor+=1
    if calls!=1 or len(urls)!=1 or not isinstance(urls[0],str):
        _invalid('未找到唯一静态 SwaggerUIBundle 文档地址，请直接提供 JSON/YAML 地址')
    return _allowed(urljoin(page_url,urls[0]),origin)[0]


def fetch(source_url: object) -> tuple[dict,dict,str]:
    url,origin=_allowed(source_url)
    deadline=time.monotonic()+TOTAL_TIMEOUT
    raw,document_url=_get(url,origin,deadline)
    if raw.removeprefix(b'\xef\xbb\xbf').lstrip().startswith(b'<'):
        document_url=_document_url(raw,document_url,origin)
        raw,document_url=_get(document_url,origin,deadline)
    parsed=api_catalog.parse_document(raw)
    return parsed,{'url':url,'document_url':document_url,'checked_at':timezone.now().isoformat()},hashlib.sha256(raw).hexdigest()


def preview(project_id: int, source_url: object, user) -> dict:
    from ..models import PerfProject
    require_project_access(project_id,user)
    parsed,source,raw_hash=fetch(source_url)
    with transaction.atomic():
        project=PerfProject.objects.select_for_update().get(pk=project_id)
        result=api_catalog.preview(project_id,parsed,user)
        version=result['current_version']['version'] if result['current_version'] else 0
        claims={'user':user.pk,'project':project_id,'url':source['url'],'document_url':source['document_url'],
                'raw_hash':raw_hash,'content_hash':parsed['content_hash'],'version':version,
                'previous_source':api_catalog.digest(project.catalog_source)}
    return {**result,'source':source,'preview_token':signing.dumps(claims,salt=TOKEN_SALT)}


def confirm(project_id: int, source_url: object, token: object, expected: object, user) -> dict:
    from ..models import PerfProject
    require_project_access(project_id,user)
    url,_=_allowed(source_url)
    version=api_catalog.expected_version(expected)
    try:
        if not isinstance(token,str) or len(token)>8192:raise signing.BadSignature()
        claims=signing.loads(token,salt=TOKEN_SALT,max_age=600)
        if not isinstance(claims,dict) or any(claims.get(k)!=v for k,v in
            {'user':user.pk,'project':project_id,'url':url,'version':version}.items()):raise signing.BadSignature()
    except signing.BadSignature:
        raise api_catalog.CatalogConflict('预览已失效或不属于当前项目，请重新预览') from None
    parsed,source,raw_hash=fetch(url)
    if source['url']!=url or source['document_url']!=claims['document_url'] or raw_hash!=claims['raw_hash'] or parsed['content_hash']!=claims['content_hash']:
        raise api_catalog.CatalogConflict('来源契约在预览后发生变化，请重新预览')
    with transaction.atomic():
        project=PerfProject.objects.select_for_update().get(pk=project_id)
        if api_catalog.digest(project.catalog_source)!=claims['previous_source']:
            raise api_catalog.CatalogConflict('接口库来源已变化，请重新预览')
        result=api_catalog.import_document(project_id,parsed,version,user)
        project.catalog_source=source
        project.save(update_fields=['catalog_source'])
    return {**result,'source':source}


def import_file(project_id: int, parsed: dict, expected: object, user) -> dict:
    from ..models import PerfProject
    with transaction.atomic():
        result=api_catalog.import_document(project_id,parsed,expected,user)
        PerfProject.objects.filter(pk=project_id).update(catalog_source={})
    return {**result,'source':{}}
