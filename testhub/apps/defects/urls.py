from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import DefectViewSet

router = DefaultRouter()
router.register(r'defects', DefectViewSet, basename='defects')

urlpatterns = [
    path('', include(router.urls)),
]
