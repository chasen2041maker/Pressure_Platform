"""性能测试模块视图。

对齐 apps/api_testing/views.py 的既有范式：
DefaultRouter + ModelViewSet + FlexiblePageNumberPagination + IsAuthenticated。

这里所有"会真的打流量"的入口（execute/debug/run-now）都必须先过 preflight，
它是平台唯一的护栏：并发上限、时长上限、禁压主机、并发执行数都在那里卡。
"""
import csv
import io
import json
import logging
import os
import shutil
from datetime import timedelta
from copy import deepcopy

from django.conf import settings
from django.db import models as db_models
from django.db import transaction
from django.http import FileResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, APIException
from django.db.models.deletion import ProtectedError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from .auth import HasPerfShareTokenOrAuthenticated, ShareTokenAuthentication

from .engines import engine_status
from .models import (PerfBaseline, PerfComparisonReport, PerfDataFile, PerfExecution,
                     PerfMetricSample, PerfProject, PerfRequestStat, PerfScenario,
                     PerfScenarioStep, PerfScheduledTask, PerfEnvironment, PerfAccountPoolVersion)
from .operation_logger import log_operation
from .serializers import (PerfBaselineSerializer, PerfDataFileSerializer,
                          PerfExecutionDetailSerializer, PerfExecutionListSerializer,
                          PerfMetricSampleSerializer, PerfProjectSerializer,
                          PerfRequestStatSerializer, PerfScenarioListSerializer,
                          PerfScenarioSerializer, PerfScenarioStepSerializer,
                          PerfScheduledTaskSerializer, PerfEnvironmentSerializer,
                          PerfAccountPoolSerializer, PerfAccountPoolVersionSerializer)
from .services import cleanup as cleanup_service
from .services import executor, reporter
from .services import environments, account_pools, api_catalog, catalog_source, prepared_requests
from functools import wraps

logger = logging.getLogger(__name__)


def catalog_boundary(func):
    @wraps(func)
    def safe(self, request, *args, **kwargs):
        try:
            for field in ('pk', 'request_id'):
                if field in kwargs:
                    kwargs[field] = api_catalog.bounded_integer(kwargs[field], field)
            return func(self, request, *args, **kwargs)
        except (APIException, Http404, DjangoPermissionDenied):
            raise
        except Exception as exc:
            logger.warning('接口库操作失败：%s', type(exc).__name__)
            return Response({'error': '接口库操作暂时失败，请稍后重试', 'code': 'api_catalog_unavailable'}, status=503)
    return safe


class CatalogPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


def catalog_page(items, request):
    pager = CatalogPagination()
    return pager.get_paginated_response(pager.paginate_queryset(items, request))


def catalog_upload(request):
    upload = request.FILES.get('file')
    if upload is None or upload.size > api_catalog.MAX_BYTES:
        raise api_catalog.CatalogInputError('请上传不超过 8 MB 的 OpenAPI/Swagger 文件')
    return api_catalog.parse_document(upload.read(api_catalog.MAX_BYTES + 1))


def catalog_source_url(request):
    if 'source_url' in request.data:
        if request.FILES.get('file') is not None:
            raise api_catalog.CatalogInputError('文件和来源地址只能选择一种')
        return request.data.get('source_url')
    return None


def resolve_script_ref(scenario, raw):
    """把前端传来的 script_ref 收敛成服务端可信的引用。

    返回 (script_ref, error)。error 非空时调用方必须拒绝执行。

    安全约束（重要）：
    前端只允许提交 {'mode': 'script', 'data_file_id': N}，jmx_path 一律由服务端
    从 PerfDataFile 反查。直接信任前端传来的绝对路径等于开放任意文件读取——
    JMeter 会把该路径当测试计划加载，配合 JSR223/BeanShell 元件甚至可升级为
    任意命令执行。    解析后还会二次校验路径落在 MEDIA_ROOT 内，防止软链绕过。

    raw 为空时回落到场景自身持久化的配置 scenario.runtime_config['script_ref']，
    这样定时压测、复跑等不经过编辑器的触发路径也能复用同一份脚本选择。
    """
    if not raw:
        raw = (getattr(scenario, 'runtime_config', None) or {}).get('script_ref') or {}
    if not raw:
        return {}, None
    if not isinstance(raw, dict):
        return None, 'script_ref 必须是对象'

    mode = str(raw.get('mode') or '').strip().lower()
    if mode in ('', 'scenario'):
        return {}, None
    if mode != 'script':
        return None, f'不支持的执行模式：{mode}'
    if scenario.engine != 'JMETER':
        return None, '仅 JMeter 引擎支持上传脚本模式，请先把场景引擎切换为 JMeter'

    data_file_id = raw.get('data_file_id')
    if not data_file_id:
        return None, '脚本模式必须选择一个已上传的 .jmx 文件'
    try:
        data_file = PerfDataFile.objects.get(pk=data_file_id)
    except (PerfDataFile.DoesNotExist, ValueError, TypeError):
        return None, f'脚本文件不存在（id={data_file_id}）'
    if data_file.project_id != scenario.project_id:
        return None, '脚本文件不属于当前场景所在项目'
    if data_file.file_type != 'JMX':
        return None, f'文件「{data_file.name}」不是 JMeter 脚本'

    try:
        jmx_path = os.path.abspath(data_file.file.path)
        media_root = os.path.abspath(settings.MEDIA_ROOT)
        if os.path.commonpath([media_root, jmx_path]) != media_root:
            return None, '脚本文件路径非法'
    except (ValueError, NotImplementedError, AttributeError) as exc:
        return None, f'无法定位脚本文件：{exc}'
    if not os.path.exists(jmx_path):
        return None, '脚本文件已丢失，请重新上传'

    return {
        'mode': 'script',
        'data_file_id': data_file.id,
        'data_file_name': data_file.name,
        'jmx_path': jmx_path,
    }, None


class FlexiblePageNumberPagination(PageNumberPagination):
    """与 api_testing 保持一致：page_size=0 表示不分页返回全部。"""

    page_size_query_param = 'page_size'
    max_page_size = 10000

    def paginate_queryset(self, queryset, request, view=None):
        page_size = request.query_params.get(self.page_size_query_param)
        if page_size is not None:
            try:
                if int(page_size) == 0:
                    return None
            except (ValueError, TypeError):
                pass
        return super().paginate_queryset(queryset, request, view)


# ====================================================================== #
# 项目
# ====================================================================== #
class PerfProjectViewSet(viewsets.ModelViewSet):
    """压测项目"""

    queryset = PerfProject.objects.all().select_related('owner').prefetch_related('members')
    serializer_class = PerfProjectSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'owner']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at', 'name']

    def get_queryset(self):
        # 注解场景/执行计数，避免序列化器逐项目 count() 的 N+1。
        # 两个 Count 必须 distinct，否则 JOIN 会产生笛卡尔积放大。
        from django.db.models import Count
        queryset = super().get_queryset()
        if self.action not in ('update', 'partial_update', 'destroy'):
            queryset = queryset.filter(pk__in=environments.accessible_projects(self.request.user))
        return queryset.annotate(
            scenario_count_anno=Count('scenarios', filter=db_models.Q(scenarios__is_preparation=False), distinct=True),
            execution_count_anno=Count('executions', filter=~db_models.Q(executions__scenario__is_preparation=True), distinct=True),
        )

    def perform_create(self, serializer):
        project = serializer.save()
        log_operation('CREATE', 'PROJECT', project.id, project.name, self.request.user)

    def catalog_project(self):
        project = get_object_or_404(self.get_queryset(),
            pk=api_catalog.bounded_integer(self.kwargs['pk'], 'project'))
        self.check_object_permissions(self.request, project)
        return project

    @action(detail=True, methods=['get'], url_path='api-catalog')
    @catalog_boundary
    def api_catalog(self, request, pk=None):
        project = self.catalog_project()
        version = api_catalog.latest_version(project.pk)
        operations = version.operations if version else []
        search = request.query_params.get('search', '').casefold()
        tag = request.query_params.get('tag')
        method = request.query_params.get('method', '').upper()
        filtered = []
        prepared = prepared_requests.public_summaries(project, operations)
        selected_status = request.query_params.get('prepared_status')
        if selected_status and selected_status not in prepared_requests.STATUSES:
            raise api_catalog.CatalogInputError('接口准备状态无效')
        for operation in operations:
            if search and search not in (operation['request']['name'] + ' ' + operation['path'] + ' ' + operation['operation_id']).casefold():
                continue
            if tag and tag not in operation['tags'] or method and method != operation['method']:
                continue
            summary = {key: operation[key] for key in ('id', 'source_key', 'operation_id', 'path', 'method', 'order', 'tags', 'content_hash', 'collection')}
            summary.update(name=operation['request']['name'], version=version.version,
                readiness=api_catalog.request_readiness(operation['request'], operation))
            summary['prepared'] = prepared[operation['source_key']]
            if selected_status and summary['prepared']['status'] != selected_status:
                continue
            if request.query_params.get('ready') == 'true' and not summary['readiness']['ready']:
                continue
            filtered.append(summary)
        result = catalog_page(filtered, request)
        result.data['version'] = api_catalog.version_summary(version)
        result.data['tags'] = list(dict.fromkeys(tag for op in operations for tag in op['tags']))
        result.data['source'] = project.catalog_source
        return result

    @action(detail=True, methods=['get'], url_path=r'api-catalog/requests/(?P<request_id>[^/.]+)')
    @catalog_boundary
    def catalog_request(self, request, pk=None, request_id=None):
        project = self.catalog_project()
        request_id = api_catalog.bounded_integer(request_id, 'request_id')
        version = api_catalog.latest_version(project.pk)
        operation = next((op for op in version.operations if op['id'] == request_id), None) if version else None
        if operation is None:
            raise Http404()
        return Response({'operation': operation, 'version': api_catalog.version_summary(version),
            'readiness': api_catalog.request_readiness(operation['request'], operation),
            'prepared': prepared_requests.public_summaries(project, [operation])[operation['source_key']]})

    @action(detail=True, methods=['get', 'put'], url_path=r'api-pool/requests/(?P<request_id>[^/.]+)')
    @catalog_boundary
    def pool_request(self, request, pk=None, request_id=None):
        project = self.catalog_project()
        request_id = api_catalog.bounded_integer(request_id, 'request_id')
        if request.method == 'PUT':
            return Response(prepared_requests.save_edit(project.pk, request_id, request.data, request.user))
        return Response(prepared_requests.editable_request(project.pk, request_id, request.user))

    @action(detail=True, methods=['get', 'put'], url_path='api-pool/config')
    @catalog_boundary
    def pool_config(self, request, pk=None):
        project = self.catalog_project()
        if request.method == 'PUT':
            return Response(prepared_requests.save_config(project.pk, request.data, request.user))
        return Response(prepared_requests.config_response(project, request.user))

    @action(detail=True, methods=['get'], url_path='scenario-defaults')
    @catalog_boundary
    def scenario_defaults(self, request, pk=None):
        from .services import scenario_defaults
        return Response(scenario_defaults.for_project(self.catalog_project(), request.user))

    @action(detail=True, methods=['post'], url_path='api-pool/prepare')
    @catalog_boundary
    def pool_prepare(self, request, pk=None):
        project = self.catalog_project()
        return Response(prepared_requests.prepare(project.pk, request.data, request.user))

    @action(detail=True, methods=['post'], url_path='api-pool/verify')
    @catalog_boundary
    def pool_verify(self, request, pk=None):
        from .services import pool_verification
        project = self.catalog_project()
        payload = request.data
        allowed = {'selection', 'expected_catalog_version', 'request_key', 'confirm_writes'}
        if not isinstance(payload, dict) or set(payload) - allowed or type(payload.get('confirm_writes', False)) is not bool:
            raise api_catalog.CatalogInputError('验证参数无效')
        return Response(pool_verification.start_verification(project.pk, payload.get('selection'),
            payload.get('expected_catalog_version'), payload.get('request_key'), request.user,
            confirm_writes=payload.get('confirm_writes', False)))

    @action(detail=True, methods=['get'], url_path=r'api-pool/verification/(?P<batch_id>[^/.]+)')
    @catalog_boundary
    def pool_verification(self, request, pk=None, batch_id=None):
        from .models import PerfPreparationBatch
        from .services import pool_verification
        project = self.catalog_project()
        batch = get_object_or_404(PerfPreparationBatch, project=project,
            pk=api_catalog.bounded_integer(batch_id, 'batch_id'))
        return Response(pool_verification.batch_summary(batch, request.user))

    @action(detail=True, methods=['post'], url_path='api-catalog/preview')
    @catalog_boundary
    def catalog_preview(self, request, pk=None):
        project = self.catalog_project()
        source_url = catalog_source_url(request)
        if source_url is not None:
            return Response(catalog_source.preview(project.pk, source_url, request.user))
        return Response(api_catalog.preview(project.pk, catalog_upload(request), request.user))

    @action(detail=True, methods=['post'], url_path='api-catalog/import')
    @catalog_boundary
    def catalog_import(self, request, pk=None):
        project = self.catalog_project()
        source_url = catalog_source_url(request)
        if source_url is not None:
            result = catalog_source.confirm(project.pk, source_url, request.data.get('preview_token'),
                                            request.data.get('expected_version'), request.user)
        else:
            result = catalog_source.import_file(project.pk, catalog_upload(request), request.data.get('expected_version'), request.user)
        return Response(result, status=201 if result['changed'] else 200)

    @action(detail=True, methods=['get'], url_path='api-catalog/versions')
    @catalog_boundary
    def catalog_versions(self, request, pk=None):
        project = self.catalog_project()
        pager = CatalogPagination()
        page = pager.paginate_queryset(project.api_catalog_versions.defer('document', 'operations'), request)
        return pager.get_paginated_response([{'id': v.pk, 'version': v.version, 'content_hash': v.content_hash,
            'source_version': v.source_version, 'created_at': v.created_at} for v in page])

    def perform_update(self, serializer):
        environments.require_project_access(serializer.instance.pk, self.request.user)
        project = serializer.save()
        log_operation('UPDATE', 'PROJECT', project.id, project.name, self.request.user)

    def perform_destroy(self, instance):
        try:
            with transaction.atomic():
                project = PerfProject.objects.select_for_update().get(pk=instance.pk)
                environments.require_project_access(project.pk, self.request.user)
                running = PerfExecution.objects.filter(
                    project=project, status__in=PerfExecution.ACTIVE_STATUSES).count()
                if running:
                    raise ValidationError(f'项目下还有 {running} 个压测正在执行，请先停止后再删除')
                environment_ids = PerfEnvironment.objects.filter(project=project).values('pk')
                scenarios = PerfScenario.objects.filter(project=project)
                # PROTECT also sees references from scenes in this same cascade.
                scenarios.filter(environment_id__in=environment_ids).update(environment=None)
                scenarios.filter(global_environment_id__in=environment_ids).update(global_environment=None)
                pool_versions = PerfAccountPoolVersion.objects.filter(pool__project=project).values('pk')
                scenarios.filter(account_pool_version_id__in=pool_versions).update(account_pool_version=None)
                PerfExecution.objects.filter(project=project, account_pool_version_id__in=pool_versions).update(account_pool_version=None)
                project.delete()
                log_operation('DELETE', 'PROJECT', instance.id, instance.name, self.request.user)
        except ProtectedError:
            raise EnvironmentConflict('项目环境或账号池仍被删除范围之外的场景或运行引用，请先解除引用') from None

    @action(detail=True, methods=['get'])
    def statistics(self, request, pk=None):
        """项目概览：场景数、执行数、最近成功率、平均 TPS。"""
        project = self.get_object()
        executions = PerfExecution.objects.filter(project=project).exclude(scenario__is_preparation=True)
        recent = executions.order_by('-created_at')[:20]
        finished = [e for e in recent if e.status in PerfExecution.FINAL_STATUSES]
        sla_passed = len([e for e in finished if e.sla_result == 'PASSED'])
        tps_values = [(e.summary or {}).get('tps') or 0 for e in finished]

        return Response({
            'scenario_count': project.scenarios.filter(is_preparation=False).count(),
            'execution_count': executions.count(),
            'running_count': executions.filter(
                status__in=PerfExecution.ACTIVE_STATUSES).count(),
            'sla_pass_rate': round(sla_passed / len(finished) * 100, 1) if finished else 0,
            'avg_tps': round(sum(tps_values) / len(tps_values), 2) if tps_values else 0,
            'data_file_count': project.data_files.count(),
        })


# ====================================================================== #
# 场景
# ====================================================================== #
class EnvironmentConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = '环境已被场景引用，请先取消场景中的环境选择'


class PerfEnvironmentViewSet(viewsets.ModelViewSet):
    serializer_class = PerfEnvironmentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['scope', 'project', 'is_active']
    search_fields = ['name']
    ordering_fields = ['name', 'updated_at', 'created_at']

    def get_queryset(self):
        return environments.accessible_environments(self.request.user)

    @action(detail=False, methods=['get'])
    def permissions(self, request):
        return Response({
            'can_manage_global': environments.is_environment_admin(request.user),
            'project_ids': list(environments.accessible_projects(request.user).values_list('id', flat=True)),
        })

    def perform_destroy(self, instance):
        environments.require_environment_write(instance, self.request.user)
        try:
            instance.delete()
        except ProtectedError:
            raise EnvironmentConflict() from None


class PerfScenarioViewSet(viewsets.ModelViewSet):
    """压测场景"""

    queryset = PerfScenario.objects.all().select_related('project', 'created_by')
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['project', 'engine', 'enabled']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at', 'name']

    def get_serializer_class(self):
        if self.action == 'list':
            return PerfScenarioListSerializer
        return PerfScenarioSerializer

    def get_queryset(self):
        qs = super().get_queryset().filter(project__in=environments.accessible_projects(self.request.user))
        if self.action not in ('list', 'retrieve'):
            qs = qs.filter(is_preparation=False)
        if self.action == 'list':
            # 列表需要"最近一次执行"摘要。用关联子查询注解而非逐场景查询，
            # 把 PerfScenarioListSerializer.get_last_execution 的 N+1 收敛为单条 SQL。
            from django.db.models import OuterRef, Subquery
            latest = PerfExecution.objects.filter(
                scenario=OuterRef('pk'), project__in=environments.accessible_projects(self.request.user)).order_by('-created_at')
            return qs.filter(is_preparation=False).prefetch_related('steps').annotate(
                _latest_exec_id=Subquery(latest.values('id')[:1]),
                _latest_exec_no=Subquery(latest.values('execution_no')[:1]),
                _latest_exec_status=Subquery(latest.values('status')[:1]),
                _latest_exec_sla=Subquery(latest.values('sla_result')[:1]),
                _latest_exec_created=Subquery(latest.values('created_at')[:1]),
                _latest_summary=Subquery(latest.values('summary')[:1]),
                _latest_load=Subquery(latest.values('load_snapshot')[:1]),
            )
        return qs.prefetch_related('steps')

    def perform_create(self, serializer):
        scenario = serializer.save()
        log_operation('CREATE', 'SCENARIO', scenario.id, scenario.name, self.request.user)

    def perform_update(self, serializer):
        scenario = serializer.save()
        log_operation('UPDATE', 'SCENARIO', scenario.id, scenario.name, self.request.user)

    def perform_destroy(self, instance):
        if instance.has_active_execution():
            raise ValidationError('场景有正在执行的压测，请先停止后再删除')
        try:
            with transaction.atomic():
                log_operation('DELETE', 'SCENARIO', instance.id, instance.name, self.request.user)
                instance.delete()
        except ProtectedError:
            raise ValidationError('场景含仍需保留的执行或提醒恢复意图，请先完成恢复') from None

    # ------------------------------------------------------------------ #
    # 步骤批量保存：前端是一个可拖拽列表，整体提交比逐条 CRUD 简单可靠
    # ------------------------------------------------------------------ #
    @action(detail=True, methods=['post'], url_path='save-steps')
    @catalog_boundary
    def save_steps(self, request, pk=None):
        scenario = self.get_object()
        steps = request.data.get('steps')
        if not isinstance(steps, list) or len(steps) > api_catalog.MAX_BATCH:
            raise ValidationError('steps 必须为不超过 2000 条的数组')
        with transaction.atomic():
            scenario = PerfScenario.objects.select_for_update().get(pk=scenario.pk)
            environments.require_project_access(scenario.project_id, request.user)
            existing = {s.pk: s for s in scenario.steps.all()}
            retained = []
            for idx, raw in enumerate(steps):
                if not isinstance(raw, dict):
                    raise ValidationError('步骤必须为对象')
                payload = dict(raw)
                step_id = payload.pop('id', None)
                if step_id is not None:
                    step_id = api_catalog.bounded_integer(step_id, 'id')
                if step_id is not None and (step_id not in existing or step_id in retained):
                    raise ValidationError('步骤 ID 不属于当前场景或重复')
                payload['scenario'] = scenario.pk
                payload['order'] = payload.get('order', idx)
                serializer = PerfScenarioStepSerializer(instance=existing.get(step_id), data=payload,
                    context={'request': request})
                serializer.is_valid(raise_exception=True)
                referenced = {item['file_id'] for item in serializer.validated_data.get('files', []) if item.get('file_id')}
                valid = set(PerfDataFile.objects.filter(pk__in=referenced, project=scenario.project_id,
                    file_type='UPLOAD').values_list('pk', flat=True))
                if referenced - valid:
                    raise ValidationError('上传文件不存在或不属于当前项目')
                retained.append(serializer.save().pk)
            referenced_ids = {r.get('step_id') for r in (scenario.sla_config or {}).get('step_thresholds', [])}
            if referenced_ids - set(retained):
                raise ValidationError('请先在 SLA 页移除被删除步骤的规则并保存，再删除步骤')
            recovery_config = (scenario.runtime_config or {}).get('resource_recovery') or {}
            recovery_ids = {recovery_config.get(key) for key in ('put_step_id','receipt_step_id','get_step_id','delete_step_id')} if recovery_config else set()
            if recovery_ids - set(retained):
                raise ValidationError('请先解除提醒恢复步骤绑定并保存，再删除步骤')
            scenario.steps.exclude(pk__in=retained).delete()
            from .services.execution_policy import validate_policies
            policy_errors = validate_policies({'engine': scenario.engine, 'load_config': scenario.get_load_config(),
                'variables': scenario.variables, 'env_config': scenario.env_config,
                'runtime_config': scenario.runtime_config,
                'steps': list(scenario.steps.order_by('order', 'id').values())})
            if policy_errors:
                raise ValidationError({'execution_policy': policy_errors})
        scenario.refresh_from_db()
        return Response(PerfScenarioSerializer(scenario, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """复制场景（含步骤）。压测场景配置很重，复制比重建实用得多。"""
        source = self.get_object()
        environments.resolve_environment(source, user=request.user)
        source_steps = list(source.steps.all())
        new_name = (request.data.get('name') or f'{source.name} - 副本')[:200]

        with transaction.atomic():
            clone = PerfScenario.objects.create(
                project=source.project,
                name=new_name,
                description=source.description,
                engine=source.engine,
                load_config=source.load_config,
                sla_config=deepcopy(source.sla_config),
                perf_targets=deepcopy(source.perf_targets),
                variables=source.variables,
                env_config=source.env_config,
                environment=source.environment,
                global_environment=source.global_environment,
                account_pool_version=source.account_pool_version,
                account_pool_group=source.account_pool_group,
                runtime_config=deepcopy(source.runtime_config),
                enabled=source.enabled,
                created_by=request.user,
            )
            cloned_steps = [
                PerfScenarioStep.objects.create(
                    scenario=clone, order=s.order, name=s.name, enabled=s.enabled,
                    source_request=s.source_request, source_metadata=s.source_metadata, preparation=s.preparation,
                    protocol=s.protocol, websocket_config=deepcopy(s.websocket_config), sse_config=deepcopy(s.sse_config),
                    execution_policy=deepcopy(s.execution_policy),
                    method=s.method, url=s.url,
                    headers=s.headers, params=s.params, body_type=s.body_type,
                    body=s.body, files=s.files, extractors=s.extractors,
                    assertions=s.assertions,
                    think_time=s.think_time, weight=s.weight, is_setup=s.is_setup)
                for s in source_steps
            ]
            id_map = {old.pk: new.pk for old, new in zip(source_steps, cloned_steps)}
            for rule in clone.sla_config.get('step_thresholds', []):
                if rule.get('step_id') not in id_map:
                    raise ValidationError('来源场景 SLA 引用了已删除步骤，请先修正规则')
                rule['step_id'] = id_map[rule['step_id']]
            recovery = clone.runtime_config.get('resource_recovery') or {}
            for field in ('put_step_id', 'receipt_step_id', 'get_step_id', 'delete_step_id'):
                if recovery:
                    if recovery.get(field) not in id_map:
                        raise ValidationError('来源场景提醒恢复引用了已删除步骤，请先修正配置')
                    recovery[field] = id_map[recovery[field]]
            clone.save(update_fields=['sla_config', 'runtime_config'])

        log_operation('CREATE', 'SCENARIO', clone.id, clone.name, request.user,
                      description=f'复制自场景「{source.name}」')
        return Response(PerfScenarioSerializer(clone, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='import-from-api')
    @catalog_boundary
    def import_from_api(self, request, pk=None):
        scenario = self.get_object()
        use_prepared = request.data.get('use_prepared', False)
        if type(use_prepared) is not bool:
            raise api_catalog.CatalogInputError('use_prepared 必须为布尔值')
        bindings_applied = False
        if use_prepared:
            if request.data.get('as_setup'):
                raise api_catalog.CatalogInputError('已验证接口请作为业务步骤导入')
            created, bindings_applied = prepared_requests.import_steps(scenario, request.data, request.user)
        else:
            created = api_catalog.import_steps(scenario, request.data.get('request_ids'), request.user,
                as_setup=bool(request.data.get('as_setup')))
        return Response({'imported': len(created), 'skipped': 0, 'bindings_applied': bindings_applied,
            'steps': PerfScenarioStepSerializer(created, many=True, context={'request': request}).data}, status=201)

    @action(detail=True, methods=['get'], url_path='catalog-diff')
    @catalog_boundary
    def catalog_diff(self, request, pk=None):
        scenario = self.get_object()
        result = catalog_page(api_catalog.scene_differences(scenario), request)
        result.data['version'] = api_catalog.version_summary(api_catalog.latest_version(scenario.project_id))
        return result

    @action(detail=True, methods=['post'], url_path='catalog-update')
    @catalog_boundary
    def catalog_update(self, request, pk=None):
        scenario = self.get_object()
        updated = api_catalog.update_steps(scenario, request.data.get('step_ids'),
            request.data.get('expected_version'), request.user)
        return Response({'updated': updated})

    @action(detail=True, methods=['get'], url_path='readiness')
    @catalog_boundary
    def readiness(self, request, pk=None):
        scenario = self.get_object()
        resolved = executor._resolve_environment_inputs(scenario, request.user)
        return catalog_page(api_catalog.scenario_readiness(scenario, resolved), request)

    @action(detail=True, methods=['post'])
    def preflight(self, request, pk=None):
        """执行前检查：不落任何数据，仅返回校验结论与容量预估。"""
        scenario = self.get_object()
        if scenario.engine != 'K6':
            return self._preflight_scenario(request, scenario)
        try:
            return self._preflight_scenario(request, scenario)
        except (APIException, Http404, DjangoPermissionDenied):
            raise
        except Exception as exc:  # noqa: BLE001 - Preflight also holds resolved private inputs.
            logger.warning('K6 预检失败：%s', type(exc).__name__)
            return Response({'error': 'K6 预检暂时失败，请稍后重试', 'code': 'k6_preflight_unavailable',
                             'passed': False, 'errors': ['K6 预检暂时失败，请稍后重试'],
                             'warnings': [], 'estimated': {}}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    def _preflight_scenario(self, request, scenario):
        script_ref, err = resolve_script_ref(scenario, request.data.get('script_ref'))
        if err:
            # 脚本引用非法也是一种"检查不通过"，走同一个弹窗展示比抛 400 更连贯
            return Response({
                'passed': False, 'errors': [err], 'warnings': [],
                'estimated': {'peak_concurrency': 0, 'planned_duration': 0,
                              'estimated_requests': 0, 'target_hosts': [], 'step_count': 0},
            })
        result = executor.preflight(scenario, load_config=request.data.get('load_config'),
                                    script_ref=script_ref, user=request.user,
                                    environment_overrides=request.data.get('environment_overrides'))
        return Response(result)

    @action(detail=True, methods=['post'])
    def execute(self, request, pk=None):
        """正式压测：preflight → 建执行记录 → 拉起独立子进程。"""
        scenario = self.get_object()
        if scenario.engine != 'K6':
            return self._execute_scenario(request, scenario)
        try:
            return self._execute_scenario(request, scenario)
        except (APIException, Http404, DjangoPermissionDenied):
            raise
        except Exception as exc:  # noqa: BLE001 - Private launch inputs must not reach DEBUG responses.
            logger.warning('K6 正式执行启动异常：%s', type(exc).__name__)
            return Response({'error': 'K6 执行启动状态未确认，请先查看执行历史和运行状态，勿重复提交',
                             'code': 'k6_start_unconfirmed', 'retryable': False},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _execute_scenario(self, request, scenario):
        if scenario.has_active_execution():
            return Response({'error': '该场景已有正在执行的压测，请等待结束或先停止'},
                            status=status.HTTP_409_CONFLICT)

        script_ref, err = resolve_script_ref(scenario, request.data.get('script_ref'))
        if err:
            return Response({'error': err}, status=status.HTTP_400_BAD_REQUEST)

        execution, check = executor.start_execution(
            scenario, user=request.user, trigger_type='MANUAL',
            script_ref=script_ref, environment_overrides=request.data.get('environment_overrides'))
        if execution is None:
            return Response({'error': '执行前检查未通过', 'preflight': check},
                            status=status.HTTP_400_BAD_REQUEST)

        log_operation('EXECUTE', 'SCENARIO', scenario.id, scenario.name, request.user,
                      description=f'发起压测 {execution.execution_no}')
        return Response({
            'execution': PerfExecutionDetailSerializer(execution).data,
            'preflight': check,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def debug(self, request, pk=None):
        """K6 创建有界异步调试执行，其他引擎返回同步详情。"""
        scenario = self.get_object()
        if scenario.engine == 'K6':
            try:
                result = executor.debug_run(
                    scenario, user=request.user, script_ref=request.data.get('script_ref'),
                    environment_overrides=request.data.get('environment_overrides'))
            except Exception as exc:  # noqa: BLE001 - Private inputs must not enter API/logs.
                logger.warning('K6 调试启动失败：%s', type(exc).__name__)
                return Response({'error': 'K6 调试启动失败，请检查场景配置和引擎状态'},
                                status=status.HTTP_400_BAD_REQUEST)
            execution, check = result['execution'], result['preflight']
            if execution is None:
                return Response({'error': '调试前检查未通过', 'preflight': check},
                                status=status.HTTP_400_BAD_REQUEST)
            log_operation('EXECUTE', 'SCENARIO', scenario.id, scenario.name, request.user,
                          description=f'发起 K6 调试 {execution.execution_no}（1 VU / 1 轮）')
            return Response({
                'engine': 'K6', 'execution': PerfExecutionDetailSerializer(execution).data,
                'preflight': check,
                'monitor_url': f'/performance-testing/executions/{execution.id}/monitor',
            }, status=status.HTTP_201_CREATED)

        steps = list(scenario.steps.filter(enabled=True).order_by('order'))
        if not steps:
            return Response({'error': '场景没有启用的步骤'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            result = executor.debug_run(scenario, user=request.user,
                                        environment_overrides=request.data.get('environment_overrides'))
        except Exception as exc:  # noqa: BLE001 - 调试异常直接回给前端展示
            logger.warning('压测调试失败: %s', exc, exc_info=True)
            return Response({'error': f'调试执行失败：{exc}'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=True, methods=['get'], url_path='execution-history')
    def execution_history(self, request, pk=None):
        """场景的历史执行趋势（供趋势图使用）。"""
        scenario = self.get_object()
        limit = min(int(request.query_params.get('limit') or 20), 100)
        executions = PerfExecution.objects.filter(
            scenario=scenario, status='COMPLETED', project__in=environments.accessible_projects(request.user)).order_by('-created_at')[:limit]
        points = [{
            'id': e.id,
            'execution_no': e.execution_no,
            'created_at': e.created_at,
            'tps': reporter.normalized_summary(e).get('tps'),
            'avg_rt': reporter.normalized_summary(e).get('avg_rt'),
            'p95_rt': reporter.normalized_summary(e).get('p95_rt'),
            'error_rate': reporter.normalized_summary(e).get('error_rate'),
            'sla_result': e.sla_result,
            'peak_tps': reporter.normalized_summary(e).get('peak_tps'),
            'throughput_notice': reporter.normalized_summary(e).get('throughput_notice'),
        } for e in executions]
        points.reverse()
        return Response(points)


# ====================================================================== #
# 步骤（单条 CRUD，配合前端行内编辑）
# ====================================================================== #
class PerfScenarioStepViewSet(viewsets.ModelViewSet):
    queryset = PerfScenarioStep.objects.all().select_related('scenario')
    serializer_class = PerfScenarioStepSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['scenario', 'enabled', 'is_setup']
    ordering_fields = ['order', 'id']

    def get_queryset(self):
        qs = super().get_queryset().filter(scenario__project__in=environments.accessible_projects(self.request.user))
        if self.action not in ('list', 'retrieve'):
            qs = qs.filter(scenario__is_preparation=False)
        return qs

    def perform_destroy(self, instance):
        with transaction.atomic():
            step = PerfScenarioStep.objects.select_for_update().select_related('scenario').get(pk=instance.pk)
            environments.require_project_access(step.scenario.project_id, self.request.user)
            if any(r.get('step_id') == step.pk for r in (step.scenario.sla_config or {}).get('step_thresholds', [])):
                raise ValidationError('请先在 SLA 页移除此步骤的规则并保存，再删除步骤')
            step.delete()


# ====================================================================== #
# 执行
# ====================================================================== #
def _ensure_report_file(execution):
    """确保报告 HTML 存在，返回 (绝对路径, 错误描述)；失败时 abs_path 为 None。

    分享直链场景下报告可能因「执行收尾时生成失败」或「清理任务删除产物」
    而缺失；执行已终态时可基于库内采样/统计数据无损重建，
    避免分享链接打开即 404（历史执行记录也因此自愈可用）。
    """
    def _resolve():
        if not execution.report_url:
            return None
        path = os.path.join(settings.MEDIA_ROOT, execution.report_url)
        return path if os.path.isfile(path) else None

    abs_path = _resolve()
    if abs_path is None and not execution.is_active:
        try:
            execution.report_url = reporter.generate_report(execution)
            execution.save(update_fields=['report_url'])
            abs_path = _resolve()
        except Exception as exc:  # noqa: BLE001
            logger.warning('自愈重建压测报告失败 #%s: %s', execution.id, exc)
    if abs_path:
        return abs_path, ''
    if execution.is_active:
        return None, '压测尚未结束，报告未生成'
    if not execution.report_url:
        return None, '报告尚未生成，请先生成报告后再分享'
    return None, '报告文件已被清理且重建失败，请重新生成报告'


class PerfExecutionViewSet(mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet):
    """压测执行：只读 + 删除。执行记录不允许改，它是审计凭据。"""

    queryset = PerfExecution.objects.all().select_related(
        'scenario', 'project', 'executed_by')
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['scenario', 'project', 'status', 'sla_result', 'trigger_type']
    search_fields = ['execution_no', 'scenario__name']
    ordering_fields = ['created_at', 'start_time', 'duration']

    def get_serializer_class(self):
        if self.action == 'list':
            return PerfExecutionListSerializer
        return PerfExecutionDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        share_id = getattr(self.request, '_perf_share_execution_id', None)
        if share_id and self.action in ('report', 'download_raw'):
            qs = qs.filter(pk=share_id)
        else:
            qs = qs.filter(project__in=environments.accessible_projects(self.request.user))
        if self.action == 'retrieve':
            return qs.prefetch_related('request_stats')
        return qs

    def perform_destroy(self, instance):
        if instance.is_active:
            raise ValidationError('执行进行中，请先停止后再删除')
        from .services.reminder_recovery import may_delete
        if not may_delete(instance):
            raise ValidationError('提醒恢复尚未确认完成，必须保留本次执行与私密意图')
        # 产物目录随记录一起删，避免 media 目录里堆孤儿文件
        artifact = executor.abs_artifact_dir(instance)
        log_operation('DELETE', 'EXECUTION', instance.id, instance.execution_no,
                      self.request.user)
        instance.delete()
        if artifact and os.path.isdir(artifact):
            shutil.rmtree(artifact, ignore_errors=True)

    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        execution = self.get_object()
        graceful = request.data.get('graceful', True)
        ok, message = executor.stop_execution(execution, graceful=bool(graceful))
        if ok:
            log_operation('EXECUTE', 'EXECUTION', execution.id, execution.execution_no,
                          request.user, description='手动停止压测')
        return Response({'success': ok, 'message': message},
                        status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def realtime(self, request, pk=None):
        """WebSocket 不可用时的轮询降级接口。

        只回增量：前端传 since（上次拿到的最大 ts_offset），避免长压测每次拉全量。
        """
        execution = self.get_object()
        since = request.query_params.get('since')
        after_id = request.query_params.get('after_id')
        samples = PerfMetricSample.objects.filter(execution=execution)
        cursor = None
        if after_id is not None:
            try:
                cursor = int(after_id)
                if cursor < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return Response({'detail': 'after_id 必须为非负整数'}, status=400)
            samples = samples.filter(id__gt=cursor).order_by('id')
        else:
            if since not in (None, ''):
                try:
                    samples = samples.filter(ts_offset__gt=int(since))
                except (TypeError, ValueError):
                    pass
            samples = samples.order_by('ts_offset', 'id')
        rows = list(samples[:2001])
        has_more = len(rows) > 2000
        rows = rows[:2000]
        next_after_id = rows[-1].id if rows else cursor
        from .services.reminder_recovery import observed_status
        return Response({
            'status': execution.status,
            'progress': execution.progress,
            'sla_result': execution.sla_result,
            'summary': reporter.normalized_summary(execution) if execution.summary else {},
            'error_message': execution.error_message,
            'heartbeat_at': execution.heartbeat_at,
            'resource_recovery': observed_status(execution),
            'samples': reporter.normalized_samples(execution, PerfMetricSampleSerializer(rows, many=True).data),
            'next_after_id': next_after_id, 'has_more': has_more,
        })

    @action(detail=True, methods=['get'])
    def samples(self, request, pk=None):
        """全量时序采样点（图表用，自动降采样避免前端卡死）。"""
        execution = self.get_object()
        max_points = min(int(request.query_params.get('max_points') or 1000), 5000)
        qs = PerfMetricSample.objects.filter(execution=execution).order_by('ts_offset')
        total = qs.count()
        if total <= max_points:
            data = PerfMetricSampleSerializer(qs, many=True).data
        else:
            from .services.metrics import downsample
            data = downsample(PerfMetricSampleSerializer(qs, many=True).data, max_points)
        return Response({'total': total, 'returned': len(data), 'samples': reporter.normalized_samples(execution, data)})

    @action(detail=True, methods=['get'], url_path='request-stats')
    def request_stats(self, request, pk=None):
        execution = self.get_object()
        stats = PerfRequestStat.objects.filter(execution=execution)
        return Response(PerfRequestStatSerializer(stats, many=True).data)

    @action(detail=True, methods=['post'], url_path='generate-report')
    def generate_report(self, request, pk=None):
        execution = self.get_object()
        if execution.is_active:
            return Response({'error': '压测尚未结束，无法生成报告'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            rel_path = reporter.generate_report(execution)
        except Exception as exc:  # noqa: BLE001
            logger.error('生成压测报告失败: %s', exc, exc_info=True)
            return Response({'error': f'生成报告失败：{exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        execution.report_url = rel_path
        execution.save(update_fields=['report_url'])
        return Response({'report_url': rel_path})

    @action(detail=True, methods=['get'],
            authentication_classes=[ShareTokenAuthentication, *api_settings.DEFAULT_AUTHENTICATION_CLASSES],
            permission_classes=[HasPerfShareTokenOrAuthenticated])
    def report(self, request, pk=None):
        """直接返回 HTML 报告内容（前端 iframe / 新窗口打开）。

        支持 ?token= 分享直链；无 token 时仍需正常登录。
        报告文件缺失且执行已结束时先尝试自愈重建，失败时返回
        可读的 JSON 错误（Http404 会被 DRF 吞成笼统的 Not Found）。
        """
        execution = self.get_object()
        format_name = request.query_params.get('export', 'html')
        if format_name in ('json', 'csv'):
            document = reporter.report_document(execution)
            if format_name == 'json':
                response = HttpResponse(json.dumps(document, ensure_ascii=False, allow_nan=False), content_type='application/json; charset=utf-8')
            else:
                buffer = io.StringIO()
                columns = ['section', 'execution_no', 'execution_status', 'sla_result', 'step_id', 'step_name', 'request_path',
                    'method', 'phase', 'started', 'total', 'incomplete', 'success', 'failed', 'error_rate', 'tps', 'avg_rt',
                    'min_rt', 'max_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'match_status', 'data_json']
                if any(row.get('protocol') in ('WEBSOCKET', 'SSE') for row in document['interfaces']):
                    columns += ['protocol', 'latency_kind']
                writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction='ignore')
                writer.writeheader()
                common = {'execution_no': execution.execution_no, 'execution_status': execution.status,
                          'sla_result': execution.sla_result}
                def safe_cell(value):
                    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                        return "'" + value
                    return value
                for row in document['interfaces']:
                    values = {**common, **row, 'section': 'interface'}
                    writer.writerow({key: safe_cell(value) for key, value in values.items() if key in columns})
                for key in ('summary', 'sla_detail', 'evidence'):
                    writer.writerow({**common, 'section': key,
                        'data_json': json.dumps(document[key], ensure_ascii=False, allow_nan=False)})
                response = HttpResponse('\ufeff' + buffer.getvalue(), content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="{execution.execution_no}.{format_name}"'
            return response
        if format_name != 'html':
            raise ValidationError('支持的报告格式为 html、json、csv')
        if reporter.is_k6(execution) and not execution.is_active:
            from .models import PerfMetricSample
            samples = list(PerfMetricSample.objects.filter(execution=execution).order_by('ts_offset').values())
            return HttpResponse(reporter._render(execution, samples, list(execution.request_stats.all())), content_type='text/html; charset=utf-8')
        abs_path, error = _ensure_report_file(execution)
        if not abs_path:
            return Response({'error': error}, status=status.HTTP_404_NOT_FOUND)
        with open(abs_path, 'r', encoding='utf-8') as fh:
            return HttpResponse(fh.read(), content_type='text/html; charset=utf-8')

    @action(detail=True, methods=['get'], url_path='ai-analysis')
    def ai_analysis(self, request, pk=None):
        """压测 AI 失败分析（SSE 流式）。

        性能策略：
        1. 先查 Redis 缓存，命中则一次性返回（< 100ms）
        2. 未命中则 SSE 流式推送 LLM 输出（逐块 yield）
        3. 流式完成后写入缓存（TTL 30min）

        前端用 EventSource 消费，Tab 懒加载（点击才请求）。
        """
        import json
        from django.core.cache import cache
        from django.http import StreamingHttpResponse
        from apps.perf_testing.models import PerfRequestStat
        from apps.perf_testing.services.ai_analysis import analyze_stream

        execution = self.get_object()
        cache_key = f'perf:ai_analysis:{execution.id}'

        # 1. 缓存命中 → 即时返回
        cached = cache.get(cache_key)
        if cached:
            data = json.loads(cached)
            return Response(data)

        # 2. 未命中 → SSE 流式
        stats = list(PerfRequestStat.objects.filter(execution_id=execution.id).annotate(
            # 模型字段已重命名为 avg_rt/p95_rt，此处别名回旧键，保持下游消费键不变
            avg_response_time=db_models.F('avg_rt'), p95=db_models.F('p95_rt')
        ).values(
            'step_name', 'avg_response_time', 'p95', 'error_rate', 'failed', 'total'
        ))
        summary = execution.summary or {}
        verdict = execution.verdict or ''
        verdict_details = execution.verdict_details or []

        if not stats:
            return Response({'error': '无执行指标数据，请等待执行完成'}, status=status.HTTP_400_BAD_REQUEST)

        def sse_stream():
            full = []
            try:
                for chunk in analyze_stream(stats, summary, verdict, verdict_details):
                    full.append(chunk)
                    yield f'data: {json.dumps({"chunk": chunk}, ensure_ascii=False)}\n\n'
                # 写缓存
                result = json.dumps({'analysis': ''.join(full)}, ensure_ascii=False)
                cache.set(cache_key, result, timeout=1800)
                yield f'data: {json.dumps({"done": True}, ensure_ascii=False)}\n\n'
            except Exception as e:
                yield f'data: {json.dumps({"error": str(e)}, ensure_ascii=False)}\n\n'

        response = StreamingHttpResponse(sse_stream(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'  # Nginx 不缓冲
        return response

    @action(detail=True, methods=['get'], url_path='download-raw',
            authentication_classes=[ShareTokenAuthentication, *api_settings.DEFAULT_AUTHENTICATION_CLASSES],
            permission_classes=[HasPerfShareTokenOrAuthenticated])
    def download_raw(self, request, pk=None):
        """下载原始请求明细（gzip CSV）。

        支持 ?token= 分享直链；无 token 时仍需正常登录。
        """
        execution = self.get_object()
        abs_path = os.path.join(executor.abs_artifact_dir(execution), 'raw.csv.gz')
        if not os.path.isfile(abs_path):
            return Response({'error': '原始明细不存在或已被清理'},
                            status=status.HTTP_404_NOT_FOUND)
        response = FileResponse(open(abs_path, 'rb'), content_type='application/gzip')
        response['Content-Disposition'] = (
            f'attachment; filename="{execution.execution_no}_raw.csv.gz"')
        return response

    @action(detail=True, methods=['post'], url_path='share-link')
    def share_link(self, request, pk=None):
        """生成/重置报告分享直链。expires_in_days=None 表示永不过期。"""
        execution = self.get_object()
        if execution.is_active:
            return Response({'error': '压测尚未结束，请等待执行完成后再分享报告'},
                            status=status.HTTP_400_BAD_REQUEST)
        # 签发直链前先确保报告文件就绪，避免发出的链接打开即 404
        _, error = _ensure_report_file(execution)
        if error:
            return Response({'error': f'无法分享：{error}'},
                            status=status.HTTP_400_BAD_REQUEST)
        raw = request.data.get('expires_in_days', None)
        expires_in_days = None
        if raw not in (None, '', 'null'):
            try:
                expires_in_days = int(raw)
            except (TypeError, ValueError):
                return Response({'error': 'expires_in_days 必须是整数(天)'},
                                status=status.HTTP_400_BAD_REQUEST)
        token = execution.generate_share_token(expires_in_days)
        base = request.build_absolute_uri(
            f'/api/perf-testing/executions/{execution.id}/report/')
        raw_base = request.build_absolute_uri(
            f'/api/perf-testing/executions/{execution.id}/download-raw/')
        return Response({
            'token': token,
            'share_url': f'{base}?token={token}',
            'raw_url': f'{raw_base}?token={token}',
            'expires_at': execution.share_expires_at,
        })

    @action(detail=True, methods=['post'], url_path='revoke-share-link')
    def revoke_share_link(self, request, pk=None):
        """撤销报告分享直链。"""
        execution = self.get_object()
        execution.revoke_share_token()
        return Response({'success': True})

    @action(detail=True, methods=['get'], url_path='run-log')
    def run_log(self, request, pk=None):
        """子进程运行日志（排障用，尾部若干行）。"""
        execution = self.get_object()
        abs_path = os.path.join(executor.abs_artifact_dir(execution), 'run.log')
        if not os.path.isfile(abs_path):
            return Response({'content': '', 'exists': False})
        tail = min(int(request.query_params.get('lines') or 500), 5000)
        # 兼容历史日志编码：新子进程强制 UTF-8 写入，但旧日志可能是
        # Windows 下的 GBK/cp936，逐编码尝试避免乱码。
        raw = open(abs_path, 'rb').read()
        text = None
        for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            text = raw.decode('utf-8', errors='replace')
        lines = text.splitlines(keepends=True)[-tail:]
        return Response({'content': ''.join(lines), 'exists': True})

    @action(detail=False, methods=['get'])
    def compare(self, request):
        """多执行对比：?ids=1,2,3。返回汇总差异 + 各自时序曲线。"""
        raw_ids = (request.query_params.get('ids') or '').strip()
        ids = [int(i) for i in raw_ids.split(',') if i.strip().isdigit()]
        if len(ids) < 2:
            return Response({'error': '至少选择 2 次执行进行对比'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > 5:
            return Response({'error': '一次最多对比 5 次执行'},
                            status=status.HTTP_400_BAD_REQUEST)

        executions = list(self.get_queryset().filter(id__in=ids).select_related('scenario'))
        if len(executions) != len(set(ids)):
            return Response({'error': '所选执行记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        # 保持用户传入的顺序，第一条作为对比基准
        executions.sort(key=lambda e: ids.index(e.id))

        from .services.compare_report import build_snapshot
        return Response(build_snapshot(executions))

    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """压测总览：近 30 天概况 + 当前运行中列表。"""
        try:
            days = int(request.query_params.get('days', 30))
        except (TypeError, ValueError):
            raise ValidationError('日期范围必须为 7、30 或 90 天')
        if days not in (7, 30, 90):
            raise ValidationError('日期范围必须为 7、30 或 90 天')
        now = timezone.now()
        start = now - timedelta(days=days)
        accessible = environments.accessible_projects(request.user)
        all_runs = self.get_queryset().exclude(scenario__is_preparation=True)
        scenarios = PerfScenario.objects.filter(project__in=accessible, is_preparation=False)
        project_id = request.query_params.get('project')
        if project_id:
            all_runs = all_runs.filter(project_id=project_id)
            scenarios = scenarios.filter(project_id=project_id)
        qs = all_runs.filter(created_at__gte=start, created_at__lte=now)
        running = all_runs.filter(status__in=PerfExecution.ACTIVE_STATUSES)
        counts = {key: qs.filter(sla_result=key).count() for key in ('PASSED', 'FAILED', 'NOT_EVALUATED')}
        evaluated = counts['PASSED'] + counts['FAILED']
        trend = {}
        rates = {'K6': [], 'other': []}
        for run in qs:
            date = timezone.localtime(run.created_at).date().isoformat()
            trend[date] = trend.get(date, 0) + 1
            value = (run.summary or {}).get('business_rps' if reporter.is_k6(run) else 'tps')
            if reporter.finite(value) and run.status in PerfExecution.FINAL_STATUSES:
                rates['K6' if reporter.is_k6(run) else 'other'].append(value)
        return Response({
            'range_days': days, 'range_start': start, 'range_end': now,
            'total_scenarios': scenarios.count(), 'total_executions': qs.count(),
            'completed': qs.filter(status='COMPLETED').count(),
            'failed': qs.filter(status__in=['FAILED', 'TIMEOUT']).count(),
            'running_count': running.count(), 'sla_counts': counts,
            'sla_pass_rate': round(counts['PASSED'] / evaluated * 100, 1) if evaluated else None,
            'avg_business_rps': sum(rates['K6']) / len(rates['K6']) if rates['K6'] else None,
            'avg_tps': sum(rates['other']) / len(rates['other']) if rates['other'] else None,
            'trend': [{'date': date, 'count': count} for date, count in sorted(trend.items())],
            'running': PerfExecutionListSerializer(running, many=True).data,
            'recent': PerfExecutionListSerializer(qs.order_by('-created_at')[:10], many=True).data,
        })

    @action(detail=False, methods=['post'], url_path='reap-stale')
    def reap_stale(self, request):
        """手动触发僵尸执行回收（运维入口）。"""
        if not environments.is_environment_admin(request.user):
            raise DjangoPermissionDenied('仅管理员可以回收异常执行')
        count = cleanup_service.reap_stale_executions()
        return Response({'reaped': count})


# ====================================================================== #
# 基线
# ====================================================================== #
class PerfBaselineViewSet(viewsets.ModelViewSet):
    queryset = PerfBaseline.objects.all().select_related('scenario', 'execution', 'set_by')
    serializer_class = PerfBaselineSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['scenario']
    ordering_fields = ['created_at', 'updated_at']

    def get_queryset(self):
        projects = environments.accessible_projects(self.request.user)
        # A scenario can move projects; baseline metrics retain their execution's ownership.
        return super().get_queryset().filter(
            scenario__project__in=projects, execution__project__in=projects,
            execution__project_id=db_models.F('scenario__project_id'),
            execution__scenario_id=db_models.F('scenario_id'))

    def baseline_executions(self):
        projects = environments.accessible_projects(self.request.user)
        return PerfExecution.objects.filter(
            project__in=projects, scenario__project__in=projects,
            project_id=db_models.F('scenario__project_id')).select_related('scenario')

    def perform_create(self, serializer):
        serializer.save(set_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(set_by=self.request.user)

    @action(detail=False, methods=['post'], url_path='set-from-execution')
    def set_from_execution(self, request):
        """把某次执行的结果设为该场景的性能基线。"""
        execution_id = request.data.get('execution_id')
        if not execution_id:
            return Response({'error': '缺少 execution_id'},
                            status=status.HTTP_400_BAD_REQUEST)
        execution = self.baseline_executions().filter(id=execution_id).first()
        if not execution:
            return Response({'error': '执行记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        if execution.status != 'COMPLETED':
            return Response({'error': '只有正常完成的执行才能作为基线'},
                            status=status.HTTP_400_BAD_REQUEST)

        existing = PerfBaseline.objects.filter(scenario=execution.scenario).first()
        if existing and not self.get_queryset().filter(pk=existing.pk).exists():
            return Response({'error': '历史基线来源缺失或项目不相容，不能覆盖；原记录已保留'},
                            status=status.HTTP_409_CONFLICT)
        tolerance = PerfBaselineSerializer().validate_tolerance(request.data.get('tolerance') or PerfBaseline.DEFAULT_TOLERANCE)
        baseline, _created = PerfBaseline.objects.update_or_create(
            scenario=execution.scenario,
            defaults={
                'execution': execution,
                'metrics': execution.summary or {},
                'tolerance': tolerance,
                'note': request.data.get('note', ''),
                'set_by': request.user,
            })
        log_operation('UPDATE', 'SCENARIO', execution.scenario_id,
                      execution.scenario.name, request.user,
                      description=f'设置性能基线（来源 {execution.execution_no}）')
        return Response(PerfBaselineSerializer(baseline).data)

    @action(detail=False, methods=['get'])
    def compare(self, request):
        """执行 vs 基线：判断是否劣化。"""
        execution_id = request.query_params.get('execution_id')
        execution = self.baseline_executions().filter(id=execution_id).first()
        if not execution:
            return Response({'error': '执行记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        baseline = self.get_queryset().filter(scenario=execution.scenario).first()
        if not baseline:
            unavailable = PerfBaseline.objects.filter(scenario=execution.scenario).exists()
            return Response({'has_baseline': False, 'items': [], 'degraded': None,
                'evaluation': 'NOT_EVALUATED',
                'reason': '基线来源缺失、无访问权限或项目不相容，无法评估' if unavailable else '尚未设置基线'})

        baseline_engine = reporter.frozen_engine(baseline.execution)
        current_engine = reporter.frozen_engine(execution)
        if not baseline_engine or not current_engine or baseline_engine != current_engine:
            return Response({'has_baseline': True, 'items': [], 'degraded': None,
                'evaluation': 'NOT_EVALUATED',
                'reason': '执行引擎不同或历史引擎来源缺失，指标口径不相容，无法评估',
                'baseline_execution_no': baseline.execution.execution_no,
                'baseline_engine': baseline_engine, 'current_engine': current_engine,
                'baseline_created_at': baseline.created_at})

        tolerance = {**PerfBaseline.DEFAULT_TOLERANCE, **(baseline.tolerance or {})}
        base = reporter.normalized_summary(baseline.execution) if baseline.execution else baseline.metrics or {}
        cur = reporter.normalized_summary(execution)

        items = []
        # 响应时间类：越大越差
        for key, label in (('avg_rt', '平均响应时间'), ('p95_rt', 'P95 响应时间'),
                           ('p99_rt', 'P99 响应时间')):
            b, c = base.get(key), cur.get(key)
            if not reporter.finite(b) or b == 0 or not reporter.finite(c):
                continue
            change = round((c - b) / b * 100, 2)
            items.append({
                'metric': key, 'label': label, 'baseline': b, 'current': c,
                'change_pct': change, 'direction': 'lower_better',
                'degraded': change > tolerance['rt_degrade_pct'],
                'tolerance_pct': tolerance['rt_degrade_pct'],
            })
        # 吞吐类：越小越差
        for key, label in (('tps', '业务 RPS' if reporter.is_k6(execution) else 'TPS'), ('peak_tps', '峰值业务 RPS' if reporter.is_k6(execution) else '峰值 TPS')):
            b, c = base.get(key), cur.get(key)
            if not reporter.finite(b) or b == 0 or not reporter.finite(c):
                continue
            change = round((c - b) / b * 100, 2)
            items.append({
                'metric': key, 'label': label, 'baseline': b, 'current': c,
                'change_pct': change, 'direction': 'higher_better',
                'degraded': change < -tolerance['tps_degrade_pct'],
                'tolerance_pct': tolerance['tps_degrade_pct'],
            })

        return Response({
            'has_baseline': True,
            'baseline_execution_no': baseline.execution.execution_no
            if baseline.execution else '',
            'baseline_created_at': baseline.created_at,
            'degraded': any(i['degraded'] for i in items) if items else None,
            'evaluation': 'EVALUATED' if items else 'NOT_EVALUATED',
            'reason': '峰值业务 RPS 来源未验证，已跳过峰值比较；其余指标按各自口径评估' if reporter.is_k6(execution) and (base.get('peak_tps') is None or cur.get('peak_tps') is None) else '',
            'items': items,
        })


# ====================================================================== #
# 数据文件
# ====================================================================== #
class PerfDataFileViewSet(viewsets.ModelViewSet):
    queryset = PerfDataFile.objects.all().select_related('project', 'uploaded_by')
    serializer_class = PerfDataFileSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['project', 'file_type']
    search_fields = ['name']
    ordering_fields = ['created_at', 'name']

    MAX_SIZE = 20 * 1024 * 1024  # 20MB：再大就该走数据库参数化了

    def get_queryset(self):
        return super().get_queryset().filter(project__in=environments.accessible_projects(self.request.user)).exclude(file_type='ACCOUNT')

    def create(self, request, *args, **kwargs):
        """上传文件。file_type=CSV(默认) 走参数化数据校验，JMX 走脚本校验，
        UPLOAD 为请求体上传文件（multipart/form-data 用），不限扩展名。"""
        file_type = (request.data.get('file_type') or 'CSV').upper()
        if file_type == 'ACCOUNT' or file_type not in dict(PerfDataFile.FILE_TYPE_CHOICES):
            return Response({'error': f'不支持的文件类型：{file_type}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if file_type == 'JMX':
            return self._create_jmx(request)
        if file_type == 'UPLOAD':
            return self._create_upload(request)
        return self._create_csv(request)

    # ------------------------------------------------------------------ #
    def _create_jmx(self, request):
        """JMeter 脚本上传：先静态解析确认是可执行的测试计划，再落盘。

        不做「先存后校验」——一个坏脚本留在磁盘上，下次压测才在 prepare 阶段炸，
        用户看到的是执行失败而不是上传失败，排查成本高一个数量级。
        """
        from .services import jmx_inspect

        upload = request.FILES.get('file')
        if not upload:
            return Response({'error': '请选择要上传的 .jmx 文件'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not upload.name.lower().endswith('.jmx'):
            return Response({'error': '脚本模式仅支持 JMeter .jmx 文件'},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > jmx_inspect.MAX_JMX_SIZE:
            return Response(
                {'error': f'.jmx 不能超过 {jmx_inspect.MAX_JMX_SIZE // 1024 // 1024}MB'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            raw = upload.read()
        finally:
            upload.seek(0)

        meta = jmx_inspect.inspect_jmx_bytes(raw)
        if not meta.get('valid'):
            return Response({'error': meta.get('error') or '.jmx 解析失败'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not meta.get('sampler_count'):
            return Response({'error': '.jmx 中没有任何采样器（Sampler），执行后不会产生任何请求'},
                            status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data={
            'project': request.data.get('project'),
            'name': request.data.get('name') or upload.name,
            'file_type': 'JMX',
            'file': upload,
        })
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(uploaded_by=request.user,
                                   columns=[], row_count=0,
                                   meta=jmx_inspect.summarize(meta))
        log_operation('CREATE', 'DATAFILE', instance.id, instance.name, request.user,
                      description=f'上传 JMeter 脚本，线程组 '
                                  f'{len(meta.get("thread_groups") or [])} 个 / '
                                  f'采样器 {meta.get("sampler_count")} 个')
        return Response(self.get_serializer(instance).data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------ #
    def _create_upload(self, request):
        """请求体上传文件：供压测步骤的 multipart/form-data 文件字段引用。

        不限扩展名（业务接口可能要求任意类型），只做大小限制；
        原始文件名与 Content-Type 记入 meta，引擎发送时用它还原 multipart 头。
        """
        upload = request.FILES.get('file')
        if not upload:
            return Response({'error': '请选择要上传的文件'},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > self.MAX_SIZE:
            return Response({'error': f'文件不能超过 {self.MAX_SIZE // 1024 // 1024}MB'},
                            status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data={
            'project': request.data.get('project'),
            'name': request.data.get('name') or upload.name,
            'file_type': 'UPLOAD',
            'file': upload,
        })
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(
            uploaded_by=request.user, columns=[], row_count=0,
            meta={'size': upload.size,
                  'content_type': upload.content_type or 'application/octet-stream'})
        log_operation('CREATE', 'DATAFILE', instance.id, instance.name, request.user,
                      description=f'上传请求文件（{upload.size // 1024}KB）')
        return Response(self.get_serializer(instance).data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------ #
    def _create_csv(self, request):
        upload = request.FILES.get('file')
        if not upload:
            return Response({'error': '请选择要上传的 CSV 文件'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not upload.name.lower().endswith('.csv'):
            return Response({'error': '仅支持 CSV 文件'},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > self.MAX_SIZE:
            return Response({'error': f'文件不能超过 {self.MAX_SIZE // 1024 // 1024}MB'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 先解析再落库：格式不对就别浪费磁盘
        try:
            raw = upload.read()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('gbk', errors='replace')
            rows = list(csv.reader(io.StringIO(text)))
        except Exception as exc:  # noqa: BLE001
            return Response({'error': f'CSV 解析失败：{exc}'},
                            status=status.HTTP_400_BAD_REQUEST)
        finally:
            upload.seek(0)

        if not rows:
            return Response({'error': 'CSV 文件为空'}, status=status.HTTP_400_BAD_REQUEST)
        columns = [c.strip() for c in rows[0]]
        if not any(columns):
            return Response({'error': 'CSV 首行必须是列名'},
                            status=status.HTTP_400_BAD_REQUEST)
        data_rows = [r for r in rows[1:] if any((c or '').strip() for c in r)]
        if not data_rows:
            return Response({'error': 'CSV 除表头外没有数据行'},
                            status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data={
            'project': request.data.get('project'),
            'name': request.data.get('name') or upload.name,
            'file_type': 'CSV',
            'file': upload,
        })
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(uploaded_by=request.user, columns=columns,
                                   row_count=len(data_rows), meta={})
        return Response(self.get_serializer(instance).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        """预览：CSV 返回前 N 行，JMX 返回脚本摘要 + XML 头部片段，UPLOAD 只回元信息。"""
        data_file = self.get_object()
        if data_file.file_type == 'JMX':
            return self._preview_jmx(data_file, request)
        if data_file.file_type == 'UPLOAD':
            return Response({'file_type': 'UPLOAD', 'meta': data_file.meta or {}})
        limit = min(int(request.query_params.get('limit') or 10), 100)
        try:
            with data_file.file.open('rb') as fh:
                raw = fh.read()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('gbk', errors='replace')
            rows = list(csv.reader(io.StringIO(text)))
        except Exception as exc:  # noqa: BLE001
            return Response({'error': f'读取文件失败：{exc}'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'file_type': 'CSV',
            'columns': rows[0] if rows else [],
            'rows': rows[1:limit + 1],
            'total_rows': max(len(rows) - 1, 0),
        })

    def _preview_jmx(self, data_file, request):
        """脚本预览：重新解析一次而不是直接吐 meta，避免文件被外部改动后信息失真。"""
        from .services import jmx_inspect

        head_lines = min(int(request.query_params.get('limit') or 40), 200)
        try:
            with data_file.file.open('rb') as fh:
                raw = fh.read()
        except Exception as exc:  # noqa: BLE001
            return Response({'error': f'读取脚本失败：{exc}'},
                            status=status.HTTP_400_BAD_REQUEST)
        meta = jmx_inspect.inspect_jmx_bytes(raw)
        text = raw.decode('utf-8', errors='replace')
        return Response({
            'file_type': 'JMX',
            'valid': bool(meta.get('valid')),
            'error': meta.get('error') or '',
            'meta': jmx_inspect.summarize(meta) if meta.get('valid') else {},
            'head': '\n'.join(text.splitlines()[:head_lines]),
        })

    def perform_destroy(self, instance):
        # 被场景变量引用中的文件不允许删，否则下次压测直接崩在准备阶段
        used_by = []
        for scenario in PerfScenario.objects.filter(project=instance.project):
            for var in scenario.variables or []:
                if str(var.get('data_file_id')) == str(instance.id):
                    used_by.append(scenario.name)
                    break
        if used_by:
            raise ValidationError(
                f'文件被以下场景引用，无法删除：{"、".join(used_by[:5])}')
        # 正在执行中的压测若引用了该脚本，删掉会让 JMeter 中途读不到文件
        if instance.file_type == 'JMX':
            running = PerfExecution.objects.filter(
                project=instance.project,
                status__in=PerfExecution.ACTIVE_STATUSES,
                script_ref__data_file_id=instance.id).exists()
            if running:
                raise ValidationError('该脚本正被执行中的压测使用，无法删除')
        try:
            instance.file.delete(save=False)
        except Exception:  # noqa: BLE001 - 文件可能已不在
            pass
        instance.delete()


# ====================================================================== #
# 定时任务
# ====================================================================== #
class PerfAccountPoolViewSet(mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = PerfAccountPoolSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['project']

    def handle_exception(self, exc):
        if (self.request.method == 'POST' and self.action in {'create', 'versions', 'inspect'}
                and not isinstance(exc, (APIException, Http404, DjangoPermissionDenied))):
            logger.warning('账号池上传失败，异常类型：%s', type(exc).__name__)
            return Response({'error': '账号池导入暂时失败，请稍后重试',
                             'code': 'account_pool_import_unavailable', 'retryable': True},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={'Retry-After': '1'})
        return super().handle_exception(exc)

    def get_queryset(self):
        return PerfDataFile.objects.filter(file_type='ACCOUNT', project__in=environments.accessible_projects(self.request.user)).prefetch_related('account_versions')

    def _import(self, request, pool=None):
        try:
            project_id = pool.project_id if pool else int(request.data.get('project'))
        except (ValueError, TypeError):
            account_pools.invalid('project_required', field='project')
        mapping = request.data.get('field_mapping', {})
        if isinstance(mapping, str):
            try:
                mapping = json.loads(mapping)
            except (ValueError, TypeError):
                account_pools.invalid('invalid_mapping', field='field_mapping')
        try:
            return account_pools.import_version(project_id=project_id, name=pool.name if pool else request.data.get('name'),
                upload=request.FILES.get('file'), identity_column=request.data.get('identity_column', ''),
                field_mapping=mapping, group_column=request.data.get('group_column', ''), user=request.user, pool=pool)
        except OSError:
            raise ValidationError('账号池私密文件保存失败，请重试') from None

    def create(self, request, *args, **kwargs):
        version = self._import(request)
        return Response(self.get_serializer(version.pool).data, status=201)

    @action(detail=True, methods=['get', 'post'])
    def versions(self, request, pk=None):
        pool = self.get_object()
        if request.method == 'POST':
            version = self._import(request, pool)
            return Response(PerfAccountPoolVersionSerializer(version).data, status=201)
        return Response(PerfAccountPoolVersionSerializer(pool.account_versions.all(), many=True).data)

    @action(detail=False, methods=['post'])
    def inspect(self, request):
        try:
            project_id = int(request.data.get('project'))
        except (ValueError, TypeError):
            account_pools.invalid('project_required', field='project')
        environments.require_project_access(project_id, request.user)
        upload = request.FILES.get('file')
        if upload is None or upload.size > account_pools.MAX_BYTES:
            account_pools.invalid('file_required_or_too_large')
        parsed = account_pools.parse_accounts(upload.read(account_pools.MAX_BYTES + 1),
            os.path.splitext(upload.name)[1].lower().lstrip('.'), '', {}, inspect=True)
        return Response({'columns': parsed['columns'], 'row_count': len(parsed['rows']),
                         'rows': [{column: '******' for column in parsed['columns']} for _ in parsed['rows'][:10]]})

    def perform_destroy(self, instance):
        try:
            with transaction.atomic():
                instance.delete()
        except ProtectedError:
            raise EnvironmentConflict('账号池版本仍被场景或历史运行引用，不能删除') from None


class PerfAccountPoolVersionViewSet(mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = PerfAccountPoolVersionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['pool']

    def get_queryset(self):
        return PerfAccountPoolVersion.objects.filter(pool__project__in=environments.accessible_projects(self.request.user)).select_related('pool')

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        version = self.get_object()
        return Response({'columns': version.columns, 'row_count': version.row_count,
                         'rows': [{column: '******' for column in version.columns} for _ in range(min(version.row_count, 10))]})

    @action(detail=True, methods=['get'])
    def usage(self, request, pk=None):
        version = self.get_object()
        return Response({'scenarios': list(version.scenarios.filter(project_id=version.pool.project_id).values('id', 'name')),
                         'executions': list(version.executions.filter(project_id=version.pool.project_id).values('id', 'status', 'created_at'))})

    def perform_destroy(self, instance):
        try:
            with transaction.atomic():
                instance.delete()
        except ProtectedError:
            raise EnvironmentConflict('账号池版本仍被场景或历史运行引用，不能删除') from None


class PerfScheduledTaskViewSet(viewsets.ModelViewSet):
    queryset = PerfScheduledTask.objects.all().select_related(
        'scenario', 'scenario__project', 'created_by')
    serializer_class = PerfScheduledTaskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = FlexiblePageNumberPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['scenario', 'status', 'trigger_type']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'next_run_at']

    def get_queryset(self):
        qs = super().get_queryset()
        project_id = self.request.query_params.get('project')
        if project_id:
            qs = qs.filter(scenario__project_id=project_id)
        return qs

    def perform_create(self, serializer):
        task = serializer.save()
        log_operation('CREATE', 'TASK', task.id, task.name, self.request.user)

    def perform_update(self, serializer):
        task = serializer.save()
        log_operation('UPDATE', 'TASK', task.id, task.name, self.request.user)

    def perform_destroy(self, instance):
        log_operation('DELETE', 'TASK', instance.id, instance.name, self.request.user)
        instance.delete()

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        task = self.get_object()
        task.status = 'PAUSED' if task.status == 'ACTIVE' else 'ACTIVE'
        if task.status == 'ACTIVE':
            task.calculate_next_run()
        task.save(update_fields=['status', 'next_run_at'])
        log_operation('UPDATE', 'TASK', task.id, task.name, request.user,
                      description=f'切换状态为 {task.get_status_display()}')
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=['post'], url_path='run-now')
    def run_now(self, request, pk=None):
        """立即执行一次定时任务（不改变原有调度节奏）。"""
        task = self.get_object()
        scenario = task.scenario
        if scenario.has_active_execution():
            return Response({'error': '该场景已有正在执行的压测'},
                            status=status.HTTP_409_CONFLICT)

        execution, check = executor.start_execution(
            scenario, user=request.user, trigger_type='SCHEDULED', scheduled_task=task)
        if execution is None:
            return Response({'error': '执行前检查未通过', 'preflight': check},
                            status=status.HTTP_400_BAD_REQUEST)

        task.last_run_at = timezone.now()
        task.run_count += 1
        task.save(update_fields=['last_run_at', 'run_count'])
        log_operation('EXECUTE', 'TASK', task.id, task.name, request.user,
                      description=f'手动触发定时任务，执行 {execution.execution_no}')
        return Response({'execution': PerfExecutionDetailSerializer(execution).data,
                         'preflight': check}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def executions(self, request, pk=None):
        task = self.get_object()
        # 优先按外键精确归属；外键为空的历史记录才按「同场景 + 定时触发」兜底
        qs = PerfExecution.objects.filter(
            db_models.Q(scheduled_task=task)
            | db_models.Q(scheduled_task__isnull=True, scenario=task.scenario,
                          trigger_type='SCHEDULED')
        ).order_by('-created_at')[:50]
        return Response(PerfExecutionListSerializer(qs, many=True).data)


class EngineStatusView(APIView):
    """引擎与实时通道能力上报。

    前端据此：置灰未安装的引擎、决定走 WebSocket 还是轮询降级。
    channels 未安装或 CHANNEL_LAYERS 未配时 websocket=False，
    前端会直接用轮询，不做无谓的连接重试。
    """

    permission_classes = [IsAuthenticated]

    @staticmethod
    def _websocket_available():
        """WebSocket 实时通道是否"真正可用"。

        仅判断 channel layer 已配置是不够的：CHANNEL_LAYERS 用了 Redis 后端，
        若 Redis 进程没起（配置存在但不可达），executor 的 group_send 会全部
        静默失败（_PushWorker 熔断），前端连上 WS 却收不到任何实时样本，且因
        WS 显示“已连接”而不会降级到轮询——实时监控就一片空白。

        因此这里除通道层存在外，再用极短超时的 TCP 连接实测 Redis 可达性，
        不可达即视为 WebSocket 不可用，让前端走轮询（轮询读 DB 采样，同样能实时）。
        """
        try:
            from channels.layers import get_channel_layer
            if get_channel_layer() is None:
                return False
        except Exception:  # noqa: BLE001 - 未装 channels 属于预期情况
            return False

        from urllib.parse import urlparse
        url = (settings.REDIS_URL or '').strip()
        try:
            parsed = urlparse(url)
            host = parsed.hostname or '127.0.0.1'
            port = parsed.port or 6379
        except Exception:  # noqa: BLE001
            return False
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.4)
            try:
                sock.connect((host, port))
                return True
            except OSError:
                return False
            finally:
                sock.close()
        except Exception:  # noqa: BLE001
            return False

    def get(self, request):
        websocket_ok = self._websocket_available()
        statuses = engine_status()

        return Response({
            'engines': statuses,
            'websocket': websocket_ok,
            'limits': {
                'max_concurrency': settings.PERF_MAX_CONCURRENCY,
                'max_target_rps': settings.PERF_MAX_TARGET_RPS,
                'max_duration': settings.PERF_MAX_DURATION,
                'max_concurrent_executions': settings.PERF_MAX_CONCURRENT_EXECUTIONS,
            },
        })


class PerfComparisonReportViewSet(viewsets.ViewSet):
    """多轮执行对照报告：持久化快照 + 可选 AI 对照分析。"""
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PerfComparisonReport.objects.filter(project__in=environments.accessible_projects(self.request.user))

    @staticmethod
    def _brief(report):
        return {
            'id': report.id,
            'title': report.title,
            'project_id': report.project_id,
            'execution_ids': report.execution_ids,
            'reference_execution_id': report.reference_execution_id,
            'has_ai_analysis': bool(report.ai_analysis),
            'created_by': report.created_by.username if report.created_by else '',
            'created_at': report.created_at,
        }

    def list(self, request):
        qs = self.get_queryset().select_related('created_by')
        project_id = request.query_params.get('project')
        if project_id:
            qs = qs.filter(project_id=project_id)
        total = qs.count()
        rows = qs.order_by('-created_at')[:200]
        return Response({'items': [self._brief(r) for r in rows], 'total': total})

    def retrieve(self, request, pk=None):
        report = self.get_queryset().filter(id=pk).select_related('created_by').first()
        if not report:
            return Response({'error': '对照报告不存在'}, status=status.HTTP_404_NOT_FOUND)
        data = self._brief(report)
        from .services.compare_report import normalized_snapshot
        data['snapshot'] = normalized_snapshot(report.snapshot)
        data['ai_analysis'] = report.ai_analysis
        return Response(data)

    def destroy(self, request, pk=None):
        deleted, _ = self.get_queryset().filter(id=pk).delete()
        if not deleted:
            return Response({'error': '对照报告不存在'}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def create(self, request):
        raw_ids = request.data.get('execution_ids') or []
        ids = list(dict.fromkeys(int(i) for i in raw_ids
                                 if str(i).lstrip('-').isdigit()))
        if len(ids) < 2:
            return Response({'error': '至少选择 2 次执行进行对比'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > 5:
            return Response({'error': '一次最多对比 5 次执行'},
                            status=status.HTTP_400_BAD_REQUEST)

        project_id = request.data.get('project_id')
        executions = list(PerfExecution.objects.filter(project__in=environments.accessible_projects(request.user), id__in=ids).select_related('scenario'))
        if len(executions) < len(ids):
            return Response({'error': '所选执行记录不存在'}, status=status.HTTP_404_NOT_FOUND)
        if len({e.project_id for e in executions}) != 1:
            raise ValidationError('对照报告必须选择同一项目的执行')
        if project_id and any(str(e.project_id) != str(project_id) for e in executions):
            return Response({'error': '存在不属于该项目的执行记录'},
                            status=status.HTTP_400_BAD_REQUEST)
        executions.sort(key=lambda e: ids.index(e.id))

        reference_id = request.data.get('reference_execution_id')
        if reference_id is not None:
            try:
                reference_id = int(reference_id)
            except (TypeError, ValueError):
                reference_id = None
            if reference_id not in ids:
                return Response({'error': '基准执行必须在所选列表中'},
                                status=status.HTTP_400_BAD_REQUEST)

        from .services.compare_report import build_snapshot
        snapshot = build_snapshot(executions, reference_execution_id=reference_id)

        title = (request.data.get('title') or '').strip()
        if not title:
            from django.utils import timezone as tz
            title = f"对比报告 #{','.join(str(i) for i in ids[:5])} · " \
                    f"{tz.now().strftime('%Y-%m-%d %H:%M')}"

        report = PerfComparisonReport.objects.create(
            project_id=executions[0].project_id,
            title=title[:200],
            execution_ids=ids,
            reference_execution_id=snapshot.get('reference_execution_id'),
            snapshot=snapshot,
            created_by=request.user,
        )

        if request.data.get('ai_analyze'):
            from .tasks import comparison_ai_analysis_task
            comparison_ai_analysis_task.delay(report.id)

        return Response(self._brief(report), status=status.HTTP_201_CREATED)
