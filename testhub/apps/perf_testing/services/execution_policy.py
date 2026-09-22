"""Declarative, per-VU attempt budgets. No account values enter public plans."""
from copy import deepcopy
import re

from rest_framework.exceptions import ValidationError
from .auth_profiles import request_headers

FIELDS = {'group_id', 'vu_start', 'vu_end', 'max_runs_per_vu', 'min_interval_ms'}
MAX_GROUPS = 128
PLACEHOLDER = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}|\$\{([^{}]+)\}')


def normalize_policy(value: object) -> dict:
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValidationError('执行策略必须包含组标识、VU 起止序号、每用户次数上限和最小间隔')
    if not isinstance(value['group_id'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,47}', value['group_id']):
        raise ValidationError('执行组标识须为 1 至 48 位英文字母、数字、下划线或短横线，且以字母开头')
    limits = {'vu_start': (1, 100000), 'vu_end': (1, 100000),
              'max_runs_per_vu': (1, 1000000), 'min_interval_ms': (0, 7200000)}
    if any(type(value[key]) is not int or not low <= value[key] <= high
           for key, (low, high) in limits.items()) or value['vu_start'] > value['vu_end']:
        raise ValidationError('执行策略需要有界整数，VU 范围必须递增，次数必须大于 0，间隔不能为负数')
    return deepcopy(value)


def strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def outputs(step: dict) -> set[str]:
    rules = list(step.get('extractors') or [])
    config = step.get('websocket_config') or {}
    config = config if isinstance(config, dict) else {}
    commands = config.get('commands') or []
    for frame in [config.get('auth') or {}, *(commands if isinstance(commands, list) else [])]:
        if isinstance(frame, dict):
            rules.extend(frame.get('extractors') or [])
    for event in (step.get('sse_config') or {}).get('rules', []):
        rules.extend(event.get('extractors') or [])
    return {rule['name'] for rule in rules if isinstance(rule, dict) and isinstance(rule.get('name'), str)}


def references(step: dict, global_headers: dict | None = None, *, auth_enabled: bool = False) -> set[str]:
    fields = {key: step.get(key) for key in ('url', 'body', 'params')}
    fields['headers'] = request_headers(step, global_headers, auth_enabled=auth_enabled)
    config = step.get('websocket_config') or {}
    config = config if isinstance(config, dict) else {}
    commands = config.get('commands') or []
    frames = [frame for frame in [config.get('auth') or {}, *(commands if isinstance(commands, list) else [])]
              if isinstance(frame, dict)]
    fields['frames'] = [frame.get('request') for frame in frames]
    if step.get('protocol') == 'SSE':
        fields['sse_contract'] = step.get('sse_config')
    if step.get('protocol') == 'WEBSOCKET':
        fields['assertion_expected'] = [rule.get('expected') for frame in frames
            for rule in (frame.get('assertions') if isinstance(frame.get('assertions'), list) else [])
            if isinstance(rule, dict)]
    refs = {(m.group(1) or m.group(2)).strip() for text in strings(fields) for m in PLACEHOLDER.finditer(text)}
    # Frame-local ordering is validated by the WebSocket contract validator.
    if step.get('protocol') in ('WEBSOCKET', 'SSE'):
        refs -= outputs(step)
    return refs


def validate_policies(snapshot: dict) -> list[str]:
    errors, groups, owners = [], {}, {}
    steps = [step for step in snapshot.get('steps', []) if step.get('enabled', True)]
    users = (snapshot.get('load_config') or {}).get('concurrency', 1)
    users = users if type(users) is int and users > 0 else 1
    static = {'base_url', 'baseUrl', 'vu_id', 'iteration', 'request_id'} | {
        var.get('name') for var in snapshot.get('variables') or []}
    ordered = [s for s in steps if s.get('is_setup')] + [s for s in steps if not s.get('is_setup')]
    previous = None
    for step in ordered:
        try:
            policy = normalize_policy(step.get('execution_policy'))
        except ValidationError as exc:
            errors.append(str(exc.detail)); continue
        group = policy.get('group_id')
        if policy:
            if snapshot.get('engine', 'K6') != 'K6' or step.get('is_setup') or step.get('auth_phase'):
                errors.append('执行策略仅支持 K6 业务步骤，前置或认证步骤禁止配置')
            if policy['vu_end'] > users:
                errors.append('执行策略 VU 范围不能超出场景并发用户数')
            if group in groups and (groups[group] != policy or previous != group):
                errors.append('同一执行组的步骤必须连续且策略完全一致')
            groups[group] = policy
        previous = group
        for ref in references(step, (snapshot.get('env_config') or {}).get('headers'),
                              auth_enabled=bool((snapshot.get('runtime_config') or {}).get('auth_profile'))):
            if ref in owners:
                owner_group, owner_setup = owners[ref]
                if not owner_setup and (owner_group or group) and owner_group != group:
                    errors.append('执行组不能引用组外业务步骤的输出；请使用前置或本组前序提取值')
        for name in outputs(step):
            previous_owner = owners.get(name)
            if group and (name in static or previous_owner and previous_owner != (group, False)):
                errors.append('执行组输出不能覆盖静态、前置或其他执行组的变量')
            if previous_owner and previous_owner[0] and previous_owner[0] != group:
                errors.append('不同执行范围不能复用同一个输出变量')
            owners[name] = (group, bool(step.get('is_setup')))
    if len(groups) > MAX_GROUPS:
        errors.append(f'执行组不能超过 {MAX_GROUPS} 个')
    return list(dict.fromkeys(errors))


def compile_groups(steps: list[dict], headers: dict | None = None, *, auth_enabled: bool = False) -> list[dict]:
    """Private runtime indexes refer only to the frozen, enabled step sequence."""
    result = []
    group_number = 0
    for index, step in enumerate(steps):
        if step.get('is_setup') or step.get('auth_phase'):
            continue
        policy = normalize_policy(step.get('execution_policy'))
        if policy and result and result[-1]['policy'] == policy:
            group = result[-1]
        else:
            if policy:
                group_number += 1
            group = {'index': group_number if policy else None, 'policy': policy, 'steps': [], 'outputs': []}
            result.append(group)
        group['steps'].append({'index': index, 'requires': sorted(references(step, headers, auth_enabled=auth_enabled))})
        group['outputs'] = sorted(set(group['outputs']) | outputs(step))
    return result


def request_plan(steps: list[dict], users: int, rounds: int) -> dict:
    """Budgets count logical calls; authentication retries remain additional HTTP attempts."""
    totals = {'business': 0, 'http': 0, 'websocket': 0, 'setup_http': 0, 'setup_websocket': 0}
    groups = []
    for group in compile_groups(steps):
        policy = group['policy']
        count = ((policy['vu_end'] - policy['vu_start'] + 1) * min(rounds, policy['max_runs_per_vu'])
                 if policy and rounds else
                 (policy['vu_end'] - policy['vu_start'] + 1) * policy['max_runs_per_vu'] if policy else users * rounds)
        for item in group['steps']:
            key = 'websocket' if steps[item['index']].get('protocol') == 'WEBSOCKET' else 'http'
            totals[key] += count
            totals['business'] += count
        if policy:
            groups.append({'group_index': group['index'], **{k: policy[k] for k in FIELDS - {'group_id'}},
                           'planned_participants': policy['vu_end'] - policy['vu_start'] + 1,
                           'attempt_limit': count, 'step_count': len(group['steps'])})
    for step in steps:
        if step.get('is_setup') and not step.get('auth_phase'):
            totals['setup_websocket' if step.get('protocol') == 'WEBSOCKET' else 'setup_http'] += users
    return {'groups': groups, **totals}


def public_policies(steps: list[dict]) -> dict[int, dict]:
    result = {}
    enabled = [(index, step) for index, step in enumerate(steps) if step.get('enabled', True)]
    for group in compile_groups([step for _, step in enabled]):
        if not group['policy']:
            continue
        safe = {'group_index': group['index'], **{key: group['policy'][key] for key in FIELDS - {'group_id'}}}
        for item in group['steps']:
            result[enabled[item['index']][0]] = dict(safe)
    return result


class PolicyMetrics:
    def __init__(self, steps: list[dict], load: dict):
        self.users = int(load.get('concurrency') or 1)
        self.plan = request_plan(steps, self.users, int(load.get('iterations_per_vu') or 0))
        self.rows = {row['group_index']: dict(row, started=0, completed=0, success=0, failed=0, participants=0,
                    blocked_steps=0, executed_steps=0, not_participant=0, quota=0, interval=0)
                     for row in self.plan['groups']}
        self.states = {}
        self.invalid_events = 0
        self.enabled = bool(self.rows)

    def consume(self, event: dict, vu: int) -> None:
        if type(vu) is not int or not 1 <= vu <= self.users:
            self.invalid_events += 1; return
        if event.get('kind') == 'policy_skips':
            items = event.get('groups')
            if not isinstance(items, list) or len(items) > MAX_GROUPS:
                self.invalid_events += 1; return
        else:
            items = [event]
        for item in items:
            if not isinstance(item, dict) or type(item.get('group')) is not int or item['group'] not in self.rows:
                self.invalid_events += 1; continue
            row = self.rows[item['group']]
            state = self.states.setdefault((item['group'], vu), {'attempt': 0, 'completed': 0,
                        'not_participant': 0, 'quota': 0, 'interval': 0})
            if event['kind'] == 'policy_skips':
                if any(type(item.get(k)) is not int or not state[k] <= item[k] <= 2 ** 53 - 1
                       for k in ('not_participant', 'quota', 'interval')):
                    self.invalid_events += 1; continue
                for key in ('not_participant', 'quota', 'interval'):
                    row[key] += item[key] - state[key]; state[key] = item[key]
                continue
            attempt = item.get('attempt')
            if (type(attempt) is not int or not 1 <= attempt <= row['max_runs_per_vu']
                    or not row['vu_start'] <= vu <= row['vu_end']):
                self.invalid_events += 1; continue
            if event['kind'] == 'group_started':
                if attempt != state['attempt'] + 1 or state['completed'] != state['attempt']:
                    self.invalid_events += 1; continue
                if not state['attempt']:
                    row['participants'] += 1
                row['started'] += 1; state['attempt'] = attempt
            elif event['kind'] == 'group_completed':
                blocked, executed = item.get('blocked_steps'), item.get('executed_steps')
                if (attempt != state['attempt'] or state['completed'] != attempt - 1
                        or type(blocked) is not int or type(executed) is not int
                        or blocked < 0 or executed < 0 or blocked + executed != row['step_count']
                        or type(item.get('ok')) is not bool or item['ok'] and blocked):
                    self.invalid_events += 1; continue
                state['completed'] = attempt; row['completed'] += 1
                row['success' if item['ok'] else 'failed'] += 1
                row['blocked_steps'] += blocked; row['executed_steps'] += executed

    def incomplete(self) -> bool:
        return bool(self.invalid_events or any(row['started'] != row['completed'] or row['uncovered_participants']
                                               for row in self.snapshot()['groups']))

    def snapshot(self) -> dict:
        rows = []
        for row in self.rows.values():
            rows.append(dict(row, incomplete=row['started'] - row['completed'],
                             uncovered_participants=max(row['planned_participants'] - row['participants'], 0)))
        return {'version': 1, 'groups': rows, 'invalid_events': self.invalid_events,
                'skip_counts_are_observed': True}


def public_policy(value: object) -> dict:
    """Sanitize frozen public policy separately from editable private group names."""
    fields = {'group_index', 'vu_start', 'vu_end', 'max_runs_per_vu', 'min_interval_ms'}
    if (not isinstance(value, dict) or not fields <= set(value)
            or any(type(value[key]) is not int for key in fields)
            or not 1 <= value['group_index'] <= MAX_GROUPS):
        return {}
    try:
        normalize_policy({key: value[key] for key in FIELDS - {'group_id'}} | {'group_id': 'public'})
    except ValidationError:
        return {}
    return {key: value[key] for key in fields}
