"""智能评分器 API 视图。

对齐 apps/api_testing 和 apps/data_factory 的写法：
- ModelViewSet + DefaultRouter
- IsAuthenticated
- @action 扩展自定义动作
- DjangoFilterBackend + SearchFilter + 分页
"""
import logging
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .filters import JudgeRecordFilter
from .models import JudgeBatch, JudgeRecord, Rubric
from .serializers import (
    JudgeBatchRequestSerializer, JudgeBatchSerializer, JudgeRecordSerializer,
    JudgeSingleRequestSerializer, RubricCreateSerializer, RubricSerializer,
)

logger = logging.getLogger(__name__)


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 200


class RubricViewSet(viewsets.ModelViewSet):
    """评分标准 CRUD + 预设模板克隆。"""
    queryset = Rubric.objects.filter(is_active=True).prefetch_related('dimensions', 'rules')
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['domain', 'is_default', 'is_active']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at']
    ordering = ['-is_default', '-created_at']

    def get_serializer_class(self):
        if self.action == 'create':
            return RubricCreateSerializer
        return RubricSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='set-default')
    def set_default(self, request, pk=None):
        rubric = self.get_object()
        rubric.is_default = True
        rubric.save(update_fields=['is_default', 'updated_at'])
        return Response(RubricSerializer(rubric).data)

    @action(detail=False, methods=['get'], url_path='presets')
    def presets(self, request):
        """列出可克隆的预设模板。"""
        qs = Rubric.objects.filter(is_active=True).exclude(domain='custom')
        return Response(RubricSerializer(qs, many=True).data)


class JudgeRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """评分记录（只读 + 过滤 + 分页）。"""
    queryset = JudgeRecord.objects.select_related('rubric', 'batch').all()
    serializer_class = JudgeRecordSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_class = JudgeRecordFilter
    search_fields = ['question', 'answer', 'request_id']
    ordering_fields = ['created_at', 'final_score', 'latency_ms']
    ordering = ['-created_at']


class JudgeBatchViewSet(viewsets.ModelViewSet):
    """批量评分批次：创建即异步执行，list/retrieve 查状态和汇总。"""
    queryset = JudgeBatch.objects.select_related('rubric').all()
    serializer_class = JudgeBatchSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    http_method_names = ['get', 'post', 'head', 'options']

    def create(self, request, *args, **kwargs):
        serializer = JudgeBatchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        cases = data['cases']
        if len(cases) > 5000:
            return Response({'error': f'单次批量评分最多 5000 条用例，当前 {len(cases)} 条，请分批提交。'},
                            status=status.HTTP_400_BAD_REQUEST)
        # 字段预校验：每条必须有 question/answer
        for i, case in enumerate(cases):
            if not isinstance(case, dict):
                return Response({'error': f'第 {i+1} 条用例格式错误：应为 JSON 对象，实际 {type(case).__name__}'},
                                status=status.HTTP_400_BAD_REQUEST)
            if not (case.get('question') or '').strip():
                return Response({'error': f'第 {i+1} 条用例缺少 question（问题）'},
                                status=status.HTTP_400_BAD_REQUEST)
            if not (case.get('answer') or '').strip():
                return Response({'error': f'第 {i+1} 条用例缺少 answer（答案）'},
                                status=status.HTTP_400_BAD_REQUEST)

        rubric_id = data.get('rubric')
        if rubric_id:
            rubric = Rubric.objects.filter(pk=rubric_id, is_active=True).first()
        else:
            rubric = Rubric.objects.filter(is_default=True, is_active=True).first()

        creator = None if isinstance(request.user, AnonymousUser) else request.user
        batch = JudgeBatch.objects.create(
            name=data.get('name') or f'批量评分 {timezone.now().strftime("%Y-%m-%d %H:%M")}',
            rubric=rubric,
            cases_data=data['cases'],
            total=len(data['cases']),
            status='running',   # 避免 delay 失败还显示「排队中」；实际是否入队由 celery_task_id/error_message 反映
            is_paused=False,
            results_buffer=[],
            error_count=0,
            created_by=creator,
            started_at=timezone.now(),
        )

        from .tasks import score_batch_task
        from celery import current_app as _celery_app

        sync_fallback_msg = None

        def _broker_reachable(timeout: float = 2.0) -> bool:
            '''用 kombu 自带连接探测，2s 内失败即视为 Celery/Broker 不可用，避免用户 HTTP 阻塞 50s。
            '''
            try:
                from kombu import Connection as _KombuConn
                url = getattr(_celery_app.conf, 'broker_url', None) or getattr(_celery_app.connection(), 'as_uri', lambda: '')()
                if not url or url.startswith('memory') or url.startswith('sqla'):
                    return True
                transport_options = {'connect_timeout': timeout, 'socket_timeout': timeout,
                                     'socket_connect_timeout': timeout,
                                     'retry_on_timeout': False, 'max_retries': 0}
                with _KombuConn(url, connect_timeout=timeout, transport_options=transport_options) as conn:
                    conn.ensure_connection(max_retries=0, timeout=timeout)
                return True
            except Exception as prob_err:
                logger.info(f'[JudgeBatch#{batch.id}] Celery broker 不可达（{prob_err}），切后台线程执行')
                return False

        def _run_fallback_in_thread(batch_id, delay_err=None):
            '''在 daemon 线程里跑同步兜底，HTTP 立即返回，前端通过轮询看进度。
            '''
            import threading
            from django.db import close_old_connections

            def _worker():
                close_old_connections()
                logger.warning(f'[JudgeBatch#{batch_id}] 后台线程执行批量评分: {delay_err}')
                orig_fn = score_batch_task.__wrapped__.__func__
                class R: id = f'SYNC{batch_id}'
                class FakeS:
                    request = R()
                    def update_state(self, **kw): pass
                try:
                    orig_fn(FakeS(), batch_id)
                except Exception as sync_exc:
                    logger.exception(f'[JudgeBatch#{batch_id}] 后台线程执行失败')
                    JudgeBatch.objects.filter(pk=batch_id).update(
                        status='failed',
                        error_message=f'后台执行失败: {sync_exc}',
                        completed_at=timezone.now(),
                        is_paused=False,
                    )
                finally:
                    close_old_connections()

            t = threading.Thread(target=_worker, daemon=True, name=f'judge-batch-{batch_id}')
            t.start()

        if _broker_reachable(timeout=2.0):
            # apply_async(retry=False)：不做 producer 级重试；立即失败就走 fallback
            try:
                async_result = score_batch_task.apply_async(
                    (batch.id,), retry=False, ignore_result=False,
                )
                batch.celery_task_id = async_result.id or ''
                batch.status = 'running'
                batch.save(update_fields=['celery_task_id', 'status'])
            except Exception as delay_exc:
                sync_fallback_msg = f'Celery 投递失败已切换后台执行: {delay_exc}'
                _run_fallback_in_thread(batch.id, delay_err=str(delay_exc))
        else:
            sync_fallback_msg = 'Celery broker 不可达，已切换后台执行'
            _run_fallback_in_thread(batch.id, delay_err='broker_unreachable')

        if sync_fallback_msg:
            JudgeBatch.objects.filter(pk=batch.id).update(error_message=sync_fallback_msg)
            batch.error_message = sync_fallback_msg

        return Response(JudgeBatchSerializer(batch).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='progress')
    def progress(self, request, pk=None):
        batch = self.get_object()
        return Response({
            'id': batch.id,
            'status': batch.status,
            'progress': batch.progress,
            'scored': batch.scored,
            'total': batch.total,
            'mean_score': batch.mean_score,
            'gate_zone': batch.gate_zone,
            'blocked': batch.blocked,
        })

    @action(detail=True, methods=['get'], url_path='records')
    def records(self, request, pk=None):
        batch = self.get_object()
        records = batch.records.all().order_by('id')
        return Response(JudgeRecordSerializer(records, many=True).data)

    @action(detail=True, methods=['post'], url_path='pause')
    def pause(self, request, pk=None):
        '''Pause: atomic conditional update is_paused=True + status=paused.
        Allowed from pending/running/partial; idempotent if already paused.
        '''
        from django.db import transaction
        with transaction.atomic():
            batch = JudgeBatch.objects.select_for_update().filter(pk=pk).first()
            if not batch:
                return Response({'error': 'batch not found'}, status=status.HTTP_404_NOT_FOUND)
            if batch.status in ('completed', 'failed'):
                return Response({'error': f'status {batch.status} cannot be paused'}, status=status.HTTP_400_BAD_REQUEST)
            if batch.is_paused or batch.status == 'paused':
                return Response(JudgeBatchSerializer(batch).data)
            # best-effort celery revoke (non-terminate)
            if batch.celery_task_id:
                try:
                    from .tasks import score_batch_task as _t
                    _t.AsyncResult(batch.celery_task_id).revoke(terminate=False)
                except Exception:
                    pass
            rows = JudgeBatch.objects.filter(
                pk=batch.id,
                status__in=('pending', 'running', 'partial'),
                is_paused=False,
            ).update(is_paused=True, status='paused')
            if rows == 1:
                batch.refresh_from_db()
        if rows == 1:
            return Response(JudgeBatchSerializer(batch).data)
        batch.refresh_from_db()
        return Response(JudgeBatchSerializer(batch).data)

    @action(detail=True, methods=['post'], url_path='resume')
    def resume(self, request, pk=None):
        '''Resume: set status=running + is_paused=False; re-dispatch Celery task.
        Allowed from paused/partial/failed/pending; worker continues from scored index.
        '''
        from django.db import transaction
        with transaction.atomic():
            batch = JudgeBatch.objects.select_for_update().filter(pk=pk).first()
            if not batch:
                return Response({'error': 'batch not found'}, status=status.HTTP_404_NOT_FOUND)
            if batch.total == 0:
                return Response({'error': 'no cases in batch'}, status=status.HTTP_400_BAD_REQUEST)
            if batch.status not in ('paused', 'partial', 'failed', 'pending'):
                return Response({'error': f'status {batch.status} cannot be resumed (only paused/partial/failed/pending allowed)'},
                                status=status.HTTP_400_BAD_REQUEST)
            rows = JudgeBatch.objects.filter(
                pk=batch.id,
                status__in=('paused', 'partial', 'failed', 'pending'),
            ).update(is_paused=False, status='running')
            if rows == 1:
                batch.refresh_from_db()
                if batch.scored >= batch.total:
                    JudgeBatch.objects.filter(pk=batch.id).update(status='completed', completed_at=timezone.now())
                    batch.refresh_from_db()
                    return Response(JudgeBatchSerializer(batch).data)
                if batch.started_at is None:
                    JudgeBatch.objects.filter(pk=batch.id).update(started_at=timezone.now())
                    batch.refresh_from_db()
                from .tasks import score_batch_task
                from celery import current_app as _celery_app
                import threading as _th
                from django.db import close_old_connections as _close_old_conn

                def _resume_fallback_thread(batch_id, delay_err=None):
                    '''在 daemon 线程里续跑，HTTP 立即返回，前端轮询看进度。
                    '''
                    def _worker():
                        _close_old_conn()
                        logger.warning(f'[JudgeBatch#{batch_id}] resume: 后台线程续跑: {delay_err}')
                        orig_fn = score_batch_task.__wrapped__.__func__
                        class R: id = f'SYNC{batch_id}'
                        class FakeS:
                            request = R()
                            def update_state(self, **kw): pass
                        try:
                            orig_fn(FakeS(), batch_id)
                        except Exception as sync_exc:
                            logger.exception(f'[JudgeBatch#{batch_id}] resume: 后台续跑失败')
                            JudgeBatch.objects.filter(pk=batch_id).update(
                                status='failed',
                                error_message=f'后台续跑失败: {sync_exc}',
                                completed_at=timezone.now(),
                                is_paused=False,
                            )
                        finally:
                            _close_old_conn()
                    _th.Thread(target=_worker, daemon=True, name=f'judge-resume-{batch_id}').start()

                def _broker_reachable(timeout: float = 2.0) -> bool:
                    try:
                        from kombu import Connection as _KombuConn
                        url = getattr(_celery_app.conf, 'broker_url', None) or getattr(_celery_app.connection(), 'as_uri', lambda: '')()
                        if not url or url.startswith('memory') or url.startswith('sqla'):
                            return True
                        transport_options = {'connect_timeout': timeout, 'socket_timeout': timeout,
                                             'socket_connect_timeout': timeout,
                                             'retry_on_timeout': False, 'max_retries': 0}
                        with _KombuConn(url, connect_timeout=timeout, transport_options=transport_options) as conn:
                            conn.ensure_connection(max_retries=0, timeout=timeout)
                        return True
                    except Exception:
                        return False

                if _broker_reachable(timeout=2.0):
                    try:
                        async_result = score_batch_task.apply_async((batch.id,), retry=False)
                        JudgeBatch.objects.filter(pk=batch.id).update(celery_task_id=async_result.id or '')
                    except Exception as delay_exc:
                        JudgeBatch.objects.filter(pk=batch.id).update(error_message=f'resume: 投递失败已切换后台: {delay_exc}')
                        _resume_fallback_thread(batch.id, delay_err=str(delay_exc))
                else:
                    JudgeBatch.objects.filter(pk=batch.id).update(error_message='resume: broker 不可达，已切换后台续跑')
                    _resume_fallback_thread(batch.id, delay_err='broker_unreachable')
        return Response(JudgeBatchSerializer(batch).data)


class JudgeSingleView(APIView):
    """单条评分（同步）：Django 进程内直接调 JudgeService。"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = JudgeSingleRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from .service import JudgeService
        service = JudgeService()
        try:
            user = request.user if not isinstance(request.user, AnonymousUser) else None
            result = service.score_single(data, created_by=user)
            return Response(result, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.exception('[LLMJudge] 单条评分失败')
            return Response({
                'error': str(exc),
                'detail': '评分失败。请检查 OPENAI_API_KEY 或设置 JUDGE_MOCK=true。',
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class DashboardStatsView(APIView):
    """Dashboard 聚合统计。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        days = int(request.query_params.get('days', 7))
        rubric_id = request.query_params.get('rubric')
        now = timezone.now()
        start = now - timedelta(days=days)

        qs = JudgeRecord.objects.filter(created_at__gte=start)
        if rubric_id:
            qs = qs.filter(rubric_id=rubric_id)

        total = qs.count()
        vetoed_count = qs.filter(vetoed=True).count()
        green = qs.filter(gate_zone='green').count()
        yellow = qs.filter(gate_zone='yellow').count()
        red = qs.filter(gate_zone='red').count()

        label_dist = list(qs.values('overall_label').annotate(c=Count('id')).order_by('-c'))
        label_dict = {item['overall_label']: item['c'] for item in label_dist}

        # 否决原因 TOP5
        veto_top = []
        for rec in qs.filter(vetoed=True).values('rule_findings')[:500]:
            for f in rec.get('rule_findings', []):
                veto_top.append(f.get('rule', 'unknown'))
        veto_top5 = Counter(veto_top).most_common(5)

        # 近 N 天平均分趋势
        daily = qs.annotate(day=TruncDate('created_at')).values('day').annotate(
            mean_score=Avg('final_score'), cnt=Count('id')
        ).order_by('day')

        cache_hit = qs.filter(cache_hit=True).count()
        avg_latency = qs.aggregate(v=Avg('latency_ms'))['v'] or 0
        mean_score = qs.aggregate(v=Avg('final_score'))['v'] or 0

        return Response({
            'total': total,
            'vetoed_count': vetoed_count,
            'veto_rate': vetoed_count / max(1, total),
            'pass_rate': (green + yellow) / max(1, total),
            'zone_distribution': {'green': green, 'yellow': yellow, 'red': red},
            'label_distribution': label_dict,
            'veto_top5': [{'rule': r, 'count': c} for r, c in veto_top5],
            'daily_trend': list(daily),
            'cache_hit_rate': cache_hit / max(1, total),
            'avg_latency_ms': avg_latency,
            'mean_score': mean_score,
            'days': days,
        })


class JudgeServiceConfigView(APIView):
    """评分服务配置：连通性测试。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            'judge_model': getattr(settings, 'JUDGE_MODEL', 'deepseek-chat'),
            'judge_mock': getattr(settings, 'JUDGE_MOCK', False),
            'n_runs': getattr(settings, 'JUDGE_N_RUNS', 3),
            'rule_llm_fallback': getattr(settings, 'JUDGE_RULE_LLM_FALLBACK', False),
            'cache_timeout': getattr(settings, 'JUDGE_CACHE_TIMEOUT', 3600),
            'openai_base_url': getattr(settings, 'OPENAI_BASE_URL', None),
            'has_api_key': bool(getattr(settings, 'OPENAI_API_KEY', '')),
        })

    def post(self, request):
        """测试 LLM 连通性。"""
        try:
            from .judge_engine.config import JudgeConfig
            from .judge_engine.judge import JudgeEngine
            cfg = JudgeConfig.from_settings()
            engine = JudgeEngine(cfg)
            ok, msg = engine.test_connection()
            return Response({'ok': ok, 'message': msg})
        except Exception as exc:
            return Response({'ok': False, 'message': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

# ============================================================
# 知识库维护 + 文本解析 + 文件上传解析 API
# ============================================================
from django.http import HttpResponse
from rest_framework import viewsets, status as drf_status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser

from .models import (KnowledgeBase, KBCompany, KBReportPeriod, KBMetric, KBMetricValue)
from .serializers import (
    KnowledgeBaseSerializer, KBCompanySerializer, KBReportPeriodSerializer,
    KBMetricSerializer, KBMetricValueSerializer,
    KBParseTextRequestSerializer, KBImportRequestSerializer,
)
from .kb_service import export_kb_to_json, parse_kb_text, import_parsed_kb, annotate_kb_stats
from .file_parser import detect_and_parse, build_template_bytes


class KnowledgeBaseViewSet(viewsets.ModelViewSet):
    """知识库 CRUD。"""
    serializer_class = KnowledgeBaseSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_fields = ['domain', 'is_default', 'is_active']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at']
    ordering = ['-is_default', '-updated_at']

    def get_queryset(self):
        return annotate_kb_stats(KnowledgeBase.objects.filter(is_active=True))

    def perform_create(self, serializer):
        validated = dict(serializer.validated_data)
        domain = validated.get('domain') or 'finance'
        if validated.get('is_default'):
            KnowledgeBase.objects.filter(domain=domain, is_default=True).update(is_default=False)
        serializer.save(updated_by=self.request.user)

    def perform_update(self, serializer):
        validated = dict(serializer.validated_data)
        instance = serializer.instance
        new_domain = validated.get('domain') or (instance.domain if instance else 'finance')
        if validated.get('is_default') and not (instance and instance.is_default and instance.domain == new_domain):
            # 先把同领域其他 KB 的默认位取消
            KnowledgeBase.objects.filter(domain=new_domain, is_default=True).exclude(pk=instance.pk if instance else -1).update(is_default=False)
        elif validated.get('is_default') is False and instance and instance.is_default:
            # 手动取消当前默认：同领域自动兜底（更新时间最新那条）
            other = KnowledgeBase.objects.filter(domain=instance.domain, is_active=True).exclude(pk=instance.pk).order_by('-updated_at').first()
            if other:
                other.is_default = True
                other.save(update_fields=['is_default', 'updated_at'])
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        # 软删除：关闭 is_active；并把 is_default 迁移到同领域其他 KB
        if instance.is_default:
            other = KnowledgeBase.objects.filter(domain=instance.domain, is_active=True).exclude(pk=instance.pk).order_by('-updated_at').first()
            if other:
                other.is_default = True
                other.save(update_fields=['is_default', 'updated_at'])
        instance.is_active = False
        instance.is_default = False
        instance.save(update_fields=['is_active', 'is_default', 'updated_at'])

    @action(detail=True, methods=['post'], url_path='set-default')
    def set_default(self, request, pk=None):
        kb = self.get_object()
        KnowledgeBase.objects.filter(domain=kb.domain, is_default=True).exclude(pk=kb.pk).update(is_default=False)
        kb.is_default = True
        kb.updated_by = request.user
        kb.save(update_fields=['is_default', 'updated_by', 'updated_at'])
        return Response(KnowledgeBaseSerializer(kb).data)

    @action(detail=True, methods=['post'], url_path='export')
    def export_json(self, request, pk=None):
        """将 KB 数据导出为 financial_kb.json 并同步到 gt_provider 使用的位置。"""
        kb = self.get_object()
        try:
            path = export_kb_to_json(kb.id)
            return Response({'kb_id': kb.id, 'kb_name': kb.name, 'exported_path': str(path)})
        except Exception as exc:
            logger.exception('[KB] export 失败')
            return Response({'error': str(exc)}, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='parse-text')
    def parse_text(self, request):
        """从非结构化文本解析出结构化 KB（预览，不写入 DB）。"""
        sz = KBParseTextRequestSerializer(data=request.data)
        sz.is_valid(raise_exception=True)
        d = sz.validated_data
        kb = KnowledgeBase.objects.filter(pk=d['kb']).first()
        if not kb:
            return Response({'error': '指定的知识库不存在'}, status=drf_status.HTTP_404_NOT_FOUND)
        parsed = parse_kb_text(d['text'], kb,
                               force_company=d.get('company') or '',
                               force_period=d.get('period') or '')
        return Response({
            'kb_id': kb.id,
            'kb_name': kb.name,
            'parsed': parsed,
        })

    @action(detail=False, methods=['post'], url_path='import')
    def import_parsed(self, request):
        """把 parse_text 的结果写入 DB（用户在前端确认后调用）。"""
        sz = KBImportRequestSerializer(data=request.data)
        sz.is_valid(raise_exception=True)
        d = sz.validated_data
        kb = KnowledgeBase.objects.filter(pk=d['kb']).first()
        if not kb:
            return Response({'error': '指定的知识库不存在'}, status=drf_status.HTTP_404_NOT_FOUND)
        parsed = {
            'companies': d.get('companies', []),
            'periods': d.get('periods', []),
            'metrics': d.get('metrics', []),
            'values': d.get('values', []),
        }
        user = request.user if not isinstance(request.user, AnonymousUser) else None
        try:
            stats = import_parsed_kb(kb, parsed, user=user)
        except Exception as exc:
            logger.exception('[KB] import 失败')
            return Response({'error': str(exc)}, status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
        kb.updated_by = user
        kb.save(update_fields=['updated_by', 'updated_at'])
        return Response({'kb_id': kb.id, 'stats': stats})


class KBCompanyViewSet(viewsets.ModelViewSet):
    serializer_class = KBCompanySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter]
    filterset_fields = ['kb']
    search_fields = ['name']

    def get_queryset(self):
        return KBCompany.objects.annotate(
            metric_count=Count('metric_values', distinct=True)
        ).all()


class KBReportPeriodViewSet(viewsets.ModelViewSet):
    serializer_class = KBReportPeriodSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter]
    filterset_fields = ['kb']
    search_fields = ['name']
    queryset = KBReportPeriod.objects.all()


class KBMetricViewSet(viewsets.ModelViewSet):
    serializer_class = KBMetricSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter]
    filterset_fields = ['kb']
    search_fields = ['name']
    queryset = KBMetric.objects.all()


class KBMetricValueViewSet(viewsets.ModelViewSet):
    serializer_class = KBMetricValueSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['company', 'period', 'metric', 'company__kb']
    queryset = KBMetricValue.objects.select_related('company', 'period', 'metric').all()

    def perform_create(self, serializer):
        user = self.request.user if not isinstance(self.request.user, AnonymousUser) else None
        serializer.save(updated_by=user)

    def perform_update(self, serializer):
        user = self.request.user if not isinstance(self.request.user, AnonymousUser) else None
        serializer.save(updated_by=user)


class BatchFileUploadView(APIView):
    """批量评分：上传 CSV/XLSX/TXT 文件 → 解析成 cases（预览 + 错误提示）。"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({'error': '请上传文件 (file 字段)'}, status=drf_status.HTTP_400_BAD_REQUEST)
        filename = file.name or ''
        allowed_exts = {'.csv', '.xlsx', '.xlsm', '.txt', '.md'}
        if not any(filename.lower().endswith(e) for e in allowed_exts):
            return Response({
                'error': f'仅支持 {", ".join(allowed_exts)} 格式',
                'filename': filename,
            }, status=drf_status.HTTP_400_BAD_REQUEST)
        # 大小限制：10MB
        if file.size > 10 * 1024 * 1024:
            return Response({'error': '文件大小超过 10MB 限制'}, status=drf_status.HTTP_400_BAD_REQUEST)
        try:
            content = file.read()
        except Exception as exc:
            return Response({'error': f'读取文件失败: {exc}'}, status=drf_status.HTTP_400_BAD_REQUEST)
        try:
            result = detect_and_parse(filename, content)
        except Exception as exc:
            logger.exception('[BatchFile] 解析异常')
            return Response({'error': f'文件解析失败: {exc}'}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(result)


class BatchTemplateDownloadView(APIView):
    """下载批量用例模板文件（CSV/XLSX/TXT）。模板不含用户数据，允许匿名下载。"""
    permission_classes = [AllowAny]

    def get(self, request):
        kind = (request.query_params.get('tpl') or request.query_params.get('format') or 'csv').lower()
        if kind not in {'csv', 'xlsx', 'txt'}:
            kind = 'csv'
        data, fn, ctype = build_template_bytes(kind)
        resp = HttpResponse(data, content_type=ctype)
        from urllib.parse import quote
        resp['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(fn)}"
        return resp
