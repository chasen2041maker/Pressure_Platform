"""Bounded K6 observations owned by a persisted sample, never by rounded time."""
import json
import math

from .k6_throughput import RATE_VERSION, RATE_NOTICE, HISTORICAL_RATE_REASON
from .k6_native_vu import MAX_OBSERVATIONS, sanitize_observation

PAYLOAD_VERSION = 'k6_sample_v1'
MAX_STEPS = 200
MAX_PAYLOAD_BYTES = 128 * 1024
NUMERIC_FIELDS = ('sample_seq', 'elapsed_seconds', 'window_count', 'tps', 'avg_rt', 'p90_rt',
                  'p95_rt', 'p99_rt', 'error_rate', 'total_requests', 'business_total',
                  'success_requests', 'failed_requests', 'http_started', 'http_total',
                  'http_incomplete', 'business_started', 'business_incomplete',
                  'completed_iterations', 'executor_iterations', 'idle_iterations', 'active_users', 'cpu_percent', 'memory_mb')
STEP_NUMBERS = ('total', 'success', 'failed', 'error_rate', 'tps', 'avg_rt', 'min_rt',
                'max_rt', 'p90_rt', 'p95_rt', 'p99_rt')
LATENCY_FIELDS = ('avg_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'error_rate')
MAX_WS_COMMANDS = 200


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 2 ** 53 - 1 else None


def integer(value):
    value = number(value)
    return int(value) if value is not None and int(value) == value else None


def policy_payload(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        return None
    rows = value.get('groups')
    rows = rows if isinstance(rows, list) else []
    fields = ('group_index', 'vu_start', 'vu_end', 'max_runs_per_vu', 'min_interval_ms',
              'planned_participants', 'attempt_limit', 'step_count', 'participants',
              'started', 'completed', 'success', 'failed', 'incomplete', 'blocked_steps',
              'executed_steps', 'not_participant', 'quota', 'interval', 'uncovered_participants')
    result = {'version': 1, 'groups': [], 'invalid_events': integer(value.get('invalid_events')),
              'skip_counts_are_observed': True, 'groups_truncated': value.get('groups_truncated') is True}
    for row in rows[:128]:
        if isinstance(row, dict) and integer(row.get('group_index')) and row['group_index'] <= 128:
            result['groups'].append({key: integer(row.get(key)) for key in fields})
    result['groups_truncated'] |= len(result['groups']) != len(rows)
    return result


def websocket_payload(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        return None
    result = {'version': 1}
    for stage in ('sessions', 'connect', 'auth', 'commands', 'events', 'heartbeat'):
        counts = value.get(stage)
        counts = counts if isinstance(counts, dict) else {}
        result[stage] = {key: integer(counts.get(key)) for key in ('started', 'completed', 'success', 'failed', 'incomplete')}
    connections = value.get('connections')
    connections = connections if isinstance(connections, dict) else {}
    result['connections'] = dict(observed=connections.get('observed') is True,
                                **{key: integer(connections.get(key)) for key in ('current', 'peak', 'unclosed')})
    rows = value.get('command_metrics')
    rows = rows if isinstance(rows, list) else []
    result['command_metrics'] = []
    for row in rows[:MAX_WS_COMMANDS]:
        if not isinstance(row, dict):
            continue
        ident, command = row.get('step_id'), integer(row.get('command_index'))
        valid_ident = type(ident) is int and 0 < ident <= 2 ** 53 - 1 or (
            isinstance(ident, str) and ident.startswith('legacy:') and ident[7:].isdigit() and len(ident) <= 32)
        if not valid_ident or command is None or command > 63:
            continue
        result['command_metrics'].append(dict(step_id=ident, command_index=command,
            latency_kind='event_wait' if row.get('latency_kind') == 'event_wait' else 'command',
            **{key: number(row.get(key)) for key in STEP_NUMBERS}))
    result['commands_total'] = max(integer(value.get('commands_total')) or 0, len(rows))
    result['commands_truncated'] = value.get('commands_truncated') is True or len(result['command_metrics']) != result['commands_total']
    return result


def sample_payload(sample):
    """Copy only public numeric observations and stable step identifiers."""
    if not isinstance(sample, dict):
        return {}
    seq, elapsed = integer(sample.get('sample_seq')), number(sample.get('elapsed_seconds'))
    if not seq or elapsed is None:
        return {}
    result = {'version': PAYLOAD_VERSION}
    result.update({key: number(sample.get(key)) for key in NUMERIC_FIELDS})
    result.update(sample_seq=seq, elapsed_seconds=elapsed,
                  engine_finished=sample.get('engine_finished') is True,
                  cpu_sampled=sample.get('cpu_sampled') is True)
    native = sample.get('native_vu_observations')
    native = native if isinstance(native, list) else []
    result['native_vu_observations'] = [clean for row in native[:MAX_OBSERVATIONS]
                                       if (clean := sanitize_observation(row))]
    result['native_vu_rejected_count'] = max(integer(sample.get('native_vu_rejected_count')) or 0,
                                            len(native) - len(result['native_vu_observations']))
    source = sample.get('throughput')
    source = source if isinstance(source, dict) else {}
    bucket = integer(source.get('latest_bucket_start_ms'))
    rate = integer(source.get('latest_rps'))
    invalid = integer(source.get('invalid_timestamp_count'))
    valid = (source.get('version') == RATE_VERSION and source.get('verified') is True
             and invalid == 0 and rate is not None
             and (bucket is not None and bucket % 1000 == 0 or bucket is None and rate == 0))
    result['throughput'] = {
        'version': RATE_VERSION, 'verified': valid,
        'latest_bucket_start_ms': bucket, 'latest_rps': rate if valid else None,
        'bucket_width_ms': 1000, 'anchor': 'unix_epoch_utc',
        'timestamp_source': 'request.timestamp_ms', 'denominator_seconds': 1,
        'invalid_timestamp_count': invalid,
        'business_events': integer(source.get('business_events')),
        'timestamped_business_events': integer(source.get('timestamped_business_events')),
        'provisional': not result['engine_finished'],
        'reason': '' if valid else '采样缺少有效的完成时间来源，窗口 RPS 未采集',
        'notice': RATE_NOTICE,
    }
    result['tps'] = rate if valid else None
    if not result.get('window_count'):
        result.update(dict.fromkeys(LATENCY_FIELDS))
    rows = sample.get('steps')
    rows = rows if isinstance(rows, list) else []
    result['steps'] = []
    for row in rows[:MAX_STEPS]:
        if not isinstance(row, dict):
            continue
        ident = row.get('step_id')
        if isinstance(ident, int) and not isinstance(ident, bool) and 0 < ident <= 2 ** 53 - 1:
            pass
        elif ident in ('auth:login', 'auth:refresh'):
            pass
        elif isinstance(ident, str) and ident.startswith('legacy:') and ident[7:].isdigit() and len(ident) <= 32:
            pass
        else:
            continue
        item = {'step_id': ident, 'phase': row.get('phase') if row.get('phase') in ('business', 'setup', 'login', 'refresh') else 'unknown'}
        if row.get('protocol') in ('HTTP', 'WEBSOCKET'):
            item['protocol'] = row['protocol']
            item['latency_kind'] = 'session' if row['protocol'] == 'WEBSOCKET' else 'request'
        item.update({key: number(row.get(key)) for key in STEP_NUMBERS})
        # Names and methods are restored from the frozen step definitions in the UI.
        if not item['total']:
            item.update(dict.fromkeys(('avg_rt', 'min_rt', 'max_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'error_rate')))
        result['steps'].append(item)
    result['steps_total'] = len(rows)
    result['steps_truncated'] = len(result['steps']) != len(rows)
    websocket = websocket_payload(sample.get('websocket'))
    if websocket:
        result['websocket'] = websocket
    policy = policy_payload(sample.get('execution_policy'))
    if policy:
        result['execution_policy'] = policy
    while (len(json.dumps(result, ensure_ascii=True, allow_nan=False).encode()) > MAX_PAYLOAD_BYTES
           and websocket and websocket['command_metrics']):
        websocket['command_metrics'].pop()
        websocket['commands_truncated'] = True
    while len(json.dumps(result, ensure_ascii=True, allow_nan=False).encode()) > MAX_PAYLOAD_BYTES and result['steps']:
        result['steps'].pop()
        result['steps_truncated'] = True
    while len(json.dumps(result, ensure_ascii=True, allow_nan=False).encode()) > MAX_PAYLOAD_BYTES and policy and policy['groups']:
        policy['groups'].pop()
        policy['groups_truncated'] = True
    return result


def sanitized_payload(payload):
    if not isinstance(payload, dict) or payload.get('version') != PAYLOAD_VERSION:
        return {}
    result = sample_payload(payload)
    if result:
        total = integer(payload.get('steps_total'))
        result['steps_total'] = max(total or 0, result['steps_total'])
        result['steps_truncated'] = result['steps_truncated'] or payload.get('steps_truncated') is True
    return result


def project_sample(sample):
    result = dict(sample)
    payload = sanitized_payload(result.pop('k6_payload', {}))
    if payload:
        result.update(payload)
        # Preserve the payload on exports so a second normalization stays truthful.
        result['k6_payload'] = payload
    else:
        result['native_vu_observations'] = []
        result['tps'] = None
        result['throughput'] = {'verified': False, 'latest_bucket_start_ms': None,
                                'reason': HISTORICAL_RATE_REASON, 'notice': RATE_NOTICE}
        # Older rows have no observation-window count. Latency cannot be verified.
        result.update(dict.fromkeys(LATENCY_FIELDS))
    return result


def persistence_evidence(state):
    status = state['k6_persistence']
    missing = max(status['observed'] - status['persisted'], 0)
    result = {
        'version': 'sample_persistence_v1', 'complete': missing == 0,
        'observed_count': status['observed'], 'persisted_count': status['persisted'],
        'missing_count': missing, 'pending_sequences': [row.k6_payload.get('sample_seq') for row in state['pending']],
        'discarded_count': status['discarded'],
        'first_discarded_seq': status.get('first_discarded_seq'),
        'last_discarded_seq': status.get('last_discarded_seq'),
        'last_persisted_seq': status['last_persisted_seq'],
        'write_failures': status['write_failures'], 'heartbeat_failures': status['heartbeat_failures'],
        'stopped_due_to_storage': status['aborted'],
        'attempts_per_batch': 3, 'final_retry_attempts': 3, 'pending_limit': 4,
        'stop_error': status.get('stop_error'),
    }
    return result


def write_persistence_evidence(artifact_dir, evidence):
    """Public numeric evidence survives loss of database availability."""
    from pathlib import Path
    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'sample-integrity.json'
    temporary = directory / 'sample-integrity.json.tmp'
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(target)
