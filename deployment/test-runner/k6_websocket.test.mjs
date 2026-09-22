import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const sourcePath = new URL('../../testhub/apps/perf_testing/engines/k6_websocket.js', import.meta.url);
function pushSession(mode = 'success') {
  const source = readFileSync(sourcePath, 'utf8').replace('export function runWebSocketSession', 'function runWebSocketSession');
  const events = [], sent = [], context = { cycle: 'stale' };
  class Socket {
    constructor() {
      setTimeout(() => {
        this.onopen?.({});
        if (mode === 'initial') this.onmessage?.({ data: JSON.stringify({ type: 'quote.snapshot', code: 'sz000001', cycle: 42 }) });
      }, 1);
    }
    send(raw) {
      const frame = JSON.parse(raw); sent.push(frame);
      if (mode === 'multi_error' && frame.type === 'other.subscribe') return;
      setTimeout(() => {
        if (mode === 'timeout') return;
        if (mode === 'close') return this.onclose?.({});
        this.onmessage?.({ data: JSON.stringify({ type: 'unrelated', cycle: 'SECRET' }) });
        this.onmessage?.({ data: JSON.stringify({ type: mode === 'error' ? 'quote.error' : 'quote.snapshot',
          code: mode === 'mismatch' ? 'wrong' : 'sz000001', cycle: 42 }) });
        if (['hold_error', 'multi_error', 'hold_push'].includes(mode)) setTimeout(() => {
          this.onmessage?.({ data: JSON.stringify({ type: mode === 'hold_push' ? 'quote.snapshot' : 'quote.error' }) });
        }, 10);
      }, 1);
    }
    close() { setTimeout(() => this.onclose?.({}), 1); }
  }
  const config = { mode: 'PUSH', auth: { type: 'BEARER', token: '{{access_token}}' },
    connect_timeout_ms: 20, command_timeout_ms: 50, max_session_ms: 300,
    hold_open_ms: ['hold_error', 'hold_push'].includes(mode) ? 70 : 0, heartbeat_interval_ms: 0,
    commands: [{ name: 'Quote', event_type: 'quote.snapshot', error_types: ['quote.error'],
      ...(mode === 'initial' ? {} : { request: { type: 'quote.subscribe', codes: ['sz000001'] } }),
      assertions: [{ expr: '$.code', expected: 'sz000001' }], extractors: [{ name: 'cycle', expr: '$.cycle' }] }] };
  if (mode === 'multi_error') config.commands.push({ name: 'Other', request: { type: 'other.subscribe' },
    event_type: 'other.snapshot', event_path: '$.event_type', error_types: ['other.error'], assertions: [] });
  const sandbox = { Date, JSON, Promise, Object, Number, String, Array, setTimeout, clearTimeout };
  vm.runInNewContext(source + ';globalThis.run = runWebSocketSession;', sandbox);
  return { events, sent, context, result: sandbox.run(config, { WebSocket: Socket, context,
    render: value => value, jsonPath: (value, path) => value[path.slice(2)], nextId: () => 'unused',
    emit: event => events.push(event), remaining: () => Infinity, debug: true }) };
}

for (const mode of ['hold_error', 'multi_error', 'hold_push']) {
  test(`push ${mode} monitors previously armed errors without counting unsolicited success`, async () => {
    const s = pushSession(mode);
    assert.equal(await s.result, mode === 'hold_push');
    const terminal = s.events.filter(e => e.kind === 'request');
    assert.equal(terminal.length, 1);
    assert.equal(terminal[0].error, mode === 'hold_push' ? '' : 'WSEventError');
    assert.equal(s.events.filter(e => e.stage === 'event' && e.state === 'completed' && e.ok).length, 1);
    assert.equal(s.context.cycle, mode === 'hold_push' ? 42 : undefined);
  });
}

for (const mode of ['success', 'initial', 'timeout', 'close', 'error', 'mismatch']) {
  test(`push ${mode} requires a matched asserted event and clears failed outputs`, async () => {
    const s = pushSession(mode), ok = ['success', 'initial'].includes(mode);
    assert.equal(await s.result, ok);
    assert.equal(s.context.cycle, ok ? 42 : undefined);
    assert.equal(s.sent.length, mode === 'initial' ? 0 : 1);
    if (s.sent.length) assert.deepEqual(s.sent[0], { type: 'quote.subscribe', codes: ['sz000001'] });
    const terminal = s.events.filter(e => e.kind === 'request');
    assert.equal(terminal.length, 1);
    assert.equal(s.events.filter(e => e.stage === 'event' && e.state === 'completed').length, 1);
    assert.ok(!JSON.stringify(s.events).includes('SECRET'));
  });
}
function session(mode = 'success', changes = {}, control = {}) {
  assert.ok(existsSync(sourcePath), 'WebSocket session runtime must exist');
  const source = readFileSync(sourcePath, 'utf8').replace('export function runWebSocketSession', 'function runWebSocketSession');
  const events = [], sent = [], context = { access_token: 'SECRET_TOKEN', prior: 'stale' };
  let socket, serial = 0;
  class Socket {
    constructor(url, protocols, options) {
      socket = this; this.options = options; this.url = url;
      if (mode !== 'no_handshake') setTimeout(() => this.onopen?.({}), 1);
    }
    send(raw) {
      const frame = JSON.parse(raw); sent.push(frame);
      setTimeout(() => {
        if (mode === 'close' && frame.action !== 'auth') return this.onclose?.({ reason: 'SECRET_REASON' });
        if (mode === 'timeout' && frame.action !== 'auth') return;
        if (mode === 'second_failed' && frame.action === 'second') return this.onmessage?.({ data: JSON.stringify({ id: frame.id, ok: false }) });
        if (mode === 'binary') return this.onmessage?.({ data: new ArrayBuffer(3) });
        if (mode === 'malformed') return this.onmessage?.({ data: 'SECRET_NOT_JSON' });
        const ok = mode === 'auth_failed' && frame.action === 'auth' ? false : mode === 'false_command' && frame.action !== 'auth' ? 'true' : true;
        this.onmessage?.({ data: JSON.stringify({ id: 'wrong', ok: true, data: { secret: 'SECRET_RESPONSE' } }) });
        this.onmessage?.({ data: JSON.stringify({ event: 'changed', data: 'SECRET_EVENT' }) });
        this.onmessage?.({ data: JSON.stringify({ id: frame.id, ok, data: { value: 'SECRET_RESPONSE', next: 42 } }) });
      }, 1);
    }
    close() { setTimeout(() => this.onclose?.({ reason: 'SECRET_REASON' }), 1); }
  }
  const config = { connect_timeout_ms: 20, command_timeout_ms: 20, max_session_ms: 300,
    hold_open_ms: 0, heartbeat_interval_ms: 0,
    auth: { request: { version: 1, action: 'auth', payload: { token: '{{access_token}}' } }, assertions: [], extractors: [] },
    commands: [
      { request: { version: 1, action: 'first', payload: { id: '{{request_id}}', again: '{{request_id}}' } }, assertions: [{ expr: '$.ok', expected: true }], extractors: [{ name: 'prior', expr: '$.data.next' }] },
      { request: { version: 1, action: 'second', payload: { value: '{{prior}}', id: '{{request_id}}' } }, assertions: [], extractors: [] },
    ], ...changes };
  const render = (value, scope) => {
    if (value === 'missing_assertion_variable') throw new Error('SECRET_MISSING_VARIABLE');
    if (Array.isArray(value)) return value.map(v => render(v, scope));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, render(v, scope)]));
    if (typeof value !== 'string') return value;
    return value.replace(/\{\{(.*?)\}\}/g, (_, key) => key === 'request_id' ? scope.id ||= 'business-' + (++serial) : context[key] ?? '');
  };
  const sandbox = { Date, JSON, Promise, Object, Number, String, Array, Uint8Array, setTimeout, clearTimeout };
  vm.runInNewContext(source + ';globalThis.run = runWebSocketSession;', sandbox);
  const deps = { WebSocket: Socket, context, render: (value, scope) => {
    const rendered = render(value, scope);
    control.afterRender?.(rendered);
    return rendered;
  }, jsonPath: (data, path) => path.slice(2).split('.').reduce((v, k) => v?.[k], data),
    nextId: () => 'correlation-' + (++serial), emit: value => events.push(value), remaining: control.remaining || (() => Infinity),
    url: 'ws://localhost/socket', headers: {}, jar: {}, debug: true };
  return { result: sandbox.run(config, deps), context, events, sent, socket: () => socket };
}

test('same socket auth and ordered correlated commands with fresh per-command idempotency IDs', async () => {
  const s = session(); assert.equal(await s.result, true);
  assert.deepEqual(s.sent.map(v => v.action), ['auth', 'first', 'second']);
  assert.equal(s.sent[1].payload.id, s.sent[1].payload.again);
  assert.notEqual(s.sent[1].payload.id, s.sent[2].payload.id);
  assert.equal(s.sent[2].payload.value, '42');
  assert.equal(s.context.prior, 42);
  assert.equal(s.events.filter(v => v.kind === 'request').length, 1);
  assert.equal(s.events.filter(v => v.kind === 'ws_stage' && v.stage === 'command' && v.state === 'completed').length, 2);
  assert.ok(!JSON.stringify(s.events).includes('SECRET'));
});
for (const mode of ['auth_failed', 'false_command', 'second_failed', 'timeout', 'close', 'no_handshake', 'binary', 'malformed']) {
  test(`${mode} fails once and removes old extractor outputs`, async () => {
    const s = session(mode); assert.equal(await s.result, false);
    assert.equal(s.events.filter(v => v.kind === 'request').length, 1);
    assert.equal(s.context.prior, undefined);
    assert.ok(!JSON.stringify(s.events).includes('SECRET'));
  });
}
test('missing assertion variable fails once with fixed error and removes outputs', async () => {
  const s = session('success', { commands: [{ request: { version: 1, action: 'first', payload: {} },
    assertions: [{ expr: '$.ok', expected: 'missing_assertion_variable' }], extractors: [{ name: 'prior', expr: '$.data.next' }] }] });
  assert.equal(await s.result, false);
  assert.equal(s.events.filter(v => v.kind === 'request').length, 1);
  assert.equal(s.context.prior, undefined);
  assert.ok(!JSON.stringify(s.events).includes('SECRET'));
});
test('hold sends correlated application heartbeat outside command statistics', async () => {
  const s = session('success', { hold_open_ms: 50, heartbeat_interval_ms: 15 });
  assert.equal(await s.result, true);
  assert.ok(s.sent.some(v => v.action === 'ping'));
  assert.equal(s.events.filter(v => v.kind === 'ws_stage' && v.stage === 'command' && v.state === 'completed').length, 2);
});

test('deadline crossed while rendering cannot start or send a command', async () => {
  let remaining = 10000;
  const s = session('success', {}, { remaining: () => remaining,
    afterRender: value => { if (value?.action === 'first') remaining = 0; } });
  assert.equal(await s.result, false);
  assert.deepEqual(s.sent.map(frame => frame.action), ['auth']);
  assert.equal(s.events.filter(event => event.kind === 'ws_stage' && event.stage === 'command' && event.state === 'started').length, 0);
  assert.equal(s.events.filter(event => event.kind === 'request').length, 1);
  assert.equal(s.events.find(event => event.kind === 'request').error, 'WSLoadExpired');
  assert.equal(s.context.prior, undefined);
});

test('already expired session does not construct a socket', async () => {
  const s = session('success', {}, { remaining: () => 0 });
  assert.equal(await s.result, false);
  assert.equal(s.socket(), undefined);
  assert.equal(s.sent.length, 0);
  assert.equal(s.events.find(event => event.kind === 'request').error, 'WSLoadExpired');
});

test('deadline crossed during setup is checked again before socket construction', async () => {
  let checks = 0;
  const s = session('success', {}, { remaining: () => ++checks <= 2 ? 10000 : 0 });
  assert.equal(await s.result, false);
  assert.equal(s.socket(), undefined);
  assert.equal(s.sent.length, 0);
  assert.equal(s.events.find(event => event.kind === 'request').error, 'WSLoadExpired');
});

// Virtual time drives the shipped session, including real close observations;
// this fixture performs no network I/O and does not replace session decisions.
async function clockSession({ start = 29900, openMs = 10, ackMs = [10, 10, 10, 10, 10],
  closeMs = 0, drainEnd = 65000, maxSession = 15000, commandTimeout = 5000,
  connectTimeout = 5000, hold = 0, negativeAt = -1, fixedRounds = false,
  renderDelay = 0, delayedDelivery = false, lateHold = false, lateClose = false,
  closeAtSessionLimit = false, loadExpired, lateOpen = false } = {}) {
  let now = start, serial = 0, result, settled = false, constructed = 0, fatal = 0;
  const tasks = new Map(), events = [], sent = [], context = { output: 'old' };
  const schedule = (fn, ms) => {
    const id = ++serial;
    tasks.set(id, { at: now + ms, fn: () => { if (lateHold && ms === 40) now += 1500; fn(); }, id });
    return id;
  };
  const sandbox = { Date: { now: () => now }, Promise, setTimeout: schedule, clearTimeout: id => tasks.delete(id) };
  const source = readFileSync(sourcePath, 'utf8').replace('export function runWebSocketSession', 'function runWebSocketSession');
  vm.runInNewContext(source + '\nthis.run = runWebSocketSession;', sandbox);
  class Socket {
    constructor() {
      constructed++;
      if (openMs !== null) schedule(() => {
        if (lateOpen) now += 2000;
        if (!this.closed) this.onopen?.();
      }, openMs);
    }
    send(raw) {
      const frame = JSON.parse(raw), index = sent.length, delay = ackMs[index];
      sent.push({ action: frame.action, at: now });
      if (delay !== null) schedule(() => {
        if (delayedDelivery) now += 2000;
        this.onmessage?.({ data: JSON.stringify({ id: frame.id, ok: index !== negativeAt, action: frame.action, output: 'new' }) });
      }, delay ?? 10);
    }
    close() {
      if (this.closed) return;
      this.closed = true;
      if (closeMs !== null) schedule(() => {
        if (lateClose) now += 2000;
        this.onclose?.();
      }, closeAtSessionLimit ? Math.max(start + maxSession - now + 10, 0) : closeMs);
    }
  }
  const config = { connect_timeout_ms: connectTimeout, command_timeout_ms: commandTimeout,
    max_session_ms: maxSession, heartbeat_interval_ms: 25000, hold_open_ms: hold,
    auth: { request: { action: 'auth' }, assertions: [{ expr: '$.action', expected: 'auth' }] },
    commands: ['directory.list', 'conversation.list', 'timeline.list', 'timeline.unread'].map(action => ({
      request: { action }, assertions: [{ expr: '$.action', expected: action }], extractors: [{ name: 'output', expr: '$.output' }],
    })) };
  sandbox.run(config, { WebSocket: Socket, context, remaining: () => 30000 - now, loadExpired,
    ...(fixedRounds ? {} : { drainRemaining: () => drainEnd - now }),
    emit: event => events.push(event), nextId: () => String(++serial),
    jsonPath: (data, path) => data[path.slice(2)],
    render: value => { if (value?.action === 'directory.list') now += renderDelay; return structuredClone(value); },
    fatal: () => fatal++,
  }).then(value => { result = value; settled = true; }, error => { result = error; settled = true; });
  for (let i = 0; i < 100 && !settled; i++) {
    await Promise.resolve();
    if (settled) break;
    const task = [...tasks.values()].sort((a, b) => a.at - b.at || a.id - b.id)[0];
    assert.ok(task, 'an unfinished session must have a bounded timer');
    tasks.delete(task.id); now = Math.max(now, task.at); task.fn();
  }
  assert.equal(settled, true);
  const terminals = events.filter(event => event.kind === 'request');
  assert.equal(terminals.length, loadExpired && result === loadExpired ? 0 : 1);
  return { result, sent, events, terminal: terminals[0], constructed, fatal, context };
}

test('admitted connection can handshake, authenticate and complete commands after load cutoff', async () => {
  const s = await clockSession({ start: 29094, openMs: 1500 });
  assert.equal(s.result, true);
  assert.equal(s.terminal.timestamp_ms, 30644);
  assert.equal(s.sent.length, 5);
  assert.equal(s.events.filter(e => e.kind === 'ws_connection' && e.state === 'closed').length, 1);
});

test('pending auth or intermediate ACK across cutoff can admit the remaining session commands', async () => {
  for (const ackMs of [[100, 10, 10, 10, 10], [10, 100, 10, 10, 10]]) {
    const s = await clockSession({ openMs: 50, ackMs });
    assert.equal(s.result, true);
    assert.equal(s.sent.length, 5);
    assert.ok(s.sent.some(frame => frame.at >= 30000));
  }
});

test('deadline does not reserve or shorten the load window for fast late sessions', async () => {
  const s = await clockSession({ start: 29999, openMs: 0 });
  assert.equal(s.result, true);
  assert.equal(s.constructed, 1);
  const expired = await clockSession({ start: 30000 });
  assert.equal(expired.constructed, 0);
  assert.equal(expired.terminal.error, 'WSLoadExpired');
});

test('drain keeps original connection timeout, command timeout and negative ACK failures', async () => {
  const stalled = await clockSession({ openMs: null });
  assert.equal(stalled.terminal.error, 'WSConnectTimeout');
  assert.equal(stalled.terminal.elapsed_ms, 5000);
  const ack = await clockSession({ ackMs: [150, null] });
  assert.equal(ack.terminal.error, 'WSCommandTimeout');
  assert.equal(ack.terminal.elapsed_ms, 5160);
  const negative = await clockSession({ ackMs: [150, 10], negativeAt: 1 });
  assert.equal(negative.terminal.error, 'WSNegativeAck');
  assert.equal(negative.context.output, undefined);
});

test('original session maximum still fails and clears pending outputs during drain', async () => {
  const s = await clockSession({ ackMs: [10, 600, 600], maxSession: 1000 });
  assert.equal(s.terminal.error, 'WSSessionTimeout');
  assert.equal(s.terminal.elapsed_ms, 1000);
  assert.equal(s.sent.length, 3);
  assert.equal(s.context.output, undefined);
});

test('global hard drain reserves bounded close and cannot admit further commands', async () => {
  const s = await clockSession({ drainEnd: 32000, ackMs: [10, 1500], closeMs: 500 });
  assert.equal(s.terminal.error, 'WSLoadExpired');
  assert.equal(s.terminal.timestamp_ms, 31500);
  assert.equal(s.sent.length, 2);
  assert.equal(s.events.filter(e => e.kind === 'ws_connection' && e.state === 'closed').length, 1);
});

test('hard drain missing close is fatal, never a fabricated successful close', async () => {
  const s = await clockSession({ drainEnd: 32000, ackMs: [10, null], closeMs: null });
  assert.equal(s.terminal.error, 'WSCloseTimeout');
  assert.equal(s.terminal.timestamp_ms, 32000);
  assert.equal(s.fatal, 1);
  assert.equal(s.events.filter(e => e.kind === 'ws_connection' && e.state === 'closed').length, 0);
});

test('late timer delivery and rendering recheck the work deadline before success or send', async () => {
  const delayed = await clockSession({ drainEnd: 32000, delayedDelivery: true });
  assert.equal(delayed.result, false);
  assert.equal(delayed.terminal.error, 'WSLoadExpired');
  assert.equal(delayed.sent.length, 1);
  const rendered = await clockSession({ drainEnd: 32000, renderDelay: 1500 });
  assert.equal(rendered.result, false);
  assert.equal(rendered.terminal.error, 'WSLoadExpired');
  assert.equal(rendered.sent.length, 1);
});

test('fixed iterations retain their original cutoff and hold does not extend idle traffic', async () => {
  const fixed = await clockSession({ fixedRounds: true, openMs: 50, ackMs: [100] });
  assert.equal(fixed.terminal.error, 'WSLoadExpired');
  assert.deepEqual(fixed.sent.map(frame => frame.action), ['auth']);
  const hold = await clockSession({ hold: 5000 });
  assert.equal(hold.result, true);
  assert.equal(hold.terminal.timestamp_ms, 30000);
});

test('a late hold callback cannot report success beyond the work deadline', async () => {
  const s = await clockSession({ hold: 40, drainEnd: 32000, lateHold: true });
  assert.equal(s.result, false);
  assert.equal(s.terminal.error, 'WSLoadExpired');
});

test('close observed after its allowed wait is a failure even if its timer callback is late', async () => {
  const s = await clockSession({ lateClose: true });
  assert.equal(s.result, false);
  assert.equal(s.terminal.error, 'WSCloseTimeout');
  assert.equal(s.events.filter(e => e.kind === 'ws_connection' && e.state === 'closed').length, 1);
});

test('work completed within session maximum retains its original bounded close allowance', async () => {
  const s = await clockSession({ maxSession: 1000, closeAtSessionLimit: true });
  assert.equal(s.result, true);
  assert.equal(s.terminal.elapsed_ms, 1010);
});

test('load expiry before admission produces no attempted session or false failure', async () => {
  const expired = new Error('LoadDurationElapsed');
  const s = await clockSession({ start: 30000, loadExpired: expired });
  assert.equal(s.result, expired);
  assert.equal(s.constructed, 0);
  assert.equal(s.events.length, 0);
});

test('late handshake and ACK delivery cannot bypass their original timeouts during drain', async () => {
  const connect = await clockSession({ connectTimeout: 1000, lateOpen: true });
  assert.equal(connect.result, false);
  assert.equal(connect.terminal.error, 'WSConnectTimeout');
  assert.equal(connect.sent.length, 0);
  const ack = await clockSession({ commandTimeout: 1000, delayedDelivery: true });
  assert.equal(ack.result, false);
  assert.equal(ack.terminal.error, 'WSCommandTimeout');
  assert.equal(ack.sent.length, 1);
});
