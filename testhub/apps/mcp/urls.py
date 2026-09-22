from django.urls import path

from .views import (
    McpCallLogListView,
    McpConnectionConfigView,
    McpPendingApproveView,
    McpPendingListView,
    McpPendingRejectView,
    McpProtocolFallbackView,
    McpToolCatalogView,
    McpToolDetailView,
)

urlpatterns = [
    # 协议端点兜底（ASGI 下该精确路径被 asgi.py 分流到 MCP 子应用，
    # 仅 WSGI/runserver 或 MCP_ENABLED=False 时落到此视图）
    path('', McpProtocolFallbackView.as_view(), name='mcp-protocol-fallback'),
    # 工具目录（前端工具中心使用）
    path('tools/', McpToolCatalogView.as_view(), name='mcp-tools'),
    path('tools/<str:name>/', McpToolDetailView.as_view(), name='mcp-tool-detail'),
    # 接入配置（端点 + 本人 API-Key，供客户端配置直接复制）
    path('config/', McpConnectionConfigView.as_view(), name='mcp-config'),
    # 管理端 REST（监控页使用）
    path('logs/', McpCallLogListView.as_view(), name='mcp-logs'),
    path('pending/', McpPendingListView.as_view(), name='mcp-pending'),
    path('pending/<int:pk>/approve/', McpPendingApproveView.as_view(), name='mcp-pending-approve'),
    path('pending/<int:pk>/reject/', McpPendingRejectView.as_view(), name='mcp-pending-reject'),
]
