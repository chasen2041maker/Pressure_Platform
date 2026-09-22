"""Platform SLA over cumulative, completed k6 request events (not native thresholds)."""
from copy import deepcopy
import math

from rest_framework.exceptions import ValidationError

from .sla import SLA_METRICS

SOURCE = 'testhub_cumulative_request_events'
REASONS = {
    'sample_data_incomplete': '采样持久化失败，部分时序数据缺失',
    'execution_incomplete': '执行已停止、失败或未完整结束',
    'iterations_incomplete': '未完成指定用户轮次',
    'requests_incomplete': '存在未完成的 HTTP 请求',
    'no_business_requests': '没有已完成的业务请求',
    'no_step_requests': '目标步骤没有已完成请求',
    'missing_metric': '指标未采集或不是有限数值',
    'unknown_step': '步骤已删除、禁用或不是业务步骤',
    'invalid_config': 'SLA 配置无效',
}


def finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def validate_config(value: dict, steps: list[dict] | None = None) -> dict:
    """Units are fixed: ms, percent (0..100), requests/s, delay/window seconds."""
    if not isinstance(value, dict):
        raise ValidationError('SLA 配置必须是对象')
    extra = set(value) - {'enabled', 'thresholds', 'step_thresholds', 'abort_on_breach', 'breach_window', 'abort_delay'}
    if extra:
        raise ValidationError('SLA 包含不支持的配置字段')
    config = deepcopy(value)
    for key in ('enabled', 'abort_on_breach'):
        if key in config and not isinstance(config[key], bool):
            raise ValidationError(f'{key} 必须为布尔值')

    def clean_thresholds(raw: object) -> dict:
        if not isinstance(raw, dict):
            raise ValidationError('SLA 阈值必须是对象')
        cleaned = {}
        for key, threshold in raw.items():
            if key not in SLA_METRICS:
                raise ValidationError(f'不支持的 SLA 指标：{key}')
            if threshold is None or threshold == '':
                continue
            number = finite(threshold)
            if number is None or number < 0 or (key == 'error_rate' and number > 100):
                raise ValidationError(f'SLA 指标 {key} 必须为有限非负数（失败率上限 100%）')
            # Preserve the existing form's zero-as-unset convention for latency/rate.
            if number == 0 and key != 'error_rate':
                continue
            cleaned[key] = number
        return cleaned

    config['thresholds'] = clean_thresholds(config.get('thresholds', {}))
    rows = config.get('step_thresholds', [])
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValidationError('按接口 SLA 必须为数组且不超过 1000 项')
    known = {step.get('id') for step in steps or []
             if step.get('enabled', True) and not step.get('is_setup')}
    seen, cleaned_rows = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'step_id', 'thresholds'}:
            raise ValidationError('接口 SLA 必须包含 step_id 和 thresholds')
        step_id = row['step_id']
        if type(step_id) is not int or step_id <= 0 or step_id in seen:
            raise ValidationError('接口 SLA 必须选择不重复的稳定步骤 ID')
        if steps is not None and step_id not in known:
            raise ValidationError('SLA 步骤已删除、禁用或不是当前场景的业务步骤')
        thresholds = clean_thresholds(row['thresholds'])
        if not thresholds:
            raise ValidationError('每个接口 SLA 至少需要一个有效阈值')
        seen.add(step_id)
        cleaned_rows.append({'step_id': step_id, 'thresholds': thresholds})
    config['step_thresholds'] = cleaned_rows
    for key, default, minimum in (('abort_delay', 0, 0), ('breach_window', 10, 1)):
        number = finite(config.get(key, default))
        if number is None or not minimum <= number <= 86400:
            raise ValidationError(f'{key} 必须为 {minimum}~86400 之间的有限秒数')
        config[key] = number
    if config.get('enabled') and not (config['thresholds'] or cleaned_rows):
        raise ValidationError('启用 SLA 时至少设置一个有效阈值')
    return config


def completion_reason(summary: dict, load: dict, execution_status: str) -> str:
    if summary.get('sample_data_incomplete'):
        return 'sample_data_incomplete'
    if execution_status != 'COMPLETED':
        return 'execution_incomplete'
    business_count = finite(summary.get('business_total'))
    if business_count is None or business_count <= 0:
        return 'no_business_requests'
    rounds = finite(load.get('iterations_per_vu', 0))
    users = finite(load.get('concurrency', 1))
    policy = summary.get('execution_policy')
    policy = policy if isinstance(policy, dict) and policy.get('version') == 1 else None
    iteration_key = 'executor_iterations' if policy else 'completed_iterations'
    if rounds and (users is None or finite(summary.get(iteration_key)) != rounds * users):
        return 'iterations_incomplete'
    if policy:
        groups = policy.get('groups') or []
        if (policy.get('invalid_events') != 0 or not groups or any(
                any(finite(row.get(field)) != 0 for field in ('incomplete', 'blocked_steps', 'uncovered_participants'))
                for row in groups)):
            return 'execution_incomplete'
    for field in ('http_incomplete', 'business_incomplete'):
        count = finite(summary.get(field))
        if count is None:
            return 'missing_metric'
        if count != 0:
            return 'requests_incomplete'
    return ''


def evaluate(config: dict, summary: dict, *, steps: list[dict], load: dict | None = None,
             execution_status: str = 'COMPLETED', live: bool = False) -> tuple[str, list[dict]]:
    if not config.get('enabled'):
        return 'NOT_EVALUATED', []
    try:
        config = validate_config(config)
    except ValidationError:
        return 'NOT_EVALUATED', [{'status': 'NOT_EVALUATED', 'passed': None, 'actual': None,
                                  'reason': 'invalid_config', 'reason_label': REASONS['invalid_config']}]
    run_reason = '' if live else completion_reason(summary, load or {}, execution_status)
    business_count = finite(summary.get('business_total'))
    if business_count is None or business_count <= 0:
        run_reason = 'no_business_requests'
    known = {step.get('id'): step for step in steps
             if step.get('enabled', True) and not step.get('is_setup')}
    observed = {str(row.get('step_id')): row for row in summary.get('step_metrics', [])}
    scopes = [('global', None, config['thresholds'])] + [
        ('step', row['step_id'], row['thresholds']) for row in config['step_thresholds']]
    details = []
    for scope, step_id, thresholds in scopes:
        data = summary if scope == 'global' else observed.get(str(step_id), {})
        count = business_count if scope == 'global' else finite(data.get('total'))
        reason = run_reason
        if scope == 'step':
            if step_id not in known:
                reason = 'unknown_step'
            elif count is None or count <= 0:
                reason = reason or 'no_step_requests'
        for metric, threshold in thresholds.items():
            field, direction, label = SLA_METRICS[metric]
            actual = finite(data.get(field))
            if metric == 'error_rate':
                failed = finite(data.get('failed_requests' if scope == 'global' else 'failed'))
                success = finite(data.get('success_requests' if scope == 'global' else 'success'))
                actual = (100 * failed / count if count and count > 0 and failed is not None
                          and success is not None and failed >= 0 and success >= 0
                          and failed + success == count else None)
            elif metric == 'min_tps':
                duration = finite(summary.get('metric_duration_seconds'))
                actual = count / duration if count is not None and duration and duration > 0 else None
            if count is None or count <= 0 or (actual is not None and actual < 0):
                actual = None
            metric_reason = reason or ('missing_metric' if actual is None else '')
            passed = None if metric_reason else (actual <= threshold if direction == 'max' else actual >= threshold)
            details.append({
                'scope': scope, 'step_id': step_id,
                'step_name': known.get(step_id, {}).get('name', '') if scope == 'step' else '',
                'metric': metric, 'label': '业务 RPS' if metric == 'min_tps' else label,
                'threshold': threshold, 'actual': actual, 'direction': direction,
                'comparator': '≤' if direction == 'max' else '≥', 'passed': passed,
                'status': 'NOT_EVALUATED' if passed is None else 'PASSED' if passed else 'FAILED',
                'reason': metric_reason, 'reason_label': REASONS.get(metric_reason, ''),
                'unit': '%' if metric == 'error_rate' else 'requests/s' if metric == 'min_tps' else 'ms',
                'source': SOURCE, 'aggregation': 'cumulative',
                'percentile_method': 'fixed_bucket_interpolation' if metric.startswith('p') else None,
                'sample_count': count,
            })
    # Partial evaluation cannot certify the configured SLA as a whole.
    result = ('NOT_EVALUATED' if not details or any(row['passed'] is None for row in details)
              else 'FAILED' if any(row['passed'] is False for row in details) else 'PASSED')
    return result, details


class BreachDetector:
    """Each rule must breach continuously after delay; use elapsed time, never sample counts."""

    def __init__(self, config: dict, steps: list[dict], sample_interval: float = 1) -> None:
        self.config = validate_config(config, steps)
        self.steps = steps
        self.enabled = self.config.get('enabled') and self.config.get('abort_on_breach')
        self.sample_interval = max(sample_interval, 0.1)
        self.since: dict[tuple, float] = {}
        self.last_elapsed: float | None = None
        self.reason = ''
        self.evidence: dict = {}

    def check(self, sample: dict) -> bool:
        if not self.enabled or self.evidence:
            return False
        elapsed = finite(sample.get('elapsed_seconds'))
        if elapsed is None:
            self.since.clear()
            return False
        # Missing/nonmonotonic observation periods cannot establish continuity.
        if self.last_elapsed is not None and (elapsed <= self.last_elapsed or
                elapsed - self.last_elapsed > self.sample_interval * 2):
            self.since.clear()
        self.last_elapsed = elapsed
        if elapsed < self.config['abort_delay'] or sample.get('engine_finished'):
            self.since.clear()
            return False
        _, details = evaluate(self.config, sample.get('sla_metrics') or {}, steps=self.steps, live=True)
        active, sustained = set(), []
        for row in details:
            key = (row['scope'], row['step_id'], row['metric'])
            if row['passed'] is False:
                active.add(key)
                start = self.since.setdefault(key, elapsed)
                if elapsed - start >= self.config['breach_window']:
                    sustained.append(row)
        self.since = {key: value for key, value in self.since.items() if key in active}
        if not sustained:
            return False
        self.reason = '；'.join(f'{"全局" if row["scope"] == "global" else "步骤 " + str(row["step_id"])} '
                               f'{row["label"]} 实际 {row["actual"]:g}，阈值 {row["threshold"]:g}' for row in sustained)
        self.evidence = {'source': SOURCE, 'aggregation': 'cumulative', 'elapsed_seconds': elapsed,
                         'abort_delay': self.config['abort_delay'], 'breach_window': self.config['breach_window'],
                         'reason': self.reason, 'details': sustained}
        return True
