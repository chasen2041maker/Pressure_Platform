"""MCP 协议端点鉴权。

双通道认证（与平台现有认证体系复用，零新增依赖）：
1. ``Authorization: Bearer <jwt>`` → simplejwt AccessToken 校验
2. ``x-mcp-api-key: <drf-token>`` → rest_framework.authtoken.Token 校验

提供两层防护：
- ASGI 中间件在协议端点前置拦截（未认证直接 401，不进 MCP 会话）
- 工具内部通过 Context 请求头二次解析用户（确保权限过滤落到具体用户）
"""
import contextvars
import json
import logging

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

User = get_user_model()

#: 中间件鉴权通过后写入的 contextvar（工具内优先用请求头解析，此为兜底）
current_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    'mcp_current_user_id', default=None)

AUTH_HEADER = b'authorization'
API_KEY_HEADER = b'x-mcp-api-key'


def authenticate_headers(headers: dict) -> 'User | None':
    """从 HTTP 头解析并校验用户，失败返回 None。

    :param headers: 小写键值的头字典（ASGI/Starlette 风格）
    """
    # 本函数经 sync_to_async 跑在独立线程，其 DB 连接不受 Django 请求生命周期
    # 管理（普通视图每请求前后都会 close_old_connections）。长时间空闲后连接会被
    # MySQL/中间 NAT 静默掐断，复用死连接会让查询阻塞在 socket 上几十秒到几分钟
    # （表现为 MCP 握手挂起超时）或报 2013 错误。鉴权为低频操作，仿照 Django
    # 请求处理在查询前后回收连接，每次重连，彻底规避僵尸连接。
    # 仅 autocommit 状态下回收：事务内（如 TestCase）关连接会破坏当前事务
    from django.db import close_old_connections, transaction

    in_atomic = not transaction.get_autocommit()
    if not in_atomic:
        close_old_connections()
    try:
        auth_value = headers.get('authorization', '')
        api_key = headers.get('x-mcp-api-key', '')

        if auth_value.lower().startswith('bearer '):
            token_str = auth_value[7:].strip()
            if token_str:
                return _verify_jwt(token_str)

        if api_key:
            return _verify_api_key(api_key)

        return None
    finally:
        if not in_atomic:
            close_old_connections()


def _verify_jwt(token_str: str) -> 'User | None':
    try:
        from rest_framework_simplejwt.tokens import AccessToken
        validated = AccessToken(token_str)
        user_id = validated.get('user_id')
        if not user_id:
            return None
        return User.objects.filter(id=user_id, is_active=True).first()
    except Exception as exc:  # noqa: BLE001 - 任何令牌异常一律按未认证处理
        # 用 warning 而非 debug：静默吞异常曾掩盖过 SynchronousOnlyOperation 真因
        logger.warning('MCP JWT 校验异常（按未认证处理）: %s', exc)
        return None


def _verify_api_key(api_key: str) -> 'User | None':
    try:
        from rest_framework.authtoken.models import Token
        token = Token.objects.select_related('user').filter(key=api_key).first()
        if token and token.user and token.user.is_active:
            return token.user
        return None
    except Exception as exc:  # noqa: BLE001
        # 用 warning 而非 debug：静默吞异常曾掩盖过 SynchronousOnlyOperation 真因
        logger.warning('MCP API-Key 校验异常（按未认证处理）: %s', exc)
        return None


def headers_from_scope(scope: dict) -> dict:
    """ASGI scope 头转小写键字符串字典。"""
    return {
        (k.decode('latin-1').lower() if isinstance(k, bytes) else str(k).lower()):
            (v.decode('latin-1') if isinstance(v, bytes) else str(v))
        for k, v in scope.get('headers', [])
    }


class McpAuthMiddleware:
    """纯 ASGI 中间件：仅拦截 MCP 协议路径做前置鉴权。

    非 MCP 路径原样透传，对现有 HTTP/WebSocket 流量零影响。
    """

    #: 协议端点精确路径（与 asgi.py 分流规则保持一致）
    MCP_PATHS = ('/api/mcp', '/api/mcp/')

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http' and scope.get('path') in self.MCP_PATHS:
            headers = headers_from_scope(scope)
            user = authenticate_headers(headers)
            if user is None:
                await self._send_401(send)
                return
            token = current_user_id.set(user.id)
            try:
                await self.app(scope, receive, send)
            finally:
                current_user_id.reset(token)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _send_401(send):
        body = json.dumps({
            'jsonrpc': '2.0',
            'error': 'Unauthorized: 需要 Authorization: Bearer <JWT> 或 x-mcp-api-key 头',
        }, ensure_ascii=False).encode('utf-8')
        await send({
            'type': 'http.response.start',
            'status': 401,
            'headers': [
                (b'content-type', b'application/json; charset=utf-8'),
                (b'www-authenticate', b'Bearer'),
            ],
        })
        await send({'type': 'http.response.body', 'body': body})
