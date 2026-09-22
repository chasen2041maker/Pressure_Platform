"""monitor 序列化器单元测试（不依赖 DB，纯字段/方法级）。

回归背景（2026-07-23）：
探测历史页(CheckLogs)的「类型」列显示异常（空白或翻译 key 串）。
根因：MonitorCheckLog 模型本身没有 type 字段（type 在 MonitorTarget 上），
而 MonitorCheckLogSerializer 用 fields='__all__'，序列化结果不含 type，
前端 row.type 为 undefined，导致 $t('monitor.dashboard.type.undefined') 显示 key 串。
修复：在序列化器暴露 type（SerializerMethodField 从 target 取）。
"""
import os

import django
from unittest import mock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from apps.monitor.serializers import MonitorCheckLogSerializer


def test_checklog_serializer_exposes_type():
    """序列化器必须对外暴露 type 字段，否则前端类型列为空。"""
    fields = MonitorCheckLogSerializer().fields
    assert 'type' in fields, "MonitorCheckLogSerializer 必须暴露 type 字段"
    assert isinstance(fields['type'], __import__(
        'rest_framework', fromlist=['serializers']).serializers.SerializerMethodField)


def test_checklog_serializer_get_type_returns_target_type():
    """get_type 应从关联的 target 读取类型。"""
    ser = MonitorCheckLogSerializer()
    obj = mock.Mock()
    obj.target.type = 'LOGIN'
    assert ser.get_type(obj) == 'LOGIN'


def test_checklog_serializer_get_type_handles_null_target():
    """target 为空时回退空串，避免页面报错。"""
    ser = MonitorCheckLogSerializer()
    obj = mock.Mock()
    obj.target = None
    assert ser.get_type(obj) == ''
