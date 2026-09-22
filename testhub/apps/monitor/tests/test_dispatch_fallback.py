"""monitor 一级/二级通道分发逻辑单元测试（mock，不依赖 DB）。

覆盖核心容灾语义：
- 一级通道只要有任一成功 → 不再走二级（避免重复打扰）；
- 一级通道全部失败（或一级未配置）→ 自动切换二级通道推送，结果标记 level=secondary / fallback=True；
- 未配置一级时直接走二级。
"""
import os
from unittest import mock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
import django
django.setup()

from apps.monitor import models
from apps.monitor.models import MonitorTarget, TargetStatus


def _fake_channel(name, ctype):
    ch = mock.MagicMock()
    ch.name = name
    ch.type = ctype
    ch.enabled = True
    return ch


def _make_target():
    t = MonitorTarget(
        name='t', type='HTTP',
        consecutive_failures=3, status=TargetStatus.DOWN,
    )
    return t


def _patch_channels(primary, secondary):
    """在类级别打桩 primary_channels / secondary_channels / checks 三个描述符。"""
    pm = mock.patch.object(models.MonitorTarget, 'primary_channels', new_callable=mock.MagicMock)
    sm = mock.patch.object(models.MonitorTarget, 'secondary_channels', new_callable=mock.MagicMock)
    cm = mock.patch.object(models.MonitorTarget, 'checks', new_callable=mock.MagicMock)
    pmgr = pm.start(); smgr = sm.start(); cmgr = cm.start()
    pmgr.filter.return_value = primary
    smgr.filter.return_value = secondary
    cmgr.order_by.return_value.first.return_value = None
    return pm, sm, cm


def test_primary_success_skips_secondary():
    """一级通道成功 → 不触发二级（无重复打扰）。"""
    t = _make_target()
    primary = _fake_channel('钉钉', 'DINGTALK')
    secondary = _fake_channel('企业微信', 'WECOM')
    patchers = _patch_channels([primary], [secondary])
    try:
        with mock.patch('apps.monitor.utils.notifiers.send_via_channel', return_value=(True, 'ok')):
            results = t._dispatch_alert(mock.MagicMock(), recovered=False)
    finally:
        for p in patchers:
            p.stop()
    assert any(r['level'] == 'primary' and r['ok'] for r in results)
    assert not any(r['level'] == 'secondary' for r in results)


def test_primary_failure_falls_back_to_secondary():
    """一级全部失败 → 自动切换二级，且结果标记 fallback。"""
    t = _make_target()
    primary = _fake_channel('钉钉', 'DINGTALK')
    secondary = _fake_channel('企业微信', 'WECOM')
    patchers = _patch_channels([primary], [secondary])
    try:
        def fake_send(ch, message=None, subject=None):
            if ch.type == 'DINGTALK':
                return False, 'webhook 未配置'
            return True, 'ok'
        with mock.patch('apps.monitor.utils.notifiers.send_via_channel', side_effect=fake_send):
            results = t._dispatch_alert(mock.MagicMock(), recovered=False)
    finally:
        for p in patchers:
            p.stop()
    primary_results = [r for r in results if r['level'] == 'primary']
    secondary_results = [r for r in results if r['level'] == 'secondary']
    assert primary_results and all(not r['ok'] for r in primary_results)
    assert secondary_results and all(r['ok'] for r in secondary_results)
    assert all(r.get('fallback') for r in secondary_results)


def test_no_primary_configured_uses_secondary():
    """一级未配置 → 直接走二级通道。"""
    t = _make_target()
    secondary = _fake_channel('企业微信', 'WECOM')
    patchers = _patch_channels([], [secondary])
    try:
        with mock.patch('apps.monitor.utils.notifiers.send_via_channel', return_value=(True, 'ok')):
            results = t._dispatch_alert(mock.MagicMock(), recovered=False)
    finally:
        for p in patchers:
            p.stop()
    assert any(r['level'] == 'secondary' for r in results)
