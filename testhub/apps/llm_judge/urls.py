"""智能评分器路由：ViewSet + DefaultRouter，对齐平台规范。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    RubricViewSet,
    JudgeRecordViewSet,
    JudgeBatchViewSet,
    JudgeSingleView,
    DashboardStatsView,
    JudgeServiceConfigView,
    KnowledgeBaseViewSet,
    KBCompanyViewSet,
    KBReportPeriodViewSet,
    KBMetricViewSet,
    KBMetricValueViewSet,
    BatchFileUploadView,
    BatchTemplateDownloadView,
)

router = DefaultRouter()
router.register(r'rubrics', RubricViewSet, basename='llm-judge-rubric')
router.register(r'records', JudgeRecordViewSet, basename='llm-judge-record')
router.register(r'batches', JudgeBatchViewSet, basename='llm-judge-batch')
# 知识库维护
router.register(r'kbs', KnowledgeBaseViewSet, basename='llm-judge-kb')
router.register(r'kb/companies', KBCompanyViewSet, basename='llm-judge-kb-company')
router.register(r'kb/periods', KBReportPeriodViewSet, basename='llm-judge-kb-period')
router.register(r'kb/metrics', KBMetricViewSet, basename='llm-judge-kb-metric')
router.register(r'kb/values', KBMetricValueViewSet, basename='llm-judge-kb-value')

urlpatterns = [
    path('', include(router.urls)),
    # 单条评分（同步）
    path('judge/single/', JudgeSingleView.as_view(), name='llm-judge-single'),
    # Dashboard 聚合统计
    path('dashboard/stats/', DashboardStatsView.as_view(), name='llm-judge-dashboard-stats'),
    # Judge 服务配置（连通性测试等）
    path('config/service/', JudgeServiceConfigView.as_view(), name='llm-judge-config-service'),
    # 批量评分文件上传解析（预览）
    path('batch/upload/', BatchFileUploadView.as_view(), name='llm-judge-batch-upload'),
    # 批量用例模板下载（?format=csv|xlsx|txt）
    path('batch/template/', BatchTemplateDownloadView.as_view(), name='llm-judge-batch-template'),
]
