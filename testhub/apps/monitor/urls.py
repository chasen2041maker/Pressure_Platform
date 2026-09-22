"""监控中心路由。"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    MonitorTargetViewSet,
    MonitorCheckLogViewSet,
    NotificationChannelViewSet,
    AlertEventViewSet,
    DashboardViewSet,
)

router = DefaultRouter()
router.register(r'targets', MonitorTargetViewSet, basename='monitor-targets')
router.register(r'checks', MonitorCheckLogViewSet, basename='monitor-checks')
router.register(r'channels', NotificationChannelViewSet, basename='monitor-channels')
router.register(r'alerts', AlertEventViewSet, basename='monitor-alerts')
router.register(r'dashboard', DashboardViewSet, basename='monitor-dashboard')

urlpatterns = [
    path('', include(router.urls)),
]
