"""监控中心视图。

#1 阶段：提供 4 张表的基础 CRUD（ModelViewSet），全部需 JWT 鉴权。
后续功能点在此基础上扩展动作接口（check_now / dashboard 等）。
"""
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta
from types import SimpleNamespace

from .models import (
    MonitorTarget, MonitorCheckLog, NotificationChannel, AlertEvent,
    TargetStatus, TargetType, AlertStatus,
)
from .serializers import (
    MonitorTargetSerializer,
    MonitorCheckLogSerializer,
    NotificationChannelSerializer,
    AlertEventSerializer,
)
from .utils.notifiers import send_via_channel
from .utils.scheduler_heartbeat import get_scheduler_status


class MonitorPagination(PageNumberPagination):
    """监控模块列表统一分页：默认 20 条/页，允许前端用 page_size 调整，上限 200 防止超大页拖垮性能。"""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 200


class MonitorTargetViewSet(viewsets.ModelViewSet):
    queryset = MonitorTarget.objects.all()
    serializer_class = MonitorTargetSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = MonitorPagination

    def get_queryset(self):
        qs = super().get_queryset()
        # 性能优化：消除 N+1 查询（created_by FK + channels M2M）
        qs = qs.select_related('created_by').prefetch_related(
            'primary_channels', 'secondary_channels')
        target_type = self.request.query_params.get('type')
        status = self.request.query_params.get('status')
        search = self.request.query_params.get('search', '').strip()
        if target_type:
            qs = qs.filter(type=target_type)
        if status:
            qs = qs.filter(status=status)
        if search:
            qs = qs.filter(name__icontains=search)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def check_now(self, request, pk=None):
        """手动立即探测一次，返回最新探测记录。"""
        target = self.get_object()
        try:
            log = target.run_check(manual=True)
        except Exception as exc:
            return Response({"detail": f"检测执行失败: {exc}"}, status=500)
        return Response(MonitorCheckLogSerializer(log).data)

    @action(detail=False, methods=['post'])
    def debug_test(self, request):
        """调试测试：用前端提交的探测配置（未保存）模拟执行一次探测，返回实时结果。"""
        from .utils.probes import run_probe, ProbeResult

        probe_type = request.data.get('type', 'HTTP')
        cfg = request.data.get('check_config', {})

        # 构造轻量 target-like 对象供 run_probe 使用
        temp = SimpleNamespace(
            type=probe_type,
            url=request.data.get('url', ''),
            method=request.data.get('method', 'GET'),
            host=request.data.get('host', ''),
            port=request.data.get('port'),
        )

        result = run_probe(temp, cfg)

        # 对敏感字段做截断
        detail = dict(result.detail or {})
        for sk in ('token', 'password', 'secret'):
            if sk in detail:
                detail[sk] = str(detail[sk])[:12] + '...' if len(str(detail[sk])) > 12 else detail[sk]

        return Response({
            'ok': result.ok,
            'message': result.message,
            'latency_ms': result.latency_ms,
            'http_status': result.http_status,
            'detail': detail,
        })


class NotificationChannelViewSet(viewsets.ModelViewSet):
    queryset = NotificationChannel.objects.all()
    serializer_class = NotificationChannelSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = MonitorPagination

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        """向该渠道发送一条测试消息，返回发送结果。"""
        channel = self.get_object()
        if not channel.enabled:
            return Response({'success': False, 'detail': '渠道已禁用'}, status=400)
        ok, msg = send_via_channel(
            channel, message='【监控中心】这是一条测试消息', subject='监控中心测试'
        )
        return Response({'success': ok, 'detail': msg})


class MonitorCheckLogViewSet(viewsets.ModelViewSet):
    queryset = MonitorCheckLog.objects.all().order_by('-checked_at')
    serializer_class = MonitorCheckLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = MonitorPagination

    def get_queryset(self):
        qs = super().get_queryset()
        target = self.request.query_params.get('target')
        status = self.request.query_params.get('status')
        start = self.request.query_params.get('start_time')
        end = self.request.query_params.get('end_time')
        if target:
            qs = qs.filter(target_id=target)
        if status:
            qs = qs.filter(status=status)
        if start:
            qs = qs.filter(checked_at__gte=start)
        if end:
            # 若 end 仅为日期（无空格），补到当日 23:59:59
            end_val = end + ' 23:59:59' if ' ' not in end else end
            qs = qs.filter(checked_at__lte=end_val)
        return qs


class AlertEventViewSet(viewsets.ModelViewSet):
    queryset = AlertEvent.objects.all()
    serializer_class = AlertEventSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = MonitorPagination

    def get_queryset(self):
        qs = super().get_queryset()
        status = self.request.query_params.get('status')
        target = self.request.query_params.get('target')
        start = self.request.query_params.get('start_time')
        end = self.request.query_params.get('end_time')
        if status:
            qs = qs.filter(status=status)
        if target:
            qs = qs.filter(target_id=target)
        if start:
            qs = qs.filter(last_triggered_at__gte=start)
        if end:
            # 若 end 仅为日期（无空格），补到当日 23:59:59
            end_val = end + ' 23:59:59' if ' ' not in end else end
            qs = qs.filter(last_triggered_at__lte=end_val)
        return qs

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        """认领告警：将状态置为 ACKED，记录认领人/时间。"""
        alert = self.get_object()
        if alert.status == AlertStatus.RESOLVED:
            return Response({'detail': '已恢复的告警无需认领'}, status=400)
        alert.status = AlertStatus.ACKED
        alert.acknowledged_by = request.user
        alert.acknowledged_at = timezone.now()
        alert.save(update_fields=['status', 'acknowledged_by', 'acknowledged_at'])
        return Response(AlertEventSerializer(alert).data)

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """手动恢复告警：将状态置为 RESOLVED。"""
        alert = self.get_object()
        if alert.status == AlertStatus.RESOLVED:
            return Response({'detail': '告警已恢复'}, status=400)
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = timezone.now()
        alert.save(update_fields=['status', 'resolved_at'])
        return Response(AlertEventSerializer(alert).data)


class DashboardViewSet(viewsets.ViewSet):
    """看板聚合：概览统计 + 按类型分布 + 24h 可用率趋势 + 最近失败。

    只读聚合，全部需 JWT 鉴权。前端看板页直接消费本接口。
    """
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get'])
    def scheduler(self, request):
        """调度器在线状态：online / offline / unknown。"""
        return Response({'status': get_scheduler_status()})

    def list(self, request):
        now = timezone.now()

        # 1) 概览：基于目标当前 status（冗余字段，看板直读）
        targets = MonitorTarget.objects
        total = targets.count()
        by_status = dict(
            targets.values_list('status').order_by('status')
            .annotate(c=Count('status')).values_list('status', 'c')
        )
        up = by_status.get('UP', 0)
        down = by_status.get('DOWN', 0)
        unknown = by_status.get('UNKNOWN', 0)
        enabled = targets.filter(enabled=True).count()
        disabled = total - enabled
        checked = up + down
        availability = round(up / checked * 100, 1) if checked else None
        active_alerts = AlertEvent.objects.filter(
            status__in=[AlertStatus.FIRING, AlertStatus.ACKED]
        ).count()

        # 2) 按类型分布
        by_type_rows = (
            targets.values('type', 'status')
            .order_by('type', 'status')
            .annotate(c=Count('status'))
        )
        by_type_map = {}
        for r in by_type_rows:
            t = r['type']
            entry = by_type_map.setdefault(t, {'type': t, 'label': dict(TargetType.CHOICES).get(t, t),
                                               'total': 0, 'UP': 0, 'DOWN': 0, 'UNKNOWN': 0})
            entry['total'] += r['c']
            entry[r['status']] = r['c']
        by_type = list(by_type_map.values())

        # 3) 24h 可用率趋势（Python 端按小时槽聚合，规避 MySQL 时区表缺失问题）
        #    用「感知型 datetime 相减算整数小时索引」，天然免疫时区转换陷阱。
        #    槽起点按本地时区对齐整点（分桶与标签同基准），ts 输出带 +08:00 偏移，避免前端显示 UTC 小时。
        start = timezone.localtime(now).replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
        buckets = {}
        for log in (MonitorCheckLog.objects
                    .filter(checked_at__gte=start)
                    .only('status', 'checked_at')):
            idx = int((log.checked_at - start).total_seconds() // 3600)
            if 0 <= idx < 24:
                b = buckets.setdefault(idx, {'UP': 0, 'DOWN': 0})
                b[log.status] = b.get(log.status, 0) + 1

        trend = []
        for i in range(24):
            slot = start + timedelta(hours=i)
            b = buckets.get(i, {'UP': 0, 'DOWN': 0})
            tot = b['UP'] + b['DOWN']
            avail = round(b['UP'] / tot * 100, 1) if tot else None
            trend.append({
                'ts': slot.isoformat(),
                'up': b['UP'],
                'down': b['DOWN'],
                'availability': avail,
            })

        # 4) 最近失败（最新 10 条 DOWN 记录，带目标信息）
        recent_failures = []
        for log in (MonitorCheckLog.objects
                    .filter(status=TargetStatus.DOWN)
                    .select_related('target')
                    .order_by('-checked_at')[:10]):
            t = log.target
            recent_failures.append({
                'target_id': t.id,
                'name': t.name,
                'type': t.type,
                'checked_at': log.checked_at.isoformat() if log.checked_at else None,
                'latency_ms': log.latency_ms,
                'message': log.error_message,
            })

        return Response({
            'summary': {
                'total': total,
                'enabled': enabled,
                'disabled': disabled,
                'up': up,
                'down': down,
            'unknown': unknown,
            'availability': availability,
            'active_alerts': active_alerts,
        },
            'by_type': by_type,
            'trend': trend,
            'recent_failures': recent_failures,
        })
