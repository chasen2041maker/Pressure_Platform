"""压测引擎抽象基类。"""
from abc import ABC, abstractmethod


class EngineError(Exception):
    """引擎层可预期错误（配置非法、依赖缺失等）。"""


def build_proxy_kwargs(runtime_config):
    """构造 httpx 客户端的代理参数。

    压测必须打到被测服务本身，若默默继承机器上的 HTTP_PROXY/ALL_PROXY，
    测出来的其实是代理的性能（甚至像本地代理那样直接返回 502），
    所以这里默认 trust_env=False，只有显式配置才启用代理。
    """
    runtime_config = runtime_config or {}
    proxy = (runtime_config.get('proxy') or '').strip()
    if proxy:
        return {'proxy': proxy, 'trust_env': False}
    if runtime_config.get('use_system_proxy'):
        return {'trust_env': True}
    return {'trust_env': False}


class BaseEngine(ABC):
    """所有压测引擎的统一契约。

    生命周期：prepare() -> run() -> collect()，run() 期间周期性回调 on_sample。
    stop() 可由信号处理器在任意时刻调用，必须线程/信号安全。
    """

    name = 'base'

    def __init__(self, snapshot, on_sample=None, on_log=None):
        """
        :param snapshot: dict，执行快照（load_config / steps / variables / env / runtime）
        :param on_sample: callable(sample: dict) 采样回调
        :param on_log: callable(level: str, message: str) 日志回调
        """
        self.snapshot = snapshot or {}
        self.on_sample = on_sample or (lambda sample: None)
        self.on_log = on_log or (lambda level, message: None)
        self._stopping = False

    def log(self, message, level='INFO'):
        try:
            self.on_log(level, message)
        except Exception:  # noqa: BLE001 - 日志失败不能影响压测
            pass

    @abstractmethod
    def prepare(self):
        """校验配置、准备数据。失败应抛 EngineError。"""

    @abstractmethod
    def run(self):
        """阻塞执行压测，期间周期性调用 on_sample。"""

    def stop(self, graceful=True):
        """请求停止（信号处理器中调用，必须尽量简单）。"""
        self._stopping = True

    @abstractmethod
    def collect(self):
        """返回 {'summary': {...}, 'request_stats': [...]}。"""


def build_load_profile(load_config):
    """把四种压力模型统一编译成「时间 -> 目标并发」的曲线函数。

    返回 (target_at, planned_duration)：
    - target_at(elapsed_seconds) -> int 该时刻应有的并发用户数
    - planned_duration            -> 计划总时长（秒）

    RPS 模型不走并发曲线（由引擎内的速率投放器处理），
    这里只返回其并发上限用于连接池规格与展示。
    """
    model = (load_config or {}).get('model', 'CONCURRENCY')

    if model == 'RAMPING':
        stages = [s for s in (load_config.get('stages') or []) if s]
        if not stages:
            return (lambda t: 0), 0
        # 预计算每个阶段的起止时间与起止并发
        segments = []
        cursor = 0.0
        current = 0
        for stage in stages:
            dur = max(float(stage.get('duration') or 0), 0)
            target = max(int(stage.get('target') or 0), 0)
            segments.append((cursor, cursor + dur, current, target))
            cursor += dur
            current = target
        planned = cursor

        def target_at(t):
            if t >= planned:
                return segments[-1][3]
            for start, end, from_v, to_v in segments:
                if start <= t < end:
                    if end == start:
                        return to_v
                    ratio = (t - start) / (end - start)
                    return int(round(from_v + (to_v - from_v) * ratio))
            return segments[-1][3]

        return target_at, planned

    if model == 'SPIKE':
        baseline = max(int(load_config.get('baseline_concurrency') or 0), 0)
        spike = max(int(load_config.get('spike_concurrency') or 0), 0)
        spike_dur = max(float(load_config.get('spike_duration') or 0), 0)
        times = max(int(load_config.get('spike_times') or 1), 1)
        # 每个周期：baseline 段（与尖峰等长）+ 尖峰段
        cycle = spike_dur * 2 if spike_dur > 0 else 0
        planned = cycle * times if cycle > 0 else float(load_config.get('duration') or 0)

        def target_at(t):
            if cycle <= 0:
                return baseline
            pos = t % cycle
            return spike if pos >= spike_dur else baseline

        return target_at, planned

    if model == 'RPS':
        max_conc = int(load_config.get('max_concurrency') or 0)
        planned = float(load_config.get('duration') or 0)
        return (lambda t: max_conc), planned

    # CONCURRENCY（默认）
    concurrency = max(int(load_config.get('concurrency') or 1), 1)
    ramp_up = max(float(load_config.get('ramp_up') or 0), 0)
    planned = float(load_config.get('duration') or 0)

    def target_at(t):
        if ramp_up <= 0 or t >= ramp_up:
            return concurrency
        return max(1, int(round(concurrency * t / ramp_up)))

    return target_at, planned
