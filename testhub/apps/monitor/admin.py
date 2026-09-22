"""监控中心后台注册（simpleui）。"""
from django.contrib import admin

from .models import (
    MonitorTarget, MonitorCheckLog, NotificationChannel, AlertEvent,
)

admin.site.register(MonitorTarget)
admin.site.register(MonitorCheckLog)
admin.site.register(NotificationChannel)
admin.site.register(AlertEvent)
