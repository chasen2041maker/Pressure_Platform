"""monitor 友好告警逻辑单元测试（mock，不依赖 DB）。

设计背景（2026-07-23，用户反馈 episode 硬去重不友好）：
采用 A+B 组合：
- A. 手动「立即检测」(manual=True) 返回 DOWN 时必推钉钉，同一目标短冷却(manual_alert_cooldown)防刷；
- B. 自动监测(manual=False) 持续 DOWN 期间按 alert_repeat_interval 周期重提醒，恢复才结束 episode。

本测试覆盖：手动超冷却必重推 / 自动在 repeat 内不重推 / 手动冷却内不重推(防刷) /
重推时 notify_count 自增且消息含"第 N 次提醒"。
"""
import os
from datetime import timedelta

import django
from unittest import mock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.utils import timezone

from apps.monitor import models
from apps.monitor.models import MonitorTarget, TargetStatus, AlertEvent, AlertStatus


def _make_target(consecutive_failures=5, alert_threshold=1,
                 repeat_interval=30, manual_cooldown=5, sent=None):
    """真实 MonitorTarget 实例（不落库），覆盖 _dispatch_alert 隔离 maybe_alert。"""
    t = MonitorTarget(
        name='tmp', type='HTTP',
        consecutive_failures=consecutive_failures,
        alert_threshold=alert_threshold, status=TargetStatus.DOWN,
        alert_repeat_interval=repeat_interval,
        manual_alert_cooldown=manual_cooldown,
    )
    t._dispatch_alert = mock.MagicMock(return_value=sent or [
        {'channel': '钉钉告警助手', 'type': 'DINGTALK', 'ok': True, 'detail': 'ok'}
    ])
    return t


def _make_open_alert(last_notified_at, notify_count=1):
    """已成功发送过的 FIRING episode。"""
    open_alert = mock.MagicMock(spec=AlertEvent)
    open_alert.status = AlertStatus.FIRING
    open_alert.send_detail = [
        {'channel': '钉钉告警助手', 'type': 'DINGTALK', 'ok': True, 'detail': 'ok'}
    ]
    open_alert.last_notified_at = last_notified_at
    open_alert.notify_count = notify_count
    open_alert.message = '目标不可用'
    return open_alert


def _run(target, open_alert, manual, channels=None):
    log = mock.MagicMock()
    log.status = TargetStatus.DOWN
    channels = channels if channels is not None else [mock.MagicMock()]
    with mock.patch.object(models.MonitorTarget, 'primary_channels',
                           new_callable=mock.MagicMock) as mgr:
        mgr.filter.return_value = channels
        with mock.patch.object(models.AlertEvent.objects, 'filter',
                               return_value=mock.MagicMock(first=lambda: open_alert)):
            target.maybe_alert(log, manual=manual)
    return log


def test_manual_check_resends_after_cooldown():
    """手动检测：episode 已存在且已发过，超过 manual 冷却(5min) → 必重推。"""
    open_alert = _make_open_alert(timezone.now() - timedelta(minutes=10))
    target = _make_target()
    log = _run(target, open_alert, manual=True)
    target._dispatch_alert.assert_called_once()
    assert log.triggered_alert is True


def test_auto_check_respects_repeat_interval():
    """自动监测：episode 已存在且已发过，仍在 repeat_interval(30min) 内 → 不重推。"""
    open_alert = _make_open_alert(timezone.now() - timedelta(minutes=10))
    target = _make_target()
    log = _run(target, open_alert, manual=False)
    target._dispatch_alert.assert_not_called()
    assert log.triggered_alert is not True


def test_auto_check_resends_after_repeat_interval():
    """自动监测：超过 repeat_interval(30min) → 重推（持续 DOWN 仍周期提醒）。"""
    open_alert = _make_open_alert(timezone.now() - timedelta(minutes=40))
    target = _make_target()
    log = _run(target, open_alert, manual=False)
    target._dispatch_alert.assert_called_once()
    assert log.triggered_alert is True


def test_manual_check_within_cooldown_skips():
    """手动检测：仍在 manual 冷却(5min) 内 → 不重推（防狂点刷屏）。"""
    open_alert = _make_open_alert(timezone.now() - timedelta(minutes=1))
    target = _make_target()
    log = _run(target, open_alert, manual=True)
    target._dispatch_alert.assert_not_called()
    assert log.triggered_alert is not True


def test_resend_increments_notify_count_and_message():
    """重推时 notify_count 自增，且消息含'第 N 次提醒'便于追溯。"""
    open_alert = _make_open_alert(timezone.now() - timedelta(minutes=40), notify_count=2)
    target = _make_target()
    _run(target, open_alert, manual=False)
    assert open_alert.notify_count == 3
    assert '第 3 次提醒' in open_alert.message
    assert open_alert.last_notified_at is not None
