# -*- coding: utf-8 -*-
"""MCP 鉴权单测：JWT / API-Key 双通道 + ASGI 中间件拦截。

运行: python manage.py test apps.mcp.tests.test_auth

注意：中间件测试 mock 掉 authenticate_headers，不在 asyncio.run() 内执行
同步 ORM 查询——在 TestCase 事务内跨 asyncio 边界访问数据库会破坏事务，
鉴权函数本身的正确性由 AuthenticateHeadersTest（同步）覆盖。
"""
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.mcp.auth import McpAuthMiddleware, authenticate_headers
from apps.mcp.tests.utils import ctx_with_api_key, ctx_with_jwt

User = get_user_model()


class AuthenticateHeadersTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='mcp_auth_user', email='mcp@a.com')

    def test_jwt_bearer_ok(self):
        _, access = ctx_with_jwt(self.user)
        user = authenticate_headers({'authorization': f'Bearer {access}'})
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.user.id)

    def test_jwt_invalid_rejected(self):
        self.assertIsNone(authenticate_headers({'authorization': 'Bearer invalid.token.here'}))

    def test_inactive_user_rejected(self):
        self.user.is_active = False
        self.user.save()
        _, access = ctx_with_jwt(self.user)
        self.assertIsNone(authenticate_headers({'authorization': f'Bearer {access}'}))

    def test_api_key_ok(self):
        _, key = ctx_with_api_key(self.user)
        user = authenticate_headers({'x-mcp-api-key': key})
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.user.id)

    def test_api_key_invalid_rejected(self):
        self.assertIsNone(authenticate_headers({'x-mcp-api-key': 'not-exists'}))

    def test_no_headers_rejected(self):
        self.assertIsNone(authenticate_headers({}))


class McpAuthMiddlewareTest(SimpleTestCase):
    """ASGI 中间件：MCP 路径未认证 401；认证通过放行；非 MCP 路径透传。

    纯逻辑测试：mock authenticate_headers，不触碰数据库与事件循环边界。
    """

    def _run(self, scope, auth_return=None):
        sent = []
        called = {'inner': False}

        async def inner_app(scope, receive, send):
            called['inner'] = True

        async def receive():
            return {'type': 'http.request', 'body': b''}

        async def send(message):
            sent.append(message)

        middleware = McpAuthMiddleware(inner_app)
        import asyncio
        with mock.patch('apps.mcp.auth.authenticate_headers',
                        return_value=auth_return):
            asyncio.run(middleware(scope, receive, send))
        return sent, called['inner']

    def _scope(self, path, headers=None):
        raw = [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
        return {'type': 'http', 'path': path, 'headers': raw}

    def test_mcp_path_without_auth_returns_401(self):
        sent, inner = self._run(self._scope('/api/mcp/'))
        self.assertFalse(inner)
        self.assertEqual(sent[0]['type'], 'http.response.start')
        self.assertEqual(sent[0]['status'], 401)

    def test_mcp_path_with_jwt_passes(self):
        fake_user = SimpleNamespace(id=42)
        sent, inner = self._run(
            self._scope('/api/mcp/', {'Authorization': 'Bearer x'}),
            auth_return=fake_user)
        self.assertTrue(inner)
        self.assertEqual(sent, [])

    def test_non_mcp_path_passes_without_auth(self):
        sent, inner = self._run(self._scope('/api/projects/'))
        self.assertTrue(inner)
        self.assertEqual(sent, [])

    def test_websocket_scope_passes_through(self):
        sent, inner = self._run({'type': 'websocket', 'path': '/api/mcp/', 'headers': []})
        self.assertTrue(inner)
