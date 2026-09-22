"""Bounded, timestamp-bracketed observations of the fixed k6 status API."""
from collections import deque
import json
import math
import re
import threading
import time

VERSION = 'native_vu_v1'
SOURCE = 'k6_rest_status_v1'
MAX_RESPONSE_BYTES = 16 * 1024
MAX_OBSERVATIONS = 128
POLICY = {'version': VERSION, 'source': SOURCE, 'period_ms': 1000,
          'max_gap_ms': 2000, 'coverage_max_rtt_ms': 750, 'exec_timeout_ms': 2000}
RESULTS = frozenset(('ok', 'not_ready', 'timeout', 'transport_error', 'malformed',
                     'oversize', 'ownership_mismatch', 'collector_failed', 'missed_deadline'))
NUMBER_FIELDS = ('execution_id', 'observation_seq', 'scheduled_offset_ms',
                 'request_start_offset_ms', 'request_end_offset_ms', 'received_at_utc_ms', 'rtt_ms')


class NativeVUError(Exception):
    def __init__(self, category: str) -> None:
        self.category = category if category in RESULTS and category != 'ok' else 'collector_failed'
        super().__init__(self.category)


def _integer(value: object) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and 0 <= value <= 2**53 - 1)


def parse_status(body: bytes) -> dict:
    if len(body) > MAX_RESPONSE_BYTES:
        raise NativeVUError('oversize')
    try:
        data = json.loads(body)['data']
        if data.get('type') != 'status' or data.get('id') != 'default':
            raise ValueError()
        attrs = data['attributes']
        if any(not _integer(attrs.get(key)) for key in ('vus', 'vus-max', 'status')):
            raise ValueError()
        if any(type(attrs.get(key)) is not bool for key in ('running', 'paused', 'stopped')):
            raise ValueError()
        return {'active_vus': attrs['vus'], 'initialized_vus': attrs['vus-max'],
                'status': attrs['status'], 'running': attrs['running'],
                'paused': attrs['paused'], 'stopped': attrs['stopped']}
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise NativeVUError('malformed') from exc


def sanitize_observation(value: object) -> dict:
    if not isinstance(value, dict) or value.get('version') != VERSION or value.get('source') != SOURCE:
        return {}
    if any(not _integer(value.get(key)) for key in NUMBER_FIELDS):
        return {}
    if not value['observation_seq'] or value.get('result') not in RESULTS:
        return {}
    if not isinstance(value.get('runner_instance'), str) or not re.fullmatch('[a-f0-9]{32}', value['runner_instance']):
        return {}
    start, end = value['request_start_offset_ms'], value['request_end_offset_ms']
    if end < start or value['rtt_ms'] != end - start:
        return {}
    result = {key: value[key] for key in NUMBER_FIELDS}
    result.update(version=VERSION, source=SOURCE, runner_instance=value['runner_instance'],
                  timestamp_kind='collector_request_interval', result=value['result'])
    if value['result'] == 'ok':
        if any(not _integer(value.get(key)) for key in ('active_vus', 'initialized_vus', 'status')):
            return {}
        if any(type(value.get(key)) is not bool for key in ('running', 'paused', 'stopped')):
            return {}
        result.update({key: value[key] for key in ('active_vus', 'initialized_vus', 'status',
                                                  'running', 'paused', 'stopped')})
    else:
        result.update(dict.fromkeys(('active_vus', 'initialized_vus', 'status', 'running', 'paused', 'stopped')))
    return result


class ObservationSummary:
    def __init__(self, expected_vus: int, configured_duration: float | None = None) -> None:
        self.target = expected_vus
        self.configured_duration = configured_duration
        self.identity = None
        self.previous = None
        self.segment_first = None
        self.counts = {key: 0 for key in ('attempt_count', 'valid_count', 'missing_count',
                      'invalid_count', 'identity_mismatch_count', 'sequence_gap_count',
                      'below_target_count', 'target_count', 'state_uncertain_count',
                      'late_count', 'target_segment_count', 'max_gap_ms',
                      'longest_target_span_lower_ms', 'longest_target_span_upper_ms')}
        self.first_start = None
        self.last_end = None
        self.first_valid_start = None
        self.last_valid_end = None

    def add(self, value: dict) -> None:
        self.counts['attempt_count'] += 1
        row = sanitize_observation(value)
        if not row:
            self.counts['invalid_count'] += 1
            self.segment_first = self.previous = None
            return
        identity = (row['execution_id'], row['runner_instance'])
        if self.identity is None:
            self.identity = identity
        if self.identity != identity:
            self.counts['identity_mismatch_count'] += 1
            self.segment_first = self.previous = None
            return
        start, end = row['request_start_offset_ms'], row['request_end_offset_ms']
        if self.first_start is None:
            self.first_start = start
        self.last_end = end
        linked = False
        if self.previous is not None:
            gap = start - self.previous['request_start_offset_ms']
            self.counts['max_gap_ms'] = max(self.counts['max_gap_ms'], gap)
            sequence_ok = row['observation_seq'] == self.previous['observation_seq'] + 1
            if not sequence_ok:
                self.counts['sequence_gap_count'] += 1
            linked = sequence_ok and 0 <= gap <= POLICY['max_gap_ms'] and start >= self.previous['request_end_offset_ms']
        self.previous = row
        if row['result'] != 'ok':
            self.counts['missing_count'] += 1
            self.segment_first = None
            return
        self.counts['valid_count'] += 1
        if self.first_valid_start is None:
            self.first_valid_start = start
        self.last_valid_end = end
        late = row['rtt_ms'] > POLICY['coverage_max_rtt_ms']
        if late:
            self.counts['late_count'] += 1
        eligible = (row['status'] == 7 and row['running'] and not row['paused']
                    and not row['stopped'] and row['active_vus'] <= row['initialized_vus'])
        if not eligible:
            self.counts['state_uncertain_count'] += 1
            self.segment_first = None
            return
        if row['active_vus'] < self.target:
            self.counts['below_target_count'] += 1
        if row['active_vus'] == self.target:
            self.counts['target_count'] += 1
        # A late response remains an observation, but cannot establish coverage.
        if late or row['active_vus'] != self.target:
            self.segment_first = None
            return
        if self.segment_first is None or not linked:
            self.segment_first = row
            self.counts['target_segment_count'] += 1
        else:
            first = self.segment_first
            self.counts['longest_target_span_lower_ms'] = max(
                self.counts['longest_target_span_lower_ms'], start-first['request_end_offset_ms'])
            self.counts['longest_target_span_upper_ms'] = max(
                self.counts['longest_target_span_upper_ms'], end-first['request_start_offset_ms'])

    def result(self) -> dict:
        return dict(self.counts, version=VERSION, source=SOURCE, policy=dict(POLICY),
                    expected_vus=self.target, configured_duration_seconds=self.configured_duration,
                    first_request_start_offset_ms=self.first_start, last_request_end_offset_ms=self.last_end,
                    first_valid_request_start_offset_ms=self.first_valid_start,
                    last_valid_request_end_offset_ms=self.last_valid_end,
                    sustained_concurrency_verified=None,
                    notice='原生活动 VU 为请求时间区间内的离散观测；采样点间跨度不证明每一瞬间的持续并发。')


def summarize(observations: list, expected_vus: int, configured_duration: float | None = None) -> dict:
    summary = ObservationSummary(expected_vus, configured_duration)
    for row in observations:
        summary.add(row)
    return summary.result()


class NativeVUCollector:
    def __init__(self, transport, *, execution_id: int, runner_instance: str, expected_vus: int,
                 origin: float, configured_duration: float | None = None) -> None:
        self.transport = transport
        self.execution_id = execution_id
        self.runner_instance = runner_instance
        self.origin = origin
        self.cancel = threading.Event()
        self._lock = threading.Lock()
        self._queue = deque()
        self._thread = None
        self._seq = 0
        self._summary = ObservationSummary(expected_vus, configured_duration)
        self._late_discarded = 0
        self._overflow = 0
        self._missed_deadlines = 0
        self._failure = ''
        self._stopped_at = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self.cancel.is_set():
                return
            self._thread = threading.Thread(target=self._run, name='k6-native-vu', daemon=True)
            self._thread.start()

    def _run(self) -> None:
        deadline = time.monotonic()
        try:
            while not self.cancel.is_set():
                if self.cancel.wait(max(deadline-time.monotonic(), 0)):
                    break
                self.observe_once(deadline)
                deadline += POLICY['period_ms']/1000
                now = time.monotonic()
                if now > deadline:
                    skipped = math.floor(now-deadline) + 1
                    with self._lock:
                        self._missed_deadlines += skipped
                        self._seq += skipped
                    deadline += skipped
        except Exception:
            with self._lock:
                self._failure = 'collector_failed'
            self.cancel.set()

    def observe_once(self, scheduled: float) -> None:
        start = time.monotonic()
        attrs = {}
        try:
            attrs = parse_status(self.transport(self.cancel))
            result = 'ok'
        except NativeVUError as exc:
            result = exc.category
        except Exception:
            result = 'collector_failed'
        end = time.monotonic()
        start_ms, end_ms = max(0, round((start-self.origin)*1000)), max(0, round((end-self.origin)*1000))
        with self._lock:
            if self.cancel.is_set():
                self._late_discarded += 1
                return
            self._seq += 1
            row = sanitize_observation(dict(version=VERSION, source=SOURCE, execution_id=self.execution_id,
                runner_instance=self.runner_instance, observation_seq=self._seq,
                scheduled_offset_ms=max(0, round((scheduled-self.origin)*1000)),
                request_start_offset_ms=start_ms, request_end_offset_ms=end_ms,
                received_at_utc_ms=time.time_ns()//1_000_000, rtt_ms=end_ms-start_ms, result=result, **attrs))
            if not row:
                self._failure = 'collector_failed'
                self.cancel.set()
                return
            self._summary.add(row)
            if len(self._queue) >= MAX_OBSERVATIONS:
                self._overflow += 1
                self._failure = 'buffer_overflow'
                self.cancel.set()
            else:
                self._queue.append(row)
            if result in ('collector_failed', 'ownership_mismatch'):
                self._failure = result
                self.cancel.set()

    def drain(self) -> list:
        with self._lock:
            rows = list(self._queue)
            self._queue.clear()
            return rows

    def stop(self) -> bool:
        self.cancel.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        with self._lock:
            if self._stopped_at is None:
                self._stopped_at = time.monotonic()
            complete = not thread or not thread.is_alive()
            if not complete:
                self._failure = 'collector_stop_timeout'
            return complete

    def summary(self) -> dict:
        with self._lock:
            result = self._summary.result()
            last = result['last_valid_request_end_offset_ms']
            return dict(result, evidence_scope='collector_received', persistence_verified=None,
                        execution_id=self.execution_id, runner_instance=self.runner_instance,
                        available=result['valid_count'] > 0, timestamp_kind='collector_request_interval',
                        collector_running=bool(self._thread and self._thread.is_alive()),
                        collector_failure=self._failure or None,
                        late_discarded_count=self._late_discarded, overflow_count=self._overflow,
                        missed_deadline_count=self._missed_deadlines,
                        unobserved_head_ms=result['first_valid_request_start_offset_ms'],
                        unobserved_tail_ms=max(0,round((self._stopped_at-self.origin)*1000)-last)
                        if self._stopped_at is not None and last is not None else None)
