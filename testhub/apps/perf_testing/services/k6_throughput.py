"""K6 completion-time fixed UTC second buckets; independent of reader/drain timing."""
from collections import Counter
import math

RATE_VERSION = 'completion_epoch_1s_v1'
RATE_NOTICE = ('峰值业务 RPS = UTC 完成时间固定 1 秒桶的最大业务请求数（非滚动窗口）；'
               '桶为 [秒起点, 下一秒起点)，末尾不足 1 秒仍除以 1 秒。'
               '实时率为最近已观测完成秒桶的暂定值；乱序和迟到事件回填原桶，结束后校验。'
               '平均业务 RPS = 已完成业务请求数 / 整场引擎运行秒数。')
HISTORICAL_RATE_REASON = '历史峰值和窗口 RPS 未按请求完成时间校验，无法验证；原始数据保留，平均 RPS 仍按整场时长计算'


def valid_timestamp(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value == int(value) and 0 < value <= 253402300799999)


class CompletionBuckets:
    def __init__(self):
        self.counts = Counter()
        self.invalid = 0
        self.total = 0

    def record(self, timestamp_ms):
        self.total += 1
        if not valid_timestamp(timestamp_ms):
            self.invalid += 1
            return
        self.counts[int(timestamp_ms) // 1000] += 1

    def snapshot(self, include_buckets=True):
        latest = max(self.counts) if self.counts else None
        peak = max(self.counts.values(), default=0)
        result = {'version': RATE_VERSION, 'verified': self.invalid == 0,
                  'timestamp_source': 'request.timestamp_ms', 'bucket_width_ms': 1000,
                  'anchor': 'unix_epoch_utc', 'interval': '[start, start+1000)',
                  'denominator_seconds': 1, 'business_events': self.total,
                  'timestamped_business_events': self.total - self.invalid,
                  'invalid_timestamp_count': self.invalid,
                  'latest_bucket_start_ms': latest * 1000 if latest is not None else None,
                  'latest_rps': None if self.invalid else self.counts.get(latest, 0),
                  'peak_rps': None if self.invalid else peak,
                  'reason': '部分业务完成事件缺少有效时间戳，峰值和窗口 RPS 无法验证' if self.invalid else '',
                  'notice': RATE_NOTICE}
        if include_buckets:
            result['buckets'] = [{'start_ms': second * 1000, 'count': count}
                                 for second, count in sorted(self.counts.items())]
        return result


def peak_is_verified(summary):
    evidence = (summary or {}).get('throughput') or {}
    return (evidence.get('version') == RATE_VERSION and evidence.get('verified') is True
            and isinstance(evidence.get('peak_rps'), (int, float))
            and not isinstance(evidence['peak_rps'], bool) and math.isfinite(evidence['peak_rps'])
            and evidence['peak_rps'] >= 0)
