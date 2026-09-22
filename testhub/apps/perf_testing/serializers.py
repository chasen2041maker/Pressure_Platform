"""性能测试模块序列化器。

两个重点：
1. load_config / sla_config 是自由 JSON，必须在入口做 schema 校验 —— 否则
   非法配置会一路带到子进程才炸，用户看到的是一条 FAILED 记录而不是表单报错。
2. 场景变量里可能有密码/token，读接口要掩码，写接口要能「不传即保留原值」。
"""
from copy import deepcopy
import math

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from .models import (PerfBaseline, PerfDataFile, PerfExecution, PerfMetricSample,
                     PerfProject, PerfRequestStat, PerfScenario, PerfScenarioStep,
                     PerfScheduledTask, PerfEnvironment, PerfAccountPoolVersion)
from .services import environments, account_pools

User = get_user_model()

LOAD_MODELS = ('CONCURRENCY', 'RAMPING', 'RPS', 'SPIKE')
VARIABLE_TYPES = ('CONSTANT', 'RANDOM_INT', 'RANDOM_STRING', 'ENUM', 'UUID', 'TIMESTAMP', 'CSV')
EXTRACTOR_TYPES = ('JSON_PATH', 'REGEX', 'HEADER')
ASSERTION_TYPES = ('STATUS_CODE', 'CONTAINS', 'NOT_CONTAINS', 'JSON_PATH', 'RESPONSE_TIME', 'REGEX')


class SimpleUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


# ====================================================================== #
# 配置校验
# ====================================================================== #
def validate_load_config(value):
    """压力策略 schema 校验，同时兜住平台容量红线。"""
    if not isinstance(value, dict):
        raise serializers.ValidationError('压力策略必须是对象')

    model = (value.get('model') or 'CONCURRENCY').upper()
    if model not in LOAD_MODELS:
        raise serializers.ValidationError(f'不支持的压力模型：{model}')
    value['model'] = model

    def _positive_int(key, label, required=False, maximum=None):
        raw = value.get(key)
        if raw in (None, ''):
            if required:
                raise serializers.ValidationError(f'{label}不能为空')
            return 0
        try:
            num = int(raw)
        except (TypeError, ValueError):
            raise serializers.ValidationError(f'{label}必须是整数')
        if num < 0:
            raise serializers.ValidationError(f'{label}不能为负数')
        if required and num <= 0:
            raise serializers.ValidationError(f'{label}必须大于 0')
        if maximum and num > maximum:
            raise serializers.ValidationError(f'{label} {num} 超过平台上限 {maximum}')
        return num

    if model == 'CONCURRENCY':
        conc = _positive_int('concurrency', '并发用户数', required=True,
                             maximum=settings.PERF_MAX_CONCURRENCY)
        duration = _positive_int('duration', '压测时长', required=True,
                                 maximum=settings.PERF_MAX_DURATION)
        ramp = _positive_int('ramp_up', '加压时长')
        if ramp > duration:
            raise serializers.ValidationError('加压时长不能超过总压测时长')
        _ = conc

    elif model == 'RAMPING':
        stages = value.get('stages') or []
        if not isinstance(stages, list) or not stages:
            raise serializers.ValidationError('阶梯加压模式必须配置至少一个阶段')
        if len(stages) > 20:
            raise serializers.ValidationError('阶段数量不能超过 20 个')
        total = 0
        for idx, stage in enumerate(stages, 1):
            if not isinstance(stage, dict):
                raise serializers.ValidationError(f'第 {idx} 个阶段格式非法')
            try:
                target = int(stage.get('target') or 0)
                dur = int(stage.get('duration') or 0)
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'第 {idx} 个阶段的目标并发/持续时间必须是整数')
            if target < 0 or dur <= 0:
                raise serializers.ValidationError(
                    f'第 {idx} 个阶段：目标并发不能为负、持续时间必须大于 0')
            if target > settings.PERF_MAX_CONCURRENCY:
                raise serializers.ValidationError(
                    f'第 {idx} 个阶段目标并发 {target} 超过平台上限 {settings.PERF_MAX_CONCURRENCY}')
            total += dur
        if total > settings.PERF_MAX_DURATION:
            raise serializers.ValidationError(
                f'各阶段总时长 {total}s 超过平台上限 {settings.PERF_MAX_DURATION}s')

    elif model == 'RPS':
        _positive_int('target_rps', '目标 RPS', required=True, maximum=settings.PERF_MAX_TARGET_RPS)
        _positive_int('duration', '压测时长', required=True, maximum=settings.PERF_MAX_DURATION)
        _positive_int('max_concurrency', '最大并发上限', maximum=settings.PERF_MAX_CONCURRENCY)

    elif model == 'SPIKE':
        base = _positive_int('baseline_concurrency', '基线并发',
                             maximum=settings.PERF_MAX_CONCURRENCY)
        spike = _positive_int('spike_concurrency', '尖峰并发', required=True,
                              maximum=settings.PERF_MAX_CONCURRENCY)
        spike_dur = _positive_int('spike_duration', '单次尖峰时长', required=True)
        times = _positive_int('spike_times', '尖峰次数', required=True)
        if spike <= base:
            raise serializers.ValidationError('尖峰并发必须大于基线并发，否则不构成尖峰')
        if spike_dur * 2 * times > settings.PERF_MAX_DURATION:
            raise serializers.ValidationError(
                f'尖峰总时长 {spike_dur * 2 * times}s 超过平台上限 {settings.PERF_MAX_DURATION}s')

    _positive_int('max_requests', '最大请求数上限')
    return value


def validate_sla_config(value):
    if not isinstance(value, dict):
        raise serializers.ValidationError('SLA 配置必须是对象')
    if not value.get('enabled'):
        return value

    from .services.sla import SLA_METRICS
    thresholds = value.get('thresholds') or {}
    if not isinstance(thresholds, dict):
        raise serializers.ValidationError('SLA 阈值必须是对象')

    cleaned = {}
    for key, raw in thresholds.items():
        if key not in SLA_METRICS:
            raise serializers.ValidationError(f'不支持的 SLA 指标：{key}')
        if raw in (None, ''):
            continue
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise serializers.ValidationError(f'SLA 指标 {key} 的阈值必须是数字')
        if not math.isfinite(num) or num < 0:
            raise serializers.ValidationError(f'SLA 指标 {key} 的阈值必须为有限非负数')
        if key == 'error_rate' and num > 100:
            raise serializers.ValidationError('错误率阈值不能超过 100%')
        cleaned[key] = num

    if not cleaned:
        raise serializers.ValidationError('启用 SLA 时至少要设置一个有效阈值')
    value['thresholds'] = cleaned

    if value.get('abort_on_breach'):
        window = value.get('breach_window') or 10
        try:
            window = int(window)
        except (TypeError, ValueError):
            raise serializers.ValidationError('熔断判定窗口必须是整数秒')
        if window < 1:
            raise serializers.ValidationError('熔断判定窗口至少 1 秒')
        value['breach_window'] = window
    return value


def validate_variables(value):
    if not isinstance(value, list):
        raise serializers.ValidationError('变量列表必须是数组')
    names = set()
    for idx, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise serializers.ValidationError(f'第 {idx} 个变量格式非法')
        name = (item.get('name') or '').strip()
        if not name:
            raise serializers.ValidationError(f'第 {idx} 个变量缺少名称')
        if name in names:
            raise serializers.ValidationError(f'变量名重复：{name}')
        names.add(name)
        vtype = (item.get('type') or 'CONSTANT').upper()
        if vtype not in VARIABLE_TYPES:
            raise serializers.ValidationError(f'变量 {name} 的类型 {vtype} 不支持')
        item['type'] = vtype
        if vtype == 'RANDOM_INT':
            try:
                if int(item.get('min', 0)) > int(item.get('max', 0)):
                    raise serializers.ValidationError(f'变量 {name} 的最小值大于最大值')
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'变量 {name} 的取值范围必须是整数')
        if vtype == 'ENUM':
            # 运行时求值统一读 values；options 是早期字段名，这里做一次归一化，
            # 否则「校验通过但运行时永远取到空串」这种问题根本查不出来。
            values = item.get('values') or item.get('options') or []
            if not isinstance(values, list) or not values:
                raise serializers.ValidationError(f'枚举变量 {name} 必须配置候选值')
            item['values'] = values
            item.pop('options', None)
        if vtype == 'CSV' and not item.get('data_file_id'):
            raise serializers.ValidationError(f'CSV 变量 {name} 必须选择数据文件')
    return value


# ====================================================================== #
# 项目
# ====================================================================== #
class PerfProjectSerializer(serializers.ModelSerializer):
    api_catalog = serializers.SerializerMethodField()
    owner = SimpleUserSerializer(read_only=True)
    members = SimpleUserSerializer(many=True, read_only=True)
    member_ids = serializers.ListField(child=serializers.IntegerField(),
                                       write_only=True, required=False)
    scenario_count = serializers.SerializerMethodField()
    execution_count = serializers.SerializerMethodField()

    class Meta:
        model = PerfProject
        fields = ['id', 'name', 'description', 'status', 'default_env', 'owner',
                  'members', 'member_ids', 'scenario_count', 'execution_count',
                  'created_at', 'updated_at', 'api_project', 'api_catalog']
        read_only_fields = ['created_at', 'updated_at', 'api_project']

    def get_api_catalog(self, obj):
        from .services import api_catalog
        return api_catalog.version_summary(api_catalog.latest_version(obj.pk))

    def get_scenario_count(self, obj):
        if hasattr(obj, 'scenario_count_anno'):
            return obj.scenario_count_anno or 0
        return obj.scenarios.filter(is_preparation=False).count()

    def get_execution_count(self, obj):
        if hasattr(obj, 'execution_count_anno'):
            return obj.execution_count_anno or 0
        return obj.executions.exclude(scenario__is_preparation=True).count()

    def create(self, validated_data):
        member_ids = validated_data.pop('member_ids', [])
        validated_data['owner'] = self.context['request'].user
        project = super().create(validated_data)
        if member_ids:
            project.members.set(User.objects.filter(id__in=member_ids))
        return project

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = PerfProject.objects.select_for_update().get(pk=instance.pk)
        user = self.context['request'].user
        environments.require_project_access(instance.pk, user)
        member_ids = validated_data.pop('member_ids', None)
        if member_ids is not None:
            current_members = set(instance.members.values_list('id', flat=True))
            if set(member_ids) != current_members:
                if instance.owner_id != user.pk and not environments.is_environment_admin(user):
                    raise environments.PermissionDenied('仅项目负责人或管理员可以增删项目成员')
            else:
                member_ids = None
        project = super().update(instance, validated_data)
        if member_ids is not None:
            project.members.set(User.objects.filter(id__in=member_ids))
        return project


# ====================================================================== #
# 步骤 / 场景
# ====================================================================== #
class PerfScenarioStepSerializer(serializers.ModelSerializer):
    readiness = serializers.SerializerMethodField()
    protocol = serializers.ChoiceField(choices=PerfScenarioStep.PROTOCOL_CHOICES, required=False,
        error_messages={'invalid_choice': '步骤协议仅支持 HTTP、WEBSOCKET 或 SSE'})

    class Meta:
        model = PerfScenarioStep
        fields = ['id', 'scenario', 'order', 'name', 'enabled', 'source_request',
                  'protocol', 'websocket_config', 'sse_config', 'execution_policy',
                  'method', 'url', 'headers', 'params', 'body_type', 'body', 'files',
                  'extractors', 'assertions', 'think_time', 'weight', 'is_setup',
                  'source_metadata', 'preparation', 'readiness']
        read_only_fields = ['source_metadata']

    def get_readiness(self, obj):
        from .services import api_catalog
        if obj.protocol != 'HTTP':
            from .services import executor
            from rest_framework.exceptions import PermissionDenied
            try:
                cache = self.context.setdefault('_ws_readiness', {})
                if obj.scenario_id not in cache:
                    resolved = executor._resolve_environment_inputs(obj.scenario, obj.scenario.created_by)
                    cache[obj.scenario_id] = {item['step_id']: {'ready': item['ready'], 'gaps': item['gaps']}
                        for item in api_catalog.scenario_readiness(obj.scenario, resolved)}
                return cache[obj.scenario_id].get(obj.pk, {'ready': False, 'gaps': []})
            except (serializers.ValidationError, PermissionDenied):
                return {'ready': False, 'gaps': [api_catalog.gap('websocket_config', 'websocket_config',
                    'WebSocket 配置或场景绑定尚未就绪')]}
        request = {key: getattr(obj, key) for key in api_catalog.REQUEST_FIELDS}
        return api_catalog.request_readiness(request, obj.source_metadata, obj.preparation, project_id=obj.scenario.project_id)

    def validate_headers(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('headers 必须为对象')
        return value

    def validate_params(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('params 必须为对象')
        return value

    def validate_preparation(self, value):
        if not isinstance(value, dict) or set(value) - {'confirmed_fields', 'body_reviewed'}:
            raise serializers.ValidationError('准备状态只支持 confirmed_fields 和 body_reviewed')
        if 'body_reviewed' in value and not isinstance(value['body_reviewed'], bool):
            raise serializers.ValidationError('body_reviewed 必须为布尔值')
        fields = value.get('confirmed_fields', [])
        if not isinstance(fields, list) or len(fields) > 500 or any(not isinstance(v, str) or len(v) > 500 for v in fields):
            raise serializers.ValidationError('confirmed_fields 必须为字段路径数组')
        return value

    def validate_execution_policy(self, value):
        from .services.execution_policy import normalize_policy
        return normalize_policy(value)

    def validate(self, attrs):
        from .services import api_catalog
        scene = attrs.get('scenario', getattr(self.instance, 'scenario', None))
        if scene and (scene.is_preparation or (self.instance and self.instance.scenario.is_preparation)):
            raise serializers.ValidationError('\u9a8c\u8bc1\u526f\u672c\u4e0d\u53ef\u4fee\u6539')
        request = self.context.get('request')
        if request and scene:
            environments.require_project_access(scene.project_id, request.user)
            if self.instance:
                environments.require_project_access(self.instance.scenario.project_id, request.user)
        if self.instance and scene and (attrs.get('enabled') is False or attrs.get('is_setup') is True or scene.pk != self.instance.scenario_id):
            referenced = any(r.get('step_id') == self.instance.pk for r in (self.instance.scenario.sla_config or {}).get('step_thresholds', []))
            if referenced:
                raise serializers.ValidationError('请先在 SLA 页移除此步骤的规则并保存，再禁用或调整步骤')
        source = attrs.get('source_request', getattr(self.instance, 'source_request', None))
        protocol = attrs.get('protocol', getattr(self.instance, 'protocol', 'HTTP'))
        config = attrs.get('websocket_config', getattr(self.instance, 'websocket_config', {}))
        sse = attrs.get('sse_config', getattr(self.instance, 'sse_config', {}))
        if protocol == 'SSE':
            from .services.sse_steps import normalize_sse_config, declares_sse
            if not scene or scene.engine != 'K6':
                raise serializers.ValidationError({'protocol': 'SSE 仅支持 K6'})
            metadata = getattr(self.instance, 'source_metadata', {})
            if (source or metadata) and not declares_sse(metadata):
                raise serializers.ValidationError({'protocol': '来源契约未声明 SSE 成功响应'})
            candidate = {key: attrs.get(key, getattr(self.instance, key, None)) for key in
                ('method', 'body_type', 'body', 'files', 'assertions', 'extractors')}
            if ((candidate['method'] or 'GET') not in ('GET', 'POST') or (candidate['body_type'] or 'NONE') not in ('NONE', 'JSON')
                    or any(candidate[key] for key in ('files', 'assertions', 'extractors')) or config
                    or (candidate['method'] or 'GET') == 'GET' and (candidate['body_type'] or 'NONE') != 'NONE'):
                raise serializers.ValidationError({'sse_config': 'SSE 请求字段或事件规则位置无效'})
            attrs['sse_config'] = normalize_sse_config(sse)
        elif sse:
            raise serializers.ValidationError({'sse_config': '非 SSE 步骤不能配置 SSE 事件合同'})
        policy = attrs.get('execution_policy', getattr(self.instance, 'execution_policy', {}))
        if policy:
            if not scene or scene.engine != 'K6' or attrs.get('is_setup', getattr(self.instance, 'is_setup', False)):
                raise serializers.ValidationError({'execution_policy': '执行策略仅支持 K6 业务步骤，前置步骤不能配置'})
            if policy['vu_end'] > int(scene.get_load_config().get('concurrency') or 1):
                raise serializers.ValidationError({'execution_policy': 'VU 范围超出场景并发用户数，请先保存压力配置'})
        if protocol == 'WEBSOCKET':
            from .services.websocket_steps import normalize_websocket_config, catalog_websocket_matches
            if not scene or scene.engine != 'K6':
                raise serializers.ValidationError({'protocol': 'WebSocket 步骤仅支持 K6'})
            metadata = getattr(self.instance, 'source_metadata', {})
            if source or metadata or (getattr(self, 'initial_data', {}) or {}).get('source_metadata'):
                candidate = {'method': attrs.get('method', getattr(self.instance, 'method', 'GET')),
                    'url': attrs.get('url', getattr(self.instance, 'url', None)),
                    'source_request_id': source.pk if source else None, 'source_metadata': metadata}
                if not catalog_websocket_matches(candidate):
                    raise serializers.ValidationError({'protocol': '来源须明确声明 WebSocket GET 升级，且保持原目录地址'})
                api_catalog.validate_source(scene.project_id, source, metadata)
                if self.instance and source != self.instance.source_request:
                    raise serializers.ValidationError({'source_request': '受控来源不能直接替换'})
            candidate = {key: attrs.get(key, getattr(self.instance, key, None))
                         for key in ('method', 'body_type', 'body', 'files', 'assertions', 'extractors', 'params', 'headers')}
            if ((candidate['method'] or 'GET') != 'GET' or (candidate['body_type'] or 'NONE') != 'NONE'
                    or any(candidate[key] for key in ('body', 'files', 'assertions', 'extractors', 'params'))):
                raise serializers.ValidationError({'protocol': 'WebSocket 使用 GET 握手，HTTP 请求体、参数、文件和规则必须为空'})
            if any(str(key).lower() in ('authorization', 'cookie') for key in candidate['headers'] or {}):
                raise serializers.ValidationError({'headers': 'WebSocket 握手请求头不能覆盖账号认证'})
            attrs['websocket_config'] = normalize_websocket_config(config)
            return attrs
        if config:
            raise serializers.ValidationError({'websocket_config': 'HTTP 步骤不能配置 WebSocket 会话'})
        if source:
            from apps.api_testing.models import ApiRequest
            source = ApiRequest.objects.select_related('collection__project').get(pk=source.pk)
            if 'source_request' in attrs:
                attrs['source_request'] = source
        metadata = getattr(self.instance, 'source_metadata', {})
        legacy = api_catalog.allow_legacy_edit(self.instance, scene, source, request.user if request else None) if scene else False
        if scene:
            if not legacy:
                api_catalog.validate_source(scene.project_id, source, metadata)
            file_ids = {item['file_id'] for item in attrs.get('files', getattr(self.instance, 'files', [])) if item.get('file_id')}
            valid = set(PerfDataFile.objects.filter(pk__in=file_ids, project_id=scene.project_id,
                file_type='UPLOAD').values_list('pk', flat=True))
            if file_ids - valid:
                raise serializers.ValidationError('上传文件不存在或不属于当前项目')
        if self.instance and source != self.instance.source_request and metadata:
            raise serializers.ValidationError('受控来源不能直接替换，请从接口库重新添加步骤')
        if source and not metadata and not legacy:
            version = api_catalog.latest_version(scene.project_id)
            operation = next((op for op in version.operations if op['id'] == source.pk), None) if version else None
            if not operation:
                raise serializers.ValidationError('请选择当前接口库内的来源接口')
            data, attrs['source_metadata'] = api_catalog.controlled_source(source, operation, version)
            for key, value in data.items():
                attrs.setdefault(key, value)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        scene = PerfScenario.objects.select_for_update().get(pk=validated_data['scenario'].pk)
        validated_data['scenario'] = scene
        return super().create(self.validate(validated_data))

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = PerfScenarioStep.objects.select_for_update().select_related('scenario').get(pk=instance.pk)
        self.instance = instance
        return super().update(instance, self.validate(validated_data))

    def validate_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('步骤名称不能为空（它同时是指标聚合标识）')
        return value

    def validate_url(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('请求 URL 不能为空')
        return value

    def validate_files(self, value):
        from .services.upload_files import normalize_files
        return normalize_files(value)

    def validate_extractors(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('提取规则必须是数组')
        for idx, item in enumerate(value, 1):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f'第 {idx} 条提取规则格式非法')
            if not (item.get('name') or '').strip():
                raise serializers.ValidationError(f'第 {idx} 条提取规则缺少变量名')
            etype = (item.get('type') or 'JSON_PATH').upper()
            if etype not in EXTRACTOR_TYPES:
                raise serializers.ValidationError(f'第 {idx} 条提取规则类型 {etype} 不支持')
            item['type'] = etype
            if not (item.get('expr') or '').strip():
                raise serializers.ValidationError(f'第 {idx} 条提取规则缺少表达式')
        return value

    def validate_assertions(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('断言规则必须是数组')
        for idx, item in enumerate(value, 1):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f'第 {idx} 条断言格式非法')
            atype = (item.get('type') or '').upper()
            if atype not in ASSERTION_TYPES:
                raise serializers.ValidationError(f'第 {idx} 条断言类型 {atype} 不支持')
            item['type'] = atype
            if item.get('expected') in (None, ''):
                raise serializers.ValidationError(f'第 {idx} 条断言缺少期望值')
            if atype == 'JSON_PATH':
                paths = [item[key] for key in ('expr', 'json_path') if key in item and item[key] not in (None, '')]
                if not paths or any(not isinstance(path, str) or not path.strip() for path in paths):
                    raise serializers.ValidationError(f'第 {idx} 条 JSON_PATH 断言缺少有效路径表达式')
                if len(set(paths)) > 1:
                    raise serializers.ValidationError(f'第 {idx} 条 JSON_PATH 断言路径字段互相冲突')
        return value


class PerfScenarioListSerializer(serializers.ModelSerializer):
    """列表页轻量序列化：不返回大 JSON 字段，避免列表接口体积失控。"""

    project_name = serializers.CharField(source='project.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    step_count = serializers.IntegerField(source='steps.count', read_only=True)
    load_model = serializers.SerializerMethodField()
    last_execution = serializers.SerializerMethodField()

    class Meta:
        model = PerfScenario
        fields = ['id', 'project', 'project_name', 'name', 'description', 'engine',
                  'enabled', 'step_count', 'load_model', 'last_execution',
                  'created_by_name', 'created_at', 'updated_at']

    def get_load_model(self, obj):
        return (obj.load_config or {}).get('model', 'CONCURRENCY')

    def get_last_execution(self, obj):
        from types import SimpleNamespace
        from .services.reporter import normalized_summary, frozen_engine
        request = self.context.get('request')
        if not request:
            return None
        if hasattr(obj, '_latest_exec_id'):
            if obj._latest_exec_id is None:
                return None
            execution = SimpleNamespace(id=obj._latest_exec_id,
                summary=getattr(obj, '_latest_summary', None) or {},
                load_snapshot=getattr(obj, '_latest_load', None) or {},
                execution_no=getattr(obj, '_latest_exec_no', ''),
                status=getattr(obj, '_latest_exec_status', ''),
                sla_result=getattr(obj, '_latest_exec_sla', ''),
                created_at=getattr(obj, '_latest_exec_created', None))
        else:
            execution = obj.executions.filter(project__in=environments.accessible_projects(request.user)).order_by('-created_at').first()
        if not execution:
            return None
        summary = normalized_summary(execution)
        return {'id': execution.id, 'engine': frozen_engine(execution) or obj.engine,
                'execution_no': execution.execution_no, 'status': execution.status,
                'sla_result': execution.sla_result, 'tps': summary.get('tps'),
                'p95_rt': summary.get('p95_rt'), 'error_rate': summary.get('error_rate'),
                'peak_tps': summary.get('peak_tps'), 'throughput_notice': summary.get('throughput_notice'),
                'created_at': execution.created_at}


class PerfEnvironmentSerializer(serializers.ModelSerializer):
    created_by = SimpleUserSerializer(read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True, default=None)

    class Meta:
        model = PerfEnvironment
        fields = ['id', 'name', 'scope', 'project', 'project_name', 'base_url', 'headers',
                  'verify_ssl', 'variables', 'is_active', 'version', 'content_hash',
                  'created_by', 'created_at', 'updated_at']
        read_only_fields = ['version', 'content_hash', 'created_at', 'updated_at']

    def validate(self, attrs):
        instance = self.instance
        scope = attrs.get('scope', getattr(instance, 'scope', 'PROJECT'))
        project = attrs.get('project', getattr(instance, 'project', None))
        if (scope == 'PROJECT' and project is None) or (scope == 'GLOBAL' and project is not None):
            raise serializers.ValidationError('PROJECT 环境必须关联项目；GLOBAL 环境不能关联项目')
        user = self.context['request'].user
        if instance:
            environments.require_environment_write(instance, user)
            if (scope != instance.scope or getattr(project, 'id', None) != instance.project_id):
                if instance.scenarios.exists() or instance.global_scenarios.exists():
                    raise serializers.ValidationError('环境已被场景引用，不能变更作用域或项目')
        environments.require_environment_write(PerfEnvironment(scope=scope, project=project), user)
        config = {key: attrs[key] for key in ('base_url', 'headers', 'verify_ssl', 'variables') if key in attrs}
        previous = {key: getattr(instance, key) for key in config} if instance else {}
        attrs.update(environments.validate_config(config, previous))
        return attrs

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        locked = PerfEnvironment.objects.select_for_update().get(pk=instance.pk)
        self.instance = locked
        validated_data = self.validate(validated_data)
        # A stale masked edit must not replace a newer credential.
        raw = self.initial_data
        config = {key: raw[key] for key in ('headers', 'variables') if key in raw}
        previous = {key: getattr(locked, key) for key in config}
        validated_data.update(environments.validate_config(config, previous))
        return super().update(locked, validated_data)

    def to_representation(self, instance):
        return environments.mask_config(super().to_representation(instance))


class PerfScenarioSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            return super().to_internal_value(data)
        if self.instance is None and isinstance(data, dict):
            try:
                project_id = int(data.get('project'))
            except (TypeError, ValueError, OverflowError):
                project_id = None
            project = PerfProject.objects.filter(pk=project_id).first() if project_id else None
            if project:
                from .services import scenario_defaults
                data = scenario_defaults.creation_data(dict(data.items()), project, self.context['request'].user)
        self._input_engine = data.get('engine', getattr(self.instance, 'engine', None))
        return super().to_internal_value(data)

    steps = PerfScenarioStepSerializer(many=True, read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    created_by = SimpleUserSerializer(read_only=True)
    has_active_execution = serializers.SerializerMethodField()
    resolved_environment = serializers.SerializerMethodField()

    class Meta:
        model = PerfScenario
        fields = ['id', 'project', 'project_name', 'name', 'description', 'engine',
                  'load_config', 'sla_config', 'perf_targets', 'variables', 'env_config',
                  'runtime_config', 'enabled', 'steps', 'created_by',
                  'environment', 'global_environment', 'resolved_environment',
                  'account_pool_version', 'account_pool_group',
                  'has_active_execution', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

    def get_has_active_execution(self, obj):
        return obj.has_active_execution()

    def get_resolved_environment(self, obj):
        try:
            request = self.context.get('request')
            resolved = environments.resolve_environment(obj, user=request.user if request else None)
            return environments.public_resolution(resolved)
        except (serializers.ValidationError, environments.PermissionDenied):
            return {'error': '环境引用无效或无权访问，请重新选择'}

    def validate(self, attrs):
        instance = self.instance
        project = attrs.get('project', getattr(instance, 'project', None))
        user = self.context['request'].user
        if instance:
            environments.require_project_access(instance.project_id, user)
            if project.pk != instance.project_id:
                from .services import api_catalog
                for step in instance.steps.all():
                    api_catalog.validate_source(project.pk, step.source_request, step.source_metadata)
        environments.validate_selection(
            project.id, attrs.get('environment', getattr(instance, 'environment', None)),
            attrs.get('global_environment', getattr(instance, 'global_environment', None)), user)
        environments.validate_engine_selection(
            attrs.get('engine', getattr(instance, 'engine', 'BUILTIN')),
            attrs.get('environment', getattr(instance, 'environment', None)),
            attrs.get('global_environment', getattr(instance, 'global_environment', None)))
        selected = attrs.get('account_pool_version', getattr(instance, 'account_pool_version', None))
        candidate = PerfScenario(project=project, created_by=user,
            engine=attrs.get('engine', getattr(instance, 'engine', 'BUILTIN')),
            variables=attrs.get('variables', getattr(instance, 'variables', [])),
            env_config=attrs.get('env_config', getattr(instance, 'env_config', {})),
            environment=attrs.get('environment', getattr(instance, 'environment', None)),
            global_environment=attrs.get('global_environment', getattr(instance, 'global_environment', None)))
        if instance and candidate.engine != 'K6' and instance.steps.exclude(protocol='HTTP').exists():
            raise serializers.ValidationError({'engine': '包含 WebSocket 步骤的场景仅支持 K6'})
        if instance and candidate.engine != 'K6' and instance.steps.exclude(execution_policy={}).exists():
            raise serializers.ValidationError({'engine': '包含执行策略的场景仅支持 K6'})
        resolved = environments.resolve_environment(candidate, user=user)
        account_pools.validate_binding(project.id, candidate.engine, selected,
            attrs.get('account_pool_group', getattr(instance, 'account_pool_group', '')), resolved['variables'], user)
        runtime = attrs.get('runtime_config', getattr(instance, 'runtime_config', {})) or {}
        recovery = runtime.get('resource_recovery') or {}
        if recovery:
            if candidate.engine != 'K6' or not instance:
                raise serializers.ValidationError({'resource_recovery': '请先保存 K6 场景的四个提醒步骤，再绑定恢复'})
            ids = [recovery[key] for key in ('put_step_id','receipt_step_id','get_step_id','delete_step_id')]
            owned = set(instance.steps.filter(pk__in=ids, enabled=True, is_setup=False).values_list('pk',flat=True))
            if owned != set(ids):
                raise serializers.ValidationError({'resource_recovery': '提醒恢复只能绑定本场景已保存且启用的业务步骤'})
        sla_config = attrs.get('sla_config', getattr(instance, 'sla_config', {})) or {}
        if candidate.engine == 'K6':
            from .services.k6_thresholds import validate_config
            steps = list(instance.steps.values('id', 'name', 'enabled', 'is_setup')) if instance else []
            try:
                attrs['sla_config'] = validate_config(sla_config, steps)
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({'sla_config': exc.detail}) from exc
        elif sla_config.get('step_thresholds') or sla_config.get('abort_delay'):
            raise serializers.ValidationError({'sla_config': '按接口 SLA 和观察延迟仅支持 K6'})
        if runtime.get('auth_profile') and candidate.engine != 'K6':
            raise serializers.ValidationError({'runtime_config': '每用户认证配置仅支持 K6'})
        return attrs

    def validate_load_config(self, value):
        return validate_load_config(value or {})

    def validate_sla_config(self, value):
        engine = self._input_engine
        if engine == 'K6':
            from .services.k6_thresholds import validate_config
            return validate_config(value)
        return validate_sla_config(value or {})

    def validate_variables(self, value):
        return environments.validate_environment_variables(
            value or [], self.instance.variables if self.instance else [], strict_names=False)

    def validate_env_config(self, value):
        previous = self.instance.env_config if self.instance else {}
        return environments.validate_config(value, previous)

    def validate_runtime_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('运行时配置必须是对象')
        from .services.reminder_recovery import normalize_config, RecoveryError
        if '_reminder_recovery' in value:
            raise serializers.ValidationError('不能设置执行内部恢复字段')
        if 'resource_recovery' in value:
            try:
                value = {**value, 'resource_recovery': normalize_config(value['resource_recovery'])}
            except RecoveryError as exc:
                raise serializers.ValidationError('提醒恢复配置无效：'+str(exc)) from None
            if value['resource_recovery'] and self._input_engine != 'K6':
                raise serializers.ValidationError('提醒恢复仅支持 K6')
        identity = value.get('account_identity_variable')
        engine = self._input_engine
        if engine == 'K6' and identity not in (None, ''):
            from .services.auth_profiles import NAME, RESERVED
            if not isinstance(identity, str) or not NAME.fullmatch(identity) or identity in RESERVED:
                raise serializers.ValidationError({'account_identity_variable': '请选择有效的账号身份变量名'})
        if 'auth_profile' in value:
            from .services.auth_profiles import normalize_profile
            previous = (self.instance.runtime_config or {}).get('auth_profile') if self.instance else None
            value = {**value, 'auth_profile': normalize_profile(value['auth_profile'], previous)}
        for key, label, lo, hi in (('timeout', '请求超时', 1, 300),
                                   ('sample_interval', '采样间隔', 1, 60)):
            if value.get(key) in (None, ''):
                continue
            try:
                num = int(value[key])
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'{label}必须是整数')
            if not lo <= num <= hi:
                raise serializers.ValidationError(f'{label}应在 {lo}~{hi} 之间')
            value[key] = num

        # script_ref 落库前先削平：只保留 mode 与 data_file_id。
        # jmx_path 之类的路径字段一律丢弃，真实路径由 views.resolve_script_ref
        # 从 PerfDataFile 反查并校验落在 MEDIA_ROOT 内，避免目录穿越。
        script_ref = value.get('script_ref')
        if script_ref not in (None, '', {}):
            if not isinstance(script_ref, dict):
                raise serializers.ValidationError('script_ref 必须是对象')
            mode = str(script_ref.get('mode') or 'scenario').strip().lower()
            if mode not in ('scenario', 'script'):
                raise serializers.ValidationError(f'不支持的执行模式：{mode}')
            if mode == 'script':
                if not script_ref.get('data_file_id'):
                    raise serializers.ValidationError('脚本模式必须选择一个 .jmx 文件')
                value['script_ref'] = {'mode': 'script',
                                       'data_file_id': script_ref['data_file_id']}
            else:
                value['script_ref'] = {'mode': 'scenario'}
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['variables'] = environments.mask_config({'variables': data.get('variables') or []})['variables']
        data['env_config'] = environments.mask_config(data.get('env_config') or {})
        if (data.get('runtime_config') or {}).get('auth_profile'):
            from .services.auth_profiles import public_profile
            data['runtime_config'] = deepcopy(data['runtime_config'])
            data['runtime_config']['auth_profile'] = public_profile(data['runtime_config']['auth_profile'])
        return data

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        locked = PerfScenario.objects.select_for_update().get(pk=instance.pk)
        self.instance = locked
        # Reinterpret masks after locking: validation may predate a credential rotation.
        if 'env_config' in self.initial_data:
            validated_data['env_config'] = self.validate_env_config(self.initial_data['env_config'])
        if 'variables' in self.initial_data:
            validated_data['variables'] = self.validate_variables(self.initial_data['variables'])
        if 'runtime_config' in self.initial_data:
            validated_data['runtime_config'] = self.validate_runtime_config(self.initial_data['runtime_config'])
        for field in ('environment', 'global_environment'):
            if validated_data.get(field) is not None:
                selected = PerfEnvironment.objects.select_for_update().filter(
                    pk=validated_data[field].pk).first()
                if selected is None:
                    raise serializers.ValidationError('引用的环境不存在，请重新选择')
                validated_data[field] = selected
        validated_data = self.validate(validated_data)
        return super().update(locked, validated_data)


# ====================================================================== #
# 执行
# ====================================================================== #
class PerfRequestStatSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerfRequestStat
        exclude = ['execution']


class PerfMetricSampleSerializer(serializers.ModelSerializer):
    k6_payload = serializers.SerializerMethodField()

    def get_k6_payload(self, obj):
        from .services.k6_samples import sanitized_payload
        return sanitized_payload(obj.k6_payload)

    class Meta:
        model = PerfMetricSample
        exclude = ['execution']


class PerfExecutionListSerializer(serializers.ModelSerializer):
    resource_recovery = serializers.SerializerMethodField()

    def get_resource_recovery(self, obj):
        from .services.reminder_recovery import observed_status
        return observed_status(obj)
    scenario_name = serializers.CharField(source='scenario.name', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    executed_by_name = serializers.SerializerMethodField()
    scheduled_task_name = serializers.CharField(source='scheduled_task.name', read_only=True,
                                                default=None)
    engine = serializers.SerializerMethodField()
    tps = serializers.SerializerMethodField()
    p95_rt = serializers.SerializerMethodField()
    error_rate = serializers.SerializerMethodField()
    total_requests = serializers.SerializerMethodField()
    progress = serializers.FloatField(read_only=True)

    class Meta:
        model = PerfExecution
        fields = ['id', 'execution_no', 'scenario', 'scenario_name', 'project',
                  'project_name', 'trigger_type', 'status', 'sla_result',
                  'executed_by_name', 'scheduled_task', 'scheduled_task_name',
                  'tps', 'p95_rt', 'error_rate', 'total_requests',
                  'engine', 'progress', 'start_time', 'end_time', 'duration', 'created_at', 'resource_recovery']

    def get_executed_by_name(self, obj):
        return obj.executed_by.username if obj.executed_by else '系统'

    def get_engine(self, obj):
        from .services.reporter import is_k6
        return 'K6' if is_k6(obj) else (obj.load_snapshot or {}).get('_engine', obj.scenario.engine)

    def get_tps(self, obj):
        from .services.reporter import normalized_summary
        return normalized_summary(obj).get('tps')

    def get_p95_rt(self, obj):
        from .services.reporter import normalized_summary
        return normalized_summary(obj).get('p95_rt')

    def get_error_rate(self, obj):
        from .services.reporter import normalized_summary
        return normalized_summary(obj).get('error_rate')

    def get_total_requests(self, obj):
        return (obj.summary or {}).get('total_requests')


class PerfExecutionDetailSerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()
    steps_snapshot = serializers.SerializerMethodField()
    resource_recovery = serializers.SerializerMethodField()

    def get_resource_recovery(self, obj):
        from .services.reminder_recovery import observed_status
        return observed_status(obj)

    def get_steps_snapshot(self, obj):
        from .services.reporter import report_steps
        return report_steps(obj)

    def get_summary(self, obj):
        from .services.reporter import normalized_summary
        return normalized_summary(obj)

    scenario_name = serializers.CharField(source='scenario.name', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    executed_by = SimpleUserSerializer(read_only=True)
    request_stats = PerfRequestStatSerializer(many=True, read_only=True)
    progress = serializers.FloatField(read_only=True)
    report_evidence = serializers.SerializerMethodField()
    has_raw_detail = serializers.SerializerMethodField()

    class Meta:
        model = PerfExecution
        fields = ['id', 'execution_no', 'scenario', 'scenario_name', 'project',
                  'project_name', 'trigger_type', 'status', 'sla_result',
                  'verdict', 'verdict_details',
                  'load_snapshot', 'steps_snapshot', 'summary', 'sla_detail',
                  'error_message', 'artifact_dir', 'report_url', 'worker_host',
                  'executed_by', 'request_stats', 'progress', 'has_raw_detail',
                  'start_time', 'end_time', 'duration', 'created_at', 'report_evidence', 'resource_recovery']

    def get_report_evidence(self, obj):
        from .services.reporter import report_evidence
        return report_evidence(obj)

    def get_has_raw_detail(self, obj):
        import os
        from django.conf import settings as dj_settings
        if not obj.artifact_dir:
            return False
        return os.path.isfile(os.path.join(
            dj_settings.MEDIA_ROOT, obj.artifact_dir, 'raw.csv.gz'))


# ====================================================================== #
# 基线 / 数据文件 / 定时任务
# ====================================================================== #
class PerfBaselineSerializer(serializers.ModelSerializer):
    scenario_name = serializers.CharField(source='scenario.name', read_only=True)
    execution_no = serializers.CharField(source='execution.execution_no', read_only=True)
    set_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PerfBaseline
        fields = ['id', 'scenario', 'scenario_name', 'execution', 'execution_no',
                  'metrics', 'tolerance', 'note', 'set_by_name', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

    def validate(self, attrs):
        scenario = attrs.get('scenario', getattr(self.instance, 'scenario', None))
        execution = attrs.get('execution', getattr(self.instance, 'execution', None))
        user = self.context['request'].user
        if scenario:
            environments.require_project_access(scenario.project_id, user)
        if not execution:
            raise serializers.ValidationError('基线必须关联可验证来源的执行；无来源的历史记录保留但不可比较')
        if execution:
            environments.require_project_access(execution.project_id, user)
            if not scenario or execution.scenario_id != scenario.pk or execution.project_id != scenario.project_id:
                raise serializers.ValidationError('基线执行必须属于同一场景和项目')
            if execution.status != 'COMPLETED':
                raise serializers.ValidationError('只有正常完成的执行才能作为基线')
            attrs['metrics'] = execution.summary or {}
        return attrs

    def get_set_by_name(self, obj):
        return obj.set_by.username if obj.set_by else ''

    def validate_tolerance(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('容忍度必须是对象')
        for key in ('rt_degrade_pct', 'tps_degrade_pct'):
            if value.get(key) in (None, ''):
                continue
            try:
                num = float(value[key])
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'{key} 必须是数字')
            if not 0 <= num <= 1000:
                raise serializers.ValidationError(f'{key} 应在 0~1000 之间')
            value[key] = num
        return value


class PerfDataFileSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    file_size = serializers.SerializerMethodField()

    class Meta:
        model = PerfDataFile
        fields = ['id', 'project', 'name', 'file_type', 'file', 'columns', 'row_count',
                  'meta', 'uploaded_by_name', 'file_size', 'created_at']
        # columns/row_count/meta 一律由 ViewSet 解析后写入，不接受前端伪造
        read_only_fields = ['columns', 'row_count', 'meta', 'created_at']

    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.username if obj.uploaded_by else ''

    def validate(self, attrs):
        project = attrs.get('project', getattr(self.instance, 'project', None))
        environments.require_project_access(project.pk, self.context['request'].user)
        if self.instance:
            environments.require_project_access(self.instance.project_id, self.context['request'].user)
        if attrs.get('file_type') == 'ACCOUNT' or (self.instance and self.instance.file_type == 'ACCOUNT'):
            raise serializers.ValidationError('私密账号池请使用账号池专用入口')
        return attrs

    def get_file_size(self, obj):
        try:
            return obj.file.size
        except Exception:  # noqa: BLE001 - 文件可能已被清理
            return 0


class PerfAccountPoolVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerfAccountPoolVersion
        fields = ['id', 'pool', 'version', 'content_hash', 'columns', 'identity_column',
                  'field_mapping', 'group_column', 'groups', 'row_count', 'created_at']
        read_only_fields = fields


class PerfAccountPoolSerializer(serializers.ModelSerializer):
    latest_version = serializers.SerializerMethodField()

    class Meta:
        model = PerfDataFile
        fields = ['id', 'project', 'name', 'latest_version', 'created_at']
        read_only_fields = fields

    def get_latest_version(self, instance):
        versions = list(instance.account_versions.all())
        return PerfAccountPoolVersionSerializer(versions[0]).data if versions else None


class PerfScheduledTaskSerializer(serializers.ModelSerializer):
    scenario_name = serializers.CharField(source='scenario.name', read_only=True)
    project = serializers.IntegerField(source='scenario.project_id', read_only=True)
    project_name = serializers.CharField(source='scenario.project.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = PerfScheduledTask
        fields = ['id', 'scenario', 'scenario_name', 'project', 'project_name', 'name',
                  'description', 'trigger_type', 'cron_expression', 'interval_minutes',
                  'scheduled_time', 'status', 'next_run_at', 'last_run_at', 'run_count',
                  'success_count', 'fail_count', 'notify_channels', 'notify_on',
                  'last_error', 'created_by_name', 'created_at', 'updated_at']
        read_only_fields = ['next_run_at', 'last_run_at', 'run_count', 'success_count',
                            'fail_count', 'last_error', 'created_at', 'updated_at']

    def validate(self, attrs):
        trigger = attrs.get('trigger_type') or getattr(self.instance, 'trigger_type', 'CRON')
        if trigger == 'CRON':
            expr = attrs.get('cron_expression') or getattr(self.instance, 'cron_expression', '')
            if not expr:
                raise serializers.ValidationError({'cron_expression': 'Cron 表达式不能为空'})
            try:
                from croniter import croniter
                if not croniter.is_valid(expr):
                    raise ValueError
            except ImportError:
                pass
            except Exception:
                raise serializers.ValidationError({'cron_expression': 'Cron 表达式格式非法'})
        elif trigger == 'INTERVAL':
            minutes = attrs.get('interval_minutes') or getattr(self.instance, 'interval_minutes', None)
            if not minutes or int(minutes) < 1:
                raise serializers.ValidationError({'interval_minutes': '间隔分钟数必须大于 0'})
        elif trigger == 'ONCE':
            when = attrs.get('scheduled_time') or getattr(self.instance, 'scheduled_time', None)
            if not when:
                raise serializers.ValidationError({'scheduled_time': '单次执行时间不能为空'})
        return attrs

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        task = super().create(validated_data)
        task.calculate_next_run()
        task.save(update_fields=['next_run_at'])
        return task

    def update(self, instance, validated_data):
        task = super().update(instance, validated_data)
        task.calculate_next_run()
        task.save(update_fields=['next_run_at'])
        return task
