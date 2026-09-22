from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'projects', views.PerfProjectViewSet, basename='perf-project')
router.register(r'environments', views.PerfEnvironmentViewSet, basename='perf-environment')
router.register(r'scenarios', views.PerfScenarioViewSet, basename='perf-scenario')
router.register(r'steps', views.PerfScenarioStepViewSet, basename='perf-step')
router.register(r'executions', views.PerfExecutionViewSet, basename='perf-execution')
router.register(r'baselines', views.PerfBaselineViewSet, basename='perf-baseline')
router.register(r'data-files', views.PerfDataFileViewSet, basename='perf-data-file')
router.register(r'account-pools', views.PerfAccountPoolViewSet, basename='perf-account-pool')
router.register(r'account-pool-versions', views.PerfAccountPoolVersionViewSet, basename='perf-account-pool-version')
router.register(r'scheduled-tasks', views.PerfScheduledTaskViewSet,
                basename='perf-scheduled-task')
router.register(r'comparison-reports', views.PerfComparisonReportViewSet,
                basename='perf-comparison-report')

urlpatterns = [
    path('engines/status/', views.EngineStatusView.as_view(), name='perf-engine-status'),
    path('', include(router.urls)),
]
