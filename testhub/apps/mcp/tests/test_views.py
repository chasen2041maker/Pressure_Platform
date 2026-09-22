# -*- coding: utf-8 -*-
"""MCP 管理端 REST 接口单测：日志/待确认列表、批准/拒绝、协议兜底。

运行: python manage.py test apps.mcp.tests.test_views
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.mcp.confirm import create_preview
from apps.mcp.models import McpCallLog, McpPendingConfirm
from apps.projects.models import Project

User = get_user_model()


class McpRestBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create(
            username='mcp_admin', email='admin@a.com', is_staff=True)
        self.user = User.objects.create(username='mcp_rest_user', email='r@a.com')
        self.project = Project.objects.create(name='REST 项目', owner=self.user)
        self.client = APIClient()

    def _make_pending(self):
        return create_preview('confirm_create_testcase', {
            'project_id': self.project.id,
            'data': {'title': 'REST 用例', 'expected_result': 'ok'},
        }, self.user)


class CallLogListViewTest(McpRestBase):
    def test_requires_auth(self):
        resp = self.client.get('/api/mcp/logs/')
        self.assertIn(resp.status_code, (401, 403))

    def test_normal_user_sees_only_own_logs(self):
        McpCallLog.objects.create(user=self.user, tool_name='list_projects')
        McpCallLog.objects.create(user=self.admin, tool_name='list_projects')

        self.client.force_authenticate(user=self.user)
        data = self.client.get('/api/mcp/logs/').json()
        self.assertEqual(data['count'], 1)

        self.client.force_authenticate(user=self.admin)
        data = self.client.get('/api/mcp/logs/').json()
        self.assertEqual(data['count'], 2)

    def test_tool_filter(self):
        McpCallLog.objects.create(user=self.user, tool_name='list_projects')
        McpCallLog.objects.create(user=self.user, tool_name='get_report')
        self.client.force_authenticate(user=self.user)
        data = self.client.get('/api/mcp/logs/?tool=get_report').json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['tool_name'], 'get_report')

    def test_status_filter(self):
        McpCallLog.objects.create(user=self.user, tool_name='list_projects', status='error')
        McpCallLog.objects.create(user=self.user, tool_name='list_projects', status='success')
        self.client.force_authenticate(user=self.user)
        data = self.client.get('/api/mcp/logs/?status=error').json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['status'], 'error')

    def test_time_range_filter(self):
        McpCallLog.objects.create(user=self.user, tool_name='list_projects')
        self.client.force_authenticate(user=self.user)
        # 用 Z 后缀格式避免查询串中 + 被解码为空格
        future = (timezone.now() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        data = self.client.get(f'/api/mcp/logs/?created_after={future}').json()
        self.assertEqual(data['count'], 0)
        past = (timezone.now() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        data = self.client.get(f'/api/mcp/logs/?created_after={past}').json()
        self.assertEqual(data['count'], 1)


class PendingListViewTest(McpRestBase):
    def test_pending_list_visibility(self):
        self._make_pending()
        self.client.force_authenticate(user=self.user)
        data = self.client.get('/api/mcp/pending/').json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['status'], 'pending')

    def test_expired_pending_fixed_lazily(self):
        """列表读取时将已过期的 pending 惰性修正为 expired"""
        self._make_pending()
        McpPendingConfirm.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.client.force_authenticate(user=self.user)
        data = self.client.get('/api/mcp/pending/').json()
        self.assertEqual(data['results'][0]['status'], 'expired')


class ApproveRejectViewTest(McpRestBase):
    def test_approve_executes_action(self):
        self._make_pending()
        pending = McpPendingConfirm.objects.get()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(f'/api/mcp/pending/{pending.id}/approve/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'approved')

        pending.refresh_from_db()
        self.assertEqual(pending.status, 'approved')
        self.assertIn('testcase_id', pending.result)

    def test_reject_invalidates_token(self):
        self._make_pending()
        pending = McpPendingConfirm.objects.get()

        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(f'/api/mcp/pending/{pending.id}/reject/')
        self.assertEqual(resp.status_code, 200)
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'rejected')

    def test_foreign_user_cannot_operate(self):
        self._make_pending()
        pending = McpPendingConfirm.objects.get()
        stranger = User.objects.create(username='mcp_stranger', email='s@a.com')

        self.client.force_authenticate(user=stranger)
        resp = self.client.post(f'/api/mcp/pending/{pending.id}/reject/')
        self.assertEqual(resp.status_code, 403)

    def test_approve_nonexistent_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post('/api/mcp/pending/99999/approve/')
        self.assertEqual(resp.status_code, 404)


class ToolCatalogViewTest(McpRestBase):
    def test_requires_auth(self):
        resp = self.client.get('/api/mcp/tools/')
        self.assertIn(resp.status_code, (401, 403))

    def test_catalog_lists_all_tools(self):
        from apps.mcp.tools import ALL_TOOLS
        self.client.force_authenticate(user=self.user)
        data = self.client.get('/api/mcp/tools/').json()
        self.assertEqual(data['summary']['total'], len(ALL_TOOLS))
        self.assertEqual(len(data['tools']), len(ALL_TOOLS))
        names = {t['name'] for t in data['tools']}
        self.assertEqual(names, {fn.__name__ for fn in ALL_TOOLS})
        # 分类计数汇总与总数一致
        by_cat = data['summary']['by_category']
        self.assertEqual(sum(by_cat.values()), data['summary']['total'])

    def test_catalog_stats_from_logs(self):
        McpCallLog.objects.create(user=self.user, tool_name='list_projects',
                                  status='success', duration_ms=10)
        McpCallLog.objects.create(user=self.user, tool_name='list_projects',
                                  status='error', duration_ms=30)
        self.client.force_authenticate(user=self.user)
        data = self.client.get('/api/mcp/tools/').json()
        self.assertEqual(data['summary']['calls_7d'], 2)
        tool = next(t for t in data['tools'] if t['name'] == 'list_projects')
        self.assertEqual(tool['stats']['calls'], 2)
        self.assertEqual(tool['stats']['success'], 1)
        self.assertEqual(tool['stats']['success_rate'], 0.5)
        self.assertEqual(tool['stats']['avg_duration_ms'], 20)

    def test_detail_returns_description(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get('/api/mcp/tools/preview_create_testcase/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['name'], 'preview_create_testcase')
        self.assertTrue(data['description'])
        self.assertEqual(data['paired_with'], 'confirm_create_testcase')
        self.assertIn('properties', data['input_schema'])
        self.assertTrue(data['examples'])

    def test_detail_unknown_tool_404(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get('/api/mcp/tools/not_a_tool/')
        self.assertEqual(resp.status_code, 404)


class ConnectionConfigViewTest(McpRestBase):
    def test_requires_auth(self):
        resp = self.client.get('/api/mcp/config/')
        self.assertIn(resp.status_code, (401, 403))

    def test_config_returns_endpoint_and_usable_key(self):
        from apps.mcp.auth import authenticate_headers
        from apps.mcp.tools import ALL_TOOLS
        self.client.force_authenticate(user=self.user)
        data = self.client.get('/api/mcp/config/').json()
        self.assertTrue(data['endpoint'].endswith('/api/mcp/'))
        self.assertEqual(data['tool_count'], len(ALL_TOOLS))
        self.assertTrue(data['api_key'])
        # API-Key 可直接通过 MCP 鉴权（配置拿即可用）
        user = authenticate_headers({'x-mcp-api-key': data['api_key']})
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.user.id)

    def test_config_key_stable(self):
        """重复获取返回同一把长效 Key（get_or_create 语义）"""
        self.client.force_authenticate(user=self.user)
        key1 = self.client.get('/api/mcp/config/').json()['api_key']
        key2 = self.client.get('/api/mcp/config/').json()['api_key']
        self.assertEqual(key1, key2)


class ProtocolFallbackViewTest(McpRestBase):
    """兜底视图为纯 Django View，用会话登录（force_login）而非 force_authenticate"""

    def test_fallback_requires_auth(self):
        resp = self.client.get('/api/mcp/')
        self.assertEqual(resp.status_code, 401)

    def test_fallback_auth_by_mcp_api_key(self):
        """MCP 客户端仅持 x-mcp-api-key 时也能拿到 501 提示（而非 401）"""
        from rest_framework.authtoken.models import Token
        token, _ = Token.objects.get_or_create(user=self.user)
        resp = self.client.get('/api/mcp/', HTTP_X_MCP_API_KEY=token.key)
        self.assertEqual(resp.status_code, 501)

    def test_fallback_returns_501_hint(self):
        """WSGI 测试客户端走到兜底视图（ASGI 下该路径被分流拦截）"""
        self.client.force_login(self.user)
        resp = self.client.post('/api/mcp/')
        self.assertEqual(resp.status_code, 501)
        self.assertIn('daphne', resp.json()['hint'])

    def test_fallback_with_sse_accept_header_not_406(self):
        """MCP 客户端带 Accept: text/event-stream 探测时不应被内容协商拒绝为 406"""
        self.client.force_login(self.user)
        resp = self.client.get('/api/mcp/', HTTP_ACCEPT='text/event-stream')
        self.assertEqual(resp.status_code, 501)
        self.assertIn('daphne', resp.json()['hint'])


class AsgiBridgeAuthTest(TestCase):
    """ASGI 桥鉴权回归：桥转发前会把 path 改写为 '/'，鉴权必须在改写前完成。

    历史缺陷：鉴权包在改写后的子应用上（McpAuthMiddleware 按 /api/mcp 匹配），
    导致直连 8001 端口的未认证请求绕过鉴权直达协议栈（表现为 400 而非 401）。
    """

    def _call_bridge(self, headers):
        import asyncio
        from apps.mcp.server import mcp_bridge

        scope = {'type': 'http', 'method': 'POST', 'path': '/api/mcp/',
                 'headers': [(k.encode(), v.encode()) for k, v in headers]}
        sent = []

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        async def send(message):
            sent.append(message)

        asyncio.run(mcp_bridge(scope, receive, send))
        start = next(m for m in sent if m['type'] == 'http.response.start')
        return start['status']

    def test_bridge_rejects_unauthenticated(self):
        self.assertEqual(self._call_bridge([]), 401)

    def test_bridge_rejects_invalid_api_key(self):
        self.assertEqual(self._call_bridge([('x-mcp-api-key', 'not-a-real-key')]), 401)

    def test_bridge_rejects_invalid_bearer(self):
        self.assertEqual(
            self._call_bridge([('authorization', 'Bearer not-a-real-jwt')]), 401)

    # 注：「合法 Key 放行」用例不在此处验证——桥内鉴权经 sync_to_async 在独立
    # 线程/DB 连接执行，看不到 TestCase 未提交的事务数据；端到端合法链路已由
    # 冒烟验证覆盖（INIT 200 + tools/list 200）
