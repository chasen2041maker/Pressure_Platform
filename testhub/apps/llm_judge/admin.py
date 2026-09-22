"""智能评分器后台管理（simpleui）。"""
from django.contrib import admin

from .models import Rubric, RubricDimension, RubricRule, JudgeRecord, JudgeBatch


@admin.register(Rubric)
class RubricAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'domain', 'version', 'is_default', 'is_active', 'created_at')
    list_filter = ('domain', 'is_default', 'is_active')
    search_fields = ('name', 'description')
    list_editable = ('is_default', 'is_active')
    list_per_page = 30


@admin.register(RubricDimension)
class RubricDimensionAdmin(admin.ModelAdmin):
    list_display = ('id', 'rubric', 'dim_key', 'name', 'dim_type', 'weight', 'vetoable', 'sort_order')
    list_filter = ('dim_type', 'vetoable')
    search_fields = ('dim_key', 'name')
    list_editable = ('weight', 'vetoable', 'sort_order')
    raw_id_fields = ('rubric',)


@admin.register(RubricRule)
class RubricRuleAdmin(admin.ModelAdmin):
    list_display = ('id', 'rubric', 'rule_key', 'name', 'enabled', 'severity', 'is_veto', 'fallback_mode')
    list_filter = ('enabled', 'severity', 'is_veto', 'fallback_mode')
    search_fields = ('rule_key', 'name')
    list_editable = ('enabled', 'severity', 'is_veto', 'fallback_mode')
    raw_id_fields = ('rubric',)


@admin.register(JudgeRecord)
class JudgeRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'request_id', 'rubric', 'final_score', 'gate_zone', 'vetoed', 'overall_label', 'judge_model', 'cache_hit', 'created_at')
    list_filter = ('gate_zone', 'vetoed', 'overall_label', 'cache_hit', 'rubric')
    search_fields = ('request_id', 'question', 'answer')
    readonly_fields = ('created_at',)
    list_per_page = 50
    date_hierarchy = 'created_at'


@admin.register(JudgeBatch)
class JudgeBatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'rubric', 'status', 'progress', 'scored', 'total', 'mean_score', 'gate_zone', 'blocked', 'created_at')
    list_filter = ('status', 'gate_zone', 'blocked', 'rubric')
    search_fields = ('name', 'celery_task_id')
    readonly_fields = ('created_at', 'started_at', 'completed_at')
    list_per_page = 30
    date_hierarchy = 'created_at'
