from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/perf-testing/executions/(?P<execution_id>\d+)/$',
            consumers.PerfExecutionConsumer.as_asgi()),
]
