# Bounded SSE runtime

This owned extension is built against the existing fixed k6 revision in `runtime-lock.json`. It does not depend on the community SSE extension or change the installed runtime. `go.mod` and `go.sum` pin the dependency graph; `build.py` requires the recorded clean k6 checkout and Go toolchain, builds with `-mod=readonly`, and writes a separate binary plus source and binary hashes. Existing output is never overwritten.

```powershell
python build.py --k6-source C:/path/to/fixed-k6 --output C:/private/candidate-k6.exe --test
python verify_runtime.py --binary C:/private/candidate-k6.exe
```

The module is `k6/x/testhub-sse`, and its API version is `testhub-sse/1`:

```javascript
import sse from 'k6/x/testhub-sse';

const result = sse.open(url, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify(input),
  total_ms: 30000,
  idle_ms: 10000,
  max_event_bytes: 262144,
  max_total_bytes: 4194304,
  max_events: 256,
}, event => {
  // The platform adapter validates the actual business protocol here.
  // event has name, id and data; never log or retain raw data in public reports.
  if (event.data === '[DONE]' && validBusinessResult) return 'complete';
  if (businessError) return 'error';
  return 'continue';
});
```

All options are explicit. Only GET and POST are supported; GET cannot carry a body. Header names/values and request size are bounded. HTTP 200 and `text/event-stream` with UTF-8 are required. Redirects are rejected before a second request; the extension neither reconnects nor retries. Compression and implicit cookies/proxies are unsupported. It uses the VU dialer and TLS policy, with a dedicated HTTP/1.1 connection per session.

`open` blocks this VU's JS event loop until completion or failure. Each complete SSE event is delivered immediately on the calling JS goroutine with bounded backpressure. CR, LF, CRLF, a first UTF-8 BOM, multiline data, persistent event IDs, comments, and unknown fields are parsed. Invalid UTF-8 is rejected. A named `error` event fails before the callback. Other business error envelopes require the adapter's callback validation.

Success requires the callback to return `complete`; HTTP 200, a heartbeat, and EOF never imply success. Return `error` for a business failure. Exceptions and other callback return types become a fixed `callback_error`, without exposing exception text. Total and idle deadlines are checked before event dispatch and after every callback; total expiration takes precedence when both elapsed. A slow callback cannot complete an expired session. The adapter must validate terminal event order and business assertions before returning `complete`.

Limits: 1–300000 ms total, idle no greater than total, up to 1 MiB per frame, 16 MiB total stream, 4096 events, 1 MiB request body, 64 headers and 32 KiB request/response headers. Frame size includes fields/comments and normalized line separators, including frames that carry no data. The total byte budget includes raw consumed line endings; the reader may read one extra byte to detect an overrun. Case-insensitive duplicate headers are rejected. Idle time starts before response headers and resets only for complete data events; heartbeat comments cannot keep a stalled business stream alive. Parent VU cancellation aborts the HTTP request. The reader is joined and response closed on every exit.

The result contains only `ok`, `started`, `closed`, fixed `reason`, HTTP `status`, `events`, consumed stream `bytes`, `elapsed_ms`, and nullable `first_event_ms`. It contains no URL, response headers, ID, token, event data or exception text. No built-in HTTP sample is fabricated: the platform adapter must account for this single stream request and SSE timings using the returned counters and actual dispatched state. Raw callback payloads must stay private.

This directory proves transport behavior against local fixtures. It does not prove any Example AI provider or business endpoint is available. The platform step model, UI, report integration, Docker binary pin, and real business terminal assertions belong to the subsequent adapter change.

`build.py --test` runs the Go parser, cancellation, transport and actual Sobek module tests before building. `verify_runtime.py` then runs the candidate's native HTTP, `k6/websockets` and SSE modules in one iteration: the server withholds the final SSE event until an HTTP request made inside the first event callback confirms incremental delivery. It also verifies seven failure modes, rejects redirects without hitting the destination, and observes connection closure after a k6 executor forcibly stops a VU. Fixtures bind only to loopback and use synthetic credentials. The command prints counters and hashes, not runtime output or bodies. This proof covers local Windows execution; a Linux image build and platform CI remain required before deployment.

The build requires the exact clean k6 checkout and installed Go version; it never fetches k6 source or downloads another toolchain. Go dependencies may be downloaded with the checked-in `go.sum` verification; provision the module cache beforehand for offline builds. All source files and the produced binary receive SHA-256 entries in the adjacent receipt. Rebuild to a new output path after any source change. The repository's root `runtime/` ignore rule covers this directory, so integration must explicitly add these sources and exclude Python cache files.
