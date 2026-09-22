"""评分记录过滤器：按 rubric/zone/vetoed/日期/关键词 过滤。"""
from django.db.models import Q
import django_filters

from .models import JudgeRecord


class JudgeRecordFilter(django_filters.FilterSet):
    rubric = django_filters.NumberFilter(field_name='rubric_id')
    gate_zone = django_filters.CharFilter(field_name='gate_zone')
    vetoed = django_filters.BooleanFilter(field_name='vetoed')
    overall_label = django_filters.CharFilter(field_name='overall_label')
    cache_hit = django_filters.BooleanFilter(field_name='cache_hit')
    date_from = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    date_to = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')
    keyword = django_filters.CharFilter(method='filter_keyword', label='关键词')

    def filter_keyword(self, queryset, name, value):
        return queryset.filter(
            Q(question__icontains=value) | Q(answer__icontains=value) | Q(request_id__icontains=value)
        ) if value else queryset

    class Meta:
        model = JudgeRecord
        fields = ['rubric', 'gate_zone', 'vetoed', 'overall_label', 'cache_hit', 'date_from', 'date_to', 'keyword']
