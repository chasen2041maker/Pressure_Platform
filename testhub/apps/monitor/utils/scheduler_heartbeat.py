"""调度器心跳：判断统一调度器（run_all_scheduled_tasks）是否在线。

调度器进程每个轮询周期写入 Redis 心跳时间戳（带 TTL）；
看板/列表读取该心跳判断在线状态。Redis 不可用时静默降级为 'unknown'。
"""
import time

from django.conf import settings
import redis

BEAT_KEY = 'testhub:monitor:scheduler:beat'
BEAT_TTL = 120          # 心跳键 TTL（秒）
ONLINE_THRESHOLD = 90   # 最后心跳距现在 <= 此值视为在线（秒）


def _client():
    url = getattr(settings, 'CELERY_BROKER_URL', None) or getattr(settings, 'REDIS_URL', None)
    if not url:
        return None
    try:
        return redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    except Exception:
        return None


def set_scheduler_beat():
    """调度器每轮调用：写入当前时间戳（带 TTL）。失败静默降级。"""
    try:
        c = _client()
        if c is not None:
            c.set(BEAT_KEY, int(time.time()), ex=BEAT_TTL)
    except Exception:
        pass


def get_scheduler_status():
    """返回 'online' / 'offline' / 'unknown'。"""
    try:
        c = _client()
        if c is None:
            return 'unknown'
        val = c.get(BEAT_KEY)
        if val is None:
            return 'offline'
        age = int(time.time()) - int(val.decode('ascii'))
        return 'online' if age <= ONLINE_THRESHOLD else 'offline'
    except Exception:
        return 'unknown'


# 跨进程在途锁：防止多个调度器实例并发对同一个目标执行 run_check
MONITOR_LOCK_PREFIX = 'testhub:monitor:inflight:'


def acquire_monitor_lock(target_id, ttl=300):
    """抢同一目标的跨进程在途锁。

    返回 True 表示抢到（可派发检测）；Redis 不可用或异常时返回 True，
    降级由调度器进程内的 monitor_running 集合兜底。锁带 TTL 自动过期，
    避免某实例崩溃后永久死锁。
    """
    try:
        c = _client()
        if c is None:
            return True
        key = MONITOR_LOCK_PREFIX + str(target_id)
        # nx=True：仅当键不存在时才设置成功；ex=TTL：自动过期
        return bool(c.set(key, int(time.time()), nx=True, ex=ttl))
    except Exception:
        return True


def release_monitor_lock(target_id):
    """释放在途锁（检测线程结束时调用）。"""
    try:
        c = _client()
        if c is not None:
            c.delete(MONITOR_LOCK_PREFIX + str(target_id))
    except Exception:
        pass
