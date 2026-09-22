const MAX_FRAME_BYTES = 128 * 1024;

function frameSize(value) {
  let bytes = 0;
  for (const char of value) {
    const code = char.codePointAt(0);
    bytes += code < 128 ? 1 : code < 2048 ? 2 : code < 65536 ? 3 : 4;
    if (bytes > MAX_FRAME_BYTES) return bytes;
  }
  return bytes;
}

// Transport and scheduling are supplied by the fixed k6 runtime; no frame values
// or arbitrary error/close text cross the event boundary.
export function runWebSocketSession(config, deps) {
  const push = config.mode === 'PUSH';
  const outputs = [config.auth, ...config.commands].flatMap(item => (item.extractors || []).map(rule => rule.name));
  const clearOutputs = () => outputs.forEach(name => { delete deps.context[name]; });
  clearOutputs();
  return new Promise((resolve, reject) => {
    let socket, opened = false, closed = false, ending = false, settled = false;
    let result = false, error = '', commandIndex = -1, pending = null, heartbeat = null;
    let connecting = false, holding = false, holdElapsed = false, handshakeAuth = false, ignored = 0;
    let started = Date.now(), workDeadline = Infinity, closeDeadline = Infinity, closeWaitDeadline = Infinity;
    let workTimeout = 'WSSessionTimeout';
    const draining = typeof deps.drainRemaining === 'function', timers = new Set(), errorMatchers = [];
    const emit = value => deps.emit(value);
    const timer = (fn, delay) => {
      const id = setTimeout(() => { timers.delete(id); fn(); }, Math.max(delay, 0));
      timers.add(id); return id;
    };
    const cancel = id => { if (id !== undefined) { clearTimeout(id); timers.delete(id); } };
    const stage = (name, state, ok, elapsed, command) => emit({ kind: 'ws_stage', stage: name, state,
      ...(ok !== undefined ? { ok } : {}), ...(elapsed !== undefined ? { elapsed_ms: elapsed } : {}),
      ...(command !== undefined ? { command } : {}) });
    function completed(item, ok) {
      cancel(item.timeout);
      stage(item.stage, 'completed', ok, Date.now() - item.started, item.command);
    }
    function settle() {
      if (settled) return;
      settled = true;
      timers.forEach(clearTimeout); timers.clear();
      if (!result) clearOutputs();
      emit({ kind: 'request', elapsed_ms: Date.now() - started, timestamp_ms: Date.now(),
        status: opened ? 101 : 0, ok: result, error });
      resolve(result);
    }
    function abortUnclosed() {
      result = false; error = 'WSCloseTimeout';
      settle();
      if (deps.fatal) deps.fatal();
      emit({ kind: 'ws_fatal' });
    }
    function finish(ok, reason = '') {
      if (ending) return;
      if (ok && draining && Date.now() >= workDeadline) { ok = false; reason = workTimeout; }
      ending = true; result = ok; error = reason;
      timers.forEach(clearTimeout); timers.clear();
      if (connecting) { connecting = false; stage('connect', 'completed', false, Date.now() - started); }
      if (handshakeAuth) { handshakeAuth = false; stage('auth', 'completed', false, Date.now() - started); }
      if (pending) { completed(pending, false); pending = null; }
      if (heartbeat) { completed(heartbeat, false); heartbeat = null; }
      if (closed || !socket) { settle(); return; }
      // Await a real close observation; a forced/absent close stays unknown in
      // connection metrics. The close wait itself is bounded inside drain.
      closeWaitDeadline = Math.min(Date.now() + 1000, closeDeadline);
      timer(abortUnclosed, Math.max(closeWaitDeadline - Date.now(), 0));
      try { socket.close(); } catch (_) { abortUnclosed(); }
    }
    function workExpired() {
      if (draining) {
        if (Date.now() < workDeadline) return false;
        finish(false, workTimeout);
      } else {
        if (deps.remaining() > 0) return false;
        finish(false, 'WSLoadExpired');
      }
      return true;
    }
    function commandTimeoutReason(name) {
      return name === 'heartbeat' ? 'WSHeartbeatTimeout' : push ? 'WSEventTimeout' : 'WSCommandTimeout';
    }
    function commandExpired(item) {
      if (!draining || Date.now() - item.started < config.command_timeout_ms) return false;
      finish(false, commandTimeoutReason(item.stage));
      return true;
    }
    function checkRules(item, response) {
      const assertions = [], extractors = [], values = Object.create(null);
      let ok = push || response.ok === true;
      let failure = ok ? '' : 'WSNegativeAck';
      for (const [index, rule] of (item.config.assertions || []).entries()) {
        const actual = deps.jsonPath(response, rule.expr || rule.json_path);
        const pass = actual !== undefined && actual === deps.render(rule.expected, item.scope);
        assertions.push({ index, result: pass ? 'passed' : 'mismatch' });
        if (!pass) { ok = false; failure = failure || 'AssertionFailed'; }
      }
      for (const [index, rule] of (item.config.extractors || []).entries()) {
        if (!ok) { extractors.push({ index, result: 'skipped' }); continue; }
        const value = deps.jsonPath(response, rule.expr || rule.json_path);
        const pass = value !== undefined && value !== null && value !== '';
        extractors.push({ index, result: pass ? 'passed' : 'missing' });
        if (!pass) { ok = false; failure = 'ExtractionFailed'; }
        else values[rule.name] = value;
      }
      if (ok) Object.assign(deps.context, values);
      if (deps.debug) emit({ kind: 'ws_diagnostic', stage: item.stage, command: item.command,
        outcome: ok ? 'passed' : 'failed', assertions: item.stage === 'auth' ? [] : assertions,
        extractors: item.stage === 'auth' ? [] : extractors,
        response: item.stage === 'auth' ? { state: 'auth_omitted' } : { state: 'body_omitted' } });
      return { ok, failure };
    }
    function send(name, entry, command) {
      if (ending) return;
      if (workExpired()) return;
      const item = { stage: name, command, config: entry, id: deps.nextId(), started: Date.now(), scope: {} };
      let raw;
      try {
        if (entry.request !== undefined) {
          const frame = deps.render(entry.request, item.scope);
          if (!push) frame.id = item.id;
          raw = JSON.stringify(frame);
          if (frameSize(raw) > MAX_FRAME_BYTES) { finish(false, 'WSFrameTooLarge'); return; }
        }
      } catch (_) { finish(false, 'WSPreparationFailed'); return; }
      if (workExpired()) return;
      stage(name, 'started', undefined, undefined, command);
      if (push) errorMatchers.push(entry);
      if (name === 'heartbeat') heartbeat = item; else pending = item;
      item.timeout = timer(() => finish(false, commandTimeoutReason(name)), config.command_timeout_ms);
      try { if (raw !== undefined) socket.send(raw); } catch (_) { finish(false, 'WSTransportError'); }
    }
    function heartbeatTick() {
      if (ending || deps.remaining() <= 0) return;
      if (!heartbeat) send('heartbeat', { request: { version: 1, action: 'ping', payload: {} } });
      if (!ending) timer(heartbeatTick, config.heartbeat_interval_ms);
    }
    function nextCommand() {
      if (ending) return;
      commandIndex++;
      if (commandIndex < config.commands.length) { send(push ? 'event' : 'command', config.commands[commandIndex], commandIndex); return; }
      holding = true;
      const delay = Math.min(config.hold_open_ms, Math.max(deps.remaining(), 0));
      timer(() => { holdElapsed = true; if (!heartbeat) finish(true); }, delay);
    }
    function admissionExpired(remaining) {
      if (remaining > 0) return false;
      // The caller treats expiry before admission as a normal load boundary,
      // not as an attempted session or an authentication/business failure.
      if (deps.loadExpired) reject(deps.loadExpired);
      else finish(false, 'WSLoadExpired');
      return true;
    }
    if (admissionExpired(deps.remaining(started))) return;
    const remaining = deps.remaining();
    try {
      started = Date.now();
      if (admissionExpired(deps.remaining(started))) return;
      workDeadline = started + config.max_session_ms;
      if (draining) {
        closeDeadline = Date.now() + deps.drainRemaining();
        // Keep the original session maximum. The global drain also reserves
        // the existing one-second close observation budget before k6 exits.
        if (closeDeadline - 1000 < workDeadline) {
          workDeadline = closeDeadline - 1000;
          workTimeout = 'WSLoadExpired';
        }
      }
      emit({ kind: 'request_started', timestamp_ms: started });
      connecting = true;
      stage('connect', 'started');
      if (push) { handshakeAuth = true; stage('auth', 'started'); }
      const connectionTimeout = timer(() => finish(false, 'WSConnectTimeout'), config.connect_timeout_ms);
      timer(() => finish(false, workTimeout), workDeadline - Date.now());
      if (!draining && Number.isFinite(remaining)) timer(() => {
        if (holding && !heartbeat) finish(true);
        else if (!pending && !heartbeat) finish(false, 'WSLoadExpired');
        // Fixed iterations retain their hard load cutoff and original ACK rule.
      }, remaining);
      socket = new deps.WebSocket(deps.url, [], { headers: deps.headers, jar: deps.jar, tags: { name: deps.label || 'websocket-session' } });
      socket.onopen = () => {
        opened = true;
        emit({ kind: 'ws_connection', state: 'opened' });
        if (ending) { try { socket.close(); } catch (_) { abortUnclosed(); } return; }
        if (draining && workExpired()) return;
        if (draining && Date.now() - started >= config.connect_timeout_ms) { finish(false, 'WSConnectTimeout'); return; }
        connecting = false; cancel(connectionTimeout);
        stage('connect', 'completed', true, Date.now() - started);
        if (push) {
          handshakeAuth = false;
          stage('auth', 'completed', true, Date.now() - started);
          nextCommand();
        } else send('auth', config.auth);
      };
      socket.onmessage = message => {
        if (ending) return;
        if (draining && workExpired()) return;
        if (typeof message.data !== 'string') { finish(false, 'WSBinaryFrame'); return; }
        if (frameSize(message.data) > MAX_FRAME_BYTES) { finish(false, 'WSFrameTooLarge'); return; }
        let response;
        try { response = JSON.parse(message.data); } catch (_) { finish(false, 'WSInvalidJSON'); return; }
        if (!response || typeof response !== 'object' || Array.isArray(response)) { finish(false, 'WSInvalidJSON'); return; }
        if (push && errorMatchers.some(entry => (entry.error_types || []).includes(deps.jsonPath(response, entry.event_path || '$.type')))) {
          finish(false, 'WSEventError'); return;
        }
        const eventType = push && pending ? deps.jsonPath(response, pending.config.event_path || '$.type') : undefined;
        const item = push ? (pending && eventType === pending.config.event_type ? pending : null)
          : pending && response.id === pending.id ? pending : heartbeat && response.id === heartbeat.id ? heartbeat : null;
        if (!item) {
          if (push && ++ignored > 1024) finish(false, 'WSUnexpectedFrames');
          return;
        }
        ignored = 0;
        if (commandExpired(item)) return;
        let outcome;
        try { outcome = checkRules(item, response); }
        catch (_) { finish(false, 'WSPreparationFailed'); return; }
        if (draining && workExpired()) return;
        if (commandExpired(item)) return;
        completed(item, outcome.ok);
        if (item === pending) pending = null; else heartbeat = null;
        if (!outcome.ok) { finish(false, outcome.failure); return; }
        if (item.stage === 'heartbeat') {
          if (holding && (holdElapsed || deps.remaining() <= 0)) finish(true);
          return;
        }
        if (item.stage === 'auth' && config.heartbeat_interval_ms > 0) timer(heartbeatTick, config.heartbeat_interval_ms);
        nextCommand();
      };
      socket.onerror = () => finish(false, 'WSTransportError');
      socket.onclose = () => {
        if (!closed) {
          closed = true;
          if (opened) emit({ kind: 'ws_connection', state: 'closed' });
        }
        if (!ending) finish(false, 'WSUnexpectedClose');
        else {
          if (draining && Date.now() >= closeWaitDeadline) { result = false; error = 'WSCloseTimeout'; }
          settle();
        }
      };
    } catch (_) { finish(false, 'WSTransportError'); }
  });
}
