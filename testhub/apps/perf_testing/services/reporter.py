"""压测 HTML 报告生成。

设计取舍：报告是一个「可离线归档、可发给别人」的单文件 HTML。
图表用 CDN 引 ECharts，加载不到时自动降级为纯表格 —— 所有数字本身
都内联在 HTML 里，所以断网环境下报告依然完整可读，只是没有曲线。
"""
import html
import json
import logging
import os
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from .metrics import downsample
from .k6_throughput import peak_is_verified, HISTORICAL_RATE_REASON, RATE_NOTICE

logger = logging.getLogger(__name__)

#: 曲线最多渲染的点数，超过则等距降采样，避免浏览器卡死
MAX_CHART_POINTS = 600

ECHARTS_CDN = 'https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js'


def finite(value):
    import math
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_k6(execution):
    load = execution.load_snapshot or {}
    summary = execution.summary or {}
    return load.get('_engine') == 'K6' or (not load.get('_engine') and 'http_total' in summary and 'business_total' in summary)


def frozen_engine(execution):
    if not execution:
        return None
    return (execution.load_snapshot or {}).get('_engine') or ('K6' if is_k6(execution) else None)


def normalized_summary(execution):
    result = dict(execution.summary or {})
    if 'execution_policy' in result:
        from .k6_samples import policy_payload
        result['execution_policy'] = policy_payload(result['execution_policy'])
    for key, value in list(result.items()):
        if isinstance(value, float) and not finite(value):
            result[key] = None
    if is_k6(execution) and not result.get('business_total'):
        for key in ('avg_rt', 'min_rt', 'max_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'error_rate'):
            result[key] = None
    if is_k6(execution):
        if not peak_is_verified(result):
            result['peak_tps'] = None
            result['throughput_notice'] = (result.get('throughput') or {}).get('reason') or HISTORICAL_RATE_REASON
        else:
            result['peak_tps'] = result['throughput']['peak_rps']
            result['throughput_notice'] = RATE_NOTICE
    return clean_json(result)


def normalized_samples(execution, samples):
    from .k6_samples import project_sample
    result = [dict(sample) for sample in samples]
    if is_k6(execution):
        result = [project_sample(sample) for sample in result]
    return clean_json(result)


def clean_json(value):
    if isinstance(value, dict):
        return {k: clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    return None if isinstance(value, float) and not finite(value) else value


def safe_request_path(value):
    """只展示冻结的路径模板，不展开变量或导出 URL 中的认证/查询信息。"""
    if not isinstance(value, str) or not value or len(value) > 8192:
        return None
    if any(ord(char) < 32 for char in value) or '\\' in value:
        return None
    value = re.sub(r'^/(?=https?://)', '', value)
    value = re.sub(r'^(?:\{\{\s*(?:base_url|baseUrl)\s*\}\}|\$\{(?:base_url|baseUrl)\})', '', value)
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme and parts.scheme.lower() not in ('http', 'https'):
        return None
    path = parts.path
    return path if path.startswith('/') and not path.startswith('//') and '://' not in path else None


def report_steps(execution):
    steps = [dict(step) for step in (execution.steps_snapshot or [])]
    from .execution_policy import public_policy
    policies = [public_policy(step.get('execution_policy')) for step in steps]
    from .websocket_steps import public_websocket_step
    from .sse_steps import public_sse_step
    steps = [public_websocket_step(step) if step.get('protocol') == 'WEBSOCKET' else
             public_sse_step(step) if step.get('protocol') == 'SSE' else step for step in steps]
    for step, policy in zip(steps, policies):
        step.pop('execution_policy', None)
        if policy:
            step['execution_policy'] = policy
    if not is_k6(execution):
        return steps
    for step in steps:
        step['request_path'] = safe_request_path(step.get('request_path'))
    if all(step['request_path'] for step in steps) or not getattr(execution, 'pk', None):
        return steps
    from .k6_execution import K6ExecutionError, load_snapshot
    try:
        snapshot = load_snapshot(settings.PERF_PRIVATE_ROOT, execution.pk)
    except K6ExecutionError:
        return steps  # 历史快照缺失时保持未知，不能使用当前场景地址。
    candidates = list(snapshot.get('steps') or [])
    profile = (snapshot.get('runtime_config') or {}).get('auth_profile') or {}
    for phase in ('login', 'refresh'):
        if isinstance(profile.get(phase), dict):
            candidates.append(dict(profile[phase], id='auth:' + phase))
    for step in steps:
        if step['request_path'] or step.get('id') is None:
            continue
        matches = [item for item in candidates if isinstance(item, dict) and str(item.get('id')) == str(step['id'])]
        if (len(matches) == 1 and matches[0].get('method') == step.get('method')
                and sum(str(item.get('id')) == str(step['id']) for item in steps) == 1):
            step['request_path'] = safe_request_path(matches[0].get('url'))
    return steps


def interface_rows(execution, stats=None):
    if stats is None:
        stats = list(execution.request_stats.all())
    raw = [dict(s) if isinstance(s, dict) else {k: getattr(s, k) for k in
        ('step_name', 'method', 'url', 'total', 'success', 'failed', 'error_rate', 'tps',
         'avg_rt', 'min_rt', 'max_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'error_detail')} for s in stats]
    if not is_k6(execution):
        return raw
    metrics = (execution.summary or {}).get('step_metrics')
    metrics = metrics if isinstance(metrics, list) else raw
    definitions = [s for s in report_steps(execution) if s.get('enabled', True)]
    used, rows = set(), []
    def metric_id(stat):
        if stat.get('step_id') is not None:
            return str(stat['step_id'])
        url = str(stat.get('url') or '')
        return url[5:] if url.startswith('step:') else None
    for index, step in enumerate(definitions):
        step_id = step.get('id') if step.get('id') is not None else f'legacy:{index + 1}'
        raw_id = str(step.get('id') if step.get('id') is not None else index + 1)
        ambiguous_raw = sum(str(s.get('id') if s.get('id') is not None else n + 1) == raw_id for n, s in enumerate(definitions)) > 1
        matches = [(n, m) for n, m in enumerate(metrics) if n not in used and
                   (str(m.get('step_id')) == str(step_id) if m.get('step_id') is not None
                    else not ambiguous_raw and metric_id(m) == raw_id)]
        if not matches:
            unique_name = sum(s.get('name') == step.get('name') and s.get('method') == step.get('method') for s in definitions) == 1
            if unique_name:
                matches = [(n, m) for n, m in enumerate(metrics) if n not in used and metric_id(m) is None and
                           m.get('step_name', m.get('name')) == step.get('name') and m.get('method', step.get('method')) == step.get('method')]
        stat = {}
        if len(matches) == 1:
            n, stat = matches[0]; used.add(n)
        row = dict(stat, step_id=step_id, step_name=step.get('name'), method=step.get('method'),
            protocol=step.get('protocol', 'HTTP'),
            latency_kind='session' if step.get('protocol') == 'WEBSOCKET' else 'stream' if step.get('protocol') == 'SSE' else 'request',
            phase=step.get('auth_phase') or ('setup' if step.get('is_setup') else 'business'),
            match_status='matched' if stat else 'ambiguous_or_missing', url=f'step:{step_id}',
            request_path=step.get('request_path'))
        rows.append(row)
    for n, stat in enumerate(metrics):
        if n not in used:
            rows.append(dict(stat, phase=stat.get('phase', 'unknown'), match_status='unattributed',
                step_name=stat.get('step_name', stat.get('name')), step_id=stat.get('step_id'), request_path=None))
    for row in rows:
        if not row.get('total'):
            for key in ('avg_rt', 'min_rt', 'max_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'error_rate'):
                row[key] = None
        for key, value in list(row.items()):
            if isinstance(value, float) and not finite(value):
                row[key] = None
    return rows


def report_evidence(execution):
    load, summary = execution.load_snapshot or {}, execution.summary or {}
    snapshot = {}
    if is_k6(execution):
        try:
            from .executor import _execution_snapshot
            snapshot = _execution_snapshot(execution)
        except Exception:
            pass  # Historical private snapshot may have expired; never substitute current configuration.
    def pick(source, keys):
        return {k: source[k] for k in keys if k in (source or {})}
    sources = snapshot.get('environment_sources', load.get('_environment_sources')) or []
    pool = snapshot.get('account_pool', load.get('_account_pool')) or {}
    interfaces = []
    for step in snapshot.get('steps', []):
        source = pick(step.get('source_metadata') or {}, ('id', 'version_id', 'version', 'catalog_hash', 'project_id', 'source_version', 'content_hash'))
        interfaces.append({'step_id': step.get('id'), 'source': source})
    return {
        'snapshot_available': bool(snapshot), 'scenario_id': getattr(execution, 'scenario_id', None),
        'scenario_name': snapshot.get('scenario_name'), 'execution_id': getattr(execution, 'pk', None),
        'k6_version': snapshot.get('k6_version'),
        'adapter_version': snapshot.get('k6_adapter_version', summary.get('adapter_version')),
        'environments': [pick(e, ('id', 'name', 'scope', 'project_id', 'version', 'content_hash')) for e in sources],
        'account_pool': pick(pool, ('pool_id', 'version_id', 'version', 'content_hash', 'effective_row_count')),
        'interfaces': interfaces,
        'runner': pick(summary.get('runner_metadata') or {}, ('mode', 'fingerprint', 'image_id', 'binary_version', 'binary_sha256', 'cpus', 'memory')),
        'sla_config': snapshot.get('sla_config'),
        'server_resources': None,
    }


def report_document(execution):
    return {'schema_version': 1, 'execution_id': getattr(execution, 'pk', None), 'execution_no': execution.execution_no,
        'status': execution.status, 'sla_result': execution.sla_result, 'sla_detail': execution.sla_detail,
        'summary': normalized_summary(execution), 'interfaces': interface_rows(execution),
        'evidence': report_evidence(execution)}


def generate_report(execution):
    """生成 HTML 报告，返回相对 MEDIA_ROOT 的路径。"""
    from ..models import PerfMetricSample, PerfRequestStat

    art_rel = execution.artifact_dir or os.path.join(
        'perf-testing', 'executions', str(execution.id))
    art_abs = os.path.join(settings.MEDIA_ROOT, art_rel)
    os.makedirs(art_abs, exist_ok=True)
    report_abs = os.path.join(art_abs, 'report.html')

    samples = list(PerfMetricSample.objects.filter(
        execution=execution).order_by('ts_offset').values(
        'ts_offset', 'active_users', 'tps', 'avg_rt', 'p90_rt', 'p95_rt',
        'p99_rt', 'error_rate', 'cpu_percent', 'memory_mb', 'total_requests', 'id', 'k6_payload'))
    samples = downsample(samples, MAX_CHART_POINTS)
    stats = list(PerfRequestStat.objects.filter(execution=execution).order_by('-avg_rt'))

    content = _render(execution, samples, stats)
    with open(report_abs, 'w', encoding='utf-8') as fh:
        fh.write(content)

    return os.path.join(art_rel, 'report.html').replace('\\', '/')


# ---------------------------------------------------------------------- #
def _esc(value):
    return html.escape(str(value if value is not None else ''))


def _render(execution, samples, stats):
    summary = normalized_summary(execution)
    samples = normalized_samples(execution, samples)
    scenario = execution.scenario
    load = execution.load_snapshot or {}
    frozen_engine = load.get('_engine')
    is_k6 = frozen_engine == 'K6' or (not frozen_engine
        and 'http_total' in summary and 'business_total' in summary)
    engine_name = frozen_engine or ('K6' if is_k6 else scenario.engine)
    has_websocket = is_k6 and isinstance(summary.get('websocket'), dict)
    has_sse = is_k6 and isinstance(summary.get('sse'), dict)
    rate_label = '业务步骤/s' if has_websocket else '业务 RPS' if is_k6 else 'TPS'
    cpu_peak = summary.get('peak_load_gen_cpu')
    cpu_known = cpu_peak is not None and (not is_k6 or cpu_peak > 0 or summary.get('cpu_sample_count', 0) > 0)
    metric_semantics = summary.get('metric_semantics')
    metric_semantics = metric_semantics if isinstance(metric_semantics, dict) else {}
    cpu_source = metric_semantics.get('cpu_percent')
    cpu_source = ('CPU 采样来源：' + cpu_source if isinstance(cpu_source, str) and cpu_source.strip()
                  else 'CPU 采样来源未记录')

    sla_badge = {
        'PASSED': ('通过', '#059669', '#d1fae5'),
        'FAILED': ('未通过', '#dc2626', '#fee2e2'),
    }.get(execution.sla_result, ('未评估', '#6b7280', '#f3f4f6'))
    status_text = dict(execution.STATUS_CHOICES).get(execution.status, execution.status)

    model_text = {
        'CONCURRENCY': '固定并发', 'RAMPING': '阶梯加压',
        'RPS': '固定 RPS', 'SPIKE': '尖峰冲击',
    }.get(load.get('model'), load.get('model', '-'))

    def chart_value(sample, key, precision=2, needs_business=False):
        value = sample.get(key)
        if not finite(value) or (is_k6 and needs_business and not sample.get('total_requests')):
            return None
        return round(value, precision)

    chart_data = {
        'x': [s.get('ts_offset') for s in samples],
        'tps': [chart_value(s, 'tps') for s in samples],
        'users': [chart_value(s, 'active_users') for s in samples],
        'avg': [chart_value(s, 'avg_rt', needs_business=True) for s in samples],
        'p95': [chart_value(s, 'p95_rt', needs_business=True) for s in samples],
        'p99': [chart_value(s, 'p99_rt', needs_business=True) for s in samples],
        'err': [chart_value(s, 'error_rate', needs_business=True) for s in samples],
        'cpu': [chart_value(s.get('k6_payload') or {}, 'cpu_percent', 1)
                if (s.get('k6_payload') or {}).get('cpu_sampled') is True else None
                for s in samples] if is_k6 else [chart_value(s, 'cpu_percent', 1) for s in samples],
    }

    def display(value, suffix='', count=False):
        if not finite(value):
            return '未采集'
        return (f'{value:,}' if count else str(value)) + suffix

    error_rate = summary.get('error_rate')
    cards = [
        ('总请求数', display(summary.get('total_requests'), count=True), ''),
        ('业务成功 / 失败' if is_k6 else '成功 / 失败',
         f"{display(summary.get('success_requests'), count=True)} / {display(summary.get('failed_requests'), count=True)}", ''),
        ('业务失败率' if is_k6 else '错误率', display(error_rate, '%'),
         ('bad' if error_rate > 1 else 'good') if finite(error_rate) else ''),
        (f'平均{rate_label}' if is_k6 else '平均 TPS',
         display(summary.get('business_rps', summary.get('tps')) if is_k6 else summary.get('tps')), ''),
        (f'峰值{rate_label}' if is_k6 else '峰值 TPS', display(summary.get('peak_tps')), ''),
        ('平均业务响应' if is_k6 else '平均响应', display(summary.get('avg_rt'), ' ms'), ''),
        ('累计 P95（估算）' if is_k6 else 'P95 响应', display(summary.get('p95_rt'), ' ms'), ''),
        ('累计 P99（估算）' if is_k6 else 'P99 响应', display(summary.get('p99_rt'), ' ms'), ''),
        ('最大业务响应' if is_k6 else '最大响应', display(summary.get('max_rt'), ' ms'), ''),
        ('脚本活动用户峰值' if is_k6 else '峰值并发', display(summary.get('max_concurrency')), ''),
        ('执行时长', display(execution.duration, ' s'), ''),
        ('CPU 峰值' if is_k6 else '压力机CPU峰值',
         (display(cpu_peak, '%') if cpu_known else '暂无数据') if is_k6 else display(cpu_peak, '%'),
         '' if is_k6 else ('bad' if not summary.get('data_trustworthy', True) else 'good')),
    ]
    semantics_html = ''
    if is_k6:
        latency_labels = {'平均业务响应', '累计 P95（估算）', '累计 P99（估算）', '最大业务响应'}
        if not summary.get('business_total'):
            cards = [(label, '未采集', '') if label in latency_labels or label == '业务失败率'
                     else (label, value, css) for label, value, css in cards]
        count_cards = []
        for label, key in (
            ('已发起 HTTP 请求', 'http_started'), ('已完成 HTTP 请求', 'http_total'),
            ('未完成 HTTP 请求', 'http_incomplete'), ('已发起业务请求', 'business_started'),
            ('已完成业务请求', 'business_total'), ('未完成业务请求', 'business_incomplete'),
            ('完整执行轮次', 'completed_iterations'),
        ):
            value = summary.get(key)
            count_cards.append((label, f'{value:,}' if value is not None else '未采集',
                                'bad' if key.endswith('_incomplete') and value else ''))
        cards = count_cards + cards[1:]
        semantics_html = '<div class="panel"><p>' + _esc(summary.get('throughput_notice')) + '</p></div>'
        timed_load = summary.get('timed_load')
        if isinstance(timed_load, dict):
            timing_rows = ''.join('<p>' + label + '：' + _esc(display(timed_load.get(key), ' s')) + '</p>'
                                 for label, key in (('配置发压时间', 'configured_seconds'),
                                                    ('收尾时间上限', 'drain_limit_seconds'),
                                                    ('引擎总时间', 'engine_seconds')))
            semantics_html += '<div class="panel">' + timing_rows + '<p>' + _esc(timed_load.get('notice')) + '</p></div>'
        if summary.get('setup_incomplete_vus'):
            semantics_html += '<div class="panel"><p>前置未完成用户：' + _esc(summary['setup_incomplete_vus']) + '</p></div>'
        semantics_html += (
            '<div class="panel"><h2 style="margin-top:0">指标口径</h2>'
            '<p>已发起按请求调用开始计数；已完成以收到请求结果计数。'
            '未完成为已发起减去已完成，可能来自到时结束、手动停止或异常中止。'
            '这些请求可能已经到达服务端，不计入已完成请求的失败率和响应时间。</p>'
            '<p>HTTP 请求包含前置登录；业务请求、业务 RPS 和全局响应时间排除前置步骤。'
            '接口级统计包含前置和业务步骤，按各步骤已完成请求计算。逐接口发起和未完成次数未采集。</p>'
            '<p>完整执行轮次不代表成功事务数；一轮内的业务请求可能失败。'
            '业务失败率按已完成业务请求计算，每个请求只计一次成败。</p>'
            '<p>业务 RPS＝已完成业务请求数÷引擎启动至清理结束的时间；'
            '包含容器启动、登录与刷新、思考时间、采集及停止清理耗时。</p>'
            '<p>脚本活动用户及其峰值来自脚本事件，不等于原生活动 VU 或同时在途的 HTTP 请求数。'
            '本图不能证明配置人数（包括 1000 VU）在整个时段持续运行或系统承载能力。'
            '原生 VU 缺失时仍为未知，不能用本图补齐。</p>'
            '<p>P95 / P99 来自对应请求的累计直方图估算，不是每秒分位数的平均。'
            '请求与响应字节数未采集。</p>'
            f'<p>{_esc(cpu_source)}。</p>'
            '<p>发压器最大承载量尚未标定，CPU 采样不能单独证明发压器容量或确定系统瓶颈；'
            '较低 CPU 也不代表容量或结果可信。</p></div>'
        )
    if has_websocket:
        cards = [(label.replace('业务请求', '业务步骤'), value, css) for label, value, css in cards]
        semantics_html = semantics_html.replace('业务请求', '业务步骤').replace('业务 RPS', '业务步骤/s')
        ws = summary['websocket']
        count_rows = ''.join('<tr><td>' + label + '</td>' + ''.join('<td>' + _esc(
            (ws.get(key) or {}).get(field) if (ws.get(key) or {}).get(field) is not None else '未采集') + '</td>'
            for field in ('started', 'completed', 'success', 'failed', 'incomplete')) + '</tr>'
            for key, label in (('sessions', '会话'), ('connect', '握手'), ('auth', '认证'), ('commands', '命令'), ('events', '推送事件'), ('heartbeat', '心跳')))
        connections = ws.get('connections') or {}
        semantics_html += ('<div class="panel"><h2>WebSocket</h2><p>每个会话计一个步骤；'
            '101 仅表示握手。会话耗时包含握手、认证、命令、保活及关闭，不是命令 RTT。'
            '业务步骤/s 不是消息 QPS。步骤 SLA 对会话总耗时生效，命令级 SLA 未支持。</p><p>实际连接 当前 / 峰值 / 未观察关闭：'
            + ' / '.join(_esc(connections.get(key) if connections.get(key) is not None else '未知') for key in ('current', 'peak', 'unclosed'))
            + '</p><table><thead><tr><th>阶段</th><th>发起</th><th>完成</th><th>成功</th><th>失败</th><th>未完成</th></tr></thead><tbody>'
            + count_rows + '</tbody></table><h3>命令 RTT / 推送等待（ms，按 latency_kind 区分）</h3><pre style="white-space:pre-wrap">'
            + _esc(json.dumps(ws.get('command_metrics') or [], ensure_ascii=False, indent=2)) + '</pre></div>')
    if has_sse:
        rows = summary['sse'].get('stream_metrics') or []
        from .k6_sse_metrics import REASONS, public_diagnostics
        stream_rows = []
        diagnostic_rows = []
        frozen_configs = {str(step.get('id')): step.get('sse_config') for step in execution.steps_snapshot}
        def safe_stream_errors(items):
            return [{'phase': item.get('phase') if item.get('phase') in ('preparation', 'transport', 'business') else 'transport',
                     'reason': item.get('reason') if item.get('reason') in REASONS else 'protocol_error',
                     'count': item.get('count') if type(item.get('count')) is int else 0} for item in items]
        labels = {str(step.get('id')): step.get('name') for step in report_steps(execution)}
        for row in rows:
            values = [labels.get(str(row.get('step_id')), '未知步骤')]
            values.extend((row.get('streams') or {}).get(key) for key in ('started', 'completed', 'success', 'failed', 'incomplete'))
            values.extend((row.get(key) or {}).get('avg_ms') for key in ('first_event', 'completion'))
            values.extend(row.get(key) for key in ('events', 'bytes'))
            values.append('; '.join(f"{item['phase']} / {item['reason']} / {item['count']}"
                                    for item in safe_stream_errors(row.get('errors') or [])) or '无')
            stream_rows.append('<tr>' + ''.join('<td>' + _esc(value if value is not None else '未采集') + '</td>' for value in values) + '</tr>')
            locations = public_diagnostics(row.get('diagnostics'), frozen_configs.get(str(row.get('step_id'))))
            if locations or row.get('diagnostics_truncated') is True:
                diagnostic_rows.append('<p>' + _esc(labels.get(str(row.get('step_id')), '未知步骤')) + '</p><pre>'
                    + _esc(json.dumps(dict(diagnostics=locations, diagnostics_truncated=row.get('diagnostics_truncated') is True), ensure_ascii=False)) + '</pre>'
                    + ('<p>定位种类超过 32，已截断；失败总数保持完整。</p>' if row.get('diagnostics_truncated') is True else ''))
        errors = safe_stream_errors(summary['sse'].get('errors', []))
        semantics_html += ('<div class="panel"><h2>SSE 事件流</h2><p>一次流计一个 HTTP 请求；成功需要业务终态合同，HTTP 200 不代表成功。'
            '首事件延迟与完整流结束耗时分别统计，结束耗时包含失败样本，未完成流不计成功。</p>'
            '<table><thead><tr><th>步骤</th><th>发起</th><th>完成</th><th>成功</th><th>失败</th><th>未完成</th>'
            '<th>首事件均值(ms)</th><th>结束均值(ms)</th><th>事件数</th><th>流字节</th><th>逐流失败阶段与原因</th></tr></thead><tbody>'
            + ''.join(stream_rows) + '</tbody></table><p>失败阶段与固定原因：</p><pre>'
            + _esc(json.dumps(errors, ensure_ascii=False)) + '</pre>'
            + ('</div><div class="panel"><h2>SSE 失败定位</h2><p>事件、规则及条件序号从 1 开始，对应执行时冻结的合同；不包含响应内容。</p>' + ''.join(diagnostic_rows) if diagnostic_rows else '') + '</div>')
    cards_html = ''.join(
        f'<div class="card"><div class="card-label">{_esc(label)}</div>'
        f'<div class="card-value {cls}">{_esc(value)}</div></div>'
        for label, value, cls in cards)

    warn_html = ''
    if summary.get('sample_data_incomplete'):
        warn_html += ('<div class="alert bad">采样数据不完整：缺失 '
                      f'{_esc(summary.get("sample_missing_count"))} 条时序采样；'
                      '已收到的请求统计及原始明细保留，整体 SLA 未评估。</div>')
    if is_k6 and summary.get('data_trustworthy') is False:
        trust_explanation = metric_semantics.get('data_trustworthy')
        trust_explanation = (trust_explanation if isinstance(trust_explanation, str) and trust_explanation.strip()
                             else '原因未记录')
        warn_html += ('<div class="alert warn">⚠️ 数据可信标记为否；冻结说明：'
                      f'{_esc(trust_explanation)}。CPU 采样不能单独证明发压器容量或确定系统瓶颈。</div>')
    elif not is_k6 and summary.get('data_trustworthy') is False and not summary.get('sample_data_incomplete'):
        warn_html += (
            '<div class="alert warn">⚠️ 压测过程中压力机 CPU 峰值达 '
            f'{summary.get("peak_load_gen_cpu")}%，已接近单机瓶颈。'
            f'此时实测 {rate_label} 可能受限于压力机而非被测服务，报告数据仅供参考。</div>')
    if summary.get('aborted_by_sla'):
        warn_html += ('<div class="alert warn">⚠️ 本次压测因 SLA 持续超限触发自动熔断而提前结束。</div>')
        warn_html += f'<div class="alert warn">中止依据：{_esc((summary.get("sla_abort") or {}).get("reason"))}</div>'
    if is_k6 and summary.get('completion_reason'):
        from .k6_thresholds import REASONS
        warn_html += (f'<div class="alert warn">执行不完整：'
                      f'{_esc(REASONS.get(summary["completion_reason"], summary["completion_reason"]))}；'
                      '部分结果已保留，整体 SLA 未评估。</div>')
    if execution.error_message:
        warn_html += f'<div class="alert bad">执行错误：{_esc(execution.error_message[:500])}</div>'

    # SLA 明细
    sla_rows = []
    for detail in execution.sla_detail or []:
        passed = detail.get('passed')
        result_text = '未评估' if passed is None else '通过' if passed else '未通过'
        css = '' if passed is None else 'ok' if passed else 'fail'
        scope = f'步骤 {detail.get("step_id")} · {detail.get("step_name", "")}' if detail.get('scope') == 'step' else '全局'
        actual = '未采集' if detail.get('actual') is None else detail['actual']
        sla_rows.append(
            f'<tr><td>{_esc(scope)}</td><td>{_esc(detail.get("label"))}</td>'
            f'<td>{_esc(detail.get("comparator"))} {_esc(detail.get("threshold"))}</td>'
            f'<td>{_esc(actual)}</td><td class="{css}">{result_text}</td>'
            f'<td>{_esc(detail.get("reason_label") or detail.get("reason"))}</td></tr>')
    sla_html = (f'<h2>SLA 判定</h2><table><thead><tr><th>范围</th><th>指标</th><th>阈值</th>'
                f'<th>实测</th><th>结果</th><th>原因</th></tr></thead><tbody>{"".join(sla_rows)}</tbody></table>'
                ) if sla_rows else ''
    if sla_html and is_k6:
        sla_html += ('<p>来源：TestHub 平台累计请求事件适配（非原生 k6 thresholds）。'
                     '响应时间单位 ms，失败率单位 %，min_tps 表示业务 requests/s。'
                     '分位数使用累计固定桶插值估算。观察延迟和持续超限窗口单位为秒；'
                     '同一规则在延迟结束后持续超限才中止。停止后的 SLA 不作为完整测试结论。</p>')

    # 接口级统计
    rows = interface_rows(execution, stats)
    keys = ('step_name', 'method', 'url', 'total', 'success', 'failed', 'error_rate', 'tps', 'avg_rt', 'min_rt', 'max_rt', 'p90_rt', 'p95_rt', 'p99_rt')
    if is_k6:
        keys = ('step_name', 'request_path', 'phase') + keys[1:]
    if has_websocket or has_sse:
        keys += ('protocol', 'latency_kind')
    stat_rows = ''.join('<tr>' + ''.join(f'<td>{_esc(row.get(key) if row.get(key) is not None else "未采集")}</td>' for key in keys) + '</tr>' for row in rows)
    if is_k6:
        evidence = report_evidence(execution)
        semantics_html += '<div class="panel"><h2>运行来源与版本（冻结快照）</h2><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + _esc(json.dumps(evidence, ensure_ascii=False, indent=2)) + '</pre><p>服务端资源：未采集。历史缺失快照不会替换为当前配置。</p></div>'
        if summary.get('sla_abort'):
            semantics_html += '<div class="panel"><h2>SLA 中止触发证据</h2><pre style="white-space:pre-wrap">' + _esc(json.dumps(summary['sla_abort'], ensure_ascii=False, indent=2)) + '</pre></div>'

    # 错误 TOP
    error_rows = ''.join(
        f'<tr><td>{_esc(e.get("type"))}</td><td>{e.get("count", 0):,}</td>'
        f'<td>{_esc(e.get("sample_step"))}</td><td class="url">{_esc(e.get("message"))}</td></tr>'
        for e in (summary.get('error_top') or []))
    error_html = (f'<h2>错误 TOP</h2><table><thead><tr><th>错误类型</th><th>次数</th>'
                  f'<th>示例步骤</th><th>示例信息</th></tr></thead>'
                  f'<tbody>{error_rows}</tbody></table>') if error_rows else ''

    fmt = '%Y-%m-%d %H:%M:%S'
    start_text = timezone.localtime(execution.start_time).strftime(fmt) if execution.start_time else '-'
    end_text = timezone.localtime(execution.end_time).strftime(fmt) if execution.end_time else '-'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>性能测试报告 - {_esc(scenario.name)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:24px; background:#f5f7fa; color:#1f2937;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:1400px; margin:0 auto; }}
  header {{ background:#fff; border-radius:10px; padding:24px; margin-bottom:16px;
            box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  h1 {{ margin:0 0 6px; font-size:22px; }}
  h2 {{ font-size:16px; margin:24px 0 12px; padding-left:10px; border-left:3px solid #2563eb; }}
  .sub {{ color:#6b7280; font-size:13px; }}
  .badge {{ display:inline-block; padding:4px 12px; border-radius:14px; font-size:13px;
            font-weight:600; color:{sla_badge[1]}; background:{sla_badge[2]}; }}
  .meta {{ display:flex; flex-wrap:wrap; gap:10px 32px; margin-top:14px; font-size:13px; color:#4b5563; }}
  .meta b {{ color:#1f2937; font-weight:600; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
            gap:12px; margin-bottom:16px; }}
  .card {{ background:#fff; border-radius:8px; padding:14px 16px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  .card-label {{ font-size:12px; color:#6b7280; margin-bottom:6px; }}
  .card-value {{ font-size:20px; font-weight:600; }}
  .card-value.good {{ color:#059669; }}
  .card-value.bad {{ color:#dc2626; }}
  .panel {{ background:#fff; border-radius:10px; padding:20px; margin-bottom:16px;
            box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  .chart {{ width:100%; height:340px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ padding:9px 10px; text-align:left; border-bottom:1px solid #eef1f5; white-space:nowrap; }}
  th {{ background:#f9fafb; color:#4b5563; font-weight:600; }}
  tbody tr:hover {{ background:#f9fafb; }}
  td.url {{ max-width:340px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#6b7280; }}
  .method {{ font-size:11px; padding:2px 6px; border-radius:3px; background:#eff6ff; color:#2563eb; font-weight:600; }}
  .ok {{ color:#059669; font-weight:600; }} .fail {{ color:#dc2626; font-weight:600; }}
  .alert {{ padding:12px 16px; border-radius:8px; margin-bottom:12px; font-size:13px; }}
  .alert.warn {{ background:#fffbeb; color:#92400e; border-left:3px solid #f59e0b; }}
  .alert.bad {{ background:#fef2f2; color:#991b1b; border-left:3px solid #dc2626; }}
  .fallback {{ display:none; padding:14px; background:#f9fafb; border-radius:6px;
               color:#6b7280; font-size:13px; text-align:center; }}
  footer {{ text-align:center; color:#9ca3af; font-size:12px; padding:16px 0; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{_esc(scenario.name)} <span class="badge">SLA {sla_badge[0]}</span></h1>
    <div class="sub">{_esc(execution.execution_no)} · {_esc(scenario.project.name)}</div>
    <div class="meta">
      <span>执行状态：<b>{_esc(status_text)}</b></span>
      <span>压力模型：<b>{_esc(model_text)}</b></span>
      <span>压测引擎：<b>{_esc(engine_name)}</b></span>
      <span>开始：<b>{_esc(start_text)}</b></span>
      <span>结束：<b>{_esc(end_text)}</b></span>
      <span>执行人：<b>{_esc(execution.executed_by.username if execution.executed_by else '系统')}</b></span>
    </div>
  </header>

  {warn_html}

  <div class="cards">{cards_html}</div>
  {semantics_html}

  <div class="panel">
    <h2 style="margin-top:0">{rate_label} 与{'脚本活动用户' if is_k6 else '并发'}趋势</h2>
    <div id="c1" class="chart"></div>
    <div id="f1" class="fallback">图表库未能加载（可能处于离线环境），下方表格数据不受影响。</div>
    <h2>响应时间趋势</h2>
    <div id="c2" class="chart"></div>
    <h2>{'业务失败率与 CPU' if is_k6 else '错误率与压力机水位'}</h2>
    <div id="c3" class="chart"></div>
  </div>

  <div class="panel">
    <h2 style="margin-top:0">接口级统计</h2>
    <div style="overflow-x:auto">
    <table><thead><tr>
      <th>步骤</th>{"<th>API 地址</th><th>阶段</th>" if is_k6 else ""}<th>方法</th><th>{'步骤标识' if is_k6 else 'URL'}</th><th>{'已完成' if is_k6 else '总数'}</th><th>成功</th><th>失败</th>
      <th>错误率</th><th>{'业务步骤/s' if has_websocket else '请求 RPS' if is_k6 else 'TPS'}</th><th>平均(ms)</th><th>最小</th><th>最大</th>
      <th>{'P90（估算）' if is_k6 else 'P90'}</th><th>{'P95（估算）' if is_k6 else 'P95'}</th><th>{'P99（估算）' if is_k6 else 'P99'}</th>{'<th>协议</th><th>耗时口径</th>' if has_websocket or has_sse else ''}
    </tr></thead><tbody>{stat_rows}</tbody></table>
    </div>
    {error_html}
    {sla_html}
  </div>

  <footer>TestHub 性能测试平台 · 生成于 {timezone.localtime().strftime(fmt)}</footer>
</div>

<script src="{ECHARTS_CDN}"></script>
<script>
var D = {json.dumps(chart_data, ensure_ascii=False)};
(function () {{
  if (typeof echarts === 'undefined') {{
    ['c1','c2','c3'].forEach(function (id) {{
      var el = document.getElementById(id); if (el) el.style.display = 'none';
    }});
    var fb = document.getElementById('f1'); if (fb) fb.style.display = 'block';
    return;
  }}
  var base = {{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ top: 0 }},
    grid: {{ left: 50, right: 50, top: 36, bottom: 30 }},
    xAxis: {{ type: 'category', data: D.x, name: '秒', boundaryGap: false }}
  }};
  function nullableLine(series) {{
    if (!{str(is_k6).lower()} || [D.tps,D.avg,D.p95,D.p99,D.err,D.cpu].indexOf(series.data) < 0) return series;
    var data = series.data;
    function valid(value) {{ return typeof value === 'number' && Number.isFinite(value); }}
    return Object.assign({{}}, series, {{
      step:false, connectNulls:false, showSymbol:true, showAllSymbol:true,
      symbolSize:function (_, params) {{
        var i = params.dataIndex;
        return valid(data[i]) && !valid(data[i-1]) && !valid(data[i+1]) ? 6 : 0;
      }}
    }});
  }}
  function mk(id, series, yAxis) {{
    var el = document.getElementById(id); if (!el) return;
    var chart = echarts.init(el);
    chart.setOption(Object.assign({{}}, base, {{ yAxis: yAxis, series: series.map(nullableLine) }}));
    window.addEventListener('resize', function () {{ chart.resize(); }});
  }}
  mk('c1', [
    {{ name:'{rate_label}', type:'line', smooth:true, showSymbol:false, data:D.tps,
       areaStyle:{{opacity:.12}}, itemStyle:{{color:'#2563eb'}} }},
    {{ name:'{'脚本活动用户' if is_k6 else '并发用户'}', type:'line', smooth:true, showSymbol:false, yAxisIndex:1,
       data:D.users, itemStyle:{{color:'#f59e0b'}} }}
  ], [{{ type:'value', name:'{rate_label}' }}, {{ type:'value', name:'{'脚本活动用户' if is_k6 else '并发'}' }}]);
  mk('c2', [
    {{ name:'平均', type:'line', smooth:true, showSymbol:false, data:D.avg, itemStyle:{{color:'#10b981'}} }},
    {{ name:'P95', type:'line', smooth:true, showSymbol:false, data:D.p95, itemStyle:{{color:'#8b5cf6'}} }},
    {{ name:'P99', type:'line', smooth:true, showSymbol:false, data:D.p99, itemStyle:{{color:'#ef4444'}} }}
  ], [{{ type:'value', name:'ms' }}]);
  mk('c3', [
    {{ name:'错误率(%)', type:'line', smooth:true, showSymbol:false, data:D.err,
       itemStyle:{{color:'#dc2626'}}, areaStyle:{{opacity:.12}} }},
    {{ name:'{'CPU(%)' if is_k6 else '压力机CPU(%)'}', type:'line', smooth:true, showSymbol:false, yAxisIndex:1,
       data:D.cpu, itemStyle:{{color:'#6366f1'}} }}
  ], [{{ type:'value', name:'错误率' }}, {{ type:'value', name:'CPU'{'' if is_k6 else ', max:100'} }}]);
}})();
</script>
</body>
</html>"""
