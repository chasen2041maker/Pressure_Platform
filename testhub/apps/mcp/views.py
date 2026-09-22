"""MCP 管理端 REST 接口（普通 HTTP，runserver/Daphne 均可用）。

- /api/mcp/：协议端点兜底视图（WSGI 下提示需 Daphne；ASGI 下该路径
  被 asgi.py 分流拦截，不会走到这里）
- /api/mcp/tools/：工具目录（汇总 + 全量工具元数据/参数 schema/近 7 天统计）
- /api/mcp/tools/{name}/：单工具详情（含完整描述）
- /api/mcp/config/：接入配置（协议端点 + 本人长效 API-Key，供客户端配置直接复制）
- /api/mcp/logs/：调用日志（管理员全量，普通用户仅本人；支持 tool/status/时间范围过滤）
- /api/mcp/pending/：待确认操作列表（同上，读取时惰性修正过期状态）
- /api/mcp/pending/{id}/approve|reject/：手动批准/拒绝（本人或管理员）
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView

from .confirm import ConfirmError, approve_pending, reject_pending
from .models import McpCallLog, McpPendingConfirm
from .registry import CATEGORY_ORDER, TOOL_REGISTRY, catalog_summary, get_tool_meta
from .serializers import McpCallLogSerializer, McpPendingConfirmSerializer

logger = logging.getLogger(__name__)


def _is_privileged(user):
    return bool(user.is_superuser or user.is_staff)


class McpPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


# --------------------------------------------------------------------- #
# 工具目录（元数据来自注册表，input_schema 由 FastMCP 生成，统计来自调用日志）
# --------------------------------------------------------------------- #

def _tool_input_schemas() -> dict:
    """从 FastMCP 工具管理器生成 name -> inputSchema 映射。

    仅构建实例子不启动会话管理器，WSGI/Daphne 均可用；
    SDK 升级导致接口失效时降级为空字典，不阻断目录展示。
    """
    try:
        from .server import get_mcp
        return {t.name: t.parameters for t in get_mcp()._tool_manager.list_tools()}
    except Exception:  # noqa: BLE001
        logger.warning('MCP 工具 inputSchema 获取失败（SDK 接口变更？）', exc_info=True)
        return {}


def _tool_stats() -> dict:
    """近 7 天按工具聚合的调用统计：次数/成功数/成功率/平均耗时。"""
    since = timezone.now() - timedelta(days=7)
    rows = McpCallLog.objects.filter(created_at__gte=since).values('tool_name').annotate(
        calls=Count('id'),
        success=Count('id', filter=Q(status='success')),
        avg_duration_ms=Avg('duration_ms'),
    )
    stats = {}
    for r in rows:
        calls = r['calls'] or 0
        success = r['success'] or 0
        stats[r['tool_name']] = {
            'calls': calls,
            'success': success,
            'success_rate': round(success / calls, 4) if calls else None,
            'avg_duration_ms': int(r['avg_duration_ms']) if r['avg_duration_ms'] is not None else None,
        }
    return stats


_EMPTY_STATS = {'calls': 0, 'success': 0, 'success_rate': None, 'avg_duration_ms': None}


def _serialize_tool(meta, input_schema, stats, detail=False):
    data = {
        'name': meta.name,
        'title': meta.title,
        'category': meta.category,
        'domain': meta.domain,
        'summary': meta.summary,
        'annotations': meta.annotations_dict,
        'paired_with': meta.paired_with,
        'input_schema': input_schema or {},
        'examples': list(meta.examples),
        'stats': stats or dict(_EMPTY_STATS),
    }
    if detail:
        data['description'] = meta.description
    return data


class McpToolCatalogView(APIView):
    """工具目录：汇总（总数/分类计数/近 7 天调用量）+ 全量工具列表。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        schemas = _tool_input_schemas()
        stats = _tool_stats()
        items = [_serialize_tool(meta, schemas.get(meta.name), stats.get(meta.name))
                 for meta in TOOL_REGISTRY.values()]
        items.sort(key=lambda t: (CATEGORY_ORDER.index(t['category']), t['name']))
        summary = catalog_summary()
        summary['calls_7d'] = sum(s['calls'] for s in stats.values())
        return Response({'summary': summary, 'tools': items})


class McpToolDetailView(APIView):
    """单工具详情：目录字段 + 完整描述。"""
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        meta = get_tool_meta(name)
        if meta is None:
            return Response({'error': f'工具 {name} 不存在'},
                            status=status.HTTP_404_NOT_FOUND)
        schemas = _tool_input_schemas()
        stats = _tool_stats()
        return Response(_serialize_tool(meta, schemas.get(name), stats.get(name), detail=True))


class McpConnectionConfigView(APIView):
    """接入配置：协议端点 + 本人长效 API-Key（DRF Token）。

    仅返回当前用户自己的凭证，供页面生成可复制的客户端配置；
    API-Key 长效不过期，优于短时效 JWT（Authorization: Bearer）。
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not getattr(settings, 'MCP_ENABLED', True):
            return Response({'error': 'MCP Server 已被管理员关闭（settings.MCP_ENABLED=False）'},
                            status=status.HTTP_403_FORBIDDEN)
        from rest_framework.authtoken.models import Token
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response({
            'server_name': 'TestHub',
            'endpoint': request.build_absolute_uri('/api/mcp/'),
            'tool_count': catalog_summary()['total'],
            'api_key': token.key,
            'auth_headers': {
                'api_key': {'x-mcp-api-key': token.key},
                'jwt': {'Authorization': 'Bearer <JWT access_token>'},
            },
        })


@method_decorator(csrf_exempt, name='dispatch')
class McpProtocolFallbackView(View):
    """WSGI（runserver）下访问协议端点的兜底提示（纯 Django View）。

    不用 DRF APIView：MCP 客户端常携带 ``Accept: text/event-stream`` 探测端点，
    APIView 在 initialize_request 内容协商阶段就会因无匹配渲染器抛 406，
    视图逻辑根本来不及执行。改为纯 Django View + 手动复用 DRF 认证，
    任意 Accept 头都能拿到 501 + daphne 启动提示。
    """

    def _check_auth(self, request):
        from .auth import authenticate_headers

        # 协议头（JWT Bearer / x-mcp-api-key），与 ASGI 协议端点鉴权同源
        headers = {k.lower(): v for k, v in request.headers.items()}
        if authenticate_headers(headers) is not None:
            return None
        # 会话登录（浏览器侧访问，AuthenticationMiddleware 已解析 request.user）
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            return None
        return self._json_response({'error': '未认证：需登录会话、JWT 或 x-mcp-api-key'},
                                   status=status.HTTP_401_UNAUTHORIZED)

    def handle(self, request):
        err = self._check_auth(request)
        if err is not None:
            return err
        if not getattr(settings, 'MCP_ENABLED', True):
            return self._json_response(
                {'error': 'MCP Server 已被管理员关闭（settings.MCP_ENABLED=False）'},
                status=status.HTTP_403_FORBIDDEN)
        return self._json_response({
            'error': 'MCP streamable-http 协议端点需要 ASGI 服务器',
            'hint': '请使用 daphne 启动：daphne -b 0.0.0.0 -p 8000 backend.asgi:application',
            'endpoint': '/api/mcp/',
        }, status=status.HTTP_501_NOT_IMPLEMENTED)

    @staticmethod
    def _json_response(payload, status):
        body = json.dumps(payload, ensure_ascii=False)
        return HttpResponse(body, status=status,
                            content_type='application/json; charset=utf-8')

    def get(self, request):
        return self.handle(request)

    def post(self, request):
        return self.handle(request)

    def delete(self, request):
        return self.handle(request)


class McpCallLogListView(ListAPIView):
    """调用日志：管理员全量，普通用户仅本人；支持 tool/status/时间范围过滤。"""
    serializer_class = McpCallLogSerializer
    pagination_class = McpPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = McpCallLog.objects.select_related('user')
        if not _is_privileged(self.request.user):
            qs = qs.filter(user=self.request.user)
        params = self.request.query_params
        tool = params.get('tool')
        if tool:
            qs = qs.filter(tool_name=tool)
        log_status = params.get('status')
        if log_status:
            qs = qs.filter(status=log_status)
        created_after = parse_datetime(params.get('created_after', '')) if params.get('created_after') else None
        if created_after:
            qs = qs.filter(created_at__gte=created_after)
        created_before = parse_datetime(params.get('created_before', '')) if params.get('created_before') else None
        if created_before:
            qs = qs.filter(created_at__lte=created_before)
        return qs


class McpPendingListView(ListAPIView):
    """待确认操作：管理员全量，普通用户仅本人；支持 status 过滤。

    读取时惰性将已过期但仍为 pending 的记录批量修正为 expired，
    避免列表出现假「待确认」。
    """
    serializer_class = McpPendingConfirmSerializer
    pagination_class = McpPagination
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        McpPendingConfirm.objects.filter(
            status='pending', expires_at__lt=timezone.now(),
        ).update(status='expired')
        qs = McpPendingConfirm.objects.select_related('user')
        if not _is_privileged(self.request.user):
            qs = qs.filter(user=self.request.user)
        pending_status = self.request.query_params.get('status')
        if pending_status:
            qs = qs.filter(status=pending_status)
        return qs


class _PendingActionView(APIView):
    """approve/reject 公共基座：仅本人或管理员可操作。"""
    permission_classes = [IsAuthenticated]

    def _get_pending(self, request, pk):
        pending = McpPendingConfirm.objects.filter(id=pk).first()
        if pending is None:
            return None, Response({'error': '待确认操作不存在'},
                                  status=status.HTTP_404_NOT_FOUND)
        if pending.user_id != request.user.id and not _is_privileged(request.user):
            return None, Response({'error': '无权操作其他用户发起的待确认操作'},
                                  status=status.HTTP_403_FORBIDDEN)
        return pending, None


class McpPendingApproveView(_PendingActionView):
    """手动批准：服务端直接触发该危险操作执行。"""

    def post(self, request, pk):
        pending, err = self._get_pending(request, pk)
        if err:
            return err
        try:
            result = approve_pending(pending, request.user)
        except ConfirmError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        logger.info('MCP 待确认操作[%s]被 %s 批准执行', pk, request.user.username)
        return Response({'status': 'approved', 'result': result})


class McpPendingRejectView(_PendingActionView):
    """手动拒绝：confirm 令牌作废。"""

    def post(self, request, pk):
        pending, err = self._get_pending(request, pk)
        if err:
            return err
        try:
            reject_pending(pending, request.user)
        except ConfirmError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        logger.info('MCP 待确认操作[%s]被 %s 拒绝', pk, request.user.username)
        return Response({'status': 'rejected'})
