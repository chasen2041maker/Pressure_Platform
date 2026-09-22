# -*- coding: utf-8 -*-
"""MCP preview→confirm 机制单测：令牌签发/校验/消费/过期/越权/拒绝。

运行: python manage.py test apps.mcp.tests.test_confirm
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.mcp.confirm import (
    ConfirmError,
    approve_pending,
    consume_pending,
    create_preview,
    query_approval_status,
    reject_pending,
    verify_pending,
)
from apps.mcp.models import McpPendingConfirm
from apps.projects.models import Project
from apps.testcases.models import TestCase as FunctionalTestCase

User = get_user_model()


class ConfirmFlowBase(TestCase):
    def setUp(self):
        self.user = User.objects.create(username='mcp_confirm_user', email='c@a.com')
        self.other = User.objects.create(username='mcp_confirm_other', email='c@b.com')
        self.project = Project.objects.create(name='MCP 项目', owner=self.user)
        self.arguments = {
            'project_id': self.project.id,
            'data': {
                'title': 'MCP 创建的用例',
                'expected_result': '成功',
                'steps': [{'action': '打开页面', 'expected': '页面可见'}],
            },
        }

    def _create_pending(self):
        return create_preview('confirm_create_testcase', self.arguments, self.user)


class ConfirmTokenFlowTest(ConfirmFlowBase):
    def test_preview_creates_pending_and_token(self):
        result = self._create_pending()
        self.assertIn('confirm_token', result)
        self.assertTrue(result['preview'])
        pending = McpPendingConfirm.objects.get()
        self.assertEqual(pending.status, 'pending')
        self.assertEqual(pending.user_id, self.user.id)
        self.assertGreater(pending.expires_at, timezone.now())

    def test_confirm_consumes_and_executes_once(self):
        result = self._create_pending()
        pending = verify_pending(result['confirm_token'], self.user)
        exec_result = consume_pending(pending, self.user)

        # 动作真实执行：功能用例落库
        case = FunctionalTestCase.objects.get(project=self.project)
        self.assertEqual(case.title, 'MCP 创建的用例')
        self.assertEqual(case.step_details.count(), 1)
        self.assertEqual(exec_result['testcase_id'], case.id)

        # 状态置为 consumed
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'consumed')

        # 重复消费被拒绝
        with self.assertRaises(ConfirmError):
            consume_pending(verify_pending(result['confirm_token'], self.user), self.user)

    def test_expired_pending_rejected(self):
        result = self._create_pending()
        McpPendingConfirm.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ConfirmError) as cm:
            verify_pending(result['confirm_token'], self.user)
        self.assertIn('过期', str(cm.exception))
        self.assertEqual(McpPendingConfirm.objects.get().status, 'expired')

    def test_tampered_token_rejected(self):
        result = self._create_pending()
        tampered = result['confirm_token'][:-2] + 'xx'
        with self.assertRaises(ConfirmError):
            verify_pending(tampered, self.user)

    def test_cross_user_confirm_rejected(self):
        result = self._create_pending()
        with self.assertRaises(ConfirmError) as cm:
            verify_pending(result['confirm_token'], self.other)
        self.assertIn('无权', str(cm.exception))

    def test_reject_invalidates_token(self):
        result = self._create_pending()
        pending = McpPendingConfirm.objects.get()
        reject_pending(pending, self.other)

        with self.assertRaises(ConfirmError) as cm:
            verify_pending(result['confirm_token'], self.user)
        self.assertIn('拒绝', str(cm.exception))

    def test_approve_executes_server_side(self):
        self._create_pending()
        pending = McpPendingConfirm.objects.get()
        result = approve_pending(pending, self.other)

        self.assertTrue(FunctionalTestCase.objects.filter(project=self.project).exists())
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'approved')
        self.assertEqual(pending.result.get('approved_by'), self.other.username)
        self.assertEqual(result['project_id'], self.project.id)

        # 已批准的操作不能重复批准
        with self.assertRaises(ConfirmError):
            approve_pending(pending, self.other)

    def test_reject_non_pending_rejected(self):
        self._create_pending()
        pending = McpPendingConfirm.objects.get()
        approve_pending(pending, self.user)
        with self.assertRaises(ConfirmError):
            reject_pending(pending, self.user)


class ConfirmActionErrorTest(ConfirmFlowBase):
    def test_action_error_surfaces_as_confirm_error(self):
        # 项目不属于当前用户 → 动作执行失败
        foreign_project = Project.objects.create(name='别人项目', owner=self.other)
        result = create_preview('confirm_create_testcase', {
            'project_id': foreign_project.id,
            'data': {'title': 'x', 'expected_result': 'y'},
        }, self.user)
        pending = verify_pending(result['confirm_token'], self.user)
        with self.assertRaises(ConfirmError) as cm:
            consume_pending(pending, self.user)
        self.assertIn('无权限', str(cm.exception))


@override_settings(MCP_HUMAN_APPROVAL=True)
class HumanApprovalModeTest(ConfirmFlowBase):
    """人工审批模式：confirm 不直接执行，等待控制台批准后轮询取结果。"""

    def test_confirm_returns_awaiting_without_execution(self):
        result = self._create_pending()
        pending = verify_pending(result['confirm_token'], self.user)
        out = consume_pending(pending, self.user)

        self.assertEqual(out['status'], 'awaiting_approval')
        self.assertEqual(out['pending_id'], pending.id)
        # 动作未真实执行
        self.assertFalse(FunctionalTestCase.objects.filter(project=self.project).exists())
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'pending')
        self.assertTrue(pending.awaiting_human)

        # 重复 confirm 幂等，仍返回等待中
        out2 = consume_pending(verify_pending(result['confirm_token'], self.user), self.user)
        self.assertEqual(out2['status'], 'awaiting_approval')

    def test_approve_then_poll_returns_result(self):
        result = self._create_pending()
        consume_pending(verify_pending(result['confirm_token'], self.user), self.user)
        pending = McpPendingConfirm.objects.get()
        approve_pending(pending, self.other)

        info = query_approval_status(result['confirm_token'], self.user)
        self.assertEqual(info['status'], 'approved')
        self.assertIn('testcase_id', info['result'])
        self.assertTrue(FunctionalTestCase.objects.filter(project=self.project).exists())

    def test_reject_then_poll_returns_rejected(self):
        result = self._create_pending()
        consume_pending(verify_pending(result['confirm_token'], self.user), self.user)
        pending = McpPendingConfirm.objects.get()
        reject_pending(pending, self.other)

        info = query_approval_status(result['confirm_token'], self.user)
        self.assertEqual(info['status'], 'rejected')

    def test_poll_before_confirm_shows_pending(self):
        result = self._create_pending()
        info = query_approval_status(result['confirm_token'], self.user)
        self.assertEqual(info['status'], 'pending')
        self.assertGreater(info['expires_in_seconds'], 0)

    def test_expired_confirm_still_rejected(self):
        result = self._create_pending()
        McpPendingConfirm.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ConfirmError):
            consume_pending(verify_pending(result['confirm_token'], self.user), self.user)

    def test_cross_user_poll_rejected(self):
        result = self._create_pending()
        with self.assertRaises(ConfirmError) as cm:
            query_approval_status(result['confirm_token'], self.other)
        self.assertIn('无权', str(cm.exception))
