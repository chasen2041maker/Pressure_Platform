from django.contrib import admin

from .models import McpCallLog, McpPendingConfirm


@admin.register(McpCallLog)
class McpCallLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'tool_name', 'status', 'duration_ms', 'created_at']
    list_filter = ['status', 'tool_name']
    search_fields = ['tool_name']
    readonly_fields = ['user', 'tool_name', 'args_digest', 'status',
                       'duration_ms', 'error', 'created_at']


@admin.register(McpPendingConfirm)
class McpPendingConfirmAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'tool_name', 'status', 'expires_at', 'created_at']
    list_filter = ['status', 'tool_name']
    readonly_fields = ['user', 'tool_name', 'arguments', 'preview', 'status',
                       'result', 'expires_at', 'created_at', 'updated_at']
