# -*- coding: utf-8 -*-
"""
多执行对照快照服务：
- build_snapshot：构建指标矩阵快照（与原 GET compare 响应结构一致），供 API 与持久化报告共用
- trim_snapshot_for_ai：压缩快照为文本矩阵喂给 LLM
"""

from .reporter import finite, normalized_summary, normalized_samples, interface_rows, is_k6, report_evidence

METRIC_KEYS = ['total_requests', 'tps', 'peak_tps', 'avg_rt', 'p90_rt',
               'p95_rt', 'p99_rt', 'max_rt', 'error_rate']


def _compute_deltas(base_summary, summary):
    deltas = {}
    for key in METRIC_KEYS:
        base_val = base_summary.get(key)
        cur_val = summary.get(key)
        if finite(base_val) and base_val and \
                finite(cur_val):
            deltas[key] = round((cur_val - base_val) / base_val * 100, 2)
        else:
            deltas[key] = None
    return deltas


def build_snapshot(executions, reference_execution_id=None):
    """构建对照快照。

    :param executions: 已按用户顺序排列的 PerfExecution 列表（≥2）
    :param reference_execution_id: 基准执行 id；缺省用第一条
    :return: dict（JSON 可序列化，created_at 已转 ISO 字符串）
    """
    from ..models import PerfMetricSample
    from ..serializers import PerfMetricSampleSerializer
    from .metrics import downsample

    reference = executions[0]
    if reference_execution_id:
        for e in executions:
            if e.id == reference_execution_id:
                reference = e
                break

    base_summary = normalized_summary(reference)
    items = []
    for execution in executions:
        summary = normalized_summary(execution)
        samples = PerfMetricSampleSerializer(
            PerfMetricSample.objects.filter(execution=execution).order_by('ts_offset'),
            many=True).data
        samples = normalized_samples(execution, samples)
        deltas = _compute_deltas(base_summary, summary)
        if is_k6(execution) != is_k6(reference):
            deltas = {key: None for key in deltas}
        items.append({
            'id': execution.id,
            'engine': 'K6' if is_k6(execution) else (execution.load_snapshot or {}).get('_engine', execution.scenario.engine),
            'execution_no': execution.execution_no,
            'scenario_name': execution.scenario.name if execution.scenario else '',
            'status': execution.status,
            'sla_result': execution.sla_result,
            'created_at': execution.created_at.isoformat() if execution.created_at else None,
            'duration': execution.duration,
            'load_snapshot': execution.load_snapshot,
            'report_evidence': report_evidence(execution),
            'is_reference': execution.id == reference.id,
            'summary': {k: summary.get(k) for k in METRIC_KEYS},
            'throughput': summary.get('throughput'),
            'throughput_notice': summary.get('throughput_notice'),
            'delta_pct': deltas,
            'samples': downsample(samples, 300),
        })

    aligned = {}
    for execution in executions:
        rows = interface_rows(execution)
        names = [(r.get('step_name'), r.get('method')) for r in rows]
        for index, row in enumerate(rows):
            identity = row.get('step_id')
            stable_identity = identity is not None and not str(identity).startswith('legacy:')
            if stable_identity and row.get('match_status') != 'unattributed':
                key = f'{execution.scenario_id}:step:{identity}'
                basis = '同场景稳定步骤 ID'
            elif (row.get('match_status') in (None, 'matched') and row.get('step_name') and row.get('method')
                  and names.count((row.get('step_name'), row.get('method'))) == 1):
                key = f'{execution.scenario_id}:legacy:{row.get("phase")}:{row.get("method")}:{row.get("step_name")}'
                basis = '同场景唯一历史名称与方法（无稳定 ID）'
            else:
                key = f'execution:{execution.id}:unattributed:{index}'
                basis = '历史归属不明确，保持独立'
            item = aligned.setdefault(key, {'step_name': row.get('step_name'), 'step_id': identity if stable_identity else None,
                'scenario_id': execution.scenario_id, 'phase': row.get('phase'), 'match_basis': basis, 'by_execution': {}})
            item['by_execution'][execution.id] = row
    step_rows = []
    for key, row in aligned.items():
        values = row.pop('by_execution')
        row['identity'] = key
        row['values'] = [dict(values.get(e.id, {}), execution_no=e.execution_no) for e in executions]
        step_rows.append(row)

    return {
        'baseline_execution_no': reference.execution_no,
        'reference_execution_id': reference.id,
        'metric_keys': METRIC_KEYS,
        'executions': items,
        'step_comparison': step_rows,
        'alignment_notice': '按同场景稳定步骤 ID 对齐；跨场景接口独立显示，缺失或不明确的数据不计算增幅。',
    }


def trim_snapshot_for_ai(snapshot, max_chars=2000):
    """压缩快照为文本矩阵：每执行一行核心指标 + 相对基准 Δ%。"""
    fmt = lambda v: f'{v:.2f}' if isinstance(v, (int, float)) else '-'
    lines = [f"基准执行: {snapshot.get('baseline_execution_no')}",
             '执行 | TPS | 峰值TPS | 平均RT | P95 | P99 | 错误率% | ΔTPS% | ΔP95% | Δ错误率']
    for item in snapshot.get('executions', []):
        s = item.get('summary') or {}
        d = item.get('delta_pct') or {}
        lines.append(' | '.join([
            str(item.get('execution_no', '')),
            fmt(s.get('tps')), fmt(s.get('peak_tps')), fmt(s.get('avg_rt')),
            fmt(s.get('p95_rt')), fmt(s.get('p99_rt')), fmt(s.get('error_rate')),
            fmt(d.get('tps')), fmt(d.get('p95_rt')), fmt(d.get('error_rate')),
        ]))

    step_rows = snapshot.get('step_comparison') or []
    if step_rows:
        lines.append('')
        lines.append('接口级对比（步骤 | 各执行 TPS/P95/错误率%）:')
        for row in step_rows[:20]:
            vals = ['{}/{}/{}'.format(
                fmt(v.get('tps')), fmt(v.get('p95_rt')), fmt(v.get('error_rate')))
                for v in row.get('values', [])]
            lines.append(f"{row.get('step_name')}: " + '; '.join(vals))

    text = '\n'.join(lines)
    return text[:max_chars]


def normalized_snapshot(snapshot):
    """Do not expose unverified historical K6 peaks stored in comparison JSON."""
    from copy import deepcopy
    from types import SimpleNamespace
    result = deepcopy(snapshot or {})
    for item in result.get('executions', []):
        summary = dict(item.get('summary') or {})
        if item.get('throughput'):
            summary['throughput'] = item['throughput']
        execution = SimpleNamespace(load_snapshot={**(item.get('load_snapshot') or {}), '_engine': (item.get('load_snapshot') or {}).get('_engine') or item.get('engine')}, summary=summary)
        if is_k6(execution):
            normalized = normalized_summary(execution)
            item.setdefault('summary', {})['peak_tps'] = normalized.get('peak_tps')
            item['throughput_notice'] = normalized.get('throughput_notice')
            if normalized.get('peak_tps') is None:
                item.setdefault('delta_pct', {})['peak_tps'] = None
            item['samples'] = normalized_samples(execution, item.get('samples', []))
    # A missing reference peak also invalidates percentages for otherwise verified candidates.
    reference = next((item for item in result.get('executions', []) if item.get('is_reference')), None)
    if reference and (reference.get('summary') or {}).get('peak_tps') is None:
        for item in result.get('executions', []):
            item.setdefault('delta_pct', {})['peak_tps'] = None
    return result
