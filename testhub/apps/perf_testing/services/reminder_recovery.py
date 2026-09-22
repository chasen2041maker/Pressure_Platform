"""Bounded portfolio-reminder intents and recovery; never a generic resource executor."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

VERSION = 1
KIND = 'portfolio_reminder'
PREFIX = '/api/v1/portfolio/reminders/'
RECEIPTS = '/api/v1/portfolio/reminder-commands/'
INTERNAL_DATA = 'reminder-recovery-v1'
NAMES = ('rr_owner', 'rr_token_hash', 'rr_put_digest', 'rr_put_key', 'rr_delete_key', 'rr_recover_key', 'rr_put_body',
         'rr_delete_body', 'rr_put_fingerprint', 'rr_delete_fingerprint', 'rr_delete_digest')
CONFIG_FIELDS = {'version', 'kind', 'group_id', 'put_step_id', 'receipt_step_id', 'get_step_id',
                 'delete_step_id', 'stock_code', 'max_resources'}
TERMINAL = {'CLEANED', 'CANCELLED'}
STATES = {'PENDING', 'CLEANED', 'CANCELLED', 'CONFLICT'}
HEX = re.compile(r'[0-9a-f]{64}\Z')
OWNER = re.compile(r'[1-9][0-9]{0,18}\Z')
MAX_ATTEMPTS = 24
CONDITIONS = ('price_above','price_below','daily_pct_up','daily_pct_down','five_min_pct_up','five_min_pct_down')


class RecoveryError(RuntimeError):
    """Only fixed reason codes may cross the private recovery boundary."""
    response_received = False


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise RecoveryError(reason)


def encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_json(raw: str) -> object:
    def pairs(items: list) -> dict:
        result = {}
        for key, value in items:
            check(key not in result, 'duplicate_json_field')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(RecoveryError('nonfinite_json')))
    except (ValueError, TypeError) as exc:
        raise RecoveryError('invalid_json') from exc


def normalize_config(value: object) -> dict:
    if value in (None, {}):
        return {}
    check(isinstance(value, dict) and set(value) == CONFIG_FIELDS, 'recovery_config_fields')
    value = deepcopy(value)
    check(type(value['version']) is int and value['version'] == VERSION and value['kind'] == KIND, 'recovery_version')
    check(isinstance(value['group_id'], str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,47}', value['group_id']), 'recovery_group')
    ids = [value[key] for key in ('put_step_id', 'receipt_step_id', 'get_step_id', 'delete_step_id')]
    check(all(type(sid) is int and sid > 0 for sid in ids) and len(set(ids)) == 4, 'recovery_step_ids')
    code=value['stock_code']
    check(isinstance(code,str) and re.fullmatch(r'(sh[69]|sz[023]|bj[489])[0-9]{5}',code)
          and not code.startswith(('sz399','bj899')), 'recovery_stock')
    check(type(value['max_resources']) is int and 1 <= value['max_resources'] <= 1000, 'recovery_limit')
    return value


def origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        check(parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
              and parsed.path in ('', '/') and not parsed.query and not parsed.fragment and '\\' not in value
              and not any(c.isspace() for c in value), 'recovery_origin')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        host = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
        return f'{parsed.scheme}://{host}:{port}'
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecoveryError('recovery_origin') from exc


def canonical_owner(value: object) -> str:
    check(isinstance(value, str) and OWNER.fullmatch(value) and int(value) <= 9223372036854775807, 'recovery_owner')
    return value


def request_digest(method: str, path: str, owner: str, key: str) -> str:
    return digest(f'{method}\n{path}\nuser:{owner}\n{key}'.encode())


def fingerprint(method: str, path: str, body: str) -> str:
    return digest(f'{method}\n{path}\n\n{body}'.encode())


def cleanup_body(put_key: str, put_fingerprint: str) -> str:
    check(isinstance(put_key, str) and re.fullmatch(r'rr1-[0-9a-f]{32}-([1-9][0-9]{0,2}|1000)-put', put_key),
          'recovery_cleanup_key')
    check(isinstance(put_fingerprint, str) and HEX.fullmatch(put_fingerprint), 'recovery_cleanup_fingerprint')
    return encode(dict(put_request_key=put_key, put_request_fingerprint=put_fingerprint)).decode()


def project_cleanup_request(request: dict) -> dict:
    """Schema-only projection; callers must first validate the declaration or full binding."""
    check(request.get('protocol', 'HTTP') == 'HTTP' and request.get('method') == 'DELETE'
          and request.get('body_type') == 'JSON' and strict_json(request.get('body', '')) == {},
          'recovery_delete_body')
    # These local shape values are never persisted or sent; every real wire is checked again.
    return {**request, 'body': cleanup_body('rr1-' + '0'*32 + '-1-put', '0'*64)}


def _command_wires(put_step: dict, path: str, run_id: str, vu: int, previous: dict | None) -> dict:
    body = strict_json(put_step['body'])
    if previous:
        rule_id = canonical_owner(previous.get('rule_id'))
        revision = previous.get('revision')
        check(type(revision) is int and 1 <= revision < 4294967295, 'recovery_lineage_revision')
        body['precondition'] = dict(mode='revision', rule_id=rule_id, revision=revision, active=False)
    wire = encode(body).decode()
    key = f'rr1-{run_id}-{vu}-put'
    put_hash = fingerprint('PUT', path, wire)
    delete_body = cleanup_body(key, put_hash)
    result = dict(put_key=key, put_body=wire, put_fingerprint=put_hash, delete_body=delete_body)
    for role in ('delete', 'recover'):
        result[role+'_key'] = f'rr1-{run_id}-{vu}-{role}'
        result[role+'_fingerprint'] = fingerprint('DELETE', path, delete_body)
    return result


def _validate_wire_schema(step: dict, body: str, key: str) -> None:
    metadata = step.get('source_metadata') or {}
    if not metadata:
        return  # Manual steps retain the bounded reminder contract validated by _binding.
    from .api_catalog import _required_body_gaps, media_type
    contents = metadata.get('request_body', {}).get('content', {})
    schemas = [item.get('schema') for media, item in contents.items() if media_type(media) == 'application/json']
    check(len(schemas) == 1 and isinstance(schemas[0], dict), 'recovery_wire_schema_missing')
    confirmed = (step.get('preparation') or {}).get('confirmed_fields', [])
    check(not _required_body_gaps(schemas[0], strict_json(body), confirmed=confirmed), 'recovery_wire_schema')
    for parameter in metadata.get('parameters', []):
        if parameter.get('in') == 'header' and parameter.get('name', '').lower() == 'idempotency-key':
            check(not _required_body_gaps(parameter.get('schema', {}), key, 'headers/Idempotency-Key'),
                  'recovery_wire_key_schema')


def validate_rule(body: dict) -> None:
    conditions=body.get('conditions')
    check(isinstance(conditions,dict) and set(conditions)==set(CONDITIONS),'recovery_conditions')
    for name,condition in conditions.items():
        check(isinstance(condition,dict) and set(condition)=={'value','enabled'}
              and type(condition['enabled']) is bool and isinstance(condition['value'],str),'recovery_conditions')
        value=condition['value']
        if not condition['enabled'] and value in ('','0'):
            continue
        scale=6 if name.startswith('price_') else 4
        maximum=1000000 if scale==6 else 100
        check(bool(re.fullmatch(r'(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,'+str(scale)+'})?',value))
              and Decimal(0)<Decimal(value)<=Decimal(maximum),'recovery_condition_value')
    check(any(condition['enabled'] for condition in conditions.values()),'recovery_conditions_disabled')
    policy=body.get('policy')
    check(isinstance(policy,dict) and set(policy)=={'channels','frequency','validity','trading_session_only'}
          and type(policy['trading_session_only']) is bool,'recovery_policy_body')
    channels=policy['channels'];frequency=policy['frequency'];validity=policy['validity']
    check(isinstance(channels,dict) and set(channels)=={'app_push','message_center'}
          and all(type(v) is bool for v in channels.values()) and any(channels.values()),'recovery_policy_body')
    check(isinstance(frequency,dict) and frequency==dict(mode='cooldown',interval_minutes=30)
          and type(frequency['interval_minutes']) is int,'recovery_policy_body')
    check(isinstance(validity,dict) and set(validity)=={'mode','valid_until'},'recovery_policy_body')
    if validity['mode']=='permanent':
        check(validity['valid_until'] is None,'recovery_policy_body')
    else:
        check(validity['mode']=='until_date' and isinstance(validity['valid_until'],str)
              and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',validity['valid_until']),'recovery_policy_body')
        date.fromisoformat(validity['valid_until'])


def _binding(snapshot: dict) -> tuple:
    if (snapshot.get('runtime_config') or {}).get('_reminder_recovery'):
        snapshot = deepcopy(snapshot)
        snapshot['runtime_config'].pop('_reminder_recovery')
        snapshot['variables'] = [v for v in snapshot.get('variables', []) if v.get('source') != 'REMINDER_RECOVERY']
        snapshot['steps'] = [s for s in snapshot.get('steps', []) if s.get('id') != 'reminder:identity']
    config = normalize_config((snapshot.get('runtime_config') or {}).get('resource_recovery'))
    if not config:
        for step in snapshot.get('steps',[]):
            if (step.get('enabled',True) and step.get('method') in ('PUT','DELETE')
                    and '/portfolio/reminders/' in str(step.get('url',''))):
                try:
                    body=strict_json(step.get('body') or '{}')
                except RecoveryError:
                    continue
                check(not (isinstance(body,dict) and (('precondition' in body) or ('put_request_key' in body))),
                      'recovery_required_for_conditional_write')
        return {}, [], [], ''
    check(snapshot.get('engine') == 'K6', 'recovery_requires_k6')
    base = origin((snapshot.get('env_config') or {}).get('base_url'))
    runtime = snapshot['runtime_config']
    auth = runtime.get('auth_profile') or {}
    check(auth.get('mode') == 'STATIC' and auth.get('transport') == 'BEARER' and auth.get('max_attempts', 1) == 1
          and not auth.get('refresh'), 'recovery_auth')
    pool = snapshot.get('account_pool') or {}
    check(pool.get('version_id') and HEX.fullmatch(str(pool.get('content_hash', ''))), 'recovery_pool')
    rows = ((snapshot.get('csv_data') or {}).get(pool.get('data_key')) or {}).get('rows') or []
    users = (snapshot.get('load_config') or {}).get('concurrency')
    check(type(users) is int and 1 <= users <= 1000 and len(rows) >= users, 'recovery_pool_size')
    owners = [canonical_owner(row.get(pool.get('identity_column'))) for row in rows[:users]]
    check(len(set(owners)) == users, 'recovery_duplicate_owner')
    names = [v.get('name') for v in snapshot.get('variables', [])]
    check(not (set(names) & set(NAMES)), 'recovery_reserved_variable')
    auth_inputs = [v for v in snapshot.get('variables', []) if v.get('name') == auth.get('access_token_variable')]
    check(len(auth_inputs) == 1 and auth_inputs[0].get('type') == 'CSV'
          and str(auth_inputs[0].get('data_file_id')) == pool['data_key'], 'recovery_token_binding')
    check(all(isinstance(row.get(auth_inputs[0].get('column')), str) and row[auth_inputs[0]['column']] for row in rows[:users]), 'recovery_token_missing')
    steps = [s for s in snapshot.get('steps', []) if s.get('enabled', True)]
    ids = [config[key] for key in ('put_step_id', 'receipt_step_id', 'get_step_id', 'delete_step_id')]
    selected = [s for s in steps if s.get('id') in ids]
    check([s.get('id') for s in selected] == ids, 'recovery_step_order')
    start = next(i for i, step in enumerate(steps) if step.get('id') == ids[0])
    check([s.get('id') for s in steps[start:start+4]] == ids, 'recovery_step_contiguous')
    path = PREFIX + config['stock_code']
    paths = (path, RECEIPTS + '{{rr_put_digest}}', path, path)
    for step, method, target in zip(selected, ('PUT', 'GET', 'GET', 'DELETE'), paths):
        check(step.get('protocol', 'HTTP') == 'HTTP' and step.get('method') == method and step.get('url') == target
              and not step.get('params') and not step.get('is_setup') and not step.get('files'), 'recovery_step_contract')
        check(not any(e.get('name') in NAMES for e in step.get('extractors', [])), 'recovery_reserved_output')
    policy = selected[0].get('execution_policy') or {}
    check(policy.get('group_id') == config['group_id'] and policy.get('max_runs_per_vu') == 1
          and type(policy.get('vu_start')) is int and type(policy.get('vu_end')) is int
          and 1 <= policy['vu_start'] <= policy['vu_end'] <= users
          and policy['vu_end'] - policy['vu_start'] + 1 <= config['max_resources'], 'recovery_policy')
    check(all(s.get('execution_policy') == policy for s in selected), 'recovery_policy')
    for step in steps:
        if step.get('id') not in ids:
            check((step.get('execution_policy') or {}).get('group_id') != config['group_id'], 'recovery_extra_group_step')
            check(not (step.get('method') in ('PUT', 'DELETE') and '/portfolio/reminders/' in str(step.get('url', ''))), 'recovery_unbound_write')
            check(not any(e.get('name') in NAMES for e in step.get('extractors', [])), 'recovery_reserved_output')
    body = strict_json(selected[0].get('body', ''))
    check(selected[0].get('body_type') == 'JSON' and isinstance(body, dict)
          and set(body) == {'conditions', 'policy', 'precondition'}
          and isinstance(body['conditions'], dict) and isinstance(body['policy'], dict)
          and body['precondition'] == {'mode': 'absent'}
          and '{{' not in json.dumps(body) and '${' not in json.dumps(body), 'recovery_put_body')
    validate_rule(body)
    project_cleanup_request(selected[3])
    return config, selected, owners, base


def validate_snapshot(snapshot: dict) -> list[str]:
    try:
        _binding(snapshot)
    except (RecoveryError, KeyError, TypeError, ValueError) as exc:
        return [str(exc) if isinstance(exc, RecoveryError) else 'recovery_invalid_binding']
    return []


def build_plan(snapshot: dict, execution_id: int, secret: str, *, run_id: str | None = None,
               lineage: dict | None = None) -> dict:
    config, steps, owners, base = _binding(snapshot)
    check(bool(config) and type(execution_id) is int and execution_id > 0, 'recovery_execution')
    run_id = run_id or uuid.uuid4().hex
    check(isinstance(run_id, str) and re.fullmatch('[0-9a-f]{32}', run_id), 'recovery_run_id')
    path = PREFIX + config['stock_code']
    resources = []
    policy = steps[0]['execution_policy']
    for vu in range(policy['vu_start'], policy['vu_end'] + 1):
        owner = owners[vu-1]
        scope = hmac.new(secret.encode(), f'{base}\n{owner}\n{config["stock_code"]}'.encode(), hashlib.sha256).hexdigest()
        previous = (lineage or {}).get(scope)
        wires = _command_wires(steps[0], path, run_id, vu, previous)
        _validate_wire_schema(steps[0], wires['put_body'], wires['put_key'])
        _validate_wire_schema(steps[3], wires['delete_body'], wires['delete_key'])
        _validate_wire_schema(steps[3], wires['delete_body'], wires['recover_key'])
        token_variable = snapshot['runtime_config']['auth_profile']['access_token_variable']
        variable = next(v for v in snapshot['variables'] if v['name'] == token_variable)
        token = snapshot['csv_data'][snapshot['account_pool']['data_key']]['rows'][vu-1][variable['column']]
        item = dict(vu=vu, owner=owner, token_hash=digest(token.encode()), scope=scope, **wires,
                    put_digest=request_digest('PUT', path, owner, wires['put_key']), prior_lineage=deepcopy(previous))
        for name in ('delete', 'recover'):
            item[name+'_digest'] = request_digest('DELETE', path, owner, wires[name+'_key'])
        resources.append(item)
    result = dict(version=VERSION, kind=KIND, execution_id=execution_id, run_id=run_id, origin=base, path=path,
                config=config, pool=deepcopy(snapshot['account_pool']), definition_hash=digest(encode(snapshot['steps'])),
                resources=resources)
    check(len(encode(result)) <= 8*1024*1024,'recovery_intent_limit')
    return result


def freeze_snapshot(snapshot: dict, plan: dict) -> dict:
    config, steps, owners, base = _binding(snapshot)
    check(plan['config'] == config and plan['origin'] == base and plan['pool'] == snapshot['account_pool']
          and plan['path'] == PREFIX + config['stock_code']
          and plan['definition_hash'] == digest(encode(snapshot['steps'])), 'recovery_freeze_binding')
    policy = steps[0]['execution_policy']
    check([row['vu'] for row in plan['resources']] == list(range(policy['vu_start'], policy['vu_end'] + 1)),
          'recovery_freeze_resources')
    for row in plan['resources']:
        wires = _command_wires(steps[0], plan['path'], plan['run_id'], row['vu'], row['prior_lineage'])
        check(row['owner'] == owners[row['vu']-1] and all(row[key] == value for key, value in wires.items()),
              'recovery_freeze_wire')
        for role, step in (('put', steps[0]), ('delete', steps[3]), ('recover', steps[3])):
            body = wires['put_body'] if role == 'put' else wires['delete_body']
            _validate_wire_schema(step, body, wires[role+'_key'])
            check(row[role+'_digest'] == request_digest(step['method'], plan['path'], row['owner'], wires[role+'_key']),
                  'recovery_freeze_digest')
    frozen = deepcopy(snapshot)
    frozen['runtime_config']['_reminder_recovery'] = dict(version=VERSION, origin=plan['origin'], path=plan['path'], config=config,
        token_variable=snapshot['runtime_config']['auth_profile']['access_token_variable'], pool_hash=plan['pool']['content_hash'])
    by_vu = {row['vu']: row for row in plan['resources']}
    rows = [{name: by_vu.get(vu, {}).get(name.removeprefix('rr_'), '') for name in NAMES}
            for vu in range(1, snapshot['load_config']['concurrency']+1)]
    frozen['csv_data'][INTERNAL_DATA] = dict(columns=list(NAMES), rows=rows)
    frozen['variables'].extend(dict(name=name, type='CSV', data_file_id=INTERNAL_DATA, column=name,
                                     secret=True, source='REMINDER_RECOVERY') for name in NAMES)
    put = next(step for step in frozen['steps'] if step['id'] == config['put_step_id'])
    identity = dict(id='reminder:identity', name='提醒身份核验', protocol='HTTP', method='GET', url='/api/v1/me',
                    enabled=True, is_setup=False, body_type='NONE', body='', params={}, headers={},
                    assertions=[], extractors=[], execution_policy=deepcopy(put['execution_policy']))
    frozen['steps'].insert(frozen['steps'].index(put), identity)
    return frozen


def _fsync_directory(path: Path) -> None:
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class RecoveryStore:
    """Signed private portfolio files. Registry mutations require the resource-state OS lease."""

    def __init__(self, root: str | Path, secret: str) -> None:
        self.root = Path(root).absolute()
        self.secret = secret.encode()
        check(bool(self.secret), 'recovery_secret_missing')
        self._directory(self.root)

    def _directory(self, path: Path) -> Path:
        check(path == self.root or self.root in path.parents, 'recovery_path')
        for parent in reversed((path, *path.parents)):
            check(not parent.is_symlink() and not getattr(parent, 'is_junction', lambda: False)(), 'recovery_link')
            if not parent.exists():
                parent.mkdir(mode=0o700)
                _fsync_directory(parent.parent)
            check(parent.is_dir(), 'recovery_directory')
        return path

    def _execution(self, execution_id: int) -> Path:
        check(type(execution_id) is int and execution_id > 0, 'recovery_execution')
        return self._directory(self.root/'executions'/str(execution_id)/'reminder-recovery')

    def _write(self, path: Path, value: dict, exclusive: bool = False) -> None:
        self._directory(path.parent)
        check(not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)(), 'recovery_link')
        payload = encode(value)
        data = encode(dict(value=value, signature=hmac.new(self.secret, payload, hashlib.sha256).hexdigest()))
        destination = path if exclusive else path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if not exclusive:
                os.replace(destination, path)
            _fsync_directory(path.parent)
        except OSError as exc:
            raise RecoveryError('recovery_persist_failed') from exc

    def _read(self, path: Path) -> dict:
        check(not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)(), 'recovery_link')
        try:
            with path.open('rb') as stream:
                data = stream.read(16*1024*1024+1)
            check(len(data) <= 16*1024*1024 and path.stat().st_nlink == 1, 'recovery_file_limit')
            envelope = strict_json(data)
            check(isinstance(envelope, dict) and set(envelope) == {'value','signature'}, 'recovery_envelope')
            value = envelope['value']
            check(isinstance(value, dict) and isinstance(envelope['signature'], str) and
                  hmac.compare_digest(envelope['signature'], hmac.new(self.secret, encode(value), hashlib.sha256).hexdigest()), 'recovery_integrity')
            check(value.get('version') == VERSION, 'recovery_file_version')
            return value
        except (OSError, ValueError, TypeError) as exc:
            raise RecoveryError('recovery_file_unavailable') from exc

    def registry(self) -> dict:
        path = self._directory(self.root/'reminder-recovery')/'registry.json'
        if not path.exists():
            check(not list((self.root/'executions').glob('*/reminder-recovery/intent.json')), 'recovery_registry_missing')
            return dict(version=VERSION, scopes={})
        value = self._read(path)
        check(isinstance(value.get('scopes'), dict), 'recovery_registry_invalid')
        return value

    def occupied(self, scope: str) -> bool:
        entry = self.registry()['scopes'].get(scope)
        return bool(entry and entry.get('state') not in TERMINAL)

    def heartbeat(self, *, now: float | None = None) -> None:
        self._write(self.root/'reminder-recovery/heartbeat.json',
                    dict(version=VERSION, pid=os.getpid(), timestamp=time.time() if now is None else now))

    def require_daemon(self, *, now: float | None = None) -> None:
        value=self._read(self.root/'reminder-recovery/heartbeat.json')
        stamp=value.get('timestamp')
        check(type(stamp) in (int,float) and math.isfinite(stamp)
              and 0 <= (time.time() if now is None else now)-stamp <= 30, 'recovery_daemon_unavailable')

    def lineage(self) -> dict:
        return {scope:deepcopy(row['lineage']) for scope,row in self.registry()['scopes'].items()
                if row.get('state') in TERMINAL and isinstance(row.get('lineage'),dict)}

    def create(self, plan: dict, snapshot_hash: str) -> None:
        from .k6_execution import FileLease
        check(HEX.fullmatch(snapshot_hash), 'recovery_snapshot_hash')
        with FileLease(self.root, 'recovery-state'):
            path = self._execution(plan['execution_id'])
            check(not (path/'intent.json').exists(), 'recovery_intent_exists')
            registry = self.registry()
            for item in plan['resources']:
                entry = registry['scopes'].get(item['scope'])
                check(not entry or entry.get('state') in TERMINAL, 'recovery_scope_busy')
                previous=(entry or {}).get('lineage')
                check(previous == item.get('prior_lineage'), 'recovery_lineage_changed')
                registry['scopes'][item['scope']] = dict(execution_id=plan['execution_id'], vu=item['vu'], state='PENDING',lineage=previous)
            try:
                self._write(self.root/'reminder-recovery/registry.json', registry)
                self._write(path/'intent.json', plan, True)
                state = dict(version=VERSION, execution_id=plan['execution_id'], requests=0, reads=0, writes=0, completed_requests=0,
                    resources={str(r['vu']): dict(state='PENDING', phase='identity', attempts=0, next_retry=0, reason='not_reconciled') for r in plan['resources']})
                self._write(path/'state.json', state, True)
                # save_snapshot may have already created these directories without syncing their parents.
                # Publish ready only after the snapshot, journal and every owning directory are durable.
                ancestor=path
                while True:
                    _fsync_directory(ancestor)
                    if ancestor==self.root.parent:
                        break
                    ancestor=ancestor.parent
                self._write(path/'ready.json', dict(version=VERSION, intent_hash=digest(encode(plan)), snapshot_hash=snapshot_hash), True)
            except OSError as exc:
                raise RecoveryError('recovery_persist_failed') from exc

    def plan(self, execution_id: int) -> dict:
        value = self._read(self._execution(execution_id)/'intent.json')
        check(value.get('kind') == KIND and value.get('execution_id') == execution_id
              and isinstance(value.get('resources'), list) and 1 <= len(value['resources']) <= 1000, 'recovery_intent_invalid')
        return value

    def ready(self, execution_id: int, snapshot_hash: str) -> dict:
        value = self._read(self._execution(execution_id)/'ready.json')
        check(value.get('snapshot_hash') == snapshot_hash and value.get('intent_hash') == digest(encode(self.plan(execution_id))), 'recovery_ready_mismatch')
        self.state(execution_id)
        return value

    def state(self, execution_id: int, *, plan: dict | None = None) -> dict:
        value = self._read(self._execution(execution_id)/'state.json')
        plan = plan if plan is not None else self.plan(execution_id)
        check(value.get('execution_id') == execution_id and isinstance(value.get('resources'), dict)
              and set(value['resources']) == {str(row['vu']) for row in plan['resources']}, 'recovery_state_invalid')
        for row in value['resources'].values():
            check(row.get('state') in STATES and type(row.get('attempts')) is int and 0 <= row['attempts'] <= MAX_ATTEMPTS
                  and row.get('phase') in ('identity','normal_receipt','recover_receipt','recover_write','confirm_rule')
                  and type(row.get('next_retry')) in (int,float) and math.isfinite(row['next_retry']), 'recovery_state_invalid')
        for key in ('requests','reads','writes','completed_requests'):
            check(type(value.get(key)) is int and value[key] >= 0, 'recovery_state_invalid')
        check(value['requests'] == value['reads'] + value['writes'], 'recovery_state_counts')
        check(value['completed_requests'] <= value['requests'], 'recovery_state_counts')
        return value

    def change(self, execution_id: int, vu: int, mutation: object, *, plan: dict | None = None) -> dict:
        from .k6_execution import FileLease
        with FileLease(self.root, 'recovery-state'):
            plan = plan if plan is not None else self.plan(execution_id)
            value = self.state(execution_id,plan=plan)
            item = next((r for r in plan['resources'] if r['vu'] == vu), None)
            check(item is not None, 'recovery_vu')
            mutation(value, value['resources'][str(vu)])
            self._write(self._execution(execution_id)/'state.json', value)
            registry = self.registry()
            entry = registry['scopes'].get(item['scope'])
            check(entry and entry.get('execution_id') == execution_id and entry.get('vu') == vu, 'recovery_scope_mismatch')
            row = value['resources'][str(vu)]
            entry['state'] = row['state']
            if row['state'] == 'CLEANED':
                entry['lineage'] = dict(rule_id=row['cleanup']['rule_id'], revision=row['cleanup']['after_revision'])
            self._write(self.root/'reminder-recovery/registry.json', registry)
            return value

    def public(self, execution_id: int) -> dict:
        value = self.state(execution_id)
        registry=self.registry()['scopes']
        for item in self.plan(execution_id)['resources']:
            row=value['resources'][str(item['vu'])]
            entry=registry.get(item['scope'])
            check(entry and ((entry.get('execution_id')==execution_id and entry.get('state')==row['state'])
                  or (entry.get('execution_id')!=execution_id and row['state'] in TERMINAL)), 'recovery_registry_incomplete')
        rows = list(value['resources'].values())
        result = dict(version=VERSION, kind=KIND, observed=True, planned=len(rows),
                      requests=value['requests'], reads=value['reads'], writes=value['writes'],
                      completed_requests=value['completed_requests'], unknown_requests=value['requests']-value['completed_requests'])
        for key, state in (('pending','PENDING'), ('cleaned','CLEANED'), ('cancelled','CANCELLED'), ('conflict','CONFLICT')):
            result[key] = sum(row['state'] == state for row in rows)
        result['state'] = 'CONFLICT' if result['conflict'] else 'PENDING' if result['pending'] else 'RECOVERED'
        return result

    def repair_registry(self, execution_id: int) -> None:
        """Only the independent daemon closes the state-fsync/registry-fsync crash window."""
        from .k6_execution import FileLease
        with FileLease(self.root,'recovery-state'):
            plan=self.plan(execution_id);state=self.state(execution_id,plan=plan);registry=self.registry()
            changed=False
            for item in plan['resources']:
                row=state['resources'][str(item['vu'])];entry=registry['scopes'].get(item['scope'])
                check(entry is not None,'recovery_scope_mismatch')
                if entry.get('execution_id')!=execution_id:
                    check(row['state'] in TERMINAL,'recovery_scope_mismatch');continue
                check(entry.get('vu')==item['vu'],'recovery_scope_mismatch')
                if entry.get('state')!=row['state']:
                    check(entry.get('state')=='PENDING','recovery_registry_incomplete')
                    entry['state']=row['state'];changed=True
                    if row['state']=='CLEANED':
                        entry['lineage']=dict(rule_id=row['cleanup']['rule_id'],revision=row['cleanup']['after_revision'])
            if changed:
                self._write(self.root/'reminder-recovery/registry.json',registry)


def verify_frozen(store: RecoveryStore, execution_id: int, snapshot: dict) -> dict:
    store.ready(execution_id,digest(encode(snapshot)))
    plan=store.plan(execution_id)
    check(not validate_snapshot(snapshot), 'recovery_frozen_invalid')
    binding=(snapshot.get('runtime_config') or {}).get('_reminder_recovery') or {}
    check(binding.get('config') == plan['config'] and binding.get('origin') == plan['origin']
          and binding.get('path') == plan['path'] and snapshot.get('account_pool') == plan['pool'], 'recovery_frozen_mismatch')
    for item in plan['resources']:
        token_for(snapshot,item)
    return plan


def token_for(snapshot: dict, item: dict) -> str:
    pool=snapshot['account_pool']
    auth=snapshot['runtime_config']['auth_profile']
    variable=next(v for v in snapshot['variables'] if v['name'] == auth['access_token_variable'])
    row=snapshot['csv_data'][pool['data_key']]['rows'][item['vu']-1]
    token=row[variable['column']]
    check(canonical_owner(row[pool['identity_column']]) == item['owner']
          and digest(token.encode()) == item['token_hash'], 'recovery_frozen_identity')
    return token


def configured_store() -> RecoveryStore:
    from django.conf import settings
    from .executor import _private_root
    configured=Path(settings.PERF_PRIVATE_ROOT).absolute()
    check(all(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)()
              for path in (configured,*configured.parents)),'recovery_link')
    return RecoveryStore(_private_root(),settings.SECRET_KEY)


def is_managed(execution: object) -> bool:
    return bool((execution.load_snapshot or {}).get('_resource_recovery'))


def observed_status(execution: object) -> dict:
    if not is_managed(execution):
        return {}
    try:
        from .k6_execution import load_snapshot
        store=configured_store()
        verify_frozen(store,execution.id,load_snapshot(store.root,execution.id))
        return store.public(execution.id)
    except Exception:
        return dict(version=VERSION,kind=KIND,observed=False,state='CORRUPT')


def may_delete(execution: object) -> bool:
    return not is_managed(execution) or observed_status(execution).get('state') == 'RECOVERED'


def protect_execution(sender: object, instance: object, **kwargs: object) -> None:
    from django.db.models.deletion import ProtectedError
    if not may_delete(instance):
        raise ProtectedError('提醒恢复尚未确认完成；执行记录与私密意图必须保留', [instance])


def prepare_managed(snapshot: dict) -> RecoveryStore | None:
    if not (snapshot.get('runtime_config') or {}).get('resource_recovery'):
        return None
    errors=validate_snapshot(snapshot)
    check(not errors,'recovery_invalid_binding')
    store=configured_store()
    store.require_daemon()
    probe=build_plan(snapshot,1,store.secret.decode())
    for item in probe['resources']:
        check(not store.occupied(item['scope']),'recovery_scope_busy')
    client=RecoveryHTTP(probe['origin'],token_for(snapshot,probe['resources'][0]))
    status,data=client('GET','/api/v1/portfolio/reminder-capabilities')
    capability=data.get('recovery') if isinstance(data,dict) else None
    check(status==200 and isinstance(capability,dict) and capability.get('version')=='v1'
          and capability.get('enabled') is True,
          'recovery_capability_unavailable')
    return store


def recovery_tick(*, store: RecoveryStore | None = None, limit: int = 8, progress: object = None) -> int:
    """Daemon only: recover terminal executions under the existing engine/start exclusion."""
    from ..models import PerfExecution
    from .k6_execution import FileLease, K6ExecutionError, load_snapshot
    store=store or configured_store()
    processed=0
    def heartbeat() -> None:
        store.heartbeat()
        if progress is not None:
            progress()
    heartbeat()
    try:
        with ExitStack() as run_guard:
            # Serialize admission with create/spawn, then release start before recovery I/O.
            with FileLease(store.root, 'start'):
                active = PerfExecution.objects.filter(status__in=PerfExecution.ACTIVE_STATUSES,
                                                      load_snapshot___engine='K6')
                if active.exists():
                    return 0
                run_guard.enter_context(FileLease(store.root, 'run'))
                # A terminal DB flag is insufficient while a worker still owns run.
                if active.exists():
                    return 0
            candidates=PerfExecution.objects.filter(load_snapshot__has_key='_resource_recovery',
                status__in=PerfExecution.FINAL_STATUSES).order_by('id')
            for execution in candidates.iterator():
                try:
                    snapshot=load_snapshot(store.root,execution.id)
                    plan=verify_frozen(store,execution.id,snapshot)
                    store.repair_registry(execution.id)
                    state=store.state(execution.id)
                    for item in plan['resources']:
                        row=state['resources'][str(item['vu'])]
                        if row['state']!='PENDING' or row['next_retry']>time.time() or row['attempts']>=MAX_ATTEMPTS:
                            continue
                        client=RecoveryHTTP(plan['origin'],token_for(snapshot,item))
                        reconcile_one(store,execution.id,item['vu'],client,_plan=plan)
                        processed+=1
                        heartbeat()
                        if processed>=limit:
                            break
                    public=store.public(execution.id)
                except Exception:
                    public=dict(version=VERSION,kind=KIND,observed=False,state='CORRUPT')
                PerfExecution.objects.filter(pk=execution.id).update(resource_recovery=public)
                from .executor import push_update
                push_update(execution.id,{'resource_recovery':public})
                heartbeat()
                if processed>=limit:
                    break
    except K6ExecutionError:
        pass
    heartbeat()
    return processed


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


class RecoveryHTTP:
    """Only authenticated portfolio recovery endpoints; no redirect, proxy or arbitrary verb."""

    def __init__(self, base: str, token: str, timeout: float = 5) -> None:
        self.base = origin(base)
        check(isinstance(token, str) and token.strip() and not any(ord(c) < 32 for c in token), 'recovery_token_missing')
        self.token = token
        check(type(timeout) in (int,float) and math.isfinite(timeout) and 0 < timeout <= 5, 'recovery_timeout')
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def __call__(self, method: str, path: str, body: str = '', key: str = '') -> tuple:
        allowed = (method == 'GET' and (re.fullmatch(RECEIPTS+'[0-9a-f]{64}',path)
                   or re.fullmatch(PREFIX+'(?:sh|sz|bj)[0-9]{6}',path)
                   or path in ('/api/v1/portfolio/reminder-capabilities','/api/v1/me')))
        allowed = allowed or (method == 'DELETE' and re.fullmatch(PREFIX+'(?:sh|sz|bj)[0-9]{6}',path))
        check(allowed and len(body.encode()) <= 65536, 'recovery_request_scope')
        headers = {'Authorization':'Bearer '+self.token,'Accept':'application/json'}
        if method == 'DELETE':
            check(isinstance(key,str) and re.fullmatch(r'rr1-[0-9a-f]{32}-[1-9][0-9]{0,3}-recover',key), 'recovery_command_key')
            headers.update({'Content-Type':'application/json','Idempotency-Key':key})
        else:
            check(not body and not key, 'recovery_get_body')
        received=False
        deadline=time.monotonic()+self.timeout
        try:
            try:
                response = self.opener.open(Request(self.base+path,data=body.encode() if method=='DELETE' else None,
                                                    headers=headers,method=method),timeout=self.timeout)
            except HTTPError as error:
                response = error
            with response:
                received=True
                status = response.code
                if status not in (200,409):
                    check(status not in (301,302,303,307,308), 'recovery_redirect')
                    return status,None
                stream=response.fp if isinstance(response,HTTPError) else response
                chunks=[];size=0
                while True:
                    if stream.isclosed():
                        break
                    remaining=deadline-time.monotonic()
                    check(remaining>0,'recovery_response_deadline')
                    socket=getattr(getattr(stream.fp,'raw',None),'_sock',None)
                    check(socket is not None,'recovery_response_contract')
                    socket.settimeout(remaining)
                    part=stream.read1(min(65536,1024*1024+1-size))
                    if not part:
                        break
                    chunks.append(part);size+=len(part)
                    check(size <= 1024*1024,'recovery_response_contract')
                content=b''.join(chunks)
                check(len(content) <= 1024*1024 and response.headers.get('Content-Type','').split(';')[0] == 'application/json', 'recovery_response_contract')
                value = strict_json(content)
                if status==409:
                    check(isinstance(value,dict),'recovery_response_contract')
                    return status,{'error_code':'CONFLICT' if value.get('code')=='CONFLICT' else 'UNAVAILABLE'}
                check(isinstance(value,dict) and value.get('code') == 'OK' and isinstance(value.get('data'),dict), 'recovery_response_contract')
                return status,value['data']
        except (URLError,OSError,TimeoutError) as exc:
            error=RecoveryError('transport_unavailable');error.response_received=received
            raise error from exc
        except RecoveryError as exc:
            exc.response_received=received
            raise


def cleanup_receipt(value: object, item: dict, plan: dict, command: str) -> dict:
    check(isinstance(value,dict) and value.get('request_key') == item[command+'_digest']
          and value.get('request_fingerprint') == item[command+'_fingerprint']
          and value.get('operation') == 'delete' and value.get('stock_code') == plan['config']['stock_code']
          and value.get('state') == 'committed' and isinstance(value.get('created_at'),str), 'recovery_receipt_mismatch')
    cleanup = value.get('cleanup')
    check(isinstance(cleanup,dict), 'recovery_cleanup_missing')
    if cleanup.get('outcome') == 'cancelled_before_create':
        check(set(cleanup) == {'outcome','deleted'} and cleanup['deleted'] is False, 'recovery_cleanup_invalid')
    else:
        check(set(cleanup) == {'outcome','deleted','rule_id','before_revision','after_revision'}
              and cleanup.get('outcome') == 'deleted' and cleanup.get('deleted') is True, 'recovery_cleanup_invalid')
        canonical_owner(cleanup.get('rule_id'))
        before,after = cleanup.get('before_revision'),cleanup.get('after_revision')
        check(type(before) is int and type(after) is int and 1 <= before < 4294967295 and after == before+1, 'recovery_cleanup_revision')
    return deepcopy(cleanup)


def reconcile_one(store: RecoveryStore, execution_id: int, vu: int, client: object, *, now: float | None = None,
                  _plan: dict | None = None) -> None:
    """Caller owns the independent recovery lease; each visit spends at most three HTTP attempts."""
    now = time.time() if now is None else now
    plan = _plan if _plan is not None else store.plan(execution_id)
    item = next((r for r in plan['resources'] if r['vu'] == vu), None)
    check(item is not None, 'recovery_vu')
    def change(**fields: object) -> dict:
        return store.change(execution_id,vu,lambda _value,row:row.update(fields),plan=plan)
    def call(method: str,path: str,body: str = '',key: str = '') -> tuple:
        def started(value: dict,row: dict) -> None:
            check(row['attempts'] < MAX_ATTEMPTS, 'recovery_budget_exhausted')
            row['attempts'] += 1;value['requests'] += 1
            value['reads' if method=='GET' else 'writes'] += 1
        store.change(execution_id,vu,started,plan=plan)
        try:
            result = client(method,path,body,key)
        except RecoveryError as exc:
            if exc.response_received:
                store.change(execution_id,vu,lambda value,_row:value.update(completed_requests=value['completed_requests']+1),plan=plan)
            raise
        store.change(execution_id,vu,lambda value,_row:value.update(completed_requests=value['completed_requests']+1),plan=plan)
        return result
    # A persisted identity observation never authorizes a later recovery visit.
    row = store.state(execution_id,plan=plan)['resources'][str(vu)]
    if row['state'] != 'PENDING' or row['next_retry'] > now or row['attempts'] >= MAX_ATTEMPTS:
        return
    try:
        status,data = call('GET','/api/v1/me')
        check(status == 200, 'identity_unavailable')
        check(isinstance(data,dict) and canonical_owner(data.get('user_id')) == item['owner'], 'recovery_owner_mismatch')
        change(identity=dict(owner=item['owner'],token_hash=item['token_hash'],pool_hash=plan['pool']['content_hash'],observed_at=now),
               phase='normal_receipt' if row['phase']=='identity' else row['phase'])
    except RecoveryError as exc:
        if str(exc) in ('recovery_owner','recovery_owner_mismatch'):
            change(state='CONFLICT',reason='recovery_owner_mismatch')
        else:
            change(reason='identity_unavailable',next_retry=now+30)
        return
    for _ in range(2):
        row = store.state(execution_id,plan=plan)['resources'][str(vu)]
        if row['state'] != 'PENDING' or row['next_retry'] > now:
            return
        if row['attempts'] >= MAX_ATTEMPTS:
            change(reason='recovery_budget_exhausted');return
        try:
            phase = row['phase']
            if phase in ('normal_receipt','recover_receipt'):
                command = 'delete' if phase=='normal_receipt' else 'recover'
                status,data = call('GET',RECEIPTS+item[command+'_digest'])
                if status == 404:
                    change(phase='recover_receipt' if command=='delete' else 'recover_write');continue
                check(status == 200, 'identity_unavailable' if status in (401,403) else 'recovery_service_unavailable')
                cleanup = cleanup_receipt(data,item,plan,command)
                if cleanup['outcome'] == 'cancelled_before_create':
                    change(state='CANCELLED',cleanup=cleanup,reason='cancelled_before_create');return
                change(phase='confirm_rule',cleanup=cleanup);continue
            if phase == 'recover_write':
                # Persist the read-after-uncertain-write phase before any network side effect.
                change(phase='recover_receipt')
                status,_data = call('DELETE',plan['path'],item['delete_body'],item['recover_key'])
                check(not (status==409 and isinstance(_data,dict) and _data.get('error_code')=='CONFLICT'),'recovery_conflict')
                check(status == 200, 'identity_unavailable' if status in (401,403) else 'recovery_service_unavailable')
                continue
            check(phase == 'confirm_rule', 'recovery_phase_invalid')
            status,_data = call('GET',plan['path'])
            if status == 200:
                change(state='CONFLICT',reason='rule_active_after_cleanup');return
            check(status == 404, 'identity_unavailable' if status in (401,403) else 'recovery_service_unavailable')
            change(state='CLEANED',reason='confirmed_deleted');return
        except RecoveryError as exc:
            reason = str(exc)
            if reason in ('recovery_owner_mismatch','recovery_receipt_mismatch','recovery_cleanup_missing','recovery_cleanup_invalid','recovery_cleanup_revision','recovery_conflict'):
                change(state='CONFLICT',reason=reason);return
            change(reason=reason,next_retry=now+min(300,2**min(row['attempts']+1,8)));return
