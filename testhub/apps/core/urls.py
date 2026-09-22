"""
Core 应用路由
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import UnifiedNotificationConfigViewSet
from .docs_views import DocsListView, DocsContentView

router = DefaultRouter()
router.register(r'notification-configs', UnifiedNotificationConfigViewSet, basename='unified-notification-config')

urlpatterns = [
    path('docs/', DocsListView.as_view(), name='docs-list'),
    path('docs/content/', DocsContentView.as_view(), name='docs-content'),
    path('', include(router.urls)),
]
