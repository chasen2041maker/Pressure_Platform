"""智能评分器 DRF 序列化器。"""
from rest_framework import serializers

from .models import Rubric, RubricDimension, RubricRule, JudgeRecord, JudgeBatch


class RubricDimensionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RubricDimension
        fields = '__all__'


class RubricRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = RubricRule
        fields = '__all__'


class RubricSerializer(serializers.ModelSerializer):
    dimensions = RubricDimensionSerializer(many=True, read_only=True)
    rules = RubricRuleSerializer(many=True, read_only=True)
    dimension_count = serializers.IntegerField(source='dimensions.count', read_only=True)
    rule_count = serializers.IntegerField(source='rules.count', read_only=True)

    class Meta:
        model = Rubric
        fields = '__all__'


class RubricCreateSerializer(serializers.ModelSerializer):
    """创建 Rubric：支持同时传入 dimensions 和 rules（嵌套创建/复制预设模板）。"""
    dimensions = RubricDimensionSerializer(many=True, required=False)
    rules = RubricRuleSerializer(many=True, required=False)
    clone_from = serializers.IntegerField(required=False, write_only=True, help_text='从指定 Rubric ID 复制模板')

    class Meta:
        model = Rubric
        fields = '__all__'

    def create(self, validated_data):
        dimensions_data = validated_data.pop('dimensions', [])
        rules_data = validated_data.pop('rules', [])
        clone_from = validated_data.pop('clone_from', None)

        rubric = Rubric.objects.create(**validated_data)

        # 复制预设模板
        if clone_from:
            src = Rubric.objects.filter(pk=clone_from).first()
            if src:
                for d in src.dimensions.all():
                    RubricDimension.objects.create(rubric=rubric, dim_key=d.dim_key, name=d.name,
                                                   dim_type=d.dim_type, weight=d.weight,
                                                   anchor_text=d.anchor_text, vetoable=d.vetoable,
                                                   sort_order=d.sort_order)
                for r in src.rules.all():
                    RubricRule.objects.create(rubric=rubric, rule_key=r.rule_key, name=r.name,
                                              enabled=r.enabled, severity=r.severity, is_veto=r.is_veto,
                                              fallback_mode=r.fallback_mode, params=r.params,
                                              sort_order=r.sort_order)
        else:
            for d in dimensions_data:
                RubricDimension.objects.create(rubric=rubric, **d)
            for r in rules_data:
                RubricRule.objects.create(rubric=rubric, **r)
        return rubric


class JudgeRecordSerializer(serializers.ModelSerializer):
    rubric_name = serializers.CharField(source='rubric.name', read_only=True, default='')
    batch_id = serializers.IntegerField(source='batch.id', read_only=True, default=None)

    class Meta:
        model = JudgeRecord
        fields = '__all__'


class JudgeBatchSerializer(serializers.ModelSerializer):
    rubric_name = serializers.CharField(source='rubric.name', read_only=True, default='')
    records_count = serializers.IntegerField(source='records.count', read_only=True)

    class Meta:
        model = JudgeBatch
        fields = '__all__'


class JudgeSingleRequestSerializer(serializers.Serializer):
    """单条评分入参。"""
    question = serializers.CharField()
    answer = serializers.CharField()
    ground_truth = serializers.JSONField(required=False, allow_null=True)
    auto_gt = serializers.BooleanField(required=False, default=False)
    rubric = serializers.IntegerField(required=False, help_text='Rubric ID；不传则用默认')
    context = serializers.JSONField(required=False, default=dict)


class JudgeBatchRequestSerializer(serializers.Serializer):
    """批量评分入参。"""
    name = serializers.CharField(required=False, allow_blank=True, default='')
    rubric = serializers.IntegerField(required=False)
    cases = serializers.ListField(child=serializers.JSONField(), min_length=1)

    def validate_cases(self, cases):
        if len(cases) > 5000:
            raise serializers.ValidationError(f'单次批量评分最多 5000 条用例，当前 {len(cases)} 条，请分批提交')
        for i, case in enumerate(cases):
            if not isinstance(case, dict):
                raise serializers.ValidationError(f'第 {i+1} 条用例格式错误：应为 JSON 对象')
            if not (case.get('question') or '').strip():
                raise serializers.ValidationError(f'第 {i+1} 条用例缺少问题(question)')
            if not (case.get('answer') or '').strip():
                raise serializers.ValidationError(f'第 {i+1} 条用例缺少答案(answer)')
        return cases


# ============================================================
# 知识库维护 Serializers
# ============================================================
from .models import KnowledgeBase, KBCompany, KBReportPeriod, KBMetric, KBMetricValue


class KBMetricValueSerializer(serializers.ModelSerializer):
    metric_name = serializers.CharField(source='metric.name', read_only=True)
    period_name = serializers.CharField(source='period.name', read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True)

    class Meta:
        model = KBMetricValue
        fields = '__all__'


class KBCompanySerializer(serializers.ModelSerializer):
    metric_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = KBCompany
        fields = '__all__'


class KBReportPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = KBReportPeriod
        fields = '__all__'


class KBMetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = KBMetric
        fields = '__all__'


class KnowledgeBaseSerializer(serializers.ModelSerializer):
    company_count = serializers.IntegerField(read_only=True, default=0)
    metric_count = serializers.IntegerField(read_only=True, default=0)
    period_count = serializers.IntegerField(read_only=True, default=0)
    value_count = serializers.IntegerField(read_only=True, default=0)
    domain_label = serializers.CharField(source='get_domain_display', read_only=True)

    class Meta:
        model = KnowledgeBase
        fields = ['id','name','domain','domain_label','description','is_default','is_active',
                  'updated_by','created_at','updated_at',
                  'company_count','period_count','metric_count','value_count']
        read_only_fields = ['is_active','updated_by','created_at','updated_at',
                            'company_count','period_count','metric_count','value_count','domain_label']

    def validate_name(self, value):
        if len((value or '').strip()) < 2:
            raise serializers.ValidationError('知识库名称至少 2 个字符')
        return (value or '').strip()

    def validate_domain(self, value):
        valid = {c[0] for c in Rubric.DOMAIN_CHOICES}
        if value not in valid:
            raise serializers.ValidationError(f'领域非法，必须是: {sorted(valid)}')
        return value

    def create(self, validated_data):
        domain = validated_data.get('domain') or 'finance'
        if validated_data.get('is_default'):
            KnowledgeBase.objects.filter(domain=domain, is_default=True).update(is_default=False)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        new_domain = validated_data.get('domain') or instance.domain
        if validated_data.get('is_default') and not (instance.is_default and instance.domain == new_domain):
            KnowledgeBase.objects.filter(domain=new_domain, is_default=True).exclude(pk=instance.pk).update(is_default=False)
        return super().update(instance, validated_data)


class KBParseTextRequestSerializer(serializers.Serializer):
    """从非结构化文本解析成结构化 KB 数据。"""
    kb = serializers.IntegerField(help_text='知识库ID')
    text = serializers.CharField(help_text='待解析文本，例如：贵州茅台 2024年报：营收 1741.4 亿，归母净利润 862.3 亿，毛利率 92%。')
    company = serializers.CharField(required=False, default='', help_text='可选：强制指定主体（标准名），未填时从文本中模糊匹配')
    period = serializers.CharField(required=False, default='', help_text='可选：强制指定报告期（标准名）')


class KBImportRequestSerializer(serializers.Serializer):
    """将解析出的结构化数据批量写入 KB。"""
    kb = serializers.IntegerField()
    companies = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    periods = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    metrics = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    values = serializers.ListField(child=serializers.JSONField(), required=False, default=list)


class BatchUploadResponseSerializer(serializers.Serializer):
    """文件上传解析结果。"""
    filename = serializers.CharField()
    total_rows = serializers.IntegerField()
    valid_rows = serializers.IntegerField()
    errors = serializers.ListField(child=serializers.CharField())
    preview = serializers.ListField(child=serializers.JSONField())
    cases = serializers.ListField(child=serializers.JSONField())
