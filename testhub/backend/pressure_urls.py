"""Local validation routes. Uploaded credentials are never public static files."""
from django.urls import include, path
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def supported_variable_functions(request):
    """An explicit empty catalog prevents the editor's unsupported fallback."""
    return Response([])


urlpatterns = [
    path('api/auth/', include('apps.users.urls')),
    path('api/users/', include('apps.users.urls')),
    path('api/perf-testing/', include('apps.perf_testing.urls')),
    path('api/api-testing/', include('backend.pressure_api_urls')),
    path('api/data-factory/variable_functions/', supported_variable_functions),
]
