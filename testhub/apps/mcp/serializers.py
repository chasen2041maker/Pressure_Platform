from rest_framework import serializers

from .models import McpCallLog, McpPendingConfirm


class McpCallLogSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True, default='')

    class Meta:
        model = McpCallLog
        fields = ['id', 'username', 'tool_name', 'args_digest', 'args_brief',
                  'client_name', 'status', 'duration_ms', 'error', 'created_at']


class McpPendingConfirmSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True, default='')

    class Meta:
        model = McpPendingConfirm
        fields = ['id', 'username', 'tool_name', 'preview', 'arguments',
                  'status', 'awaiting_human', 'result', 'expires_at',
                  'created_at', 'updated_at']
