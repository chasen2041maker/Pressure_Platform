"""Bounded public counters for native WebSocket observations."""
from .metrics import StepMetrics

STAGES = ('sessions', 'connect', 'auth', 'commands', 'events', 'heartbeat')
WS_ERRORS = frozenset(('WSNegativeAck', 'WSCommandTimeout', 'WSHeartbeatTimeout', 'WSConnectTimeout',
                      'WSSessionTimeout', 'WSLoadExpired', 'WSCloseTimeout', 'WSUnexpectedClose',
                      'WSTransportError', 'WSBinaryFrame', 'WSFrameTooLarge', 'WSInvalidJSON',
                      'WSPreparationFailed', 'WSEventTimeout', 'WSEventError', 'WSUnexpectedFrames',
                      'AssertionFailed', 'ExtractionFailed'))


def counts() -> dict:
    return {'started': 0, 'completed': 0, 'success': 0, 'failed': 0}


def public_counts(value: dict) -> dict:
    return dict(value, incomplete=max(value['started'] - value['completed'], 0))


class WebSocketMetrics:
    def __init__(self, steps: list[dict]):
        self.steps = steps
        self.stages = {stage: counts() for stage in STAGES}
        self.opened = set()
        self.peak = 0
        self.observed = False
        self.commands = {}
        self.pending = set()

    def consume(self, event: dict, index: int, vu: int) -> None:
        kind = event.get('kind')
        if kind == 'ws_connection':
            key = (vu, index)
            if event.get('state') == 'opened':
                self.observed = True
                self.opened.add(key)
                self.peak = max(self.peak, len(self.opened))
            elif event.get('state') == 'closed':
                self.opened.discard(key)
            return
        stage = {'command': 'commands', 'event': 'events'}.get(event.get('stage'), event.get('stage'))
        if stage not in STAGES or stage == 'sessions':
            return
        command = event.get('command')
        definition = None
        if stage in ('commands', 'events'):
            definitions = (self.steps[index].get('websocket_config') or {}).get('commands') or []
            if type(command) is not int or not 0 <= command < len(definitions):
                return
            definition = definitions[command]
        key = (vu, index, stage, command if definition is not None else None)
        if event.get('state') == 'started':
            if key in self.pending:
                return
            self.observed = True
            self.pending.add(key)
            self.stages[stage]['started'] += 1
        elif event.get('state') == 'completed' and key in self.pending:
            self.pending.discard(key)
            ok = event.get('ok') is True
            self.stages[stage]['completed'] += 1
            self.stages[stage]['success' if ok else 'failed'] += 1
            if definition is not None:
                metric = self.commands.setdefault((index, command), StepMetrics('', 'WEBSOCKET', ''))
                from .k6_thresholds import finite
                elapsed = finite(event.get('elapsed_ms'))
                if elapsed is not None and elapsed >= 0:
                    metric.record(elapsed, ok, error_type=None if ok else 'WebSocketCommandFailed',
                                  error_message='' if ok else 'websocket_command_failed')

    def snapshot(self, duration: float, ended: bool = False) -> dict:
        commands = []
        for (index, command), metric in sorted(self.commands.items()):
            step = self.steps[index]
            definition = step['websocket_config']['commands'][command]
            commands.append(dict(metric.to_stat(duration), step_id=step.get('id', f'legacy:{index + 1}'),
                command_index=command, name=definition.get('name', ''),
                action=definition.get('event_type') or definition.get('request', {}).get('action', ''),
                latency_kind='event_wait' if definition.get('event_type') else 'command'))
        return dict(version=1, **{key: public_counts(value) for key, value in self.stages.items()},
                    connections={'observed': self.observed, 'current': len(self.opened) if self.observed and not (ended and self.opened) else None,
                                 'peak': self.peak if self.observed else None, 'unclosed': len(self.opened)},
                    command_metrics=commands)
