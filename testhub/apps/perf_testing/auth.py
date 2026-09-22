"""性能测试「可分享直链」鉴权。

报告 (report) 与原始数据 (download-raw) 默认走 DRF 认证，浏览器直开/新标签会 401。
这里提供 ShareTokenAuthentication：请求携带有效且未过期的 ?token= 时直接放行，
使这两个动作变成可分享直链；否则仍要求正常登录（JWT/Token/Session）。
"""
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import NotAuthenticated

from .models import PerfExecution


class ShareTokenAuthentication(BaseAuthentication):
    """从 query 参数读取分享令牌。仅在 ?token= 存在时介入。"""

    def authenticate(self, request):
        token = request.query_params.get('token')
        if not token:
            return None
        try:
            execution = PerfExecution.objects.select_related('executed_by').get(
                share_token=token)
        except PerfExecution.DoesNotExist:
            raise NotAuthenticated('分享链接无效')
        if execution.share_expires_at and execution.share_expires_at < timezone.now():
            raise NotAuthenticated('分享链接已过期')
        view = request.parser_context.get('view')
        if (getattr(view, 'action', None) not in ('report', 'download_raw')
                or str(view.kwargs.get('pk')) != str(execution.pk)):
            raise NotAuthenticated('分享链接不属于该执行')
        request._perf_share_execution_id = execution.pk
        from django.contrib.auth.models import AnonymousUser
        return (AnonymousUser(), None)



class HasPerfShareTokenOrAuthenticated(BasePermission):
    """分享令牌有效 或 已正常登录 均可访问。"""

    def has_permission(self, request, view):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            return True
        return bool(getattr(request, '_perf_share_execution_id', None))
