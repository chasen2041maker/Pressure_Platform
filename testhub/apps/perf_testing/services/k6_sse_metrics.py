"""Safe bounded stream counters, distinct first-event and completion timings."""
from collections import Counter
from copy import deepcopy
from .k6_thresholds import finite

REASONS = frozenset(('completed', 'invalid_options', 'cancelled', 'total_timeout', 'idle_timeout',
    'transport_error', 'http_status', 'redirect', 'content_type', 'unexpected_eof', 'read_error',
    'event_bytes_limit', 'total_bytes_limit', 'events_limit', 'invalid_utf8', 'event_error',
    'business_error', 'callback_error', 'protocol_error', 'assertion_failed', 'extraction_failed',
    'invalid_json', 'event_unmatched', 'event_ambiguous', 'event_order', 'event_count', 'sequence',
    'terminal_missing', 'preparation_failed', 'runtime_unavailable'))


def safe_diagnostic(value, config=None):
    """Accept only positions, bounded by the frozen contract when available."""
    if not isinstance(value, dict) or set(value) - {'scope', 'event_index', 'rule_index', 'condition_index'}:
        return None
    scope = value.get('scope')
    shapes = {'event': (False, False), 'error_condition': (False, True), 'global_assertion': (False, True),
              'rule': (True, False), 'rule_assertion': (True, True), 'sequence': (True, False),
              'extractor': (True, True), 'terminal': (True, False), 'transport': (False, False)}
    if not isinstance(scope, str) or scope not in shapes:
        return None
    rule_required, condition_required = shapes[scope]
    expected = {'scope'} | ({'rule_index'} if rule_required else set()) | ({'condition_index'} if condition_required else set())
    if scope != 'transport' or 'event_index' in value:
        expected.add('event_index')
    if set(value) != expected:
        return None
    rules = config.get('rules', []) if isinstance(config, dict) else None
    bounds = {'event_index': config.get('max_events', 256) if isinstance(config, dict) else 4096,
              'rule_index': len(rules) if rules is not None else 16, 'condition_index': 16 if scope == 'extractor' else 32}
    for key in expected - {'scope'}:
        if type(value[key]) is not int or not 1 <= value[key] <= bounds[key]:
            return None
    if rules is not None:
        rule = rules[value['rule_index'] - 1] if rule_required else {}
        field = {'error_condition': 'error_conditions', 'global_assertion': 'assertions',
                 'rule_assertion': 'assertions', 'extractor': 'extractors'}.get(scope)
        if field and value['condition_index'] > len((rule if rule_required else config).get(field, [])):
            return None
        if scope == 'sequence' and not rule.get('sequence'):
            return None
    return dict(value)


def public_diagnostics(items, config=None):
    result = []
    for item in items[:32] if isinstance(items, list) else []:
        if not isinstance(item, dict) or set(item) - {'reason', 'count', 'scope', 'event_index', 'rule_index', 'condition_index'}:
            continue
        reason, count = item.get('reason'), item.get('count')
        if not isinstance(reason, str) or reason not in REASONS or reason == 'completed' or type(count) is not int or count <= 0:
            continue
        location = safe_diagnostic({key: value for key, value in item.items() if key not in ('reason', 'count')}, config)
        if location:
            result.append(dict(location, reason=reason, count=count))
    return result


def row():
    return {'streams': dict(started=0, completed=0, success=0, failed=0), 'events': 0, 'bytes': 0,
            'first_event': dict(count=0, total=0, min_ms=None, max_ms=None),
            'completion': dict(count=0, total=0, min_ms=None, max_ms=None), 'errors': Counter(),
            'diagnostics': Counter(), 'diagnostics_truncated': False}


def timing(target, value):
    value = finite(value)
    if value is None or value < 0 or value > 3600000:
        return
    target['count'] += 1; target['total'] += value
    target['min_ms'] = min(target['min_ms'], value) if target['min_ms'] is not None else value
    target['max_ms'] = max(target['max_ms'], value) if target['max_ms'] is not None else value


def public(value):
    result = deepcopy(value)
    result['streams']['incomplete'] = max(value['streams']['started'] - value['streams']['completed'], 0)
    for key in ('first_event', 'completion'):
        result[key]['avg_ms'] = round(result[key]['total'] / result[key]['count'], 3) if result[key]['count'] else None
        del result[key]['total']
    result['errors'] = [{'phase': phase, 'reason': reason, 'count': count}
                        for (phase, reason), count in sorted(value['errors'].items())]
    result['diagnostics'] = [dict(key, count=count) for key, count in value['diagnostics'].items()]
    return result


class SSEMetrics:
    def __init__(self, steps):
        self.steps = steps
        self.total = row()
        self.rows = {index: row() for index, step in enumerate(steps) if step.get('protocol') == 'SSE'}
        self.pending = {}

    def start(self, index, vu):
        if index not in self.rows or (index, vu) in self.pending:
            return
        self.pending[index, vu] = False
        for value in (self.total, self.rows[index]):
            value['streams']['started'] += 1

    def consume(self, event, index, vu):
        if index not in self.rows:
            return
        key = (index, vu)
        if event.get('kind') == 'sse_first_event':
            if key not in self.pending or self.pending[key]:
                return
            self.pending[key] = True
            for value in (self.total, self.rows[index]):
                timing(value['first_event'], event.get('elapsed_ms'))
            return
        if event.get('kind') != 'sse_result':
            return
        reason = event.get('reason') if event.get('reason') in REASONS else 'protocol_error'
        phase = event.get('phase') if event.get('phase') in ('preparation', 'transport', 'business') else 'transport'
        actual = key in self.pending and event.get('started') is True
        rejected = key in self.pending and event.get('started') is not True
        ok = actual and event.get('ok') is True and event.get('closed') is True and reason == 'completed'
        self.pending.pop(key, None)
        diagnostic = safe_diagnostic(event.get('diagnostic'), self.steps[index].get('sse_config')) if not ok else None
        for value in (self.total, self.rows[index]):
            if rejected:
                value['streams']['started'] -= 1
            if actual:
                value['streams']['completed'] += 1
                value['streams']['success' if ok else 'failed'] += 1
                timing(value['completion'], event.get('elapsed_ms'))
                for field, bound in (('events', 4097), ('bytes', 16777217)):
                    count = event.get(field)
                    if type(count) is int and 0 <= count <= bound:
                        value[field] += count
            if not ok:
                value['errors'][phase, reason] += 1
                if diagnostic:
                    position = tuple(sorted(dict(diagnostic, reason=reason).items()))
                    if position in value['diagnostics'] or len(value['diagnostics']) < 32:
                        value['diagnostics'][position] += 1
                    else:
                        value['diagnostics_truncated'] = True

    def snapshot(self):
        return dict(version=1, **public(self.total), stream_metrics=[
            dict(step_id=self.steps[index].get('id', f'legacy:{index + 1}'), **public(value))
            for index, value in self.rows.items()])
