"""僵尸执行回收与数据保留策略。

压测子进程是脱离 Django 进程组独立跑的（start_new_session=True），
好处是重启 web 服务不会打断压测，代价是「进程死了但数据库还写着 RUNNING」
这种悬挂记录必须由外部兜底回收，否则场景互斥会被永久锁死。
"""
import logging
import os
import shutil
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from ..models import PerfExecution

logger = logging.getLogger(__name__)

#: 心跳超过该秒数未更新即视为失联
HEARTBEAT_TIMEOUT = 90
#: PENDING 状态超过该秒数仍未启动，视为拉起失败
PENDING_TIMEOUT = 300


def _pid_alive(pid):
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False


def reap_stale_executions(startup=False):
    """扫描并回收僵尸执行，返回被回收的数量。

    判定条件（满足其一）：
    1. 记录了 PID 但进程已不存在；
    2. 心跳超过 HEARTBEAT_TIMEOUT 未更新（进程卡死，或 PID 被系统复用）；
    3. PENDING 超过 PENDING_TIMEOUT 仍未拿到 PID（子进程拉起失败）。
    """
    now = timezone.now()
    reaped = 0

    candidates = PerfExecution.objects.filter(
        status__in=PerfExecution.ACTIVE_STATUSES).only(
        'id', 'execution_no', 'status', 'process_pid', 'heartbeat_at',
        'created_at', 'start_time', 'error_message')

    for execution in candidates:
        reason = None
        pid = execution.process_pid
        beat = execution.heartbeat_at or execution.start_time or execution.created_at
        beat_age = (now - beat).total_seconds()

        if execution.status == 'PENDING' and not pid:
            if (now - execution.created_at).total_seconds() > PENDING_TIMEOUT:
                reason = '子进程拉起超时，未获得 PID'
        elif pid and not _pid_alive(pid):
            # 注意：PID 不可见不能单独作为判死依据。多容器部署下压测子进程跑在
            # backend 容器，而巡检任务跑在 scheduler 容器（或 gunicorn 重启后换了
            # 进程命名空间），PID 天然互不可见。只有心跳同时停止才判定为僵尸；
            # 心跳还在刷新说明子进程活着且能连库，绝不能误杀（历史缺陷：正在
            # 跑的长压测被巡检中途标记 FAILED，采样数据随之被丢弃）。
            if beat_age > HEARTBEAT_TIMEOUT:
                reason = (f'执行进程 {pid} 在当前巡检进程视图中不可见，'
                          f'且心跳已停止 {int(beat_age)} 秒（进程可能已崩溃）')
        elif beat_age > HEARTBEAT_TIMEOUT:
            reason = (f'心跳已停止 {int(beat_age)} 秒'
                      f'（阈值 {HEARTBEAT_TIMEOUT} 秒）')

        if not reason:
            continue

        prefix = '服务启动自检：' if startup else '定时巡检：'
        execution.status = 'FAILED'
        execution.error_message = (f'{execution.error_message or ""}\n'
                                   f'{prefix}{reason}，已自动标记为失败').strip()[:5000]
        execution.end_time = now
        if execution.start_time:
            execution.duration = round((now - execution.start_time).total_seconds(), 2)
        execution.save(update_fields=['status', 'error_message', 'end_time', 'duration'])
        reaped += 1
        logger.warning('回收僵尸压测执行 %s：%s', execution.execution_no, reason)

        try:
            from .executor import push_update
            push_update(execution.id, {
                'status': 'FAILED', 'message': reason, 'finished': True})
        except Exception:  # noqa: BLE001
            pass

    if reaped:
        logger.info('本轮共回收 %s 条僵尸压测执行', reaped)
    return reaped


def cleanup_perf_data(retention_days=None, artifact_days=None, dry_run=False):
    """按保留策略清理历史数据。

    两级保留：产物文件先删（占空间大、价值衰减快），
    执行记录后删（体积小，还要留着看趋势）。
    """
    retention_days = retention_days if retention_days is not None else settings.PERF_RETENTION_DAYS
    artifact_days = artifact_days if artifact_days is not None else settings.PERF_ARTIFACT_RETENTION_DAYS
    now = timezone.now()

    result = {'artifacts_removed': 0, 'bytes_freed': 0, 'executions_removed': 0,
              'dry_run': dry_run}

    # --- 1. 清理产物文件 ---
    artifact_cutoff = now - timedelta(days=artifact_days)
    olds = PerfExecution.objects.filter(
        created_at__lt=artifact_cutoff,
        status__in=PerfExecution.FINAL_STATUSES,
    ).exclude(artifact_dir='').only('id', 'artifact_dir', 'report_url')

    for execution in olds:
        from .reminder_recovery import may_delete
        if not may_delete(execution):
            continue
        abs_dir = os.path.join(settings.MEDIA_ROOT, execution.artifact_dir)
        if not os.path.isdir(abs_dir):
            continue
        size = _dir_size(abs_dir)
        if not dry_run:
            try:
                shutil.rmtree(abs_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning('删除压测产物目录 %s 失败: %s', abs_dir, exc)
                continue
            PerfExecution.objects.filter(id=execution.id).update(
                artifact_dir='', report_url='')
        result['artifacts_removed'] += 1
        result['bytes_freed'] += size

    # --- 2. 清理执行记录（级联删除采样点与接口统计） ---
    record_cutoff = now - timedelta(days=retention_days)
    expired = PerfExecution.objects.filter(
        created_at__lt=record_cutoff,
        status__in=PerfExecution.FINAL_STATUSES,
    ).exclude(baselines__isnull=False)  # 被设为基线来源的执行不删

    from .reminder_recovery import may_delete
    expired = expired.filter(pk__in=[row.pk for row in expired if may_delete(row)])
    result['executions_removed'] = expired.count()
    if not dry_run and result['executions_removed']:
        for execution in expired.only('id', 'artifact_dir'):
            abs_dir = os.path.join(settings.MEDIA_ROOT, execution.artifact_dir or '')
            if execution.artifact_dir and os.path.isdir(abs_dir):
                try:
                    shutil.rmtree(abs_dir)
                except Exception:  # noqa: BLE001
                    pass
        expired.delete()

    return result


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total
