"""SLA 阈值判定（借鉴 k6 thresholds）。"""
import math

#: 指标 key -> (summary 字段, 比较方向, 展示名)
#: direction = 'max' 表示实际值超过阈值即失败；'min' 表示低于阈值即失败
SLA_METRICS = {
    'avg_response_time': ('avg_rt', 'max', '平均响应时间(ms)'),
    'p90_response_time': ('p90_rt', 'max', 'P90响应时间(ms)'),
    'p95_response_time': ('p95_rt', 'max', 'P95响应时间(ms)'),
    'p99_response_time': ('p99_rt', 'max', 'P99响应时间(ms)'),
    'error_rate': ('error_rate', 'max', '错误率(%)'),
    'min_tps': ('tps', 'min', 'TPS'),
}


def evaluate(sla_config, summary):
    """根据 SLA 配置评估汇总指标。

    返回 (result, detail)：
    - result ∈ PASSED / FAILED / NOT_EVALUATED
    - detail  = [{metric, label, threshold, actual, direction, passed}]
    """
    sla_config = sla_config or {}
    if not sla_config.get('enabled'):
        return 'NOT_EVALUATED', []

    thresholds = sla_config.get('thresholds') or {}
    detail = []
    all_passed = True

    for key, threshold in thresholds.items():
        meta = SLA_METRICS.get(key)
        if not meta or threshold in (None, '', 0) and key != 'error_rate':
            # 0 视为未设置（error_rate=0 是合法的严格要求，单独放行）
            continue
        field, direction, label = meta
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            continue
        try:
            actual = float(summary[field])
        except (KeyError, TypeError, ValueError, OverflowError):
            actual = None
        missing = (actual is None or not math.isfinite(actual) or not math.isfinite(threshold)
                   or summary.get('total_requests') == 0)
        passed = None if missing else (actual <= threshold if direction == 'max' else actual >= threshold)
        if not passed:
            all_passed = False
        detail.append({
            'metric': key,
            'label': label,
            'threshold': threshold,
            'actual': None if missing else round(actual, 2),
            'direction': direction,
            'comparator': '≤' if direction == 'max' else '≥',
            'passed': passed,
            'status': 'NOT_EVALUATED' if missing else 'PASSED' if passed else 'FAILED',
            'reason': 'missing_metric_or_no_requests' if missing else '',
        })

    if not detail:
        return 'NOT_EVALUATED', []
    if any(item['passed'] is None for item in detail):
        return 'NOT_EVALUATED', detail
    return ('PASSED' if all_passed else 'FAILED'), detail


class BreachDetector:
    """运行期 SLA 熔断检测：连续 N 个采样周期违规才触发，避免抖动误判。"""

    def __init__(self, sla_config, sample_interval=1):
        self.config = sla_config or {}
        self.enabled = bool(self.config.get('enabled')) and bool(self.config.get('abort_on_breach'))
        window_seconds = self.config.get('breach_window') or 10
        self.required = max(1, int(round(window_seconds / max(sample_interval, 1))))
        self.streak = 0
        self.reason = ''

    def check(self, sample):
        """传入一个采样点，返回是否应该熔断中止。"""
        if not self.enabled:
            return False

        def _fmt(value):
            # 整数展示不带多余的 .0，长小数保留 2 位，便于人读
            num = round(float(value), 2)
            return str(int(num)) if num == int(num) else f'{num:g}'

        thresholds = self.config.get('thresholds') or {}
        breached = []
        for key, threshold in thresholds.items():
            meta = SLA_METRICS.get(key)
            if not meta:
                continue
            field, direction, label = meta
            if field not in sample:
                continue
            try:
                threshold = float(threshold)
            except (TypeError, ValueError):
                continue
            if threshold <= 0 and key != 'error_rate':
                continue
            actual = float(sample.get(field) or 0)
            # TPS 为 0 的采样点（如加压阶段刚开始）不参与 min 类判定，避免误熔断
            if direction == 'min' and actual <= 0:
                continue
            if (direction == 'max' and actual > threshold) or (direction == 'min' and actual < threshold):
                breached.append(f'{label} 实际 {_fmt(actual)} 超出阈值 {_fmt(threshold)}')

        if breached:
            self.streak += 1
            self.reason = '；'.join(breached)
        else:
            self.streak = 0
            self.reason = ''

        return self.streak >= self.required
