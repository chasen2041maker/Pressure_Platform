"""性能测试模块数据模型。

设计遵循 apps/api_testing/models.py 的既有范式：
显式 db_table + db_table_comment + verbose_name + ordering，
所有字段带 db_comment，JSONField 必带 default。
"""
from datetime import timedelta
from copy import copy

from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone

User = get_user_model()


class PerfProject(models.Model):
    """压测项目：性能测试资产的顶层容器，提供数据隔离与成员管理。"""

    STATUS_CHOICES = [
        ('NOT_STARTED', '未开始'),
        ('IN_PROGRESS', '进行中'),
        ('COMPLETED', '已结束'),
    ]

    name = models.CharField(max_length=200, verbose_name='项目名称', db_comment='项目名称')
    description = models.TextField(blank=True, verbose_name='项目描述', db_comment='项目描述')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='IN_PROGRESS',
                              verbose_name='项目状态', db_comment='项目状态')
    default_env = models.JSONField(default=dict, blank=True, verbose_name='默认环境',
                                   db_comment='默认环境配置(JSON:{base_url,headers})')
    api_project = models.OneToOneField('api_testing.ApiProject', null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='perf_project')
    catalog_source = models.JSONField(default=dict, blank=True, editable=False)
    pool_config = models.JSONField(default=dict, blank=True, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_perf_projects',
                              verbose_name='负责人', db_comment='负责人')
    members = models.ManyToManyField(User, blank=True, related_name='perf_projects', verbose_name='团队成员')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间', db_comment='更新时间')

    class Meta:
        db_table_comment = '压测项目'
        db_table = 'perf_projects'
        verbose_name = '压测项目'
        verbose_name_plural = '压测项目'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class PerfScenario(models.Model):
    """压测场景：压测的核心编排单元，等价于 JMeter 的一个线程组 + 采样器集合。"""

    ENGINE_CHOICES = [
        ('K6', 'k6'),
        ('BUILTIN', '内置引擎(asyncio+httpx)'),
        ('LOCUST', 'Locust'),
        ('JMETER', 'JMeter'),
    ]

    project = models.ForeignKey(PerfProject, on_delete=models.CASCADE, related_name='scenarios',
                                verbose_name='所属项目', db_comment='所属项目')
    name = models.CharField(max_length=200, verbose_name='场景名称', db_comment='场景名称')
    description = models.TextField(blank=True, verbose_name='场景描述', db_comment='场景描述')
    is_preparation = models.BooleanField(default=False, editable=False)
    engine = models.CharField(max_length=20, choices=ENGINE_CHOICES, default='BUILTIN',
                              verbose_name='压测引擎', db_comment='压测引擎')
    load_config = models.JSONField(default=dict, blank=True, verbose_name='压力策略',
                                   db_comment='压力策略配置(JSON:{model,duration,concurrency,stages...})')
    sla_config = models.JSONField(default=dict, blank=True, verbose_name='SLA阈值',
                                  db_comment='SLA阈值配置(JSON:{enabled,thresholds,abort_on_breach})')
    perf_targets = models.JSONField(default=dict, blank=True, verbose_name='验收目标',
                                    db_comment='验收目标(JSON:{max_p95_rt,max_avg_rt,min_tps,max_error_rate})')
    variables = models.JSONField(default=list, blank=True, verbose_name='场景变量',
                                 db_comment='场景变量列表(JSON:[{name,type,value...}])')
    env_config = models.JSONField(default=dict, blank=True, verbose_name='环境配置',
                                  db_comment='环境配置(JSON:{base_url,headers,verify_ssl})')
    environment = models.ForeignKey('PerfEnvironment', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='scenarios',
                                    verbose_name='项目环境', db_comment='显式选择的项目环境')
    global_environment = models.ForeignKey('PerfEnvironment', on_delete=models.PROTECT,
                                           null=True, blank=True, related_name='global_scenarios',
                                           verbose_name='全局环境', db_comment='显式选择的全局环境')
    account_pool_version = models.ForeignKey('PerfAccountPoolVersion', on_delete=models.PROTECT,
                                            null=True, blank=True, related_name='scenarios')
    account_pool_group = models.CharField(max_length=64, blank=True, default='')
    runtime_config = models.JSONField(default=dict, blank=True, verbose_name='运行时配置',
                                      db_comment='运行时配置(JSON:{timeout,keep_alive,sample_interval})')
    enabled = models.BooleanField(default=True, verbose_name='是否启用', db_comment='是否启用')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_perf_scenarios',
                                   verbose_name='创建者', db_comment='创建者')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间', db_comment='更新时间')

    class Meta:
        db_table_comment = '压测场景'
        db_table = 'perf_scenarios'
        verbose_name = '压测场景'
        verbose_name_plural = '压测场景'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', '-created_at'], name='perf_scen_proj_created_idx'),
        ]

    def __str__(self):
        return self.name

    # ------------------------------------------------------------------ #
    # 默认配置：保证前端不传时也能安全执行
    # ------------------------------------------------------------------ #
    DEFAULT_LOAD_CONFIG = {
        'model': 'CONCURRENCY',
        'duration': 60,
        'max_requests': 0,
        'concurrency': 10,
        'ramp_up': 0,
        'stages': [],
        'target_rps': 0,
        'max_concurrency': 0,
        'baseline_concurrency': 0,
        'spike_concurrency': 0,
        'spike_duration': 0,
        'spike_times': 0,
    }

    DEFAULT_RUNTIME_CONFIG = {
        'timeout': 30,
        'keep_alive': True,
        'follow_redirects': False,
        'sample_interval': 1,
        'worker_processes': 1,
    }

    def get_load_config(self):
        cfg = dict(self.DEFAULT_LOAD_CONFIG)
        cfg.update(self.load_config or {})
        return cfg

    def get_runtime_config(self):
        cfg = dict(self.DEFAULT_RUNTIME_CONFIG)
        cfg.update(self.runtime_config or {})
        return cfg

    def has_active_execution(self):
        """场景级互斥：是否存在未进入终态的执行。"""
        return self.executions.filter(status__in=PerfExecution.ACTIVE_STATUSES).exists()


class PerfScenarioStep(models.Model):
    """压测场景步骤：一次虚拟用户迭代中的单个请求，指标按 name 聚合。"""

    HTTP_METHODS = [
        ('GET', 'GET'),
        ('POST', 'POST'),
        ('PUT', 'PUT'),
        ('DELETE', 'DELETE'),
        ('PATCH', 'PATCH'),
        ('HEAD', 'HEAD'),
        ('OPTIONS', 'OPTIONS'),
    ]

    PROTOCOL_CHOICES = [('HTTP', 'HTTP'), ('WEBSOCKET', 'WebSocket'), ('SSE', 'SSE')]

    BODY_TYPE_CHOICES = [
        ('NONE', '无'),
        ('JSON', 'JSON'),
        ('FORM', '表单'),
        ('BINARY', '原始文件'),
        ('RAW', '原始文本'),
        ('XML', 'XML'),
    ]

    scenario = models.ForeignKey(PerfScenario, on_delete=models.CASCADE, related_name='steps',
                                 verbose_name='所属场景', db_comment='所属场景')
    order = models.IntegerField(default=0, verbose_name='排序', db_comment='排序')
    name = models.CharField(max_length=200, verbose_name='步骤名称', db_comment='步骤名称(指标聚合标识)')
    enabled = models.BooleanField(default=True, verbose_name='是否启用', db_comment='是否启用')
    source_request = models.ForeignKey('api_testing.ApiRequest', on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='perf_steps',
                                       verbose_name='来源接口用例', db_comment='来源接口用例(用于溯源)')
    source_metadata = models.JSONField(default=dict, blank=True, editable=False)
    preparation = models.JSONField(default=dict, blank=True)
    protocol = models.CharField(max_length=20, choices=PROTOCOL_CHOICES, default='HTTP')
    websocket_config = models.JSONField(default=dict, blank=True)
    sse_config = models.JSONField(default=dict, blank=True)
    execution_policy = models.JSONField(default=dict, blank=True)
    method = models.CharField(max_length=10, choices=HTTP_METHODS, default='GET',
                              verbose_name='请求方法', db_comment='请求方法')
    url = models.CharField(max_length=1000, verbose_name='请求URL', db_comment='请求URL(支持${var}与相对路径)')
    headers = models.JSONField(default=dict, blank=True, verbose_name='请求头', db_comment='请求头')
    params = models.JSONField(default=dict, blank=True, verbose_name='Query参数', db_comment='Query参数')
    body_type = models.CharField(max_length=20, choices=BODY_TYPE_CHOICES, default='NONE',
                                 verbose_name='请求体类型', db_comment='请求体类型')
    body = models.TextField(blank=True, verbose_name='请求体', db_comment='请求体')
    files = models.JSONField(default=list, blank=True, verbose_name='文件上传字段',
                             db_comment='multipart文件字段(JSON:[{field,file_id,filename,content_type}])，'
                                        'file_id 指向 PerfDataFile(file_type=UPLOAD)')
    extractors = models.JSONField(default=list, blank=True, verbose_name='关联提取',
                                  db_comment='关联提取规则(JSON:[{name,type,expr}])')
    assertions = models.JSONField(default=list, blank=True, verbose_name='断言规则',
                                  db_comment='断言规则(JSON:[{type,expected}])')
    think_time = models.JSONField(default=dict, blank=True, verbose_name='思考时间',
                                  db_comment='思考时间(JSON:{type,min,max})')
    weight = models.IntegerField(default=1, verbose_name='权重', db_comment='权重')
    is_setup = models.BooleanField(default=False, verbose_name='是否前置步骤',
                                   db_comment='是否前置步骤(每虚拟用户仅执行一次，不计入TPS)')

    class Meta:
        db_table_comment = '压测场景步骤'
        db_table = 'perf_scenario_steps'
        verbose_name = '压测场景步骤'
        verbose_name_plural = '压测场景步骤'
        ordering = ['order', 'id']
        indexes = [
            models.Index(fields=['scenario', 'order'], name='perf_step_scen_order_idx'),
        ]

    def __str__(self):
        return f'{self.method} {self.name}'


class PerfPreparedRequest(models.Model):
    project = models.ForeignKey(PerfProject, on_delete=models.CASCADE, related_name='prepared_requests')
    source_key = models.CharField(max_length=1100)
    revision = models.PositiveIntegerField(default=1)
    source_catalog_version = models.PositiveIntegerField()
    source_hash = models.CharField(max_length=64)
    source_asset_hash = models.CharField(max_length=64)
    source_metadata = models.JSONField(default=dict)
    request = models.JSONField(default=dict)
    preparation = models.JSONField(default=dict)
    context = models.JSONField(default=dict)
    context_fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default='blocked')
    gaps = models.JSONField(default=list)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'perf_prepared_requests'
        constraints = [models.UniqueConstraint(fields=['project', 'source_key'], name='perf_prepared_source_unique')]


class PerfPreparationBatch(models.Model):
    project = models.ForeignKey(PerfProject, on_delete=models.CASCADE, related_name='preparation_batches')
    execution = models.OneToOneField('PerfExecution', null=True, blank=True, on_delete=models.SET_NULL)
    scenario = models.OneToOneField(PerfScenario, null=True, blank=True, on_delete=models.SET_NULL)
    request_key = models.CharField(max_length=64)
    launch_state = models.CharField(max_length=20, default='PENDING')
    error_code = models.CharField(max_length=64, blank=True, default='')
    entries = models.JSONField(default=list)
    context_fingerprint = models.CharField(max_length=64)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'perf_preparation_batches'
        constraints = [models.UniqueConstraint(fields=['project', 'created_by', 'request_key'], name='perf_preparation_request_unique')]


class ImmutableCatalogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('接口库版本不可修改，请导入新版本')


class PerfApiCatalogVersion(models.Model):
    project = models.ForeignKey(PerfProject, on_delete=models.CASCADE, related_name='api_catalog_versions')
    version = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    source_version = models.CharField(max_length=200, blank=True)
    document = models.JSONField(default=dict)
    operations = models.JSONField(default=list)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableCatalogQuerySet.as_manager()

    class Meta:
        db_table = 'perf_api_catalog_versions'
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['project', 'version'], name='perf_catalog_version_unique')]

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk:
            raise ValidationError('接口库版本不可修改，请导入新版本')
        return super().save(*args, **kwargs)


class PerfExecution(models.Model):
    """压测执行：一次压测任务的完整生命周期记录。"""

    TRIGGER_TYPE_CHOICES = [
        ('MANUAL', '手动触发'),
        ('SCHEDULED', '定时触发'),
        ('API', '接口触发'),
    ]

    STATUS_CHOICES = [
        ('PENDING', '待执行'),
        ('PREPARING', '准备中'),
        ('RUNNING', '执行中'),
        ('STOPPING', '停止中'),
        ('COMPLETED', '已完成'),
        ('FAILED', '失败'),
        ('STOPPED', '已停止'),
        ('TIMEOUT', '超时'),
    ]

    SLA_RESULT_CHOICES = [
        ('PASSED', '通过'),
        ('FAILED', '未通过'),
        ('NOT_EVALUATED', '未评估'),
    ]

    #: 非终态：用于场景互斥与僵尸回收扫描
    ACTIVE_STATUSES = ['PENDING', 'PREPARING', 'RUNNING', 'STOPPING']
    #: 终态
    FINAL_STATUSES = ['COMPLETED', 'FAILED', 'STOPPED', 'TIMEOUT']

    scenario = models.ForeignKey(PerfScenario, on_delete=models.CASCADE, related_name='executions',
                                 verbose_name='压测场景', db_comment='压测场景')
    project = models.ForeignKey(PerfProject, on_delete=models.CASCADE, related_name='executions',
                                verbose_name='所属项目', db_comment='所属项目(冗余，便于项目维度直查)')
    execution_no = models.CharField(max_length=50, unique=True, verbose_name='执行编号', db_comment='执行编号')
    account_pool_version = models.ForeignKey('PerfAccountPoolVersion', on_delete=models.PROTECT,
                                            null=True, blank=True, related_name='executions')
    trigger_type = models.CharField(max_length=20, choices=TRIGGER_TYPE_CHOICES, default='MANUAL',
                                    verbose_name='触发方式', db_comment='触发方式')
    # 同一场景可挂多个定时任务，只按 scenario 反查会把通知配置和统计算串，
    # 因此定时触发的执行必须记住是「哪个任务」拉起的；任务删除后执行记录仍需保留。
    scheduled_task = models.ForeignKey('PerfScheduledTask', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='executions', verbose_name='来源定时任务',
                                       db_comment='来源定时任务(手动执行为空)')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING',
                              verbose_name='执行状态', db_comment='执行状态')
    sla_result = models.CharField(max_length=20, choices=SLA_RESULT_CHOICES, default='NOT_EVALUATED',
                                  verbose_name='SLA判定结果', db_comment='SLA判定结果')
    verdict = models.CharField(max_length=20, choices=SLA_RESULT_CHOICES, default='NOT_EVALUATED',
                               verbose_name='验收判定', db_comment='验收目标判定(PASSED/FAILED/NOT_EVALUATED)')
    verdict_details = models.JSONField(default=list, blank=True, verbose_name='验收明细',
                                       db_comment='验收判定明细(JSON:[{step,target,actual,result}])')
    load_snapshot = models.JSONField(default=dict, blank=True, verbose_name='压力策略快照',
                                     db_comment='执行时的压力策略快照')
    steps_snapshot = models.JSONField(default=list, blank=True, verbose_name='步骤快照',
                                      db_comment='执行时的步骤快照')
    script_ref = models.JSONField(default=dict, blank=True, verbose_name='脚本引用',
                                   db_comment='执行模式与脚本引用；模式 script 时含 jmx_path（直接上传的 .jmx）')
    summary = models.JSONField(default=dict, blank=True, verbose_name='汇总指标', db_comment='汇总指标')
    resource_recovery = models.JSONField(default=dict, blank=True, editable=False, verbose_name='资源恢复状态',
                                         db_comment='提醒恢复安全计数；与压力结果独立')
    sla_detail = models.JSONField(default=list, blank=True, verbose_name='SLA逐项判定',
                                  db_comment='SLA逐项判定明细')
    error_message = models.TextField(blank=True, verbose_name='错误信息', db_comment='错误信息')
    process_pid = models.IntegerField(null=True, blank=True, verbose_name='子进程PID', db_comment='子进程PID')
    worker_host = models.CharField(max_length=100, blank=True, default='', verbose_name='执行机标识',
                                   db_comment='执行机标识')
    artifact_dir = models.CharField(max_length=500, blank=True, default='', verbose_name='产物目录',
                                    db_comment='产物目录相对路径')
    report_url = models.CharField(max_length=500, blank=True, default='', verbose_name='报告地址',
                                  db_comment='HTML报告地址')
    heartbeat_at = models.DateTimeField(null=True, blank=True, verbose_name='心跳时间',
                                        db_comment='心跳时间(用于僵尸执行检测)')
    executed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='perf_executions', verbose_name='执行人', db_comment='执行人')
    start_time = models.DateTimeField(null=True, blank=True, verbose_name='开始时间', db_comment='开始时间')
    end_time = models.DateTimeField(null=True, blank=True, verbose_name='结束时间', db_comment='结束时间')
    duration = models.FloatField(null=True, blank=True, verbose_name='执行时长', db_comment='执行时长(秒)')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')
    #: 报告/原始数据「可分享直链」令牌。空表示未开启分享。
    share_token = models.CharField(max_length=64, blank=True, null=True, unique=True,
                                   verbose_name='分享令牌', db_comment='报告分享直链令牌(空=未分享)')
    share_expires_at = models.DateTimeField(null=True, blank=True, verbose_name='分享过期时间',
                                            db_comment='分享直链过期时间(空=永不过期)')

    class Meta:
        db_table_comment = '压测执行'
        db_table = 'perf_executions'
        verbose_name = '压测执行'
        verbose_name_plural = '压测执行'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-start_time'], name='perf_exec_start_idx'),
            models.Index(fields=['scenario', '-created_at'], name='perf_exec_scen_created_idx'),
            models.Index(fields=['status'], name='perf_exec_status_idx'),
        ]

    def __str__(self):
        return self.execution_no

    @staticmethod
    def generate_execution_no():
        """生成人可读的执行编号：PERF + 年月日时分秒 + 3 位序号。"""
        now = timezone.localtime()
        prefix = f"PERF{now.strftime('%Y%m%d%H%M%S')}"
        # 同秒内可能并发，用当日计数补足序号
        seq = PerfExecution.objects.filter(execution_no__startswith=prefix).count() + 1
        return f'{prefix}{seq:03d}'

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def share_enabled(self):
        """分享直链是否有效（已生成令牌且未过期）。"""
        if not self.share_token:
            return False
        if self.share_expires_at and self.share_expires_at < timezone.now():
            return False
        return True

    def generate_share_token(self, expires_in_days=None):
        """生成/重置分享令牌。expires_in_days=None 表示永不过期。"""
        import secrets
        self.share_token = secrets.token_urlsafe(32)
        if expires_in_days:
            try:
                days = int(expires_in_days)
            except (TypeError, ValueError):
                days = 0
            self.share_expires_at = timezone.now() + timedelta(days=days) if days > 0 else None
        else:
            self.share_expires_at = None
        self.save(update_fields=['share_token', 'share_expires_at'])
        return self.share_token

    def revoke_share_token(self):
        """撤销分享直链。"""
        self.share_token = None
        self.share_expires_at = None
        self.save(update_fields=['share_token', 'share_expires_at'])

    @property
    def progress(self):
        """执行进度百分比（基于快照中的计划时长估算）。"""
        if self.status in self.FINAL_STATUSES:
            return 100.0
        if not self.start_time:
            return 0.0
        planned = (self.load_snapshot or {}).get('_planned_duration') or 0
        if planned <= 0:
            return 0.0
        elapsed = (timezone.now() - self.start_time).total_seconds()
        return round(min(elapsed / planned * 100, 99.9), 1)


class PerfRequestStat(models.Model):
    """接口级聚合统计：一次执行 × 每个步骤一行。"""

    execution = models.ForeignKey(PerfExecution, on_delete=models.CASCADE, related_name='request_stats',
                                  verbose_name='所属执行', db_comment='所属执行')
    step_name = models.CharField(max_length=200, verbose_name='步骤名称', db_comment='步骤名称')
    method = models.CharField(max_length=10, blank=True, default='', verbose_name='请求方法', db_comment='请求方法')
    url = models.CharField(max_length=1000, blank=True, default='', verbose_name='请求URL', db_comment='请求URL')
    total = models.IntegerField(default=0, verbose_name='请求总数', db_comment='请求总数')
    success = models.IntegerField(default=0, verbose_name='成功数', db_comment='成功数')
    failed = models.IntegerField(default=0, verbose_name='失败数', db_comment='失败数')
    error_rate = models.FloatField(default=0, verbose_name='错误率', db_comment='错误率(%)')
    tps = models.FloatField(default=0, verbose_name='TPS', db_comment='每秒事务数')
    avg_rt = models.FloatField(default=0, verbose_name='平均响应时间', db_comment='平均响应时间(ms)')
    min_rt = models.FloatField(default=0, verbose_name='最小响应时间', db_comment='最小响应时间(ms)')
    max_rt = models.FloatField(default=0, verbose_name='最大响应时间', db_comment='最大响应时间(ms)')
    p50_rt = models.FloatField(default=0, verbose_name='P50响应时间', db_comment='P50响应时间(ms)')
    p90_rt = models.FloatField(default=0, verbose_name='P90响应时间', db_comment='P90响应时间(ms)')
    p95_rt = models.FloatField(default=0, verbose_name='P95响应时间', db_comment='P95响应时间(ms)')
    p99_rt = models.FloatField(default=0, verbose_name='P99响应时间', db_comment='P99响应时间(ms)')
    sent_bytes = models.BigIntegerField(default=0, verbose_name='发送字节数', db_comment='发送字节数')
    recv_bytes = models.BigIntegerField(default=0, verbose_name='接收字节数', db_comment='接收字节数')
    error_detail = models.JSONField(default=list, blank=True, verbose_name='错误明细',
                                    db_comment='错误明细(JSON:[{type,count,message}])')

    class Meta:
        db_table_comment = '压测接口级统计'
        db_table = 'perf_request_stats'
        verbose_name = '压测接口级统计'
        verbose_name_plural = '压测接口级统计'
        ordering = ['-avg_rt']
        indexes = [
            models.Index(fields=['execution'], name='perf_stat_exec_idx'),
        ]

    def __str__(self):
        return f'{self.step_name}({self.total})'


class PerfMetricSample(models.Model):
    """时序采样点：标量聚合兼容原引擎；K6来源由每行独立保存，时间偏移不作身份。"""

    execution = models.ForeignKey(PerfExecution, on_delete=models.CASCADE, related_name='samples',
                                  verbose_name='所属执行', db_comment='所属执行')
    ts_offset = models.IntegerField(default=0, verbose_name='时间偏移', db_comment='相对执行开始的秒偏移')
    active_users = models.IntegerField(default=0, verbose_name='活跃虚拟用户', db_comment='活跃虚拟用户数')
    tps = models.FloatField(default=0, verbose_name='TPS', db_comment='每秒事务数')
    avg_rt = models.FloatField(default=0, verbose_name='平均响应时间', db_comment='平均响应时间(ms)')
    p90_rt = models.FloatField(default=0, verbose_name='P90响应时间', db_comment='P90响应时间(ms)')
    p95_rt = models.FloatField(default=0, verbose_name='P95响应时间', db_comment='P95响应时间(ms)')
    p99_rt = models.FloatField(default=0, verbose_name='P99响应时间', db_comment='P99响应时间(ms)')
    error_rate = models.FloatField(default=0, verbose_name='错误率', db_comment='错误率(%)')
    total_requests = models.IntegerField(default=0, verbose_name='累计请求数', db_comment='累计请求数')
    cpu_percent = models.FloatField(default=0, verbose_name='压力机CPU', db_comment='压力机进程CPU占用(%)')
    memory_mb = models.FloatField(default=0, verbose_name='压力机内存', db_comment='压力机进程内存(MB)')

    k6_payload = models.JSONField(default=dict, blank=True, verbose_name='K6采样来源')

    class Meta:
        db_table_comment = '压测时序采样点'
        db_table = 'perf_metric_samples'
        verbose_name = '压测时序采样点'
        verbose_name_plural = '压测时序采样点'
        ordering = ['ts_offset']
        indexes = [
            models.Index(fields=['execution', 'ts_offset'], name='perf_sample_exec_ts_idx'),
        ]

    def __str__(self):
        return f'{self.execution_id}@{self.ts_offset}s'


class PerfBaseline(models.Model):
    """性能基线：一个场景一条，用于历史对比与劣化告警。"""

    scenario = models.OneToOneField(PerfScenario, on_delete=models.CASCADE, related_name='baseline',
                                    verbose_name='压测场景', db_comment='压测场景')
    execution = models.ForeignKey(PerfExecution, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='baselines', verbose_name='基线来源执行', db_comment='基线来源执行')
    metrics = models.JSONField(default=dict, blank=True, verbose_name='基线指标', db_comment='基线指标快照')
    tolerance = models.JSONField(default=dict, blank=True, verbose_name='容忍度',
                                 db_comment='容忍度(JSON:{rt_degrade_pct,tps_degrade_pct})')
    note = models.TextField(blank=True, verbose_name='备注', db_comment='备注')
    set_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='perf_baselines', verbose_name='设置人', db_comment='设置人')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间', db_comment='更新时间')

    DEFAULT_TOLERANCE = {'rt_degrade_pct': 20, 'tps_degrade_pct': 15}

    class Meta:
        db_table_comment = '压测性能基线'
        db_table = 'perf_baselines'
        verbose_name = '压测性能基线'
        verbose_name_plural = '压测性能基线'
        ordering = ['-created_at']

    def __str__(self):
        return f'baseline of {self.scenario_id}'


class PerfDataFile(models.Model):
    """压测文件资产：CSV 参数化数据文件 + JMeter .jmx 脚本 + 请求上传文件。

    为什么三类文件共用一张表：
    - 生命周期、权限、归属项目、上传人、删除保护规则完全一致，拆表只会带来
      两套几乎相同的 ViewSet/序列化器/前端列表。
    - 差异只体现在「上传时如何校验」与「解析出什么元信息」，用 file_type 分流即可。
    file_type=CSV 时 columns/row_count 有效；file_type=JMX 时元信息落在 meta；
    file_type=UPLOAD 时为请求体上传文件（multipart/form-data），元信息落在 meta。
    """

    FILE_TYPE_CHOICES = [
        ('CSV', 'CSV 参数化数据'),
        ('JMX', 'JMeter 脚本'),
        ('UPLOAD', '请求上传文件'),
        ('ACCOUNT', '私密账号池'),
    ]

    project = models.ForeignKey(PerfProject, on_delete=models.CASCADE, related_name='data_files',
                                verbose_name='所属项目', db_comment='所属项目')
    name = models.CharField(max_length=200, verbose_name='文件名称', db_comment='文件名称')
    file_type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default='CSV',
                                 verbose_name='文件类型', db_comment='文件类型：CSV参数化数据/JMX脚本')
    file = models.FileField(upload_to='perf-testing/datafiles/%Y%m/', verbose_name='文件', db_comment='文件路径')
    columns = models.JSONField(default=list, blank=True, verbose_name='列名', db_comment='首行解析出的列名(仅CSV)')
    row_count = models.IntegerField(default=0, verbose_name='数据行数', db_comment='数据行数(不含表头，仅CSV)')
    meta = models.JSONField(default=dict, blank=True, verbose_name='解析元信息',
                            db_comment='文件解析摘要，JMX 存线程组/采样器/目标域名等')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='perf_data_files', verbose_name='上传人', db_comment='上传人')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')

    class Meta:
        db_table_comment = '压测数据文件'
        db_table = 'perf_data_files'
        verbose_name = '压测数据文件'
        verbose_name_plural = '压测数据文件'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def is_script(self):
        return self.file_type == 'JMX'


class ImmutableAccountVersionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('账号池版本不可修改，请导入新版本')


class PerfAccountPoolVersion(models.Model):
    pool = models.ForeignKey(PerfDataFile, on_delete=models.CASCADE, related_name='account_versions')
    version = models.PositiveIntegerField()
    private_file = models.CharField(max_length=64, editable=False)
    content_hash = models.CharField(max_length=64, editable=False)
    columns = models.JSONField(default=list)
    identity_column = models.CharField(max_length=128)
    field_mapping = models.JSONField(default=dict)
    group_column = models.CharField(max_length=128, blank=True, default='')
    groups = models.JSONField(default=list)
    row_count = models.PositiveIntegerField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableAccountVersionQuerySet.as_manager()

    class Meta:
        db_table = 'perf_account_pool_versions'
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['pool', 'version'], name='perf_pool_version_unique')]

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk:
            raise ValidationError('账号池版本不可修改，请导入新版本')
        return super().save(*args, **kwargs)


from django.db.models.signals import post_delete
from .services.account_pools import cleanup_version_file
post_delete.connect(cleanup_version_file, sender=PerfAccountPoolVersion,
                    dispatch_uid='perf_account_version_private_cleanup')


class PerfScheduledTask(models.Model):
    """定时压测任务：由平台统一调度命令 run_all_scheduled_tasks 驱动。"""

    TRIGGER_TYPE_CHOICES = [
        ('CRON', 'Cron表达式'),
        ('INTERVAL', '固定间隔'),
        ('ONCE', '单次执行'),
    ]

    STATUS_CHOICES = [
        ('ACTIVE', '激活'),
        ('PAUSED', '暂停'),
    ]

    NOTIFY_ON_CHOICES = [
        ('ALWAYS', '总是通知'),
        ('ON_SLA_FAIL', '仅SLA失败时'),
        ('NEVER', '不通知'),
    ]

    scenario = models.ForeignKey(PerfScenario, on_delete=models.CASCADE, related_name='scheduled_tasks',
                                 verbose_name='压测场景', db_comment='压测场景')
    name = models.CharField(max_length=200, verbose_name='任务名称', db_comment='任务名称')
    description = models.TextField(blank=True, verbose_name='任务描述', db_comment='任务描述')
    trigger_type = models.CharField(max_length=20, choices=TRIGGER_TYPE_CHOICES, default='CRON',
                                    verbose_name='触发器类型', db_comment='触发器类型')
    cron_expression = models.CharField(max_length=100, blank=True, default='', verbose_name='Cron表达式',
                                       db_comment='Cron表达式')
    interval_minutes = models.IntegerField(null=True, blank=True, verbose_name='间隔分钟数', db_comment='间隔分钟数')
    scheduled_time = models.DateTimeField(null=True, blank=True, verbose_name='单次执行时间', db_comment='单次执行时间')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE',
                              verbose_name='任务状态', db_comment='任务状态')
    next_run_at = models.DateTimeField(null=True, blank=True, verbose_name='下次执行时间', db_comment='下次执行时间')
    last_run_at = models.DateTimeField(null=True, blank=True, verbose_name='上次执行时间', db_comment='上次执行时间')
    run_count = models.IntegerField(default=0, verbose_name='执行次数', db_comment='执行次数')
    success_count = models.IntegerField(default=0, verbose_name='成功次数', db_comment='成功次数')
    fail_count = models.IntegerField(default=0, verbose_name='失败次数', db_comment='失败次数')
    notify_channels = models.JSONField(default=list, blank=True, verbose_name='通知渠道',
                                       db_comment='通知渠道ID列表(复用monitor.NotificationChannel)')
    notify_on = models.CharField(max_length=20, choices=NOTIFY_ON_CHOICES, default='ON_SLA_FAIL',
                                 verbose_name='通知时机', db_comment='通知时机')
    last_error = models.TextField(blank=True, verbose_name='最后错误', db_comment='最后一次错误信息')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='perf_scheduled_tasks',
                                   verbose_name='创建者', db_comment='创建者')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间', db_comment='更新时间')

    class Meta:
        db_table_comment = '定时压测任务'
        db_table = 'perf_scheduled_tasks'
        verbose_name = '定时压测任务'
        verbose_name_plural = '定时压测任务'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'next_run_at'], name='perf_sched_status_next_idx'),
        ]

    def __str__(self):
        return self.name

    def calculate_next_run(self):
        """计算并写入下次执行时间（不落库，由调用方 save）。"""
        now = timezone.now()
        next_run = None

        if self.trigger_type == 'CRON' and self.cron_expression:
            try:
                from croniter import croniter
                next_run = croniter(self.cron_expression, now).get_next(timezone.datetime)
            except Exception:  # noqa: BLE001 - 非法表达式不应中断调度循环
                next_run = None
        elif self.trigger_type == 'INTERVAL' and self.interval_minutes:
            next_run = now + timedelta(minutes=self.interval_minutes)
        elif self.trigger_type == 'ONCE' and self.scheduled_time:
            next_run = self.scheduled_time if self.scheduled_time > now else None

        self.next_run_at = next_run
        return next_run

    def should_run_now(self):
        if self.status != 'ACTIVE':
            return False
        if not self.next_run_at:
            return False
        return timezone.now() >= self.next_run_at

    def mark_dispatched(self, error=''):
        """调度器派发一次执行后调用（落库）。

        压测是异步长任务，调度器只知道「有没有成功拉起」，不知道压测结果，
        所以这里只累加 run_count 并推进下次执行时间；
        真正的 success_count / fail_count 由压测子进程收尾时回写
        （见 services/executor._update_task_stats），避免两处重复计数。
        派发阶段就失败（如前置校验不通过）的，直接计入失败。
        """
        self.run_count += 1
        self.last_run_at = timezone.now()
        if error:
            self.fail_count += 1
            self.last_error = error[:2000]

        fields = ['run_count', 'last_run_at', 'fail_count', 'last_error',
                  'status', 'next_run_at', 'updated_at']
        # ONCE 任务执行后自动暂停，避免下轮循环重复触发
        if self.trigger_type == 'ONCE':
            self.status = 'PAUSED'
            self.next_run_at = None
        else:
            self.calculate_next_run()
        self.save(update_fields=fields)


class PerfEnvironment(models.Model):
    """压测环境：跨场景复用的命名环境，等价于一组 base_url/headers/变量。

    解决同一套接口在 dev/staging/prod 等多环境压测时反复手工改 base_url、
    以及从接口导入的 {{baseUrl}} 只能手动填的问题。参照 api_testing.Environment
    范式，但字段更贴合压测（含 base_url/headers/verify_ssl）。
    """

    SCOPE_CHOICES = [
        ('GLOBAL', '全局环境'),
        ('PROJECT', '项目环境'),
    ]

    name = models.CharField(max_length=200, verbose_name='环境名称', db_comment='环境名称')
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default='PROJECT',
                             verbose_name='作用域', db_comment='作用域(GLOBAL/PROJECT)')
    project = models.ForeignKey(PerfProject, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='environments', verbose_name='关联项目',
                                db_comment='关联压测项目(PROJECT 作用域必填)')
    base_url = models.CharField(max_length=500, blank=True, default='', verbose_name='基础地址',
                               db_comment='环境基础地址(如 https://api.example.com)')
    headers = models.JSONField(default=dict, blank=True, verbose_name='全局请求头',
                               db_comment='环境级全局请求头(JSON:{k:v})')
    verify_ssl = models.BooleanField(default=False, verbose_name='校验 SSL 证书',
                                     db_comment='是否校验 SSL 证书')
    variables = models.JSONField(default=list, blank=True, verbose_name='环境变量',
                                 db_comment='环境变量列表(JSON:[{name,type,value}])')
    is_active = models.BooleanField(default=False, verbose_name='是否激活',
                                   db_comment='是否激活(同作用域内唯一)')
    version = models.PositiveIntegerField(default=1, editable=False,
                                         verbose_name='配置版本', db_comment='环境内容版本')
    content_hash = models.CharField(max_length=64, blank=True, editable=False,
                                    verbose_name='内容指纹', db_comment='环境内容 HMAC-SHA256 指纹')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='创建者',
                                   db_comment='创建者')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间', db_comment='更新时间')

    class Meta:
        db_table_comment = '压测环境'
        db_table = 'perf_environments'
        verbose_name = '压测环境'
        verbose_name_plural = '压测环境'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['scope', 'is_active'], name='perf_env_scope_active_idx'),
            models.Index(fields=['project', 'scope'], name='perf_env_proj_scope_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=(models.Q(scope='GLOBAL', project__isnull=True)
                       | models.Q(scope='PROJECT', project__isnull=False)),
                name='perf_env_valid_scope_project'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_scope_display()})"

    @transaction.atomic
    def save(self, *args, **kwargs):
        """保证同作用域内只有一个激活环境（GLOBAL 全局唯一；PROJECT 每项目唯一）。"""
        from .services.environments import content_hash
        fields = kwargs.get('update_fields')
        if fields is not None and not fields:
            return
        persisted = self
        old = None
        if self.pk:
            old = type(self).objects.select_for_update().filter(pk=self.pk).first()
            if old and fields is not None:
                persisted = copy(old)
                for field in self._meta.concrete_fields:
                    if field.name in fields or field.attname in fields:
                        setattr(persisted, field.attname, getattr(self, field.attname))
        fingerprint = content_hash(persisted)
        if old:
            self.version = old.version + int(fingerprint != old.content_hash)
        self.content_hash = fingerprint
        if fields is not None:
            kwargs['update_fields'] = set(fields) | {'version', 'content_hash'}
        super().save(*args, **kwargs)
        if persisted.is_active:
            qs = PerfEnvironment.objects.filter(scope=persisted.scope, is_active=True)
            if persisted.scope == 'PROJECT':
                qs = qs.filter(project_id=persisted.project_id)
            qs = qs.exclude(pk=self.pk)
            if qs.exists():
                qs.update(is_active=False)


class PerfComparisonReport(models.Model):
    """多轮执行对照报告：持久化指标矩阵快照 + 可选 AI 对照分析。"""

    project = models.ForeignKey(PerfProject, on_delete=models.CASCADE,
                                related_name='comparison_reports',
                                verbose_name='所属项目', db_comment='所属项目')
    title = models.CharField(max_length=200, verbose_name='报告标题', db_comment='报告标题')
    execution_ids = models.JSONField(default=list, verbose_name='参与对比的执行ID列表',
                                     db_comment='参与对比的执行ID列表(2~5个，保持顺序)')
    reference_execution_id = models.IntegerField(null=True, blank=True,
                                                 verbose_name='基准执行ID', db_comment='基准执行ID')
    snapshot = models.JSONField(default=dict, verbose_name='对照快照',
                                db_comment='指标矩阵快照(同 compare API 结构)')
    ai_analysis = models.TextField(null=True, blank=True,
                                   verbose_name='AI对照分析', db_comment='AI对照分析(markdown)')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE,
                                   related_name='perf_comparison_reports',
                                   verbose_name='创建者', db_comment='创建者')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间', db_comment='创建时间')

    class Meta:
        db_table_comment = '压测多轮对照报告'
        db_table = 'perf_comparison_reports'
        verbose_name = '压测多轮对照报告'
        verbose_name_plural = '压测多轮对照报告'
        ordering = ['-created_at']

    def __str__(self):
        return self.title
