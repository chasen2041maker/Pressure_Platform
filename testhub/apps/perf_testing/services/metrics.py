"""指标聚合与分位数计算。

核心设计：**固定桶直方图**。
朴素做法是把全部响应时间存进 list 再排序求分位数，100 万请求约 40MB/步骤，
长时压测必然 OOM。这里改用固定边界的桶计数，内存 O(1)，
分位数由桶内线性插值估算，误差受桶宽和样本分布影响，不保证固定相对误差。
同时保留 min/max/sum/count，用于计算全部已完成请求的均值。
"""
import bisect

#: 桶上界（毫秒）。超出最大边界的样本落入溢出桶。
BUCKET_BOUNDS = [
    1, 2, 3, 5, 8, 10, 15, 20, 30, 50, 75, 100, 150, 200, 300, 500,
    750, 1000, 1500, 2000, 3000, 5000, 8000, 10000, 20000, 30000, 60000,
]


class Histogram:
    """固定桶响应时间直方图。"""

    __slots__ = ('buckets', 'overflow', 'count', 'total', 'min_v', 'max_v')

    def __init__(self):
        self.buckets = [0] * len(BUCKET_BOUNDS)
        self.overflow = 0
        self.count = 0
        self.total = 0.0
        self.min_v = None
        self.max_v = None

    def add(self, value):
        """记录一个响应时间样本（毫秒）。"""
        self.count += 1
        self.total += value
        if self.min_v is None or value < self.min_v:
            self.min_v = value
        if self.max_v is None or value > self.max_v:
            self.max_v = value

        idx = bisect.bisect_left(BUCKET_BOUNDS, value)
        if idx >= len(BUCKET_BOUNDS):
            self.overflow += 1
        else:
            self.buckets[idx] += 1

    def merge(self, other):
        """合并另一个直方图（多进程 worker 汇总用）。"""
        if not other or other.count == 0:
            return
        for i, v in enumerate(other.buckets):
            self.buckets[i] += v
        self.overflow += other.overflow
        self.count += other.count
        self.total += other.total
        if other.min_v is not None:
            self.min_v = other.min_v if self.min_v is None else min(self.min_v, other.min_v)
        if other.max_v is not None:
            self.max_v = other.max_v if self.max_v is None else max(self.max_v, other.max_v)

    @property
    def avg(self):
        return round(self.total / self.count, 2) if self.count else 0.0

    @property
    def min(self):
        return round(self.min_v, 2) if self.min_v is not None else 0.0

    @property
    def max(self):
        return round(self.max_v, 2) if self.max_v is not None else 0.0

    def percentile(self, p):
        """估算 p 分位数（p ∈ (0, 100]），桶内线性插值。"""
        if self.count == 0:
            return 0.0
        target = self.count * p / 100.0
        cumulative = 0
        lower = 0.0
        for i, bucket_count in enumerate(self.buckets):
            if bucket_count == 0:
                lower = BUCKET_BOUNDS[i]
                continue
            upper = BUCKET_BOUNDS[i]
            if cumulative + bucket_count >= target:
                # 桶内线性插值
                ratio = (target - cumulative) / bucket_count
                value = lower + (upper - lower) * ratio
                # 分位数不可能超过实际最大值
                if self.max_v is not None:
                    value = min(value, self.max_v)
                return round(max(value, self.min_v or 0.0), 2)
            cumulative += bucket_count
            lower = upper
        # 落在溢出桶：只能给出最大值
        return self.max

    def to_dict(self):
        """序列化（跨进程传递用）。"""
        return {
            'buckets': self.buckets,
            'overflow': self.overflow,
            'count': self.count,
            'total': self.total,
            'min_v': self.min_v,
            'max_v': self.max_v,
        }

    @classmethod
    def from_dict(cls, data):
        h = cls()
        if not data:
            return h
        h.buckets = list(data.get('buckets') or [0] * len(BUCKET_BOUNDS))
        h.overflow = data.get('overflow', 0)
        h.count = data.get('count', 0)
        h.total = data.get('total', 0.0)
        h.min_v = data.get('min_v')
        h.max_v = data.get('max_v')
        return h


class StepMetrics:
    """单个步骤的累计指标。"""

    def __init__(self, name, method='', url=''):
        self.name = name
        self.method = method
        self.url = url
        self.hist = Histogram()
        self.total = 0
        self.success = 0
        self.failed = 0
        self.sent_bytes = 0
        self.recv_bytes = 0
        self.errors = {}  # {error_type: {'count': n, 'message': str}}

    def record(self, elapsed_ms, ok, sent=0, recv=0, error_type=None, error_message=''):
        self.total += 1
        self.hist.add(elapsed_ms)
        self.sent_bytes += sent
        self.recv_bytes += recv
        if ok:
            self.success += 1
        else:
            self.failed += 1
            key = error_type or 'Unknown'
            item = self.errors.setdefault(key, {'count': 0, 'message': error_message})
            item['count'] += 1
            if not item['message'] and error_message:
                item['message'] = error_message

    @property
    def error_rate(self):
        return round(self.failed / self.total * 100, 2) if self.total else 0.0

    def to_stat(self, duration_seconds):
        """转成 PerfRequestStat 的字段字典。"""
        duration_seconds = duration_seconds or 1
        return {
            'step_name': self.name,
            'method': self.method,
            'url': self.url[:1000],
            'total': self.total,
            'success': self.success,
            'failed': self.failed,
            'error_rate': self.error_rate,
            'tps': round(self.total / duration_seconds, 2),
            'avg_rt': self.hist.avg,
            'min_rt': self.hist.min,
            'max_rt': self.hist.max,
            'p50_rt': self.hist.percentile(50),
            'p90_rt': self.hist.percentile(90),
            'p95_rt': self.hist.percentile(95),
            'p99_rt': self.hist.percentile(99),
            'sent_bytes': self.sent_bytes,
            'recv_bytes': self.recv_bytes,
            'error_detail': [
                {'type': k, 'count': v['count'], 'message': (v['message'] or '')[:500]}
                for k, v in sorted(self.errors.items(), key=lambda x: -x[1]['count'])[:20]
            ],
        }


class MetricsCollector:
    """全局指标收集器：维护累计指标 + 采样窗口指标。"""

    def __init__(self):
        self.steps = {}            # {step_name: StepMetrics}
        self.total_hist = Histogram()
        self.total = 0
        self.success = 0
        self.failed = 0
        self.sent_bytes = 0
        self.recv_bytes = 0
        self.peak_tps = 0.0
        self.max_concurrency = 0
        # 窗口指标（每次采样后重置）
        self._win_hist = Histogram()
        self._win_total = 0
        self._win_failed = 0

    def record(self, step_name, elapsed_ms, ok, sent=0, recv=0,
               error_type=None, error_message='', method='', url='', counted=True):
        """记录一次请求。

        counted=False 表示该请求不计入全局 TPS（如前置登录步骤），
        但仍记录到步骤级统计，便于观察前置步骤耗时。
        """
        step = self.steps.get(step_name)
        if step is None:
            step = StepMetrics(step_name, method, url)
            self.steps[step_name] = step
        step.record(elapsed_ms, ok, sent, recv, error_type, error_message)

        if not counted:
            return

        self.total += 1
        self.total_hist.add(elapsed_ms)
        self.sent_bytes += sent
        self.recv_bytes += recv
        if ok:
            self.success += 1
        else:
            self.failed += 1

        self._win_hist.add(elapsed_ms)
        self._win_total += 1
        if not ok:
            self._win_failed += 1

    def take_window(self, interval_seconds):
        """取出并重置窗口指标，返回本次采样点数据。"""
        interval_seconds = interval_seconds or 1
        hist = self._win_hist
        tps = round(self._win_total / interval_seconds, 2)
        if tps > self.peak_tps:
            self.peak_tps = tps
        sample = {
            'tps': tps,
            'avg_rt': hist.avg,
            'p90_rt': hist.percentile(90),
            'p95_rt': hist.percentile(95),
            'p99_rt': hist.percentile(99),
            'error_rate': round(self._win_failed / self._win_total * 100, 2) if self._win_total else 0.0,
            'total_requests': self.total,
        }
        self._win_hist = Histogram()
        self._win_total = 0
        self._win_failed = 0
        return sample

    def step_snapshot(self, elapsed_seconds):
        """步骤级实时快照（推送给前端实时表格）。"""
        elapsed_seconds = elapsed_seconds or 1
        return [
            {
                'name': s.name,
                'total': s.total,
                'failed': s.failed,
                'tps': round(s.total / elapsed_seconds, 2),
                'avg_rt': s.hist.avg,
                'p95_rt': s.hist.percentile(95),
                'error_rate': s.error_rate,
            }
            for s in sorted(self.steps.values(), key=lambda x: -x.hist.avg)
        ]

    def build_summary(self, duration_seconds):
        """构建 PerfExecution.summary。"""
        duration_seconds = duration_seconds or 1
        error_top = []
        for step in self.steps.values():
            for etype, info in step.errors.items():
                error_top.append({
                    'type': etype,
                    'count': info['count'],
                    'sample_step': step.name,
                    'message': (info['message'] or '')[:300],
                })
        error_top.sort(key=lambda x: -x['count'])

        return {
            'total_requests': self.total,
            'success_requests': self.success,
            'failed_requests': self.failed,
            'error_rate': round(self.failed / self.total * 100, 2) if self.total else 0.0,
            'tps': round(self.total / duration_seconds, 2),
            'peak_tps': self.peak_tps,
            'avg_rt': self.total_hist.avg,
            'min_rt': self.total_hist.min,
            'max_rt': self.total_hist.max,
            'p50_rt': self.total_hist.percentile(50),
            'p90_rt': self.total_hist.percentile(90),
            'p95_rt': self.total_hist.percentile(95),
            'p99_rt': self.total_hist.percentile(99),
            'sent_bytes': self.sent_bytes,
            'recv_bytes': self.recv_bytes,
            'max_concurrency': self.max_concurrency,
            'error_top': error_top[:10],
        }

    def build_request_stats(self, duration_seconds):
        return [s.to_stat(duration_seconds) for s in self.steps.values()]


def downsample(points, target):
    """等距降采样到 target 个点（保留首尾）。

    比 LTTB 简单，对压测曲线足够；避免前端渲染上千点卡顿。
    """
    n = len(points)
    if target <= 0 or n <= target:
        return points
    if target == 1:
        return [points[-1]]
    step = (n - 1) / (target - 1)
    result = []
    seen = set()
    for i in range(target):
        idx = int(round(i * step))
        idx = min(idx, n - 1)
        if idx not in seen:
            seen.add(idx)
            result.append(points[idx])
    return result
