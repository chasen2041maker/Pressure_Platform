from django.contrib import admin

from .models import (
    PerfBaseline,
    PerfDataFile,
    PerfExecution,
    PerfMetricSample,
    PerfProject,
    PerfRequestStat,
    PerfScenario,
    PerfScenarioStep,
    PerfScheduledTask,
)


@admin.register(PerfProject)
class PerfProjectAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'status', 'owner', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['name', 'description']
    filter_horizontal = ['members']


class PerfScenarioStepInline(admin.TabularInline):
    model = PerfScenarioStep
    extra = 0
    fields = ['order', 'name', 'method', 'url', 'is_setup', 'enabled']


@admin.register(PerfScenario)
class PerfScenarioAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'project', 'engine', 'enabled', 'created_by', 'created_at']
    list_filter = ['engine', 'enabled', 'project']
    search_fields = ['name', 'description']
    inlines = [PerfScenarioStepInline]


@admin.register(PerfScenarioStep)
class PerfScenarioStepAdmin(admin.ModelAdmin):
    list_display = ['id', 'scenario', 'order', 'name', 'method', 'is_setup', 'enabled']
    list_filter = ['method', 'is_setup', 'enabled']
    search_fields = ['name', 'url']


@admin.register(PerfExecution)
class PerfExecutionAdmin(admin.ModelAdmin):
    list_display = ['id', 'execution_no', 'scenario', 'status', 'sla_result',
                    'trigger_type', 'start_time', 'duration']
    list_filter = ['status', 'sla_result', 'trigger_type']
    search_fields = ['execution_no']
    readonly_fields = ['execution_no', 'summary', 'sla_detail', 'load_snapshot', 'steps_snapshot']


@admin.register(PerfRequestStat)
class PerfRequestStatAdmin(admin.ModelAdmin):
    list_display = ['id', 'execution', 'step_name', 'total', 'failed', 'avg_rt', 'p95_rt', 'tps']
    search_fields = ['step_name']


@admin.register(PerfMetricSample)
class PerfMetricSampleAdmin(admin.ModelAdmin):
    list_display = ['id', 'execution', 'ts_offset', 'tps', 'avg_rt', 'p95_rt', 'error_rate', 'active_users']


@admin.register(PerfBaseline)
class PerfBaselineAdmin(admin.ModelAdmin):
    list_display = ['id', 'scenario', 'execution', 'set_by', 'created_at']


@admin.register(PerfDataFile)
class PerfDataFileAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'project', 'row_count', 'uploaded_by', 'created_at']
    search_fields = ['name']


@admin.register(PerfScheduledTask)
class PerfScheduledTaskAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'scenario', 'trigger_type', 'status', 'next_run_at',
                    'run_count', 'success_count', 'fail_count']
    list_filter = ['trigger_type', 'status']
    search_fields = ['name']
