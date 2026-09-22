"""
智能评分器数据模型。

设计原则：
- Rubric 配置化：维度、规则参数、权重、门禁阈值全部存 DB，支持多领域（金融/客服/通用问答/自定义）
- JudgeRecord/JudgeBatch：评分历史全链路落库，替代原 SQLite log，便于前端查询和 Dashboard 聚合
"""
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Rubric(models.Model):
    """评分标准：一个 Rubric = 一套维度 + 规则参数 + 权重 + 门禁配置。"""

    DOMAIN_CHOICES = (
        ('finance', '金融领域'),
        ('qa', '通用问答'),
        ('customer_service', '客服场景'),
        ('custom', '自定义'),
    )

    name = models.CharField(max_length=100, unique=True, verbose_name='名称')
    domain = models.CharField(max_length=30, choices=DOMAIN_CHOICES, default='qa', verbose_name='领域')
    description = models.TextField(blank=True, default='', verbose_name='描述')
    version = models.CharField(max_length=20, default='1.0.0', verbose_name='版本')
    is_default = models.BooleanField(default=False, verbose_name='是否默认')
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    # 评分合成权重：{"rule": 0.4, "llm": 0.6}
    scoring_weights = models.JSONField(default=dict, verbose_name='评分权重')
    # 门禁阈值：{"green_mean": 85, "yellow_mean": 70, "safety_pass_rate": 1.0, "critical_success_rate": 0.95}
    gate_config = models.JSONField(default=dict, verbose_name='门禁配置')
    # LLM Judge 参数：{"n_runs": 3, "temperature": 0, "judge_models": ["deepseek-chat"]}
    judge_config = models.JSONField(default=dict, verbose_name='Judge 配置')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='创建人')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'llm_judge_rubric'
        verbose_name = '评分标准'
        verbose_name_plural = verbose_name
        ordering = ['-is_default', '-created_at']
        indexes = [
            models.Index(fields=['domain', 'is_active']),
            models.Index(fields=['is_default']),
        ]

    def __str__(self):
        return f'{self.name} v{self.version}'

    def save(self, *args, **kwargs):
        # 唯一默认：设为默认时，取消其他默认
        if self.is_default:
            Rubric.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        # 默认权重兜底
        if not self.scoring_weights:
            self.scoring_weights = {'rule': 0.4, 'llm': 0.6}
        if not self.gate_config:
            self.gate_config = {
                'green_mean': 85, 'yellow_mean': 70,
                'safety_pass_rate': 1.0, 'critical_success_rate': 0.95,
                'veto_threshold': 0,
            }
        if not self.judge_config:
            self.judge_config = {'n_runs': 3, 'temperature': 0.0, 'judge_models': []}
        super().save(*args, **kwargs)


class RubricDimension(models.Model):
    """评分维度：score 型（1-5 分）/ bool 型（通过/不通过）。"""

    TYPE_CHOICES = (
        ('score', '评分型(1-5)'),
        ('bool', '布尔型'),
    )

    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name='dimensions', verbose_name='评分标准')
    dim_key = models.CharField(max_length=50, verbose_name='维度键')
    name = models.CharField(max_length=100, verbose_name='维度名称')
    dim_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='score', verbose_name='维度类型')
    weight = models.FloatField(default=1.0, verbose_name='权重')
    # 评分锚点：{"1": "差", "2": "较差", "3": "中", "4": "良", "5": "优"}
    anchor_text = models.JSONField(default=dict, verbose_name='评分锚点')
    vetoable = models.BooleanField(default=False, verbose_name='是否可触发否决')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'llm_judge_rubric_dimension'
        verbose_name = '评分维度'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']
        unique_together = [('rubric', 'dim_key')]
        indexes = [models.Index(fields=['rubric', 'sort_order'])]

    def __str__(self):
        return f'{self.rubric.name} · {self.name}'


class RubricRule(models.Model):
    """规则参数：absolute_words / disclaimer / timeliness / numeric_gt / custom_regex 等。"""

    SEVERITY_CHOICES = (
        ('info', '提示'),
        ('warn', '警告'),
        ('critical', '严重'),
    )
    FALLBACK_CHOICES = (
        ('keyword', '关键词匹配'),
        ('regex', '正则匹配'),
        ('llm', 'LLM 兜底'),
        ('hybrid', '混合(关键词+LLM)'),
    )

    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name='rules', verbose_name='评分标准')
    rule_key = models.CharField(max_length=50, verbose_name='规则键')
    name = models.CharField(max_length=100, verbose_name='规则名称')
    enabled = models.BooleanField(default=True, verbose_name='是否启用')
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='warn', verbose_name='严重度')
    is_veto = models.BooleanField(default=False, verbose_name='是否一票否决')
    fallback_mode = models.CharField(max_length=10, choices=FALLBACK_CHOICES, default='keyword', verbose_name='兜底模式')
    # 参数：{"keywords": [...], "tolerance": 0.05, "calendar_code": "CN_A_SHARE", ...}
    params = models.JSONField(default=dict, verbose_name='规则参数')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'llm_judge_rubric_rule'
        verbose_name = '评分规则'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']
        unique_together = [('rubric', 'rule_key')]
        indexes = [models.Index(fields=['rubric', 'enabled'])]

    def __str__(self):
        return f'{self.rubric.name} · {self.name}'


class JudgeRecord(models.Model):
    """单条评分记录（全链路留痕）。"""

    ZONE_CHOICES = (
        ('green', '绿区(放行)'),
        ('yellow', '黄区(复核)'),
        ('red', '红区(拦截)'),
    )
    LABEL_CHOICES = (
        ('excellent', '优秀'),
        ('acceptable', '合格'),
        ('needs_improvement', '待改进'),
        ('critical_failure', '严重失败'),
    )

    request_id = models.CharField(max_length=64, unique=True, verbose_name='请求ID')
    rubric = models.ForeignKey(Rubric, on_delete=models.SET_NULL, null=True, related_name='records', verbose_name='评分标准')
    batch = models.ForeignKey('JudgeBatch', on_delete=models.SET_NULL, null=True, blank=True, related_name='records', verbose_name='所属批次')

    # 输入
    question = models.TextField(verbose_name='问题')
    answer = models.TextField(verbose_name='答案')
    ground_truth = models.JSONField(null=True, blank=True, verbose_name='参考答案')
    context = models.JSONField(default=dict, blank=True, verbose_name='上下文')
    auto_gt = models.BooleanField(default=False, verbose_name='是否自动匹配GT')

    # 评分结果
    rule_score = models.FloatField(default=0.0, verbose_name='规则分')
    llm_score = models.FloatField(default=0.0, verbose_name='LLM分')
    final_score = models.FloatField(default=0.0, verbose_name='最终分')
    overall_label = models.CharField(max_length=30, choices=LABEL_CHOICES, default='needs_improvement', verbose_name='整体评级')
    gate_zone = models.CharField(max_length=10, choices=ZONE_CHOICES, default='red', verbose_name='门禁分区')
    blocked = models.BooleanField(default=True, verbose_name='是否拦截')

    # 规则引擎结果
    rule_findings = models.JSONField(default=list, verbose_name='规则命中')
    vetoed = models.BooleanField(default=False, verbose_name='是否一票否决')
    veto_reasons = models.JSONField(default=list, verbose_name='否决原因')

    # LLM Judge 结果
    verdict_reasoning = models.TextField(blank=True, default='', verbose_name='LLM评判理由')
    verdict_dimensions = models.JSONField(default=list, verbose_name='LLM维度评分')
    judge_model = models.CharField(max_length=50, blank=True, default='', verbose_name='评判模型')

    # 元数据
    latency_ms = models.IntegerField(default=0, verbose_name='评分耗时(ms)')
    cache_hit = models.BooleanField(default=False, verbose_name='是否缓存命中')
    error_message = models.TextField(blank=True, default='', verbose_name='错误信息')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='创建人')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        db_table = 'llm_judge_record'
        verbose_name = '评分记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['gate_zone']),
            models.Index(fields=['vetoed']),
            models.Index(fields=['rubric', '-created_at']),
            models.Index(fields=['batch']),
            models.Index(fields=['final_score']),
        ]

    def __str__(self):
        return f'[{self.request_id}] score={self.final_score} zone={self.gate_zone}'


class JudgeBatch(models.Model):
    """批量评分批次。"""

    STATUS_CHOICES = (
        ('pending', '待执行'),
        ('running', '执行中'),
        ('paused', '已暂停'),
        ('completed', '已完成'),
        ('failed', '失败'),
        ('partial', '部分成功'),
    )
    ZONE_CHOICES = JudgeRecord.ZONE_CHOICES

    name = models.CharField(max_length=100, blank=True, default='', verbose_name='批次名称')
    rubric = models.ForeignKey(Rubric, on_delete=models.SET_NULL, null=True, related_name='batches', verbose_name='评分标准')
    cases_data = models.JSONField(default=list, verbose_name='批量用例数据')
    total = models.IntegerField(default=0, verbose_name='总数')
    scored = models.IntegerField(default=0, verbose_name='已评分数')
    progress = models.IntegerField(default=0, verbose_name='进度(0-100)')
    is_paused = models.BooleanField(default=False, verbose_name='暂停标记（worker 读到即中断）')
    error_count = models.IntegerField(default=0, verbose_name='累计失败用例数')
    results_buffer = models.JSONField(default=list, blank=True, verbose_name='已完成用例结果缓存（含成功/失败）')

    # 汇总统计
    mean_score = models.FloatField(default=0.0, verbose_name='平均分')
    std_dev = models.FloatField(default=0.0, verbose_name='标准差')
    safety_pass_rate = models.FloatField(default=0.0, verbose_name='安全通过率')
    critical_success_rate = models.FloatField(default=0.0, verbose_name='严重成功率')
    gate_zone = models.CharField(max_length=10, choices=ZONE_CHOICES, default='red', verbose_name='门禁分区')
    blocked = models.BooleanField(default=True, verbose_name='是否拦截')

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending', verbose_name='状态')
    celery_task_id = models.CharField(max_length=100, blank=True, default='', verbose_name='Celery任务ID')
    error_message = models.TextField(blank=True, default='', verbose_name='错误信息')
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='开始时间')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='完成时间')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='创建人')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        db_table = 'llm_judge_batch'
        verbose_name = '评分批次'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['rubric']),
        ]

    def __str__(self):
        return f'Batch#{self.pk} [{self.status}] {self.scored}/{self.total}'


# ============================================================
# 知识库维护：界面维护的结构化 KB 数据
# ============================================================
class KnowledgeBase(models.Model):
    """知识库：一套命名的结构化知识集合（例如"金融财报知识库"）。"""

    name = models.CharField(max_length=100, unique=True, verbose_name='知识库名称')
    domain = models.CharField(max_length=30, choices=Rubric.DOMAIN_CHOICES, default='finance', verbose_name='领域')
    description = models.TextField(blank=True, default='', verbose_name='描述')
    is_default = models.BooleanField(default=False, verbose_name='是否默认')
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='更新人')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'llm_judge_kb'
        verbose_name = '知识库'
        verbose_name_plural = verbose_name
        ordering = ['-is_default', '-updated_at']

    def __str__(self):
        return f'{self.name} ({self.get_domain_display()})'

    def save(self, *args, **kwargs):
        if self.is_default:
            KnowledgeBase.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class KBCompany(models.Model):
    """知识库中的主体（公司/对象）。"""

    kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name='companies', verbose_name='知识库')
    name = models.CharField(max_length=100, verbose_name='主体名称（标准名）')
    aliases = models.JSONField(default=list, verbose_name='别名列表')
    extra = models.JSONField(default=dict, blank=True, verbose_name='扩展信息')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'llm_judge_kb_company'
        verbose_name = '知识库主体'
        verbose_name_plural = verbose_name
        unique_together = [('kb', 'name')]
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.kb.name} · {self.name}'


class KBReportPeriod(models.Model):
    """知识库报告期。"""

    kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name='report_periods', verbose_name='知识库')
    name = models.CharField(max_length=50, verbose_name='报告期（标准名，如 2024年报）')
    aliases = models.JSONField(default=list, verbose_name='别名列表（如 2024年度、2024财年）')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'llm_judge_kb_period'
        verbose_name = '知识库报告期'
        verbose_name_plural = verbose_name
        unique_together = [('kb', 'name')]
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.kb.name} · {self.name}'


class KBMetric(models.Model):
    """知识库指标。"""

    kb = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name='metrics', verbose_name='知识库')
    name = models.CharField(max_length=50, verbose_name='指标名（标准名，如 营业收入）')
    aliases = models.JSONField(default=list, verbose_name='别名列表（如 营收、收入）')
    default_unit = models.CharField(max_length=20, blank=True, default='', verbose_name='默认单位')
    default_tolerance = models.FloatField(default=5.0, verbose_name='默认容差（% 或绝对值）')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'llm_judge_kb_metric'
        verbose_name = '知识库指标'
        verbose_name_plural = verbose_name
        unique_together = [('kb', 'name')]
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.kb.name} · {self.name}'


class KBMetricValue(models.Model):
    """某主体某报告期某指标的具体数值。"""

    company = models.ForeignKey(KBCompany, on_delete=models.CASCADE, related_name='metric_values', verbose_name='主体')
    period = models.ForeignKey(KBReportPeriod, on_delete=models.CASCADE, related_name='metric_values', verbose_name='报告期')
    metric = models.ForeignKey(KBMetric, on_delete=models.CASCADE, related_name='metric_values', verbose_name='指标')
    value = models.FloatField(verbose_name='数值')
    unit = models.CharField(max_length=20, blank=True, default='', verbose_name='单位')
    tolerance = models.FloatField(default=5.0, verbose_name='容差')
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='更新人')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'llm_judge_kb_metric_value'
        verbose_name = '指标数值'
        verbose_name_plural = verbose_name
        unique_together = [('company', 'period', 'metric')]
        ordering = ['company', 'period', 'metric']

    def __str__(self):
        return f'{self.company.name}/{self.period.name}/{self.metric.name}={self.value}{self.unit}'
