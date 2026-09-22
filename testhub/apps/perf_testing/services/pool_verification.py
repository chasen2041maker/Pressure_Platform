"""Explicit one-round verification of frozen prepared requests; polling is read-only."""
from copy import deepcopy
from collections import Counter
import hashlib
import hmac
import json
import uuid

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError

from . import api_catalog
from .environments import require_project_access
from .k6_execution import load_snapshot
from .prepared_requests import validation_cached, validation_scope, memo_get, valid_contains_assertion
from .reminder_recovery import RecoveryError

PROOF_FIELDS = ('scenario_id', 'engine', 'load_config', 'sla_config', 'perf_targets',
                'variables', 'env_config', 'environment_sources', 'account_pool',
                'runtime_config', 'steps', 'csv_data')
COUNT_FIELDS = ('total', 'success', 'failed')


def _snapshot_hash(snapshot, entries=None):
    payload = {key: snapshot.get(key, {} if key == 'perf_targets' else None) for key in PROOF_FIELDS}
    if snapshot.get('upload_data'):
        payload['upload_data'] = snapshot['upload_data']
    prefix = b'prepared-verification-v1\0'
    managed = entries and any(entry.get('recovery_group') for entry in entries)
    if entries and (managed or any(entry.get('setup_dependencies') for entry in entries)):
        prefix = b'prepared-verification-v3\0' if managed else b'prepared-verification-v2\0'
        payload['prepared_dependencies'] = [{key: entry.get(key) for key in
            ('prepared_id', 'revision', 'step_id', 'definition_hash', 'setup_dependencies', *(['recovery_group'] if managed else []))}
            for entry in entries]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    return hmac.new(settings.SECRET_KEY.encode(), prefix + encoded, hashlib.sha256).hexdigest()


def _integer(value):
    return type(value) is int and value >= 0


def _result(entry, status='unverified', verdict='evidence_invalid', counts=None):
    return {'prepared_id': entry['prepared_id'], 'revision': entry['revision'],
            'status': status, 'verdict': verdict,
            **dict(zip(COUNT_FIELDS, counts or (None, None, None)))}


def _business_assertions(step, *, require_status=False):
    if step.get('protocol') == 'WEBSOCKET':
        from .websocket_steps import normalize_websocket_config
        try:
            config = normalize_websocket_config(step.get('websocket_config'))
            return not step.get('assertions') and all(command['assertions'] for command in config['commands'])
        except ValidationError:
            return False
    if step.get('protocol') == 'SSE':
        from .sse_steps import normalize_sse_config
        try:
            # A normalized SSE terminal must depend on asserted JSON events.
            # HTTP status/content-type are enforced by the stream transport.
            normalize_sse_config(step.get('sse_config'))
            return not step.get('assertions')
        except ValidationError:
            return False
    rules = step.get('assertions') or []
    business = any(valid_contains_assertion(rule) or (isinstance(rule, dict)
        and str(rule.get('type', '')).upper().replace('JSONPATH', 'JSON_PATH') == 'JSON_PATH'
        and 'expected' in rule and (rule.get('expr') or rule.get('json_path'))) for rule in rules)
    status = any(isinstance(rule, dict) and rule.get('type') == 'STATUS_CODE'
        and str(rule.get('expected', '')).isdigit() and 200 <= int(rule['expected']) < 300 for rule in rules)
    return business and (status or not require_status)


def _sse_evidence(summary, steps):
    ids = {step['id'] for step in steps if step.get('protocol') == 'SSE'}
    if not ids:
        return {}
    value = summary.get('sse')
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('missing SSE evidence')
    rows = value.get('stream_metrics')
    if not isinstance(rows, list) or len(rows) != len(ids):
        raise ValueError('missing or duplicated SSE steps')
    by_id = {}
    for row in rows:
        sid = row.get('step_id')
        if not _integer(sid) or sid not in ids or sid in by_id:
            raise ValueError('ambiguous SSE step evidence')
        by_id[sid] = row
    fields = ('started', 'completed', 'success', 'failed', 'incomplete')
    errors = []
    for item in [value, *rows]:
        counts = item.get('streams') or {}
        if (not all(_integer(counts.get(key)) for key in fields)
                or counts['completed'] != counts['success'] + counts['failed']
                or counts['started'] != counts['completed'] + counts['incomplete']
                or not all(_integer(item.get(key)) for key in ('events', 'bytes'))):
            raise ValueError('inconsistent SSE stream counts')
        for key, bound in (('first_event', counts['started']), ('completion', counts['completed'])):
            count = (item.get(key) or {}).get('count')
            if not _integer(count) or count > bound:
                raise ValueError('inconsistent SSE timing count')
        problems = item.get('errors')
        if not isinstance(problems, list):
            raise ValueError('missing SSE outcome evidence')
        grouped = Counter()
        for problem in problems:
            if (not isinstance(problem, dict) or problem.get('phase') not in ('preparation', 'transport', 'business')
                    or not isinstance(problem.get('reason'), str) or not problem['reason']
                    or not _integer(problem.get('count')) or problem['count'] == 0):
                raise ValueError('invalid SSE error evidence')
            key = (problem['phase'], problem['reason'])
            if key in grouped:
                raise ValueError('duplicated SSE error evidence')
            grouped[key] = problem['count']
        errors.append(grouped)
    if (any(value['streams'][key] != sum(row['streams'][key] for row in rows) for key in fields)
            or any(value[key] != sum(row[key] for row in rows) for key in ('events', 'bytes'))
            or any(value[key]['count'] != sum(row[key]['count'] for row in rows) for key in ('first_event', 'completion'))
            or errors[0] != sum(errors[1:], Counter())):
        raise ValueError('SSE aggregate differs from step evidence')
    return by_id


def _sse_success(step, row):
    if step.get('protocol') != 'SSE':
        return True
    if row is None or not _business_assertions(step):
        return False
    from .sse_steps import normalize_sse_config
    config = normalize_sse_config(step['sse_config'])
    counts = row['streams']
    return (all(counts[key] == expected for key, expected in (
        ('started', 1), ('completed', 1), ('success', 1), ('failed', 0), ('incomplete', 0)))
        and row['first_event']['count'] == row['completion']['count'] == 1 and not row['errors']
        and max(1, sum(rule['min_events'] for rule in config['rules'])) <= row['events'] <= config['max_events']
        and 0 < row['bytes'] <= config['max_total_bytes'])


def _websocket_success(summary, steps):
    """One round requires every declared frame, an authenticated session and actual close."""
    sockets = [step for step in steps if step.get('protocol') == 'WEBSOCKET']
    if not sockets:
        return True
    from .websocket_steps import normalize_websocket_config
    from .k6_websocket_metrics import STAGES
    value = summary.get('websocket')
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        return False
    for stage in STAGES:
        row = value.get(stage) or {}
        if (not all(_integer(row.get(key)) for key in ('started', 'completed', 'success', 'failed', 'incomplete'))
                or row['started'] != row['completed'] or row['completed'] != row['success']
                or row['failed'] or row['incomplete']):
            return False
    if any(value[key]['success'] != len(sockets) for key in ('sessions', 'connect', 'auth')):
        return False
    connections = value.get('connections') or {}
    if (connections.get('observed') is not True or type(connections.get('current')) is not int
            or connections['current'] != 0 or type(connections.get('unclosed')) is not int
            or connections['unclosed'] != 0 or not _integer(connections.get('peak')) or connections['peak'] != 1):
        return False
    expected = {}
    for step in sockets:
        try:
            config = normalize_websocket_config(step['websocket_config'])
        except ValidationError:
            return False
        for index, command in enumerate(config['commands']):
            expected[(step['id'], index)] = (command.get('event_type') or command['request']['action'],
                                           'event_wait' if command.get('event_type') else 'command')
    rows = value.get('command_metrics')
    if not isinstance(rows, list) or len(rows) != len(expected):
        return False
    seen = set()
    for row in rows:
        key = row.get('step_id'), row.get('command_index')
        if (not all(type(item) is int for item in key) or key not in expected or key in seen
                or (row.get('action'), row.get('latency_kind', 'command')) != expected[key]
                or row.get('method') != 'WEBSOCKET'
                or any(type(row.get(k)) is not int or row[k] != n for k, n in (('total', 1), ('success', 1), ('failed', 0)))):
            return False
        seen.add(key)
    return (value['events']['success'] == sum(kind == 'event_wait' for _, kind in expected.values())
            and value['commands']['success'] == sum(kind == 'command' for _, kind in expected.values()))


def _setup_references(entries):
    references = {}; keys = {}
    for entry in entries:
        dependencies = entry.get('setup_dependencies', [])
        if not isinstance(dependencies, list):
            raise ValueError('invalid setup association')
        seen = set()
        for reference in dependencies:
            if (not isinstance(reference, dict) or set(reference) != {
                    'step_id', 'key', 'source_key', 'revision', 'definition_hash'}
                    or not _integer(reference['step_id']) or reference['step_id'] == 0
                    or not _integer(reference['revision']) or reference['revision'] == 0
                    or any(not isinstance(reference[key], str) or not reference[key] for key in
                           ('key', 'source_key', 'definition_hash'))):
                raise ValueError('invalid setup reference')
            sid = reference['step_id']; key = reference['key']
            if sid in seen or (sid in references and references[sid] != reference) or (key in keys and keys[key] != sid):
                raise ValueError('ambiguous setup reference')
            seen.add(sid); references[sid] = reference; keys[key] = sid
    return references


def evaluate_evidence(batch, execution, snapshot, stats):
    """Require exact immutable input, stable step IDs, and persisted count agreement."""
    entries = batch.entries
    invalid = [_result(entry) for entry in entries]
    try:
        if (execution.project_id != batch.project_id or execution.scenario_id != batch.scenario_id
                or execution.executed_by_id != batch.created_by_id
                or snapshot.get('execution_id') != execution.id or snapshot.get('scenario_id') != batch.scenario_id
                or snapshot.get('engine') != 'K6'):
            return invalid
        managed = any(entry.get('recovery_group') for entry in entries)
        recovery_ok = True
        if managed:
            from . import prepared_recovery, reminder_recovery
            config = reminder_recovery.normalize_config(entries[0].get('recovery_group'))
            if (not config or any(entry.get('recovery_group') != config or entry.get('setup_dependencies') for entry in entries)
                    or not 2 <= len(entries) <= 3):
                return invalid
            base, recovery_state = prepared_recovery.original_snapshot(snapshot, execution.id)
            if base['runtime_config'].get('resource_recovery') != config:
                return invalid
            target_ids = {entry['step_id'] for entry in entries}
            required = {config['put_step_id'], config['delete_step_id']}
            if not required <= target_ids <= required | {config['get_step_id']}:
                return invalid
            proof = _snapshot_hash(base, entries)
            recovery_ok = (recovery_state.get('observed') is True and recovery_state.get('state') == 'RECOVERED'
                and all(type(recovery_state.get(key)) is int and recovery_state[key] == val for key, val in (
                    ('planned', 1), ('cleaned', 1), ('cancelled', 0), ('pending', 0), ('conflict', 0))))
        else:
            proof = _snapshot_hash(snapshot, entries)
        if any(not hmac.compare_digest(entry.get('snapshot_hash', ''), proof) for entry in entries):
            return invalid
        load = snapshot.get('load_config') or {}
        if load.get('concurrency') != 1 or load.get('iterations_per_vu') != 1 or load.get('_purpose') != 'debug':
            return invalid
        if snapshot.get('sla_config') or snapshot.get('perf_targets'):
            return invalid
        step_ids = [entry['step_id'] for entry in entries]
        setup_references = _setup_references(entries)
        setup_ids = set(setup_references)
        all_ids = set(step_ids) | setup_ids
        auxiliary_ids = set()
        if managed:
            auxiliary_ids = {config[key] for key in prepared_recovery.STEP_KEYS} | {'reminder:identity'}
            auxiliary_ids -= set(step_ids)
            all_ids |= auxiliary_ids
        steps = snapshot.get('steps') or []
        if (len(set(step_ids)) != len(entries) or set(step_ids) & setup_ids
                or {step['id'] for step in steps} != all_ids or len(steps) != len(all_ids)):
            return invalid
        step_map = {step['id']: step for step in steps}
        if any(not step.get('enabled') or bool(step.get('is_setup')) != (step['id'] in setup_ids) for step in steps):
            return invalid
        summary = execution.summary or {}
        sse_by_id = _sse_evidence(summary, steps)
        websocket_ok = _websocket_success(summary, steps)
        websocket_ids = {step['id'] for step in steps if step.get('protocol') == 'WEBSOCKET'}
        metrics = summary.get('step_metrics') or []
        profile = (snapshot.get('runtime_config') or {}).get('auth_profile') or {}
        auth_ids = {'auth:' + phase for phase in ('login', 'refresh') if profile.get(phase)}
        allowed_urls = {f'step:{sid}' for sid in all_ids} | {f'step:{sid}' for sid in auth_ids}
        if any(row.get('url') not in allowed_urls for row in stats):
            return invalid
        by_id = {}
        for metric in metrics:
            if metric.get('phase') not in ('business', 'setup'):
                if metric.get('step_id') not in auth_ids:
                    return invalid
                continue
            sid = metric.get('step_id')
            if (sid not in step_map or sid in by_id
                    or metric.get('phase') != ('setup' if sid in setup_ids else 'business')):
                return invalid
            by_id[sid] = metric
        complete = execution.status == 'COMPLETED'
        business_count = len(entries) + len(auxiliary_ids)
        counters_ok = all(type(summary.get(key)) is int and summary[key] == expected for key, expected in (
            ('completed_iterations', 1), ('distinct_vus', 1), ('business_incomplete', 0),
            ('http_incomplete', 0), ('setup_failed_vus', 0), ('auth_failed_vus', 0),
            ('business_total', business_count), ('business_started', business_count), ('total_requests', business_count)))
        counters_ok = counters_ok and all(_integer(summary.get(key)) for key in
            ('success_requests', 'failed_requests', 'http_total', 'http_started'))
        counters_ok = counters_ok and summary.get('success_requests', -1) + summary.get('failed_requests', -1) == business_count
        counters_ok = counters_ok and summary.get('http_total') == summary.get('http_started') and summary.get('http_total', -1) >= len(all_ids - websocket_ids)

        def counts_for(sid):
            step = step_map[sid]; metric = by_id.get(sid)
            persisted = [stat for stat in stats if stat.get('url') == f'step:{sid}']
            if metric is None and not persisted:
                return None
            if metric is None or len(persisted) != 1:
                raise ValueError('missing or duplicated step evidence')
            row = persisted[0]
            for item in (metric, row):
                if (item.get('step_name') != step['name'] or item.get('method') != ('WEBSOCKET' if sid in websocket_ids else step['method'])
                        or item.get('url') != f'step:{sid}' or not all(_integer(item.get(key)) for key in COUNT_FIELDS)
                        or item['total'] != item['success'] + item['failed']):
                    raise ValueError('inconsistent step evidence')
            counts = tuple(metric[key] for key in COUNT_FIELDS)
            if counts != tuple(row[key] for key in COUNT_FIELDS):
                raise ValueError('persisted counts disagree')
            if sid in sse_by_id and counts != tuple(sse_by_id[sid]['streams'][key]
                                                     for key in ('completed', 'success', 'failed')):
                raise ValueError('SSE outcome differs from persisted request counts')
            return counts

        dependency_results = {}
        auxiliary_counts = {sid: counts_for(sid) for sid in auxiliary_ids}
        if managed:
            recovery_ok = recovery_ok and all(counts_for(sid) == (1, 1, 0) for sid in all_ids)
            recovery_ok = recovery_ok and all(_business_assertions(step_map[sid], require_status=True)
                                               for sid in all_ids if sid != 'reminder:identity')
            counters_ok = counters_ok and summary.get('http_total') == 5
        for sid, reference in setup_references.items():
            counts = counts_for(sid)
            if counts is None or counts == (0, 0, 0):
                status = verdict = 'not_run'
            elif counts == (1, 0, 1):
                status = verdict = 'failed'
            elif counts != (1, 1, 0):
                status, verdict = 'unverified', 'evidence_invalid'
            elif not _business_assertions(step_map[sid], require_status=True):
                status, verdict = 'unverified', 'http_only'
            elif not _sse_success(step_map[sid], sse_by_id.get(sid)) or sid in websocket_ids and not websocket_ok:
                status, verdict = 'unverified', 'evidence_invalid'
            else:
                status = verdict = 'passed'
            # The catalog source key contains the template path, never a resolved URL or response.
            dependency_results[sid] = {'step_id': sid, 'source_key': reference['source_key'],
                'revision': reference['revision'], 'method': step_map[sid]['method'],
                'status': status, 'verdict': verdict, **dict(zip(COUNT_FIELDS, counts or (0, 0, 0)))}
        results = []
        observed_success = observed_failed = 0
        for entry in entries:
            sid = entry['step_id']; step = step_map[sid]
            counts = counts_for(sid)
            if counts is None:
                results.append(_result(entry, 'not_run', 'not_run', (0, 0, 0)))
                continue
            observed_success += counts[1]; observed_failed += counts[2]
            if counts == (0, 0, 0):
                results.append(_result(entry, 'not_run', 'not_run', counts))
            elif counts != (1, 1, 0):
                results.append(_result(entry, 'failed', 'failed', counts))
            elif not complete:
                results.append(_result(entry, 'unverified', 'incomplete', counts))
            elif not counters_ok:
                results.append(_result(entry, counts=counts))
            elif not recovery_ok:
                results.append(_result(entry, 'unverified', 'recovery_unconfirmed', counts))
            elif not all(dependency_results[ref['step_id']]['status'] == 'passed'
                         for ref in entry.get('setup_dependencies', [])):
                results.append(_result(entry, counts=counts))
            elif not _business_assertions(step):
                results.append(_result(entry, 'unverified', 'http_only', counts))
            elif not _sse_success(step, sse_by_id.get(sid)) or sid in websocket_ids and not websocket_ok:
                results.append(_result(entry, counts=counts))
            else:
                results.append(_result(entry, 'passed', 'passed', counts))
        observed_success += sum(value[1] for value in auxiliary_counts.values() if value is not None)
        observed_failed += sum(value[2] for value in auxiliary_counts.values() if value is not None)
        if complete and len(by_id) == len(all_ids) and (
                summary.get('success_requests') != observed_success or summary.get('failed_requests') != observed_failed):
            return invalid
        if complete and len(by_id) == len(all_ids) and (any(not _integer(row.get('total')) for row in stats)
                or sum(row['total'] for row in stats if row.get('url') not in {f'step:{sid}' for sid in websocket_ids}) != summary.get('http_total')):
            return invalid
        for entry, result in zip(entries, results):
            if entry.get('setup_dependencies'):
                result['dependency_results'] = [deepcopy(dependency_results[ref['step_id']])
                    for ref in entry['setup_dependencies']]
        return results
    except (KeyError, TypeError, ValueError, AttributeError, RecoveryError):
        return invalid


@validation_cached
def batch_summary(batch, user=None):
    if user is not None:
        require_project_access(batch.project_id, user)
    return memo_get(('verification_batch', batch.pk), lambda: _batch_summary(batch))


def _batch_summary(batch):
    execution = batch.execution
    result = {'id': batch.pk, 'execution_id': batch.execution_id,
              'monitor_url': f'/performance-testing/executions/{batch.execution_id}/monitor' if batch.execution_id else None,
              'status': batch.launch_state.lower(), 'launch_state': batch.launch_state.lower(),
              'results': [_result(entry, 'not_run', 'not_run', (0, 0, 0)) for entry in batch.entries],
              'error_code': batch.error_code or '', 'scope': 'one_vu_one_iteration_configured_response_assertions'}
    if execution is None:
        return result
    if execution.status not in ('COMPLETED', 'FAILED', 'STOPPED', 'TIMEOUT', 'ABORTED'):
        result['status'] = 'running' if execution.status in ('RUNNING', 'STOPPING') else 'pending'
        result['results'] = [_result(entry, verdict='pending') for entry in batch.entries]
        return result
    result['status'] = 'completed' if execution.status == 'COMPLETED' else 'failed'
    try:
        snapshot = load_snapshot(settings.PERF_PRIVATE_ROOT, execution.pk)
        stats = list(execution.request_stats.values('step_name', 'method', 'url', *COUNT_FIELDS))
        result['results'] = evaluate_evidence(batch, execution, snapshot, stats)
    except Exception:
        # Only a fixed diagnosis is public; private snapshots may contain credentials.
        result['results'] = [_result(entry) for entry in batch.entries]
        result['error_code'] = 'verification_evidence_unavailable'
    return result


@validation_cached
def verification_summary(row):
    from ..models import PerfPreparationBatch
    from . import prepared_requests
    try:
        prepared_requests.validate_current(row, require_passed=False)
        current_hash = prepared_requests.definition_hash(row)
    except Exception:
        return {'status': 'unverified', 'last_evidence': None}
    def latest_entries():
        result = {}
        for batch in PerfPreparationBatch.objects.filter(project_id=row.project_id).select_related('execution').order_by('-pk').iterator():
            for entry in batch.entries:
                result.setdefault(entry.get('prepared_id'), (batch, entry))
        return result
    latest = memo_get(('verification_latest_entries', row.project_id), latest_entries)
    if row.pk in latest:
        batch, entry = latest[row.pk]
        if entry.get('revision') != row.revision or entry.get('definition_hash') != current_hash:
            return {'status': 'unverified', 'last_evidence': None}
        data = batch_summary(batch)
        item = next(item for item in data['results'] if item['prepared_id'] == row.pk)
        evidence = {'batch_id': batch.pk, 'execution_id': batch.execution_id,
                    'protocol': row.request.get('protocol', 'HTTP'),
                    'verdict': item['verdict'], 'total': item['total'],
                    'success': item['success'], 'failed': item['failed']}
        if 'dependency_results' in item:
            evidence['dependency_results'] = deepcopy(item['dependency_results'])
        return {'status': item['status'] if item['status'] in ('passed', 'failed') else 'unverified',
                'last_evidence': evidence}
    return {'status': 'unverified', 'last_evidence': None}


def start_verification(project_id, selection, expected_catalog_version, request_key, user, *, confirm_writes=False):
    from ..models import PerfProject, PerfPreparedRequest, PerfPreparationBatch, PerfScenario, PerfScenarioStep
    from . import executor, prepared_requests
    require_project_access(project_id, user)
    try:
        request_key = str(uuid.UUID(request_key))
    except (TypeError, ValueError, AttributeError):
        raise api_catalog.CatalogInputError('验证请求必须包含唯一 request_key') from None
    if not isinstance(selection, list) or not 1 <= len(selection) <= api_catalog.MAX_BATCH or type(confirm_writes) is not bool:
        raise api_catalog.CatalogInputError('请选择要验证的接口')
    ids = []; revisions = {}
    for item in selection:
        if not isinstance(item, dict) or set(item) != {'id', 'revision'}:
            raise api_catalog.CatalogInputError('验证选择仅支持 id 和 revision')
        pk = api_catalog.bounded_integer(item['id'], 'id')
        revisions[pk] = api_catalog.bounded_integer(item['revision'], 'revision'); ids.append(pk)
    api_catalog.strict_ids(ids, 'selection')
    version = api_catalog.expected_version(expected_catalog_version)
    request_hash = api_catalog.digest({'selection': selection, 'version': version, 'confirm_writes': confirm_writes})
    with transaction.atomic():
        project = PerfProject.objects.select_for_update().get(pk=project_id)
        existing = PerfPreparationBatch.objects.filter(project=project, created_by=user, request_key=request_key).first()
        if existing:
            if not existing.entries or existing.entries[0].get('request_hash') != request_hash:
                raise api_catalog.CatalogConflict('request_key 已用于其他验证选择')
            return batch_summary(existing, user)
        current = api_catalog.latest_version(project_id)
        if not current or current.version != version:
            raise api_catalog.CatalogConflict()
        found = {row.pk: row for row in PerfPreparedRequest.objects.select_for_update().filter(project=project, pk__in=ids)}
        if len(found) != len(ids):
            raise api_catalog.CatalogInputError('接口模板不存在或不属于当前项目')
        rows = [found[pk] for pk in ids]
        with validation_scope(fresh=True):
            for row in rows:
                if row.revision != revisions[row.pk]:
                    raise api_catalog.CatalogConflict()
                prepared_requests.validate_current(row, require_passed=False)
                if row.status == 'blocked' or row.gaps:
                    raise api_catalog.CatalogInputError('选择中仍有待补齐配置的接口')
            setups = prepared_requests.expanded_setup_steps(rows)
            dependencies = {row.pk: prepared_requests.resolve_setup_steps(row) for row in rows}
            setup_definitions = {setup['key']: setup['reference'] for setup in setups}
            if (len(setup_definitions) != len(setups)
                    or set(setup_definitions) != {setup['key'] for values in dependencies.values() for setup in values}
                    or any(setup_definitions.get(setup['key']) != setup['reference']
                           for values in dependencies.values() for setup in values)):
                raise api_catalog.CatalogConflict('前置接口在冻结期间发生变化')
        if len({row.context_fingerprint for row in rows}) != 1:
            raise api_catalog.CatalogConflict('所选接口的环境或身份配置不一致')
        requests = [prepared_requests.step_kwargs(row) for row in rows]
        from . import prepared_recovery
        recovery_group = prepared_recovery.group(rows)
        if any(item.get('is_setup') or item.get('enabled') is False for item in requests):
            raise api_catalog.CatalogInputError('批量验证仅支持已启用的业务接口')
        from .websocket_steps import request_has_writes
        if not confirm_writes and any(request_has_writes(item)
                for item in requests + [setup['kwargs'] for setup in setups]):
            raise api_catalog.CatalogInputError('选择包含写操作，请明确确认后再验证')
        batch = PerfPreparationBatch.objects.create(project=project, created_by=user, request_key=request_key,
            entries=[], context_fingerprint=rows[0].context_fingerprint, launch_state='PENDING')
        scene = PerfScenario.objects.create(project=project, created_by=user, name=f'接口池验证 {batch.pk}',
            is_preparation=True, sla_config={}, perf_targets={}, **prepared_requests.scenario_kwargs(rows[0]))
        # Resolve mutable environments once; the hidden verification copy must not retarget later.
        from .environments import resolve_environment
        resolved = resolve_environment(scene, user=user)
        scene.environment_id = scene.global_environment_id = None
        scene.env_config = deepcopy(resolved['env_config'])
        scene.variables = deepcopy(resolved['variables'])
        scene.save(update_fields=['environment', 'global_environment', 'env_config', 'variables'])
        setup_steps = {}
        for index, setup in enumerate(setups):
            step = PerfScenarioStep.objects.create(scenario=scene, order=index, **deepcopy(setup['kwargs']))
            setup_steps[setup['key']] = {'step_id': step.pk, 'key': setup['key'],
                **{key: setup['reference'][key] for key in ('source_key', 'revision', 'definition_hash')}}
        entries = []
        recovery_associations = {}
        if recovery_group:
            _, recovery_associations = prepared_recovery.create_steps(scene, rows, user, verification=True)
        for index, (row, kwargs) in enumerate(zip(rows, requests)):
            step = recovery_associations.get(row.pk) or PerfScenarioStep.objects.create(
                scenario=scene, order=len(setups) + index, **deepcopy(kwargs))
            entries.append({'prepared_id': row.pk, 'revision': row.revision, 'source_key': row.source_key,
                'step_id': step.pk, 'definition_hash': prepared_requests.definition_hash(row), 'request_hash': request_hash,
                'setup_dependencies': [deepcopy(setup_steps[setup['key']]) for setup in dependencies[row.pk]]})
        if recovery_group:
            for entry in entries:
                entry['recovery_group'] = deepcopy(scene.runtime_config['resource_recovery'])
        load = {'model': 'CONCURRENCY', 'concurrency': 1, 'iterations_per_vu': 1,
                'duration': min(60, settings.PERF_MAX_DURATION), 'ramp_up': 0, 'max_requests': 0, '_purpose': 'debug'}
        expected = executor.build_snapshot(scene, load_config=load, user=user)
        proof = _snapshot_hash(expected, entries)
        for entry in entries:
            entry['snapshot_hash'] = proof
        batch.scenario = scene; batch.entries = entries
        batch.save(update_fields=['scenario', 'entries'])
    # Never start a worker inside an uncommitted transaction or from a GET request.
    try:
        require_project_access(project_id, user)
        with validation_scope(fresh=True):
            for entry in entries:
                current_row = PerfPreparedRequest.objects.get(pk=entry['prepared_id'], project_id=project_id)
                prepared_requests.validate_current(current_row, require_passed=False)
                if prepared_requests.definition_hash(current_row) != entry['definition_hash']:
                    raise api_catalog.CatalogConflict()
                current_dependencies = [{key: setup['reference'][key] for key in
                    ('source_key', 'revision', 'definition_hash')} | {'key': setup['key']}
                    for setup in prepared_requests.resolve_setup_steps(current_row)]
                frozen_dependencies = [{key: value for key, value in reference.items() if key != 'step_id'}
                    for reference in entry.get('setup_dependencies', [])]
                if current_dependencies != frozen_dependencies:
                    raise api_catalog.CatalogConflict()
    except Exception:
        batch.launch_state = 'REJECTED'
        batch.error_code = 'verification_definition_changed'
        batch.save(update_fields=['launch_state', 'error_code'])
        return batch_summary(batch, user)
    try:
        launched = executor.debug_run(scene, user=user)
        execution = launched.get('execution')
        batch.execution = execution
        batch.launch_state = 'STARTED' if execution else 'REJECTED'
        batch.error_code = '' if execution else 'verification_preflight_rejected'
    except Exception:
        execution = scene.executions.order_by('-pk').first()
        batch.execution = execution
        batch.launch_state = 'UNCERTAIN'
        batch.error_code = 'verification_launch_uncertain'
    batch.save(update_fields=['execution', 'launch_state', 'error_code'])
    return batch_summary(batch, user)
