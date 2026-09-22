"""monitor 告警补发逻辑单元测试（mock，不依赖 DB）。

回归背景（2026-07-23）：
目标 37「tmp_verify_target」连续失败达阈值创建了 FIRING 告警(id=8)，
但其 send_detail=[]（首次触发时尚未绑定钉钉渠道，派发循环空转）。
之后补绑钉钉渠道，但因 open_alert 仍 FIRING，maybe_alert 去重分支直接
return open_alert 不再通知，导致钉钉永远收不到。
修复：open_alert 已存在但从未成功发送过通知时，补发。
"""
import os

import django
from unittest import mock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.utils import timezone

from apps.monitor import models
from apps.monitor.models import MonitorTarget, TargetStatus, AlertEvent


def _make_target(consecutive_failures=5, alert_threshold=1, sent=None):
    """真实 MonitorTarget 实例（不落库），覆盖 _dispatch_alert 以便隔离 maybe_alert。"""
    t = MonitorTarget(
        name='tmp', type='HTTP',
        consecutive_failures=consecutive_failures,
        alert_threshold=alert_threshold, status=TargetStatus.DOWN,
    )
    t._dispatch_alert = mock.MagicMock(return_value=sent or [
        {'channel': '钉钉告警助手', 'type': 'DINGTALK', 'ok': True, 'detail': 'ok'}
    ])
    return t


def _run(target, open_alert, channels):
    """在隔离 primary_channels / AlertEvent.objects 的前提下执行 maybe_alert。"""
    log = mock.MagicMock()
    log.status = TargetStatus.DOWN
    with mock.patch.object(models.MonitorTarget, 'primary_channels',
                           new_callable=mock.MagicMock) as mgr:
        mgr.filter.return_value = channels
        with mock.patch.object(models.AlertEvent.objects, 'filter',
                               return_value=mock.MagicMock(first=lambda: open_alert)):
            target.maybe_alert(log)
    return log


def test_maybe_alert_resends_when_open_alert_never_sent():
    """open_alert 存在但其 send_detail 为空（从未成功通知）→ 应补发。"""
    open_alert = mock.MagicMock(spec=AlertEvent)
    open_alert.send_detail = []
    target = _make_target()
    log = _run(target, open_alert, [mock.MagicMock()])
    target._dispatch_alert.assert_called_once()
    assert log.triggered_alert is True


def test_maybe_alert_no_resend_when_open_alert_already_sent():
    """open_alert 已成功发送过（send_detail 含 ok=True）→ 不应重复补发（回归保护）。"""
    open_alert = mock.MagicMock(spec=AlertEvent)
    open_alert.send_detail = [{'channel': '钉钉告警助手', 'type': 'DINGTALK', 'ok': True, 'detail': 'ok'}]
    open_alert.last_notified_at = timezone.now()
    open_alert.first_triggered_at = timezone.now()
    target = _make_target()
    log = _run(target, open_alert, [mock.MagicMock()])
    target._dispatch_alert.assert_not_called()
    assert log.triggered_alert is not True


def test_maybe_alert_resends_when_open_alert_all_failed():
    """open_alert 存在但 send_detail 全 ok=False（均发送失败）→ 应补发重试。"""
    open_alert = mock.MagicMock(spec=AlertEvent)
    open_alert.send_detail = [{'channel': '钉钉告警助手', 'type': 'DINGTALK', 'ok': False, 'detail': 'webhook 未配置'}]
    target = _make_target()
    log = _run(target, open_alert, [mock.MagicMock()])
    target._dispatch_alert.assert_called_once()
    assert log.triggered_alert is True
