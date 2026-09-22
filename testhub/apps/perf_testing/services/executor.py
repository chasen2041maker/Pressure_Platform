"""压测执行编排。

职责边界：
- 主进程侧（被 views / 调度命令调用）：快照 → 前置校验 → 建执行 → 起子进程 → 停止
- 子进程侧（被 management command run_perf_execution 调用）：真正驱动引擎、落库、收尾

之所以用「子进程」而不是线程或 Celery：
1. 压测是 CPU/IO 双密集型，跑在 Django 进程内会直接拖垮平台自身的接口响应；
2. 平台不强依赖 Celery（api_testing 的定时任务也是命令行驱动），引入 broker 会抬高部署成本；
3. 独立进程可以被 SIGTERM 精确停止，并且进程崩溃不会带走整个 web 服务。
"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

from .child_process import start_process

logger = logging.getLogger(__name__)


def _private_root():
    from .k6_execution import K6ExecutionError
    configured = getattr(settings, 'PERF_PRIVATE_ROOT', None)
    if not configured:
        raise K6ExecutionError('未配置 K6 私密目录 PERF_PRIVATE_ROOT')
    root = Path(configured).resolve()
    media = getattr(settings, 'MEDIA_ROOT', None)
    if media and root.is_relative_to(Path(media).resolve()):
        raise K6ExecutionError('K6 私密目录必须位于可下载的 MEDIA_ROOT 之外')
    return root


def _is_k6_execution(execution):
    return (execution.load_snapshot or {}).get('_engine') == 'K6'


def _execution_snapshot(execution):
    if _is_k6_execution(execution):
        from .k6_execution import load_snapshot
        return load_snapshot(_private_root(), execution.id)
    snapshot = build_snapshot(execution.scenario)
    if execution.load_snapshot is not None:
        snapshot['load_config'] = execution.load_snapshot
    if execution.steps_snapshot is not None:
        snapshot['steps'] = execution.steps_snapshot
    if execution.script_ref is not None:
        snapshot['script_ref'] = execution.script_ref
    return snapshot


def _validate_frozen_k6_version(snapshot):
    from ..engines import k6_version
    from ..engines.k6_engine import ADAPTER_VERSION
    from .k6_execution import K6ExecutionError
    if (snapshot.get('k6_version') != k6_version()
            or snapshot.get('k6_adapter_version') != ADAPTER_VERSION):
        raise K6ExecutionError('K6 程序或适配器版本已改变，请重新创建执行以固定当前版本')


def _close_stale_connections():
    """安全地关闭过期连接。

    设计给独立子进程（run_execution 由 manage.py run_perf_execution 驱动）在
    开跑前收敛陈旧连接。但若调用方仍持有活动事务（in_atomic_block=True，
    如被单元测试/内嵌调用包裹），此时关闭连接会摧毁调用方的事务状态，
    导致下一次查询报 'broken transaction'。因此仅在非事务块内才关闭。
    """
    if not connection.in_atomic_block:
        close_old_connections()


#: 子进程被判定为「僵尸」的心跳超时（秒）
HEARTBEAT_TIMEOUT = 90
#: 优雅停止后等待子进程退出的秒数，超时则 SIGKILL
STOP_GRACE_SECONDS = 15


# ====================================================================== #
# 快照
# ====================================================================== #
def build_snapshot(scenario, load_config=None, *, user=None, environment_overrides=None,
                   _resolved_environment=None):
    """把场景及其步骤冻结成执行快照。

    快照的意义：场景随时可能被改，但已产生的执行报告必须能复现当时的配置，
    否则历史对比就是在比两份不同的东西。
    """
    resolved = (_resolved_environment if _resolved_environment is not None else
                _resolve_environment_inputs(scenario, user, environment_overrides))
    steps = []
    upload_data = {}
    from .upload_files import resolve_files
    for step in scenario.steps.all().order_by('order', 'id'):
        steps.append({
            'id': step.id,
            'order': step.order,
            'name': step.name,
            'enabled': step.enabled,
            'protocol': step.protocol,
            'websocket_config': deepcopy(step.websocket_config),
            'sse_config': deepcopy(step.sse_config),
            'execution_policy': deepcopy(step.execution_policy),
            'source_request_id': step.source_request_id,
            'method': step.method,
            'url': step.url,
            'headers': step.headers or {},
            'params': step.params or {},
            'body_type': step.body_type,
            'body': step.body or '',
            'files': (resolve_files(step.files or [], scenario.project_id, upload_data)
                      if scenario.engine == 'K6' and step.enabled else
                      deepcopy(step.files or []) if scenario.engine == 'K6' else
                      _resolve_step_files(step.files or [], scenario.project_id)),
            'extractors': step.extractors or [],
            'assertions': step.assertions or [],
            'think_time': step.think_time or {},
            'weight': step.weight,
            'is_setup': step.is_setup,
            'source_metadata': deepcopy(getattr(step, 'source_metadata', {})),
            'preparation': deepcopy(getattr(step, 'preparation', {})),
        })

    load_config = deepcopy(scenario.get_load_config() if load_config is None else load_config)
    from ..engines.base import build_load_profile
    _, planned = build_load_profile(load_config)
    load_config['_planned_duration'] = round(planned, 2)

    return {
        'scenario_id': scenario.id,
        'scenario_name': scenario.name,
        'engine': scenario.engine,
        'load_config': load_config,
        'sla_config': scenario.sla_config or {},
        'variables': deepcopy(resolved['variables']),
        'env_config': deepcopy(resolved['env_config']),
        'environment_sources': deepcopy(resolved['environment_sources']),
        'account_pool': deepcopy(resolved.get('account_pool')),
        'runtime_config': deepcopy(scenario.get_runtime_config()),
        'steps': steps,
        'csv_data': deepcopy(resolved['csv_data']),
        'upload_data': upload_data,
    }


def _resolve_environment_inputs(scenario, user=None, overrides=None):
    from .environments import resolve_environment
    resolved = resolve_environment(scenario, user=user, overrides=overrides)
    resolved['csv_data'] = _load_csv_data(scenario, resolved['variables'])
    from .account_pools import resolve_accounts
    return resolve_accounts(scenario, resolved, user)


def _resolve_step_files(file_items, project_id):
    """把步骤的 multipart 文件字段解析为引擎可直接消费的绝对路径。

    只认 file_id（服务端反查 PerfDataFile），绝不接受前端传路径，
    与 resolve_script_ref 同一安全思路；路径由服务端生成，子进程直接用。
    file_id 为空的占位项（导入自接口测试但未补传文件）不参与发送。
    """
    resolved = []
    for item in file_items or []:
        if not isinstance(item, dict):
            continue
        file_id = item.get('file_id')
        if not file_id:
            continue
        try:
            from ..models import PerfDataFile
            data_file = PerfDataFile.objects.filter(
                id=file_id, project_id=project_id, file_type='UPLOAD').first()
            if not data_file or not data_file.file:
                logger.warning('步骤文件字段 %s 引用的文件 %s 不存在', item.get('field'), file_id)
                continue
            resolved.append({
                'field': str(item.get('field') or ''),
                'path': data_file.file.path,
                'filename': str(item.get('filename') or data_file.name)[:200],
                'content_type': str(item.get('content_type')
                                    or (data_file.meta or {}).get('content_type')
                                    or 'application/octet-stream')[:100],
            })
        except Exception as exc:  # noqa: BLE001 - 单文件解析失败不阻断整体快照
            logger.warning('解析步骤文件字段 %s 失败: %s', item.get('field'), exc)
    return resolved


def _load_csv_data(scenario, variables=None):
    """把场景变量里引用的 CSV 数据文件预加载成内存表。"""
    from .variables import load_csv_file

    data = {}
    for item in (scenario.variables if variables is None else variables) or []:
        if (item.get('type') or '').upper() != 'CSV':
            continue
        file_id = item.get('data_file_id')
        if not file_id or str(file_id) in data:
            continue
        try:
            from ..models import PerfDataFile
            data_file = PerfDataFile.objects.filter(
                id=file_id, project=scenario.project_id, file_type='CSV').first()
            if not data_file or not data_file.file:
                continue
            # 快照要经 JSON 传给子进程，int key 会变成字符串，这里统一用 str key；
            # 同时 load_csv_file 返回的是 (rows, columns) 元组，必须转成 dict，
            # 否则运行时 data.get('rows') 直接 AttributeError。
            rows, columns = load_csv_file(data_file.file.path)
            data[str(file_id)] = {'rows': rows, 'columns': columns}
        except Exception as exc:  # noqa: BLE001 - 缺文件应在 preflight 提示，这里不炸
            logger.warning('加载 CSV 数据文件 %s 失败: %s', file_id, exc)
    return data


# ====================================================================== #
# 前置校验（preflight）
# ====================================================================== #
def preflight(scenario, load_config=None, script_ref=None, *, user=None,
              environment_overrides=None, _resolved_environment=None):
    """压测前的安全与容量体检。

    返回 {'passed': bool, 'errors': [...], 'warnings': [...], 'estimated': {...}}

    errors 会阻断执行，warnings 只提示。这里刻意把「打到平台自己」
    这类误操作单独拎出来，因为一旦发生就是自己把自己压垮。

    script_ref 为脚本模式（{'mode': 'script', 'jmx_path': ...}）时走 _preflight_script：
    此时平台的 load_config 与步骤列表都不参与执行，真相全在 .jmx 里，
    继续按场景规则校验只会得出「没有步骤」这类误报。
    """
    from .environments import public_resolution
    from rest_framework.exceptions import ValidationError, PermissionDenied
    try:
        resolved = (_resolved_environment if _resolved_environment is not None else
                    _resolve_environment_inputs(scenario, user, environment_overrides))
    except (ValidationError, PermissionDenied) as exc:
        return {'passed': False, 'errors': [str(exc.detail)], 'warnings': [], 'estimated': {}}
    if (script_ref or {}).get('mode') == 'script':
        return _preflight_script(scenario, script_ref)

    errors, warnings = [], []
    from ..models import PerfExecution
    cfg = dict(scenario.get_load_config() if load_config is None else load_config)
    model = cfg.get('model', 'CONCURRENCY')

    # --- 步骤 ---
    steps = list(scenario.steps.filter(enabled=True))
    if any(getattr(step, 'source_metadata', {}) or getattr(step, 'protocol', 'HTTP') != 'HTTP' for step in steps):
        from . import api_catalog
        try:
            readiness = api_catalog.scenario_readiness(scenario, resolved, load_config=cfg)
            for item in readiness:
                for issue in item['gaps']:
                    errors.append(f"步骤 {item['step_id']} / {issue['field']}：{issue['message']}")
        except (ValidationError, PermissionDenied):
            errors.append('接口来源不存在或跨项目，请重新选择项目接口')
    main_steps = [s for s in steps if not s.is_setup]
    if not main_steps:
        errors.append('场景没有启用的业务步骤，无法压测')

    # --- 步骤权重 ---
    # weight 字段目前所有引擎都不消费（业务步骤每轮按顺序等次执行），
    # 静默忽略会让用户误以为请求按权重分配、报告次数与预期对不上，这里显式提示。
    weighted = [s.name for s in steps if (s.weight or 1) != 1]
    if weighted:
        warnings.append(f'步骤「{"、".join(weighted)}」配置了权重，但当前所有引擎均按顺序等次执行业务步骤，'
                        f'权重不会生效（不影响每步骤被调用的次数）')

    # --- 压力参数 ---
    from ..engines.base import build_load_profile
    try:
        _, planned = build_load_profile(cfg)
    except Exception as exc:  # noqa: BLE001
        errors.append(f'压力策略配置非法：{exc}')
        planned = 0

    if planned <= 0:
        errors.append('压测时长必须大于 0')
    elif planned > settings.PERF_MAX_DURATION:
        errors.append(f'压测时长 {int(planned)}s 超过平台上限 {settings.PERF_MAX_DURATION}s')

    peak = _estimate_peak(cfg)
    if peak > settings.PERF_MAX_CONCURRENCY:
        errors.append(f'峰值并发 {peak} 超过平台上限 {settings.PERF_MAX_CONCURRENCY}')
    if model == 'RPS':
        target_rps = int(cfg.get('target_rps') or 0)
        if target_rps <= 0:
            errors.append('RPS 模式下目标 RPS 必须大于 0')
        elif target_rps > settings.PERF_MAX_TARGET_RPS:
            errors.append(f'目标 RPS {target_rps} 超过平台上限 {settings.PERF_MAX_TARGET_RPS}')
    if model == 'RAMPING' and not (cfg.get('stages') or []):
        errors.append('阶梯加压模式必须配置至少一个阶段')

    # --- 目标地址 ---
    base_url = (resolved['env_config'].get('base_url') or '').strip()
    hosts = set()
    for step in steps:
        url = step.url or ''
        if getattr(step, 'protocol', 'HTTP') == 'WEBSOCKET':
            from .websocket_steps import websocket_url
            try:
                full = websocket_url(url, base_url)
            except ValidationError:
                errors.append('WebSocket 目标地址无效，请使用环境同一服务的 WS(S) 地址')
                continue
            host = (urlparse(full).hostname or '').lower()
            if host:
                hosts.add(host)
            continue
        full = url if url.startswith(('http://', 'https://')) else f'{base_url.rstrip("/")}/{url.lstrip("/")}'
        if not url.startswith(('http://', 'https://')) and not base_url:
            errors.append(f'步骤「{step.name}」使用相对路径，但环境未配置 base_url')
            continue
        host = (urlparse(full).hostname or '').lower()
        if host:
            hosts.add(host)
    # Authentication endpoints are deliberately same-origin relative paths.
    if (scenario.get_runtime_config() or {}).get('auth_profile') and base_url:
        auth_host = (urlparse(base_url).hostname or '').lower()
        if auth_host:
            hosts.add(auth_host)

    forbidden = [h.lower() for h in (settings.PERF_FORBIDDEN_HOSTS or [])]
    for host in hosts:
        if any(host == f or host.endswith('.' + f) for f in forbidden):
            errors.append(f'目标主机 {host} 在平台禁止压测名单中')
    self_hosts = {h.lower() for h in (settings.ALLOWED_HOSTS or []) if h not in ('*', '')}
    hit_self = hosts & self_hosts
    if hit_self:
        warnings.append(f'目标主机 {", ".join(sorted(hit_self))} 疑似平台自身，'
                        f'压测可能导致平台服务不可用，请确认后再执行')

    # --- 并发执行数 ---
    running = PerfExecution.objects.filter(status__in=PerfExecution.ACTIVE_STATUSES).count()
    if running >= settings.PERF_MAX_CONCURRENT_EXECUTIONS:
        errors.append(f'当前已有 {running} 个压测在执行，'
                      f'达到平台上限 {settings.PERF_MAX_CONCURRENT_EXECUTIONS}，请稍后再试')
    if scenario.has_active_execution():
        errors.append('该场景已有正在执行的压测任务，请先停止或等待其结束')

    # --- CSV 变量 ---
    for item in resolved['variables']:
        if (item.get('type') or '').upper() == 'CSV' and not item.get('data_file_id'):
            errors.append(f'变量「{item.get("name")}」为 CSV 类型但未选择数据文件')
        elif (item.get('type') or '').upper() == 'CSV' and str(item.get('data_file_id')) not in resolved['csv_data']:
            errors.append('CSV 变量引用的文件不存在、不可访问或不属于当前项目')

    # --- multipart 文件字段 ---
    # 已引用但文件丢失 → error（请求体会与用户预期不符）；
    # 导入自接口测试的占位（file_id 为空）→ warning（不阻断，仅提示未补传）。
    from ..models import PerfDataFile
    for step in steps:
        for item in (step.files or []):
            if not isinstance(item, dict) or not (item.get('field') or '').strip():
                continue
            file_id = item.get('file_id')
            if not file_id:
                warnings.append(f'步骤「{step.name}」的文件字段「{item.get("field")}」'
                                f'尚未上传文件，压测时不会携带该文件')
                continue
            exists = PerfDataFile.objects.filter(
                id=file_id, project_id=scenario.project_id, file_type='UPLOAD').exists()
            if not exists:
                errors.append(f'步骤「{step.name}」的文件字段「{item.get("field")}」'
                              f'引用的文件不存在或已被删除，请重新选择')

    # --- 引擎 ---
    if scenario.engine == 'LOCUST':
        from ..engines import locust_available
        if not locust_available():
            errors.append('Locust 引擎不可用，请先在服务器安装 locust 或改用内置引擎')
    elif scenario.engine == 'JMETER':
        from ..engines import jmeter_available
        if not jmeter_available():
            errors.append('JMeter 引擎不可用，请先在服务器安装 java + jmeter，'
                          '或设置环境变量 JMETER_BIN 指向 jmeter 可执行文件')
    elif scenario.engine == 'K6':
        from ..engines import k6_available, validate_k6_snapshot
        from .k6_execution import K6ExecutionError
        if not k6_available():
            errors.append('K6 引擎不可用，请配置 K6_BIN 指向本地 k6 程序')
        try:
            _private_root()
            snapshot = build_snapshot(scenario, load_config=cfg, _resolved_environment=resolved)
            snapshot['load_config'] = cfg
            errors.extend(validate_k6_snapshot(snapshot))
        except K6ExecutionError as exc:
            errors.append(str(exc))

    # --- 引擎与压力模型兼容性 ---
    # Locust/JMeter 引擎只实现了固定并发模型：RAMPING/RPS/SPIKE 配置在执行时会被
    # 静默忽略（Locust 仍按固定并发、JMeter 仍生成普通线程组），导致报告的请求次数
    # 与并发数同用户输入严重不符。与其产出误导性结果，不如在预检直接拦截；
    # 脚本模式（上传 .jmx）走 _preflight_script，不受此限制。
    if scenario.engine in ('LOCUST', 'JMETER') and model != 'CONCURRENCY':
        engine_name = {'LOCUST': 'Locust', 'JMETER': 'JMeter'}[scenario.engine]
        model_name = {'RAMPING': '阶梯加压', 'RPS': '固定 RPS', 'SPIKE': '尖峰冲击'}.get(model, model)
        errors.append(f'{engine_name} 引擎目前仅支持「固定并发」压力模型（当前为{model_name}），'
                      f'请改用内置引擎，或将压力模型改为固定并发')

    # --- 容量预估 ---
    estimated_requests = int(peak * planned) if model != 'RPS' else int(
        int(cfg.get('target_rps') or 0) * planned)
    request_plan = {}
    if scenario.engine == 'K6':
        try:
            raw_rounds = float(cfg.get('iterations_per_vu') or 0)
            rounds = int(raw_rounds) if raw_rounds.is_integer() and raw_rounds > 0 else 0
        except (TypeError, ValueError, OverflowError):
            rounds = 0  # Invalid round counts are rejected by validate_k6_snapshot above.
        if model == 'CONCURRENCY' and rounds:
            estimated_requests = peak * rounds * len(main_steps)
            setup_requests = peak * sum(1 for step in steps if step.is_setup)
            http_requests = estimated_requests + setup_requests
            request_plan = {
                'estimate_kind': 'fixed_iterations',
                'estimated_setup_requests': setup_requests,
                'estimated_http_requests': http_requests,
                'request_estimate_note': (
                    f'完整执行时计划业务请求 {estimated_requests:,} 次，前置请求 {setup_requests:,} 次，'
                    f'合计 {http_requests:,} 次 HTTP 请求；提前停止、超时或前置失败会减少实际请求数。'),
            }
            if (scenario.get_runtime_config() or {}).get('auth_profile'):
                request_plan.update(estimate_kind='auth_dependent', estimated_setup_requests=None,
                    estimated_http_requests=None,
                    request_estimate_note=(f'计划业务步骤调用 {estimated_requests:,} 次；认证失败可能减少调用，'
                        '登录、刷新及认证后重试产生额外 HTTP 请求，实际次数以分阶段运行统计为准。'))
        else:
            request_plan = {
                'estimate_kind': 'time_heuristic',
                'estimated_setup_requests': None,
                'estimated_http_requests': None,
                'request_estimate_note': (
                    '按每个虚拟用户每秒 1 次业务请求粗估；实际随接口耗时和等待变化，'
                    '包含前置的总请求数以运行报告为准。'),
            }
        if any(step.execution_policy or step.protocol == 'WEBSOCKET' for step in steps):
            from .execution_policy import request_plan as bounded_plan
            try:
                counts = bounded_plan([{'execution_policy': step.execution_policy, 'protocol': step.protocol,
                    'is_setup': step.is_setup} for step in steps], peak, rounds)
            except ValidationError:
                counts = None  # Strict snapshot validation already reports the invalid policy.
            if counts:
                request_plan['execution_groups'] = counts['groups']
                has_daily = any(not step.is_setup and not step.execution_policy for step in steps)
                if rounds or not has_daily:
                    estimated_requests = counts['business']
                    request_plan.update(estimate_kind='policy_upper_bound',
                        estimated_setup_requests=counts['setup_http'] + counts['setup_websocket'],
                        estimated_http_requests=counts['http'] + counts['setup_http'],
                        estimated_websocket_sessions=counts['websocket'] + counts['setup_websocket'],
                        request_estimate_note=(f'业务步骤调用上限 {counts["business"]:,} 次；'
                            f'HTTP 调用上限 {counts["http"] + counts["setup_http"]:,} 次，'
                            f'WebSocket 会话上限 {counts["websocket"] + counts["setup_websocket"]:,} 次。'
                            '按固定参与用户与每用户次数计算；间隔、依赖失败、前置失败或截止会减少调用。认证及刷新重试另计。'))
                else:
                    request_plan.update(estimate_kind='time_heuristic_with_group_caps', estimated_http_requests=None,
                        estimated_websocket_sessions=None,
                        request_estimate_note='按时长的日常请求数只是启发估计；专项组另列固定参与人数和尝试次数上限，认证及刷新重试另计。')
    if estimated_requests > 2_000_000:
        warnings.append(f'预计产生约 {estimated_requests:,} 次请求，'
                        f'原始明细文件可能达数百 MB，请关注磁盘空间')
    recovery_config = (scenario.get_runtime_config() or {}).get('resource_recovery') or {}
    if recovery_config:
        from .reminder_recovery import MAX_ATTEMPTS, configured_store
        try:
            configured_store().require_daemon()
        except Exception:
            errors.append('提醒恢复守护进程未就绪，当前不能启动提醒写入组')
        binding = next((s for s in steps if s.pk == recovery_config.get('put_step_id')), None)
        if binding and binding.execution_policy:
            policy = binding.execution_policy
            count = policy.get('vu_end', 0) - policy.get('vu_start', 1) + 1
            if type(count) is int and count > 0:
                estimated_requests += count
                if type(request_plan.get('estimated_http_requests')) is int:
                    request_plan['estimated_http_requests'] += count
                request_plan.update(recovery_identity_reads=count, recovery_attempt_limit=count*MAX_ATTEMPTS)
                request_plan['request_estimate_note'] = (request_plan.get('request_estimate_note', '')
                    + f' 提醒组额外计入 {count} 次本人身份核验；启动另读 1 次能力。执行结束后的恢复最多 {count*MAX_ATTEMPTS} 次请求，独立统计且不计入压测吞吐。')
    # 内置/Locust 单机能力有限；JMeter 依赖 JVM 与机器配置，不在 preflight 判定上限
    if scenario.engine in ('BUILTIN', 'LOCUST') and peak > 800:
        warnings.append(f'峰值并发 {peak} 已接近单机内置/Locust 引擎能力上限，'
                        f'实测 TPS 可能受压力机自身 CPU 限制，建议关注报告中的压力机水位')

    return {
        'passed': not errors,
        'errors': errors,
        'warnings': warnings,
        'resolved_environment': public_resolution(resolved),
        'estimated': {
            'peak_concurrency': peak,
            'planned_duration': round(planned, 2),
            'estimated_requests': estimated_requests,
            'target_hosts': sorted(hosts),
            'step_count': len(main_steps),
            **request_plan,
        },
    }


def _preflight_script(scenario, script_ref):
    """脚本模式的体检：护栏改成从 .jmx 里读线程组，而不是读平台 load_config。

    如果这里不解析脚本，平台就彻底失去了并发/时长/禁压主机三道护栏——
    用户上传一个 5000 线程直压生产库的 .jmx，平台会一路放行。
    脚本里用 ${__P(...)} 参数化的部分无法静态求值，降级为 warning 而非 error，
    否则所有参数化脚本都会被一刀切拒绝。
    """
    from ..models import PerfExecution
    from . import jmx_inspect

    errors, warnings = [], []

    if scenario.engine != 'JMETER':
        errors.append('仅 JMeter 引擎支持上传脚本模式')

    from ..engines import jmeter_available
    if not jmeter_available():
        errors.append('JMeter 引擎不可用，请先在服务器安装 java + jmeter，'
                      '或设置环境变量 JMETER_BIN 指向 jmeter 可执行文件')

    jmx_path = (script_ref or {}).get('jmx_path')
    meta = jmx_inspect.inspect_jmx_file(jmx_path)
    if not meta.get('valid'):
        errors.append(meta.get('error') or '.jmx 解析失败')
        peak, planned, hosts = 0, 0, []
    else:
        peak = meta.get('total_threads')
        planned = meta.get('max_duration')
        hosts = meta.get('hosts') or []

        if peak is None:
            warnings.append('脚本线程数使用了 ${__P(...)} 等动态表达式，'
                            '平台无法静态校验并发上限，请自行确认不会超过 '
                            f'{settings.PERF_MAX_CONCURRENCY}')
            peak = 0
        elif peak > settings.PERF_MAX_CONCURRENCY:
            errors.append(f'脚本线程总数 {peak} 超过平台上限 {settings.PERF_MAX_CONCURRENCY}')

        if not planned:
            warnings.append('脚本未启用调度器（scheduler）或未设置 duration，'
                            '压测将按循环次数自行结束，平台时长上限无法生效')
            planned = 0
        elif planned > settings.PERF_MAX_DURATION:
            errors.append(f'脚本压测时长 {planned}s 超过平台上限 {settings.PERF_MAX_DURATION}s')

        forbidden = [h.lower() for h in (settings.PERF_FORBIDDEN_HOSTS or [])]
        for host in hosts:
            if any(host == f or host.endswith('.' + f) for f in forbidden):
                errors.append(f'脚本目标主机 {host} 在平台禁止压测名单中')
        self_hosts = {h.lower() for h in (settings.ALLOWED_HOSTS or []) if h not in ('*', '')}
        hit_self = set(hosts) & self_hosts
        if hit_self:
            warnings.append(f'脚本目标主机 {", ".join(sorted(hit_self))} 疑似平台自身，'
                            f'压测可能导致平台服务不可用，请确认后再执行')
        if not hosts:
            warnings.append('未能从脚本中静态解析出目标主机（可能全部使用变量），'
                            '平台无法校验禁压名单，请自行确认压测目标')
        for ds in (meta.get('csv_datasets') or []):
            fname = ds.get('filename') or ''
            if fname and not os.path.isabs(fname):
                warnings.append(f'脚本引用了相对路径的 CSV 数据集「{fname}」，'
                                f'执行时工作目录可能不同导致读取失败，建议改为绝对路径')

    # --- 并发执行数（与场景模式共用同一套闸门）---
    running = PerfExecution.objects.filter(status__in=PerfExecution.ACTIVE_STATUSES).count()
    if running >= settings.PERF_MAX_CONCURRENT_EXECUTIONS:
        errors.append(f'当前已有 {running} 个压测在执行，'
                      f'达到平台上限 {settings.PERF_MAX_CONCURRENT_EXECUTIONS}，请稍后再试')
    if scenario.has_active_execution():
        errors.append('该场景已有正在执行的压测任务，请先停止或等待其结束')

    return {
        'passed': not errors,
        'errors': errors,
        'warnings': warnings,
        'estimated': {
            'peak_concurrency': peak,
            'planned_duration': round(float(planned or 0), 2),
            'estimated_requests': int(peak * (planned or 0)),
            'target_hosts': sorted(hosts),
            'step_count': meta.get('sampler_count', 0) if meta.get('valid') else 0,
            'mode': 'script',
            'script_name': (script_ref or {}).get('data_file_name', ''),
        },
    }


def _estimate_peak(cfg):
    model = cfg.get('model', 'CONCURRENCY')
    if model == 'RAMPING':
        return max([int(s.get('target') or 0) for s in (cfg.get('stages') or [])] or [0])
    if model == 'SPIKE':
        return max(int(cfg.get('spike_concurrency') or 0), int(cfg.get('baseline_concurrency') or 0))
    if model == 'RPS':
        cap = int(cfg.get('max_concurrency') or 0)
        return cap if cap > 0 else int(cfg.get('target_rps') or 0)
    return max(int(cfg.get('concurrency') or 0), 0)


# ====================================================================== #
# 主进程侧：创建 + 拉起
# ====================================================================== #
def artifact_dir_for(execution_id):
    return os.path.join('perf-testing', 'executions', str(execution_id))


def abs_artifact_dir(execution):
    return os.path.join(settings.MEDIA_ROOT, execution.artifact_dir or artifact_dir_for(execution.id))


def create_execution(scenario, user=None, trigger_type='MANUAL', scheduled_task=None,
                     script_ref=None, load_config=None, environment_overrides=None,
                     _resolved_environment=None):
    """创建执行记录并冻结快照（不启动子进程）。

    script_ref: 引擎执行模式与脚本引用，形如
        {'mode': 'script', 'data_file_id': 12, 'data_file_name': 'plan.jmx',
         'jmx_path': '<MEDIA_ROOT>/perf-testing/datafiles/202607/plan.jmx'}
    jmx_path 必须由 views.resolve_script_ref 从 PerfDataFile 反查得到，
    禁止直接采信调用方传入的路径（详见该函数的安全说明）。
    为空字典时引擎按「场景生成」模式工作（JMETER 由步骤现场生成 .jmx）。
    """
    from ..models import PerfExecution

    snapshot = build_snapshot(scenario, load_config=load_config, user=user,
                              environment_overrides=environment_overrides,
                              _resolved_environment=_resolved_environment)
    from rest_framework.exceptions import ValidationError
    from .execution_policy import validate_policies
    policy_errors = validate_policies(snapshot)
    if policy_errors:
        raise ValidationError({'execution_policy': policy_errors})
    from . import reminder_recovery
    try:
        recovery_store = reminder_recovery.prepare_managed(snapshot)
    except reminder_recovery.RecoveryError as exc:
        raise ValidationError({'resource_recovery': str(exc)}) from exc
    if any(step.get('protocol', 'HTTP') not in ('HTTP', 'WEBSOCKET', 'SSE') or
           (step.get('protocol') in ('WEBSOCKET', 'SSE') and snapshot.get('engine') != 'K6') for step in snapshot['steps']):
        raise ValidationError('步骤协议无效；WebSocket 步骤仅支持 K6')
    private_k6 = snapshot.get('engine') == 'K6'
    load_snapshot = dict(snapshot['load_config'])
    steps_snapshot = snapshot['steps']
    if recovery_store:
        load_snapshot['_resource_recovery'] = {'version': 1, 'kind': 'portfolio_reminder'}
    if private_k6:
        from ..engines import k6_version
        from ..engines.k6_engine import ADAPTER_VERSION
        from .k6_execution import SCHEMA_VERSION
        _private_root()
        snapshot['k6_version'] = k6_version()
        snapshot['k6_adapter_version'] = ADAPTER_VERSION
        snapshot['script_ref'] = script_ref or {}
        snapshot['perf_targets'] = getattr(scenario, 'perf_targets', {}) or {}
        load_snapshot.update(_private_snapshot_version=SCHEMA_VERSION, _engine='K6')
        load_snapshot['_environment_sources'] = snapshot['environment_sources']
        load_snapshot['_account_pool'] = snapshot.get('account_pool')
        profile = (snapshot.get('runtime_config') or {}).get('auth_profile')
        if profile:
            load_snapshot['_auth_profile'] = {key: deepcopy(profile[key]) for key in
                ('mode', 'transport', 'max_attempts', 'refresh_on_status') if key in profile}
        from .reporter import safe_request_path
        steps_snapshot = [
            {**{key: step.get(key) for key in ('id', 'order', 'name', 'protocol', 'method', 'enabled', 'is_setup')},
             'request_path': safe_request_path(step.get('url'))}
            for step in snapshot['steps']
        ]
        from .websocket_steps import public_websocket_step
        steps_snapshot = [public_websocket_step(original) if original.get('protocol') == 'WEBSOCKET' else public
                          for original, public in zip(snapshot['steps'], steps_snapshot)]
        from .execution_policy import public_policies
        policies = public_policies(snapshot['steps'])
        for index, step in enumerate(steps_snapshot):
            if index in policies:
                step['execution_policy'] = policies[index]
        if profile:
            from .auth_profiles import profile_steps
            steps_snapshot += [{**{key: step[key] for key in ('id', 'name', 'method', 'is_setup', 'auth_phase')},
                                'request_path': safe_request_path(step.get('url'))}
                               for step in profile_steps(profile)]
    execution = PerfExecution.objects.create(
        scenario=scenario,
        project=scenario.project,
        execution_no=PerfExecution.generate_execution_no(),
        trigger_type=trigger_type,
        scheduled_task=scheduled_task,
        account_pool_version_id=(snapshot.get('account_pool') or {}).get('version_id'),
        status='PENDING',
        load_snapshot=load_snapshot,
        steps_snapshot=steps_snapshot,
        script_ref=script_ref or {},
        executed_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
        worker_host=_hostname(),
        resource_recovery=({'version': 1, 'kind': 'portfolio_reminder', 'observed': False, 'state': 'PREPARING'}
                           if recovery_store else {}),
    )
    if private_k6:
        from .k6_execution import save_snapshot
        try:
            snapshot['execution_id'] = execution.id
            if recovery_store:
                plan = reminder_recovery.build_plan(snapshot, execution.id, settings.SECRET_KEY,
                                                    lineage=recovery_store.lineage())
                snapshot = reminder_recovery.freeze_snapshot(snapshot, plan)
                identity = snapshot['steps'][next(i for i,s in enumerate(snapshot['steps']) if s['id']=='reminder:identity')]
                public = {key: identity[key] for key in ('id','name','protocol','method','enabled','is_setup','execution_policy')}
                public['request_path'] = '/api/v1/me'
                position = next(i for i,s in enumerate(execution.steps_snapshot) if s['id']==plan['config']['put_step_id'])
                public['execution_policy'] = deepcopy(execution.steps_snapshot[position].get('execution_policy') or {})
                execution.steps_snapshot.insert(position,public)
                execution.save(update_fields=['steps_snapshot'])
            save_snapshot(_private_root(), execution.id, snapshot)
            if recovery_store:
                recovery_store.create(plan,reminder_recovery.digest(reminder_recovery.encode(snapshot)))
                execution.resource_recovery = recovery_store.public(execution.id)
                execution.save(update_fields=['resource_recovery'])
        except Exception:
            execution.status = 'FAILED'
            execution.error_message = '私密执行快照保存失败；未启动压测'
            execution.end_time = timezone.now()
            execution.save(update_fields=['status', 'error_message', 'end_time'])
            raise
    execution.artifact_dir = artifact_dir_for(execution.id)
    execution.save(update_fields=['artifact_dir'])
    os.makedirs(abs_artifact_dir(execution), exist_ok=True)
    return execution


def spawn_execution(execution):
    """启动独立子进程执行压测。

    start_new_session=True 让子进程脱离 Django 的进程组：
    否则 Ctrl+C 关掉 runserver 会连带把正在跑的压测一起杀掉，
    执行记录会永远卡在 RUNNING。
    """
    log_path = os.path.join(abs_artifact_dir(execution), 'run.log')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    manage_py = os.path.join(settings.BASE_DIR, 'manage.py')
    cmd = [sys.executable, manage_py, 'run_perf_execution', str(execution.id)]

    # 强制子进程 stdout/stderr 用 UTF-8：Windows 默认 cp936(GBK)，而 run_log 接口
    # 与报告页面按 UTF-8 读取，中文日志会全部变成乱码（历史缺陷）。
    child_env = dict(os.environ)
    child_env['PYTHONIOENCODING'] = 'utf-8'
    child_env['PYTHONUTF8'] = '1'
    if _is_k6_execution(execution):
        child_env['PERF_PRIVATE_ROOT'] = str(_private_root())

    try:
        with open(log_path, 'ab') as log_fh:
            proc = start_process(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(settings.BASE_DIR),
                env=child_env,
            )
    except Exception as exc:  # noqa: BLE001
        execution.status = 'FAILED'
        execution.error_message = ('K6 子进程启动失败，请检查执行记录和引擎状态'
                                   if _is_k6_execution(execution) else f'子进程启动失败：{exc}')
        execution.end_time = timezone.now()
        execution.save(update_fields=['status', 'error_message', 'end_time'])
        raise

    execution.process_pid = proc.pid
    execution.heartbeat_at = timezone.now()
    execution.save(update_fields=['process_pid', 'heartbeat_at'])
    logger.info('压测 %s 子进程已启动 pid=%s', execution.execution_no, proc.pid)
    return execution


def start_execution(scenario, user=None, trigger_type='MANUAL', skip_preflight=False,
                    scheduled_task=None, script_ref=None, load_config=None,
                    environment_overrides=None):
    """一步到位：校验 → 建记录 → 起进程。返回 (execution, preflight_result)。"""
    # script_ref 必须一起带进 preflight，否则脚本模式会被按场景规则误判为"没有步骤"
    load_config = deepcopy(load_config)
    from rest_framework.exceptions import ValidationError, PermissionDenied
    try:
        resolved = _resolve_environment_inputs(scenario, user, environment_overrides)
    except (ValidationError, PermissionDenied) as exc:
        return None, {'passed': False, 'errors': [str(exc.detail)], 'warnings': [], 'estimated': {}}
    check = preflight(scenario, script_ref=script_ref, load_config=load_config,
                      _resolved_environment=resolved)
    has_non_http = scenario.steps.exclude(protocol='HTTP').exists()
    if (scenario.engine == 'K6' or has_non_http or not skip_preflight) and not check['passed']:
        return None, check
    if scenario.engine == 'K6':
        from django.db.models import Q
        from ..models import PerfExecution
        from .k6_execution import FileLease, K6ExecutionError, K6LeaseBusy
        try:
            active = PerfExecution.objects.filter(status__in=PerfExecution.ACTIVE_STATUSES).filter(
                Q(load_snapshot___engine='K6') | Q(scenario__engine='K6'))
            deadline = time.monotonic() + 3.0

            def check_admission():
                if active.exists():
                    raise K6ExecutionError('已有 K6 任务待执行或运行中；本地版串行执行以避免账号池冲突')
                if time.monotonic() >= deadline:
                    raise K6LeaseBusy('已有 K6 任务正在启动或运行；本地版串行执行以避免账号池冲突')

            def acquire_lease(guard, name):
                # Both locks share one budget; only OS contention is retried.
                while True:
                    check_admission()
                    lease = FileLease(_private_root(), name)
                    try:
                        lease.__enter__()
                    except K6LeaseBusy:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise
                        time.sleep(min(0.05, remaining))
                        continue
                    guard.callback(lease.__exit__, None, None, None)
                    check_admission()
                    return

            with ExitStack() as start_guard:
                acquire_lease(start_guard, 'start')
                with ExitStack() as run_guard:
                    acquire_lease(run_guard, 'run')
                    execution = create_execution(scenario, user=user, trigger_type=trigger_type,
                                                 scheduled_task=scheduled_task, script_ref=script_ref,
                                                 load_config=load_config, _resolved_environment=resolved)
                spawn_execution(execution)
                return execution, check
        except K6ExecutionError as exc:
            return None, dict(check, passed=False, errors=[str(exc)])
    execution = create_execution(scenario, user=user, trigger_type=trigger_type,
                                 scheduled_task=scheduled_task, script_ref=script_ref,
                                 load_config=load_config, _resolved_environment=resolved)
    spawn_execution(execution)
    return execution, check


def debug_run(scenario, max_steps=50, user=None, script_ref=None, environment_overrides=None):
    """K6 以固定 1 VU/1 轮创建异步执行；其他引擎保留同步调试详情。"""
    engine = scenario.engine or 'BUILTIN'
    if engine == 'K6':
        # Arbitrary scripts cannot inherit the generated scenario's load bound.
        saved_ref = (getattr(scenario, 'runtime_config', None) or {}).get('script_ref')
        for ref in (script_ref, saved_ref):
            if ref and (not isinstance(ref, dict)
                        or str(ref.get('mode') or '').strip().lower() not in ('', 'scenario')):
                return {'engine': 'K6', 'execution': None, 'preflight': {
                    'passed': False, 'errors': ['K6 调试仅支持页面场景，不支持脚本模式'],
                    'warnings': [], 'estimated': {},
                }}
        execution, check = start_execution(scenario, user=user, environment_overrides=environment_overrides, load_config={
            'model': 'CONCURRENCY', 'concurrency': 1, 'iterations_per_vu': 1,
            'duration': min(60, settings.PERF_MAX_DURATION),
            'ramp_up': 0, 'max_requests': 0, '_purpose': 'debug',
        })
        return {'engine': 'K6', 'execution': execution, 'preflight': check}

    snapshot = build_snapshot(scenario, user=user, environment_overrides=environment_overrides)
    # 调试不看压力策略，只关心请求本身是否正确
    snapshot['load_config'] = dict(snapshot.get('load_config') or {})

    if engine == 'JMETER':
        from ..engines import jmeter_debug_run
        result = jmeter_debug_run(snapshot, max_steps=max_steps)
        return {
            'scenario_id': scenario.id,
            'scenario_name': scenario.name,
            'engine': engine,
            'passed': result.get('passed', False),
            'jmx_valid': result.get('jmx_valid', False),
            'total_steps': len(result.get('steps', [])),
            'failed_count': len([i for i in result.get('steps', []) if not i.get('ok')]),
            'error': result.get('error', ''),
            'steps': result.get('steps', []),
        }

    # 内置 / Locust：真实发 1 个请求，验证 URL/鉴权/断言是否通畅
    import asyncio

    from ..engines.builtin import debug_run as _engine_debug_run

    started = time.time()
    results = asyncio.run(_engine_debug_run(snapshot, max_steps=max_steps))
    return {
        'scenario_id': scenario.id,
        'scenario_name': scenario.name,
        'engine': engine,
        'total_steps': len(results),
        'passed': all(item.get('success') for item in results) if results else False,
        'failed_count': len([i for i in results if not i.get('success')]),
        'elapsed_ms': round((time.time() - started) * 1000, 2),
        'steps': results,
    }


def stop_execution(execution, graceful=True):
    """请求停止压测：先 SIGTERM，宽限期后仍存活则 SIGKILL。"""
    from ..models import PerfExecution

    if execution.status in PerfExecution.FINAL_STATUSES:
        return False, '执行已结束，无需停止'

    if _is_k6_execution(execution):
        # The worker may finish after the view reads RUNNING. Compare the current
        # database state atomically so a late stop can never overwrite a result.
        updated = PerfExecution.objects.filter(
            pk=execution.pk, status__in=PerfExecution.ACTIVE_STATUSES,
        ).update(status='STOPPING')
        if not updated:
            execution.refresh_from_db(fields=['status'])
            if execution.status in PerfExecution.FINAL_STATUSES:
                return False, '执行已结束，无需停止'
            return False, '执行状态已变化，未请求停止'
        execution.status = 'STOPPING'
        # Let the owning worker stop and collect its child. Killing the Windows
        # parent directly would bypass result collection even with Job protection.
        return True, '已请求停止；正在等待 K6 退出并保存已有结果'

    execution.status = 'STOPPING'
    execution.save(update_fields=['status'])

    pid = execution.process_pid
    if not pid:
        _finalize_orphan(execution, 'STOPPED', '未记录子进程 PID，直接标记为已停止')
        return True, '未找到子进程，已直接标记为停止'

    if not _pid_alive(pid):
        _finalize_orphan(execution, 'STOPPED', '子进程已不存在')
        return True, '子进程已退出，已标记为停止'

    try:
        os.kill(pid, signal.SIGTERM if graceful else signal.SIGKILL)
    except ProcessLookupError:
        _finalize_orphan(execution, 'STOPPED', '子进程已退出')
        return True, '子进程已退出，已标记为停止'
    except PermissionError as exc:
        return False, f'无权限停止子进程 {pid}：{exc}'

    if not graceful:
        return True, '已强制终止'

    # 宽限等待：子进程需要时间收尾（写统计、生成报告）
    deadline = time.time() + STOP_GRACE_SECONDS
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True, '压测已优雅停止'
        time.sleep(0.5)

    try:
        os.kill(pid, signal.SIGKILL)
        logger.warning('压测 %s 子进程 %s 优雅停止超时，已强制终止', execution.execution_no, pid)
    except ProcessLookupError:
        pass
    return True, '优雅停止超时，已强制终止'


def _finalize_orphan(execution, status, message):
    execution.status = status
    execution.error_message = (execution.error_message or '') + message
    execution.end_time = timezone.now()
    if execution.start_time:
        execution.duration = round((execution.end_time - execution.start_time).total_seconds(), 2)
    execution.save(update_fields=['status', 'error_message', 'end_time', 'duration'])


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
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            return False


def _hostname():
    try:
        import socket
        return socket.gethostname()[:100]
    except Exception:  # noqa: BLE001
        return ''


# ====================================================================== #
# WebSocket 推送
#
# 这里必须异步化，教训来自实测：Redis 未启动时 channels_redis 的
# group_send 会阻塞约 40 秒才抛错。如果直接在采样回调里同步调用，
# 一次 6 秒的压测会被拖成 4 分钟，而且拖慢的是"发压侧"，
# 采出来的 TPS 数据直接失真 —— 监控通道绝不能反过来影响被监控对象。
#
# 方案：单后台守护线程 + 有界队列 + 熔断。
# - 队列满 → 直接丢最旧的（实时曲线丢几个点无所谓，前端会轮询兜底）
# - 连续失败 3 次 → 整个进程内熔断，后续推送变成空操作
# ====================================================================== #
_PUSH_QUEUE_SIZE = 200
_PUSH_FAILURE_THRESHOLD = 3
_PUSH_SLOW_SECONDS = 3.0


class _PushWorker:
    """把 WebSocket 推送搬到后台线程，保证发压主线程永不阻塞。"""

    def __init__(self):
        self._queue = None
        self._thread = None
        self._lock = threading.Lock()
        self._failures = 0
        self._disabled = False
        self._warned = False

    def _ensure_started(self):
        if self._thread is not None:
            return
        with self._lock:
            if self._thread is not None:
                return
            self._queue = queue.Queue(maxsize=_PUSH_QUEUE_SIZE)
            self._thread = threading.Thread(
                target=self._loop, name='perf-ws-push', daemon=True)
            self._thread.start()

    def submit(self, execution_id, payload):
        if self._disabled:
            return
        self._ensure_started()
        item = (execution_id, payload)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # 丢最旧的一条，保证最新状态优先送达
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(item)
            except (queue.Empty, queue.Full):
                pass

    def flush(self, timeout=3.0):
        """收尾时给终态推送一个有界的送达机会，超时即放弃。"""
        if self._disabled or self._queue is None:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0:
                return
            time.sleep(0.05)

    # ------------------------------------------------------------------ #
    def _loop(self):
        while True:
            execution_id, payload = self._queue.get()
            try:
                if not self._disabled:
                    self._send(execution_id, payload)
            finally:
                self._queue.task_done()

    def _send(self, execution_id, payload):
        started = time.time()
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            layer = get_channel_layer()
            if layer is None:
                self._trip('channel layer 未配置')
                return
            async_to_sync(layer.group_send)(
                f'perf_execution_{execution_id}',
                {'type': 'execution_update', **payload},
            )
            self._failures = 0
            if time.time() - started > _PUSH_SLOW_SECONDS:
                logger.warning('WebSocket 推送耗时 %.1fs，channel layer 可能不健康',
                               time.time() - started)
        except Exception as exc:  # noqa: BLE001 - 推送失败绝不能影响压测
            self._failures += 1
            logger.debug('WebSocket 推送失败(%s/%s): %s',
                         self._failures, _PUSH_FAILURE_THRESHOLD, exc)
            if self._failures >= _PUSH_FAILURE_THRESHOLD:
                self._trip(str(exc))

    def _trip(self, reason):
        self._disabled = True
        if not self._warned:
            self._warned = True
            logger.warning('WebSocket 推送已熔断（前端将降级为轮询）：%s', reason)
        # 熔断后清空积压，避免后台线程继续消费无效任务
        try:
            while True:
                self._queue.get_nowait()
                self._queue.task_done()
        except queue.Empty:
            pass


_push_worker = _PushWorker()


def push_update(execution_id, payload):
    """向 WebSocket 组推送执行更新（非阻塞）。

    channels 不可用 / Redis 不通时静默熔断，前端降级为轮询 /realtime/。
    """
    _push_worker.submit(execution_id, payload)


def flush_push_updates(timeout=3.0):
    """等待推送队列排空（有界），用于执行收尾。"""
    _push_worker.flush(timeout=timeout)


# ====================================================================== #
# 采样点落库：独立后台线程
#
# 教训（实测 + 线上日志）：BUILTIN 引擎以 asyncio.run 驱动，on_sample 回调
# 跑在事件循环所在线程里。Django 禁止在异步上下文中同步访问数据库
# （SynchronousOnlyOperation: "You cannot call this from an async context"），
# 直接 bulk_create 会被拦截，采样点全丢、时序曲线为空（与历史缺陷同源）。
#
# 方案：与 WS 推送一致，落库也搬到独立后台线程；事件循环只负责攒批并交付，
# 绝不碰 DB。交付的是独立列表（调用方立即换一个新列表），避免多线程竞争
# pending。队列满丢最旧一批；终态时 flush 有界等待排空。
#
# 门控与 Django 自身保持一致：仅当「处于异步上下文 且 未设置
# DJANGO_ALLOW_ASYNC_UNSAFE」时才走后台线程；其余情况（同步上下文，或测试
# 环境放开 ALLOW_ASYNC_UNSAFE）直接落库——既修复线上，又不破坏测试事务回滚。
# ====================================================================== #
_DB_FLUSH_QUEUE_SIZE = 50


class _DbFlushWorker:
    """把采样点批量落库搬到后台线程，保证发压的事件循环线程永不碰数据库。"""

    def __init__(self):
        self._queue = None
        self._thread = None
        self._lock = threading.Lock()
        self._disabled = False

    def _ensure_started(self):
        if self._thread is not None:
            return
        with self._lock:
            if self._thread is not None:
                return
            self._queue = queue.Queue(maxsize=_DB_FLUSH_QUEUE_SIZE)
            self._thread = threading.Thread(
                target=self._loop, name='perf-db-flush', daemon=True)
            self._thread.start()

    def submit(self, execution_id, batch):
        """交付一批已构建好的 PerfMetricSample 实例（独立列表，调用方不再持有）。"""
        if self._disabled or not batch:
            return
        self._ensure_started()
        try:
            self._queue.put_nowait((execution_id, batch))
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait((execution_id, batch))
            except (queue.Empty, queue.Full):
                pass

    def flush(self, timeout=5.0):
        """收尾时给终态落库一个有界的送达机会，超时即放弃。"""
        if self._queue is None:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0:
                return
            time.sleep(0.05)

    def _loop(self):
        while True:
            execution_id, batch = self._queue.get()
            try:
                if not self._disabled and batch:
                    from ..models import PerfExecution, PerfMetricSample
                    PerfMetricSample.objects.bulk_create(batch, batch_size=200)
                    PerfExecution.objects.filter(id=execution_id).update(
                        heartbeat_at=timezone.now())
            except Exception as exc:  # noqa: BLE001 - 数据库抖动不能中断压测
                logger.warning('采样点落库失败: %s', exc)
            finally:
                self._queue.task_done()


_db_flush_worker = _DbFlushWorker()


def _in_async_context():
    """与 django.utils.asyncio.async_unsafe 同款判据：当前线程是否有运行中事件循环。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _async_db_allowed():
    """与 Django utils.asyncio.async_unsafe 同款放开开关（调用时读取，非冻结）。"""
    return bool(os.environ.get('DJANGO_ALLOW_ASYNC_UNSAFE'))


# ====================================================================== #
# 子进程侧：真正执行
# ====================================================================== #
def _db_sample_interval(planned_duration, sample_interval):
    """自适应入库粒度：长压测不能按秒落库，否则单次执行几万行采样点。"""
    if planned_duration <= 300:
        target = 1
    elif planned_duration <= 1800:
        target = 5
    else:
        target = 10
    return max(target, sample_interval)


def run_execution(execution_id, stdout=None):
    """K6 holds an OS lease for its entire worker lifetime, including shutdown."""
    from ..models import PerfExecution
    from .k6_execution import FileLease, K6ExecutionError
    execution = PerfExecution.objects.get(id=execution_id)
    if not _is_k6_execution(execution):
        return _run_execution(execution_id, stdout)
    if execution.status != 'PENDING':
        if execution.status == 'STOPPING' and not execution.start_time:
            _finalize_orphan(execution, 'STOPPED', '已在开始发压前停止')
        return execution
    try:
        with FileLease(_private_root(), 'run'):
            claimed = PerfExecution.objects.filter(id=execution_id, status='PENDING').update(
                status='PREPARING', heartbeat_at=timezone.now())
            if not claimed:
                execution.refresh_from_db()
                return execution
            return _run_execution(execution_id, stdout)
    except K6ExecutionError as exc:
        execution.refresh_from_db()
        if execution.status in ('PENDING', 'PREPARING'):
            _finalize_orphan(execution, 'FAILED', str(exc))
        return execution


def _run_execution(execution_id, stdout=None):
    """子进程主逻辑。异常一律落到 FAILED，绝不留下 RUNNING 悬挂记录。"""
    from ..models import PerfExecution, PerfMetricSample, PerfRequestStat

    def emit(msg):
        line = f'[{timezone.localtime():%H:%M:%S}] {msg}'
        if stdout:
            stdout.write(line)
        else:
            print(line, flush=True)

    _close_stale_connections()
    execution = PerfExecution.objects.select_related('scenario', 'project').get(id=execution_id)
    scenario = execution.scenario
    private_k6 = _is_k6_execution(execution)
    if private_k6 and execution.status == 'STOPPING':
        _finalize_orphan(execution, 'STOPPED', '已在开始发压前停止')
        return execution

    art_dir = abs_artifact_dir(execution)
    os.makedirs(art_dir, exist_ok=True)
    raw_csv_path = os.path.join(art_dir, 'raw.csv.gz')

    execution.status = 'PREPARING'
    execution.heartbeat_at = timezone.now()
    execution.save(update_fields=['heartbeat_at'] if private_k6 else ['status', 'heartbeat_at'])
    push_update(execution.id, {'status': 'PREPARING', 'message': '正在准备压测环境'})

    snapshot = _execution_snapshot(execution)
    if private_k6:
        _validate_frozen_k6_version(snapshot)
        from . import reminder_recovery
        if reminder_recovery.is_managed(execution):
            try:
                store = reminder_recovery.configured_store()
                store.require_daemon()
                reminder_recovery.verify_frozen(store, execution.id, snapshot)
            except Exception as exc:
                from .k6_execution import K6ExecutionError
                raise K6ExecutionError('提醒恢复意图或守护进程未就绪，禁止发送业务请求') from exc

    sla_config = snapshot.get('sla_config') or {}
    sample_interval = max(int((snapshot.get('runtime_config') or {}).get('sample_interval') or 1), 1)
    planned = float((snapshot['load_config'] or {}).get('_planned_duration') or 0)
    db_interval = _db_sample_interval(planned, sample_interval)

    state = {
        'engine': None,
        'pending': [],
        'last_db_offset': -db_interval,
        'last_k6_seen_seq': 0,
        'k6_persistence': {'observed': 0, 'persisted': 0, 'write_failures': 0,
                           'heartbeat_failures': 0, 'aborted': False, 'discarded': 0,
                           'last_persisted_seq': 0, 'retry_needed': False} if private_k6 else None,
        'aborted_by_sla': False,
        'cpu_warned': False,
        'peak_cpu': 0.0,
    }

    from ..engines import get_engine_class
    from ..engines.base import EngineError
    from .sla import BreachDetector, evaluate

    if private_k6:
        from . import k6_thresholds
        detector = None
    else:
        detector = BreachDetector(sla_config, sample_interval=sample_interval)

    def on_log(level, message):
        emit(f'{level} {message}')

    def on_sample(sample):
        """采样回调：K6 逐点事务落库，其他引擎保留抽稀；推送与 SLA 判定。"""
        offset = int(sample.get('ts_offset') or 0)
        cpu = float(sample.get('cpu_percent') or 0)
        state['peak_cpu'] = max(state['peak_cpu'], cpu)

        push_update(execution.id, {'status': 'RUNNING', 'sample': sample})

        from .k6_samples import sample_payload
        payload = sample_payload(sample) if private_k6 else {}
        seq = payload.get('sample_seq', 0)
        retain = (not seq or seq > state['last_k6_seen_seq']) if private_k6 else offset - state['last_db_offset'] >= db_interval
        if retain:
            state['last_k6_seen_seq'] = max(seq, state['last_k6_seen_seq'])
            state['last_db_offset'] = offset
            if private_k6:
                state['k6_persistence']['observed'] += 1
            row = PerfMetricSample(
                execution_id=execution.id,
                ts_offset=offset,
                active_users=int(sample.get('active_users') or 0),
                tps=float(sample.get('tps') or 0),
                avg_rt=float(sample.get('avg_rt') or 0),
                p90_rt=float(sample.get('p90_rt') or 0),
                p95_rt=float(sample.get('p95_rt') or 0),
                p99_rt=float(sample.get('p99_rt') or 0),
                error_rate=float(sample.get('error_rate') or 0),
                total_requests=int(sample.get('total_requests') or 0),
                cpu_percent=cpu,
                memory_mb=float(sample.get('memory_mb') or 0),
                k6_payload=payload,
            )
            if not private_k6 or len(state['pending']) < 4:
                state['pending'].append(row)
            else:
                persistence = state['k6_persistence']
                persistence['discarded'] += 1
                persistence.setdefault('first_discarded_seq', seq)
                persistence['last_discarded_seq'] = seq
                _stop_for_sample_failure(state)

        # 批量落库 + 心跳，合并成一次数据库往返。
        # BUILTIN 引擎在 asyncio 事件循环线程里回调本函数，Django 禁止异步上下文
        # 同步访问数据库。门控与 Django 一致：仅当「异步上下文 且 未放开
        # ALLOW_ASYNC_UNSAFE」时交付给后台落库线程；其余直接落库（同步上下文本就
        # 安全；测试放开 ALLOW_ASYNC_UNSAFE 时落在事务内、可回滚）。
        if private_k6 or len(state['pending']) >= 10 or offset % 10 == 0:
            if _in_async_context() and not _async_db_allowed():
                batch = state['pending']
                state['pending'] = []
                _db_flush_worker.submit(execution.id, batch)
            else:
                _flush(state, execution)

        # K6 reports summed process/container CPU across cores; this legacy
        # machine-percentage threshold cannot establish load-generator capacity.
        if not private_k6 and cpu >= settings.PERF_LOAD_GEN_CPU_WARN and not state['cpu_warned']:
            state['cpu_warned'] = True
            emit(f'WARNING 压力机 CPU 达 {cpu}%，实测 TPS 可能已受压力机自身限制，'
                 f'报告数据可信度下降')

        if detector.check(sample) and not state['aborted_by_sla']:
            state['aborted_by_sla'] = True
            reason = detector.reason or 'SLA 指标持续超限'
            emit(f'ERROR SLA 持续超限，触发自动熔断：{reason}')
            push_update(execution.id, {'status': 'STOPPING', 'message': f'SLA 熔断：{reason}'})
            if state['engine']:
                state['engine'].stop()

    # ---- 信号处理：把 SIGTERM 转成引擎的优雅停止 ---- #
    def _on_term(signum, frame):  # noqa: ARG001
        emit('收到停止信号，正在优雅停止（等待在途请求收尾）')
        if state['engine']:
            state['engine'].stop()

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)

    result = None
    error_message = ''
    status = 'COMPLETED'

    def mark_started():
        now = timezone.now()
        if private_k6:
            updated = PerfExecution.objects.filter(id=execution.id, status='PREPARING').update(
                status='RUNNING', start_time=now, heartbeat_at=now)
            if not updated:
                return False
        execution.status = 'RUNNING'
        execution.start_time = now
        execution.heartbeat_at = now
        if not private_k6:
            execution.save(update_fields=['status', 'start_time', 'heartbeat_at'])
        push_update(execution.id, {'status': 'RUNNING', 'message': '压测开始',
                                  'start_time': now.isoformat()})
        emit(f'压测开始：{snapshot.get("scenario_name", scenario.name)}（引擎 {snapshot.get("engine")}）')
        return True

    def read_execution_status():
        # Every monitor thread owns and closes its own Django connection.
        close_old_connections()
        try:
            return PerfExecution.objects.values_list('status', flat=True).get(id=execution.id)
        finally:
            close_old_connections()

    try:
        if private_k6:
            detector = k6_thresholds.BreachDetector(sla_config, snapshot.get('steps') or [], sample_interval)
        engine_class = get_engine_class(snapshot.get('engine') or 'BUILTIN')
        engine = engine_class(snapshot, on_sample=on_sample, on_log=on_log,
                              raw_csv_path=raw_csv_path)
        state['engine'] = engine

        if private_k6:
            from .k6_execution import drive_engine
            result, engine_error = drive_engine(engine, read_execution_status, mark_started)
            if engine_error:
                raise engine_error
        else:
            engine.prepare()
            mark_started()
            engine.run()
            result = engine.collect()

    except EngineError as exc:
        status = 'FAILED'
        from .k6_execution import safe_engine_error
        error_message = safe_engine_error(exc) if private_k6 else str(exc)
        emit(f'ERROR 配置校验失败：{error_message}')
    except Exception as exc:  # noqa: BLE001 - 兜底，任何异常都要落终态
        status = 'FAILED'
        error_message = (f'K6 执行异常：{type(exc).__name__}' if private_k6
                         else f'{type(exc).__name__}: {exc}')
        if not private_k6:
            logger.exception('压测 %s 执行异常', execution.execution_no)
        emit(f'ERROR 执行异常：{error_message}')
    finally:
        _close_stale_connections()
        _flush(state, execution, final=True)
        if private_k6:
            from .k6_samples import persistence_evidence, write_persistence_evidence
            evidence = persistence_evidence(state)
            try:
                write_persistence_evidence(art_dir, evidence)
            except OSError:
                emit('ERROR 无法写入采样完整性本地证据；本次结果需要人工核对')
            if evidence['stopped_due_to_storage']:
                emit(f'ERROR 采样写入连续失败，已停止发压；缺失 {evidence["missing_count"]} 条采样，已有原始明细保留')
        # 排空事件循环期间交付给后台线程的采样批，确保进程退出前全部落库
        _db_flush_worker.flush(timeout=5.0)

    # ---- 收尾 ---- #
    execution.refresh_from_db(fields=['status'])
    if status != 'FAILED':
        if state['aborted_by_sla']:
            status = 'STOPPED' if private_k6 else 'COMPLETED'
        elif execution.status == 'STOPPING' or (private_k6 and (result or {}).get('stop_reason')):
            status = 'STOPPED'

    if private_k6 and state['k6_persistence']['aborted']:
        from .k6_samples import persistence_evidence
        persistence = persistence_evidence(state)
        status = 'FAILED' if not persistence['complete'] else 'STOPPED'
        error_message = (f'采样持久化连续失败，已停止发压；缺失 {persistence["missing_count"]} 条采样。'
                         '已收到的请求统计和原始明细已保留，整体 SLA 未评估。' if not persistence['complete']
                         else '采样持久化连续失败导致提前停止；待写采样后来已恢复，业务负载未完整执行，整体 SLA 未评估。')

    summary = (result or {}).get('summary') or {}
    if summary:
        if snapshot.get('engine') == 'K6':
            summary.setdefault('peak_load_gen_cpu', None)
            summary['data_trustworthy'] = None
            summary['load_generator_capacity_verified'] = False
        else:
            summary['peak_load_gen_cpu'] = round(state['peak_cpu'], 1)
            summary['data_trustworthy'] = state['peak_cpu'] < settings.PERF_LOAD_GEN_CPU_WARN
        summary['stop_reason'] = (result or {}).get('stop_reason') or ''
        summary['aborted_by_sla'] = state['aborted_by_sla']
        summary['raw_rows'] = (result or {}).get('raw_rows') or 0

    if private_k6:
        from .k6_samples import persistence_evidence
        summary['sample_persistence'] = persistence_evidence(state)
        summary['sample_data_incomplete'] = not summary['sample_persistence']['complete']
        summary['sample_missing_count'] = summary['sample_persistence']['missing_count']
        if state['k6_persistence']['aborted']:
            summary['stop_reason'] = '采样持久化连续失败触发保护停止，保留已收到的请求结果'
        summary['execution_status'] = status
        summary['completion_reason'] = k6_thresholds.completion_reason(
            summary, snapshot.get('load_config') or {}, status)
        summary['execution_complete'] = not summary['completion_reason']
        summary['sla_implementation'] = k6_thresholds.SOURCE
        if state['aborted_by_sla']:
            summary['sla_abort'] = detector.evidence
            summary['stop_reason'] = f'SLA 持续超限：{detector.reason}'

    sla_result, sla_detail = ('NOT_EVALUATED', [])
    if private_k6 and sla_config.get('enabled'):
        sla_result, sla_detail = k6_thresholds.evaluate(
            sla_config, summary, steps=snapshot.get('steps') or [],
            load=snapshot.get('load_config') or {}, execution_status=status)
    elif summary and sla_config.get('enabled'):
        sla_result, sla_detail = evaluate(sla_config, summary)

    stats = (result or {}).get('request_stats') or []
    if stats:
        PerfRequestStat.objects.bulk_create([
            PerfRequestStat(
                execution_id=execution.id,
                step_name=s.get('step_name', '')[:200],
                method=s.get('method', '')[:10],
                url=(s.get('url') or '')[:1000],
                total=s.get('total', 0),
                success=s.get('success', 0),
                failed=s.get('failed', 0),
                error_rate=s.get('error_rate', 0),
                tps=s.get('tps', 0),
                avg_rt=s.get('avg_rt', 0),
                min_rt=s.get('min_rt', 0),
                max_rt=s.get('max_rt', 0),
                p50_rt=s.get('p50_rt', 0),
                p90_rt=s.get('p90_rt', 0),
                p95_rt=s.get('p95_rt', 0),
                p99_rt=s.get('p99_rt', 0),
                sent_bytes=s.get('sent_bytes', 0),
                recv_bytes=s.get('recv_bytes', 0),
                error_detail=s.get('error_detail', []),
            ) for s in stats
        ], batch_size=200)

    execution.status = status
    execution.summary = summary
    execution.sla_result = sla_result
    execution.sla_detail = sla_detail

    # 验收目标评估（perf_targets）：基于场景的 perf_targets 字段，
    # 判定执行结果是否"通过"。与 SLA（实时熔断）不同，验收是事后判定。
    from .targets_eval import evaluate_targets
    perf_targets = (snapshot.get('perf_targets', {}) if private_k6
                    else getattr(scenario, 'perf_targets', None) or {})
    if private_k6 and state['k6_persistence']['aborted']:
        execution.verdict = 'NOT_EVALUATED'
        execution.verdict_details = []
    elif perf_targets and stats:
        verdict, verdict_details = evaluate_targets(perf_targets, stats, summary)
        execution.verdict = verdict
        execution.verdict_details = verdict_details

    execution.error_message = (error_message or '')[:5000]
    execution.end_time = timezone.now()
    # 引擎上报的时长也统一 round，防御未来新引擎直接回传浮点噪声
    # （历史数据曾出现 300.8600000000000136 这类值导致前端显示异常）
    engine_duration = (result or {}).get('duration')
    execution.duration = round(engine_duration, 2) if engine_duration else (
        round((execution.end_time - execution.start_time).total_seconds(), 2)
        if execution.start_time else 0)
    execution.heartbeat_at = timezone.now()
    execution.save()

    # 紧跟终态回写任务计数：报告生成可能耗时数秒，放在其后会让
    # 「执行已结束但任务统计还是旧值」的窗口肉眼可见
    _update_task_stats(execution)

    # 报告生成失败不应改变执行结论
    try:
        from .reporter import generate_report
        report_path = generate_report(execution)
        if report_path:
            execution.report_url = report_path
            execution.save(update_fields=['report_url'])
    except Exception as exc:  # noqa: BLE001
        logger.warning('压测 %s 报告生成失败: %s', execution.execution_no, exc)
        emit(f'WARNING 报告生成失败：{exc}')

    push_update(execution.id, {
        'status': status,
        'message': '压测结束',
        'summary': summary,
        'sla_result': sla_result,
        'finished': True,
    })
    request_label = '业务请求' if snapshot.get('engine') == 'K6' else '请求'
    rate_label = '业务 RPS' if snapshot.get('engine') == 'K6' else 'TPS'
    if snapshot.get('engine') == 'K6' and summary.get('websocket'):
        request_label = '业务步骤'
        rate_label = '业务步骤/秒'
    emit(f'压测结束：{status}，共 {summary.get("total_requests", 0)} 次{request_label}，'
         f'{rate_label} {summary.get("tps", 0)}，错误率 {summary.get("error_rate", 0)}%，SLA {sla_result}')

    # 终态推送值得等一小会儿，但必须有界，否则进程退不掉
    flush_push_updates(timeout=3.0)

    _notify_if_needed(execution)
    return execution


def _flush(state, execution, *, final=False):
    """批量写采样点 + 更新心跳。

    注意：run_execution 内的模型导入是局部的，本模块级函数必须在自身作用域导入，
    否则会因 NameError 被上方 except 吞掉，导致采样点与心跳从未真正落库
    （历史缺陷：时序曲线全空 + 长压测被僵尸回收误判）。
    """
    from ..models import PerfExecution, PerfMetricSample

    if state.get('k6_persistence') is not None:
        return _flush_k6(state, execution, final=final)
    pending = state.get('pending') or []
    try:
        if pending:
            PerfMetricSample.objects.bulk_create(pending, batch_size=200)
            state['pending'] = []
        PerfExecution.objects.filter(id=execution.id).update(heartbeat_at=timezone.now())
    except Exception as exc:  # noqa: BLE001 - 数据库抖动不能中断压测
        logger.warning('采样点落库失败: %s', exc)
        state['pending'] = []


def _resolve_task(execution):
    """定位拉起本次执行的定时任务。

    优先用外键；老数据（外键为空）才退回按场景反查，此时同场景多任务只能取其一。
    """
    if execution.scheduled_task_id:
        return execution.scheduled_task
    if execution.trigger_type != 'SCHEDULED':
        return None
    return execution.scenario.scheduled_tasks.filter(status__in=['ACTIVE', 'PAUSED']).first()


def _update_task_stats(execution):
    """把压测结果回写到定时任务的成败计数。

    调度器只负责「拉起」，真正的成败要等压测跑完才知道，因此计数在这里落。
    判定口径：执行完成且 SLA 未失败才算成功。
    """
    task = _resolve_task(execution)
    if task is None:
        return
    success = execution.status == 'COMPLETED' and execution.sla_result != 'FAILED'
    if success:
        error = ''
    elif execution.error_message:
        error = execution.error_message
    else:
        error = f'执行 {execution.execution_no} 状态 {execution.status}，SLA {execution.sla_result}'
    try:
        task.refresh_from_db()
        if success:
            task.success_count += 1
        else:
            task.fail_count += 1
        task.last_error = error[:2000]
        task.save(update_fields=['success_count', 'fail_count', 'last_error', 'updated_at'])
    except Exception as exc:  # noqa: BLE001 - 统计回写失败不应影响执行结果
        logger.warning('定时压测任务统计回写失败: %s', exc)


def _notify_if_needed(execution):
    """定时任务触发的执行按配置发通知。手动执行不打扰。"""
    if execution.trigger_type != 'SCHEDULED':
        return
    task = _resolve_task(execution)
    if not task or task.notify_on == 'NEVER' or not task.notify_channels:
        return
    if task.notify_on == 'ON_SLA_FAIL' and execution.sla_result != 'FAILED':
        return
    try:
        from .notifier import send_execution_notification
        send_execution_notification(execution, task.notify_channels)
    except Exception as exc:  # noqa: BLE001
        logger.warning('压测通知发送失败: %s', exc)


def _stop_for_sample_failure(state):
    persistence = state['k6_persistence']
    if persistence['aborted']:
        return
    persistence['aborted'] = True
    logger.error('K6 sample storage retry limit reached; stopping load and preserving partial results')
    engine = state.get('engine')
    if engine:
        try:
            engine.stop()
        except Exception as exc:
            persistence['stop_error'] = type(exc).__name__
            logger.error('K6 stop after sample storage failure failed: %s', type(exc).__name__)


def _flush_k6(state, execution, *, final=False):
    """Single leased writer; atomic retry checks committed sample identities.

    A failed heartbeat is independent of a committed batch and never resubmits it.
    After three write failures stop load; only the final drain gets three more tries.
    """
    from django.db import transaction
    from ..models import PerfExecution, PerfMetricSample
    persistence = state['k6_persistence']
    pending = list(state['pending'])
    if pending and (not persistence['aborted'] or final):
        for attempt in range(3):
            original_ids = [row.pk for row in pending]
            try:
                with transaction.atomic():
                    # Reconcile uncertain completion before replay; the worker lease
                    # prevents another writer from creating this execution's samples.
                    existing = set()
                    if persistence['retry_needed']:
                        sequences = [row.k6_payload.get('sample_seq') for row in pending if row.k6_payload.get('sample_seq')]
                        if sequences:
                            existing = set(PerfMetricSample.objects.filter(
                                execution_id=execution.id, k6_payload__sample_seq__in=sequences
                            ).values_list('k6_payload__sample_seq', flat=True))
                    missing = [row for row in pending if not row.k6_payload.get('sample_seq') or row.k6_payload['sample_seq'] not in existing]
                    if missing:
                        PerfMetricSample.objects.bulk_create(missing, batch_size=4)
                # Acknowledge only after transaction commit succeeds.
                del state['pending'][:len(pending)]
                persistence['persisted'] += len(pending)
                persistence['last_persisted_seq'] = max(
                    [persistence['last_persisted_seq']] + [row.k6_payload.get('sample_seq', 0) for row in pending])
                persistence['retry_needed'] = False
                break
            except Exception as exc:
                for row, previous_id in zip(pending, original_ids):
                    row.pk = previous_id
                persistence['write_failures'] += 1
                persistence['retry_needed'] = True
                logger.warning('K6 sample transaction failed (%s), attempt %s/3', type(exc).__name__, attempt + 1)
        else:
            _stop_for_sample_failure(state)
    try:
        with transaction.atomic():
            PerfExecution.objects.filter(id=execution.id).update(heartbeat_at=timezone.now())
    except Exception as exc:
        persistence['heartbeat_failures'] += 1
        logger.warning('K6 heartbeat update failed after independent sample transaction: %s', type(exc).__name__)
