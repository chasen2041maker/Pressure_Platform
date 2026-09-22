"""性能测试模块操作日志。

方案 §10 约定复用 `apps.api_testing.OperationLog`，不另建表。
该表的 resource_type 只有 7 个既有取值，这里做一层映射，
并在描述里显式带上「压测」前缀，避免与接口测试的记录混淆。
"""
import logging

logger = logging.getLogger(__name__)

#: 压测资源 -> OperationLog.RESOURCE_TYPE_CHOICES 的映射
RESOURCE_MAP = {
    'perf_project': ('project', '压测项目'),
    'perf_scenario': ('collection', '压测场景'),
    'perf_step': ('request', '压测步骤'),
    'perf_execution': ('execution', '压测执行'),
    'perf_task': ('task', '定时压测任务'),
    'perf_baseline': ('environment', '性能基线'),
    'perf_datafile': ('environment', '压测数据文件'),
}

OPERATION_TEXT = {
    'create': '新增',
    'edit': '编辑',
    'delete': '删除',
    'execute': '执行',
    'run': '运行',
    'save': '保存',
}


def log_operation(operation_type, resource_type, resource_id, resource_name, user, description=None):
    """记录一条压测操作日志。失败绝不影响主流程。"""
    mapped_type, label = RESOURCE_MAP.get(resource_type, ('project', resource_type))
    if description is None:
        description = f"{OPERATION_TEXT.get(operation_type, operation_type)}{label}「{resource_name}」"

    try:
        from apps.api_testing.models import OperationLog
        OperationLog.objects.create(
            operation_type=operation_type,
            resource_type=mapped_type,
            resource_id=resource_id or 0,
            resource_name=(resource_name or '')[:200],
            description=description,
            user=user if (user and getattr(user, 'is_authenticated', False)) else None,
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不能影响业务
        logger.warning('记录压测操作日志失败: %s', exc)
