"""Read-only, scoped API asset directory; never include api_testing's media routes."""
from django.db.models import Q
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.routers import SimpleRouter

from apps.api_testing.models import ApiProject, ApiCollection, ApiRequest
from apps.perf_testing.services.environments import accessible_projects
from apps.perf_testing.services.api_catalog import bounded_integer
from apps.perf_testing.views import CatalogPagination, catalog_boundary


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiProject
        fields = ('id', 'name', 'description', 'project_type', 'status')


class CollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiCollection
        fields = ('id', 'name', 'project', 'parent', 'order')


class RequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiRequest
        fields = ('id', 'collection', 'name', 'request_type', 'method', 'url', 'headers', 'params', 'body', 'assertions', 'order')


class CatalogBase(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = CatalogPagination

    @catalog_boundary
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @catalog_boundary
    def retrieve(self, request, *args, **kwargs):
        self.kwargs['pk'] = bounded_integer(self.kwargs['pk'], 'id')
        return super().retrieve(request, *args, **kwargs)

    def api_projects(self):
        return accessible_projects(self.request.user).exclude(api_project=None).values_list('api_project_id', flat=True)

    def project_filter(self, queryset, field):
        value = self.request.query_params.get('project')
        if value is not None:
            queryset = queryset.filter(**{field: bounded_integer(value, 'project')})
        return queryset


class Projects(CatalogBase):
    serializer_class = ProjectSerializer
    def get_queryset(self):
        return ApiProject.objects.filter(pk__in=self.api_projects()).order_by('id')


class Collections(CatalogBase):
    serializer_class = CollectionSerializer
    def get_queryset(self):
        return self.project_filter(ApiCollection.objects.filter(project_id__in=self.api_projects()), 'project_id').order_by('order', 'id')


class Requests(CatalogBase):
    serializer_class = RequestSerializer
    def get_queryset(self):
        query = ApiRequest.objects.filter(collection__project_id__in=self.api_projects(), request_type='HTTP')
        query = self.project_filter(query, 'collection__project_id')
        collection = self.request.query_params.get('collection')
        if collection is not None:
            query = query.filter(collection_id=bounded_integer(collection, 'collection'))
        search = self.request.query_params.get('search')
        if search:
            query = query.filter(Q(name__icontains=search) | Q(url__icontains=search))
        return query.order_by('order', 'id')


router = SimpleRouter()
router.register('projects', Projects, basename='pressure-api-project')
router.register('collections', Collections, basename='pressure-api-collection')
router.register('requests', Requests, basename='pressure-api-request')
urlpatterns = router.urls
