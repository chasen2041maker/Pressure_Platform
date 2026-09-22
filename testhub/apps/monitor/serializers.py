"""监控中心序列化器。

读取时对敏感子键（password/token/secret/webhook 等）做掩码，
写入时接受明文（加密落库在当前阶段由 create/update 逻辑在 #2/#3 接入）。
"""
from rest_framework import serializers

from .models import (
    MonitorTarget, MonitorCheckLog, NotificationChannel, AlertEvent,
)
from .utils.crypto import is_secret_key


def mask_secrets(data):
    if not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = mask_secrets(v)
        elif is_secret_key(k) and v not in (None, '', '******'):
            out[k] = '******'
        else:
            out[k] = v
    return out


def merge_config(existing, incoming):
    """合并配置：

    - incoming 中值为掩码 '******' 的保留 existing（即不改秘密）。
    - incoming 中敏感键值为空串 '' 且 existing 已有该键时，保留 existing
      （编辑时把密码框清空 == 不修改，避免误清空）。
    - 其余键直接覆盖。
    """
    if not isinstance(incoming, dict):
        return incoming
    merged = dict(existing or {})
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = merge_config(merged[k], v)
        elif v == '******':
            continue  # 保留原值，不覆盖
        elif v == '' and is_secret_key(k) and k in (existing or {}):
            continue  # 编辑时清空敏感字段视为不修改
        else:
            merged[k] = v
    return merged


class MonitorTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonitorTarget
        fields = '__all__'
        read_only_fields = (
            'status', 'last_check_at', 'consecutive_failures',
            'next_check_at', 'created_at', 'updated_at',
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # retrieve（详情/编辑）返回明文，便于查看与修改；list/看板掩码敏感键
        view = self.context.get('view')
        action = getattr(view, 'action', None)
        if action == 'retrieve':
            data['check_config'] = instance.get_decrypted_check_config()
        else:
            data['check_config'] = mask_secrets(data.get('check_config') or {})
        return data

    def update(self, instance, validated_data):
        incoming = validated_data.get('check_config')
        if incoming is not None:
            validated_data['check_config'] = merge_config(
                instance.get_decrypted_check_config(), incoming
            )
        return super().update(instance, validated_data)


class NotificationChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationChannel
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # retrieve（编辑回显）返回明文配置；list 等掩码敏感键
        view = self.context.get('view')
        action = getattr(view, 'action', None)
        if action == 'retrieve':
            data['config'] = instance.get_decrypted_config()
        else:
            data['config'] = mask_secrets(data.get('config') or {})
        return data

    def update(self, instance, validated_data):
        incoming = validated_data.get('config')
        if incoming is not None:
            validated_data['config'] = merge_config(
                instance.get_decrypted_config(), incoming
            )
        return super().update(instance, validated_data)


class MonitorCheckLogSerializer(serializers.ModelSerializer):
    target_name = serializers.SerializerMethodField(read_only=True)
    type = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = MonitorCheckLog
        fields = '__all__'

    def get_target_name(self, obj):
        return obj.target.name if obj.target else ''

    def get_type(self, obj):
        return obj.target.type if obj.target else ''


class AlertEventSerializer(serializers.ModelSerializer):
    target_name = serializers.SerializerMethodField(read_only=True)
    acknowledged_by_username = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = AlertEvent
        fields = '__all__'

    def get_target_name(self, obj):
        return obj.target.name if obj.target else ''

    def get_acknowledged_by_username(self, obj):
        return obj.acknowledged_by.username if obj.acknowledged_by else ''
