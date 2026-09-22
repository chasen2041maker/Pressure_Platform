"""Explicit prepared reminder groups share the normal durable execution contract."""
from copy import copy, deepcopy
from types import SimpleNamespace

from rest_framework.exceptions import ValidationError

from . import reminder_recovery as recovery

FIELDS = {'version', 'kind', 'group_id', 'stock_code', 'max_resources'}
STEP_KEYS = ('put_step_id', 'receipt_step_id', 'get_step_id', 'delete_step_id')


def declaration(value: object, request: dict) -> dict:
    if value == {}:
        return {}
    try:
        recovery.check(isinstance(value, dict) and set(value) == FIELDS, 'prepared_recovery_fields')
        config = recovery.normalize_config({**value, **dict(zip(STEP_KEYS, (1, 2, 3, 4)))})
        recovery.check(request.get('protocol', 'HTTP') == 'HTTP'
            and request.get('method') in ('PUT', 'GET', 'DELETE')
            and request.get('url') == recovery.PREFIX + config['stock_code']
            and not request.get('params') and not request.get('files')
            and not request.get('is_setup') and request.get('enabled', True), 'prepared_recovery_target')
        if request['method'] == 'DELETE':
            recovery.check(request.get('body_type') == 'JSON'
                and recovery.strict_json(request.get('body', '')) == {}, 'prepared_recovery_delete_body')
        if request['method'] == 'PUT':
            body = recovery.strict_json(request.get('body', ''))
            recovery.check(request.get('body_type') == 'JSON' and isinstance(body, dict)
                and body.get('precondition') == {'mode': 'absent'}, 'prepared_recovery_put_precondition')
    except (recovery.RecoveryError, TypeError, ValueError, KeyError) as exc:
        raise ValidationError({'resource_recovery': str(exc) if isinstance(exc, recovery.RecoveryError)
                               else 'prepared_recovery_invalid'}) from None
    return {key: deepcopy(config[key]) for key in FIELDS}


def group(rows: list) -> dict:
    marked = [(row, declaration(row.preparation.get('resource_recovery', {}), row.request)) for row in rows]
    active = [(row, value) for row, value in marked if value]
    if not active:
        return {}
    value = active[0][1]
    methods = [row.request['method'] for row, _ in active]
    if (len(active) != len(rows) or not 2 <= len(rows) <= 3 or any(item != value for _, item in active)
            or len(set(methods)) != len(methods) or not {'PUT', 'DELETE'}.issubset(methods)
            or any(row.preparation.get('setup_steps') for row in rows)):
        raise ValidationError({'resource_recovery': '请选择同一提醒恢复组的保存、删除接口，可同时选择读取接口；本次仅验证这一组'})
    return value


def readiness_request(request: dict, preparation: dict) -> dict:
    value = declaration(preparation.get('resource_recovery', {}), request)
    return recovery.project_cleanup_request(request) if value and request['method'] == 'DELETE' else request


def create_steps(scene: object, rows: list, user: object, *, order: int = 0, verification: bool = False) -> tuple:
    """Validate normal serializer contracts; auxiliary steps have no catalog identity."""
    from ..models import PerfScenarioStep
    from ..serializers import PerfScenarioSerializer, PerfScenarioStepSerializer
    from .prepared_requests import step_kwargs
    value = group(rows)
    if not value:
        raise ValidationError('缺少提醒恢复组声明')
    if (scene.runtime_config or {}).get('resource_recovery') or scene.steps.filter(
            enabled=True, method__in=['PUT', 'DELETE'], url__contains=recovery.PREFIX).exists():
        raise ValidationError({'resource_recovery': '请先通过场景编辑停用已有提醒写入或删除步骤，并移除已有恢复绑定，再导入完整组'})
    users = 1 if verification else (scene.load_config or {}).get('concurrency', 1)
    if type(users) is not int or not 1 <= users <= 1000:
        raise ValidationError({'resource_recovery': '提醒恢复要求固定 1 至 1000 并发用户'})
    policy = dict(group_id=value['group_id'], vu_start=1, vu_end=min(users, value['max_resources']),
                  max_runs_per_vu=1, min_interval_ms=0)
    targets = {row.request['method']: row for row in rows}
    created, associations = [], {}
    validation_scene = copy(scene)
    validation_scene.is_preparation = False
    validation_scene.load_config = {**(scene.load_config or {}), 'concurrency': users}
    for role, method, url in [('put', 'PUT', recovery.PREFIX + value['stock_code']),
            ('receipt', 'GET', recovery.RECEIPTS + '{{rr_put_digest}}'),
            ('get', 'GET', recovery.PREFIX + value['stock_code']),
            ('delete', 'DELETE', recovery.PREFIX + value['stock_code'])]:
        row = targets.get(method) if role != 'receipt' else None
        kwargs = step_kwargs(row) if row else dict(name='提醒命令回执检查' if role == 'receipt' else '提醒状态检查',
            protocol='HTTP', method=method, url=url, body_type='NONE', body='', enabled=True, is_setup=False,
            params={}, headers={}, assertions=[{'type': 'STATUS_CODE', 'expected': 200},
                {'type': 'JSON_PATH', 'json_path': '$.code', 'expected': 'OK'}], extractors=[],
            source_metadata={}, preparation={})
        kwargs['execution_policy'] = deepcopy(policy)
        candidate = PerfScenarioStep(scenario=validation_scene, order=order + len(created), **deepcopy(kwargs))
        data = {key: deepcopy(val) for key, val in kwargs.items()
                if key not in ('source_metadata', 'source_request_id')}
        serializer = PerfScenarioStepSerializer(candidate, data=data, partial=True,
            context={'request': SimpleNamespace(user=user)})
        serializer.is_valid(raise_exception=True)
        checked = {**kwargs, **serializer.validated_data}
        step = PerfScenarioStep.objects.create(scenario=scene, order=order + len(created), **checked)
        created.append(step)
        if row:
            associations[row.pk] = step
    config = recovery.normalize_config({**value, **dict(zip(STEP_KEYS, [s.pk for s in created]))})
    runtime = {**(scene.runtime_config or {}), 'resource_recovery': config}
    serializer = PerfScenarioSerializer(scene, data={'runtime_config': runtime}, partial=True,
        context={'request': SimpleNamespace(user=user)})
    serializer.is_valid(raise_exception=True)
    # Binding this group must not introduce serializer defaults into unrelated SLA settings.
    scene.runtime_config = deepcopy(serializer.validated_data['runtime_config'])
    scene.save(update_fields=['runtime_config'])
    return created, associations


def original_snapshot(snapshot: dict, execution_id: int) -> tuple:
    """Require the signed ready plan and an exact reproducible freeze transformation."""
    store = recovery.configured_store()
    plan = recovery.verify_frozen(store, execution_id, snapshot)
    base = deepcopy(snapshot)
    base['runtime_config'].pop('_reminder_recovery')
    base['csv_data'].pop(recovery.INTERNAL_DATA)
    base['variables'] = [v for v in base['variables'] if v.get('source') != 'REMINDER_RECOVERY']
    base['steps'] = [s for s in base['steps'] if s['id'] != 'reminder:identity']
    lineage = {item['scope']: item['prior_lineage'] for item in plan['resources'] if item.get('prior_lineage')}
    expected = recovery.build_plan(base, execution_id, store.secret.decode(), run_id=plan['run_id'], lineage=lineage)
    recovery.check(recovery.encode(expected) == recovery.encode(plan), 'prepared_recovery_plan_mismatch')
    recovery.check(recovery.encode(recovery.freeze_snapshot(base, expected)) == recovery.encode(snapshot),
                   'prepared_recovery_freeze_mismatch')
    return base, store.public(execution_id)
