"""可配置重提醒间隔：基线默认由 Django settings 决定（全局可配置），而非写死 30/5。

这样运维改 .env 即可调整全站基线，同时前端仍可在单目标上覆盖（per-target）。
本测试覆盖：未显式指定字段时，实例默认值取自 settings。
"""
import os

import django
from unittest import mock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.conf import settings

from apps.monitor.models import MonitorTarget


def _with_setting(key, value, fn):
    """临时改写 settings 某项并还原，避免污染其它用例。"""
    old = getattr(settings, key, None)
    settings.__setattr__(key, value)
    try:
        return fn()
    finally:
        if old is None:
            settings.__delattr__(key)
        else:
            settings.__setattr__(key, old)


def test_default_alert_repeat_interval_from_settings():
    """未指定 alert_repeat_interval 时，取 settings 全局默认（可配置）。"""
    def run():
        t = MonitorTarget(name='cfg', type='HTTP')
        return t.alert_repeat_interval
    assert _with_setting('MONITOR_DEFAULT_ALERT_REPEAT_INTERVAL', 45, run) == 45


def test_default_manual_alert_cooldown_from_settings():
    """未指定 manual_alert_cooldown 时，取 settings 全局默认（可配置）。"""
    def run():
        t = MonitorTarget(name='cfg', type='HTTP')
        return t.manual_alert_cooldown
    assert _with_setting('MONITOR_DEFAULT_MANUAL_ALERT_COOLDOWN', 8, run) == 8
