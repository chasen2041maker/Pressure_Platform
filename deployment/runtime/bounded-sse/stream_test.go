package boundsse

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func options() Options {
	return Options{Method: "POST", Headers: map[string]string{"Authorization": "Bearer local-fixture-only"},
		Body: `{"input":"fixture"}`, TotalMS: 1000, IdleMS: 200, MaxEventBytes: 1024, MaxTotalBytes: 4096, MaxEvents: 20}
}
func done(e Event) (string, error) {
	if e.Data == "[DONE]" {
		return "complete", nil
	}
	return "continue", nil
}
func eventHeader(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.WriteHeader(200)
	w.(http.Flusher).Flush()
}

func TestIncrementalPOSTAndParser(t *testing.T) {
	observed := make(chan struct{})
	var valid atomic.Bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var value map[string]string
		json.NewDecoder(r.Body).Decode(&value)
		valid.Store(r.Method == "POST" && value["input"] == "fixture" && r.Header.Get("Authorization") == "Bearer local-fixture-only")
		eventHeader(w)
		for _, piece := range []string{"\xef", "\xbb\xbf:idignored\r", "\nid: 7\r\nevent: meta\r\ndata: first\r\n", "data: 第二行\r\nignored: harmless\r\n\r\n"} {
			fmt.Fprint(w, piece)
			w.(http.Flusher).Flush()
		}
		select {
		case <-observed:
		case <-r.Context().Done():
			return
		}
		fmt.Fprint(w, "data: [DONE]\n\n")
		w.(http.Flusher).Flush()
	}))
	defer server.Close()
	var events []Event
	result := Run(context.Background(), server.URL, options(), func(e Event) (string, error) {
		events = append(events, e)
		if len(events) == 1 {
			close(observed)
		}
		return done(e)
	}, nil)
	if !result.OK || !result.Started || !result.Closed || result.Events != 2 || !valid.Load() {
		t.Fatalf("result: %+v", result)
	}
	if len(events) != 2 || events[0].Name != "meta" || events[0].Data != "first\n第二行" || events[0].ID != "7" || events[1].ID != "7" || events[1].Name != "message" {
		t.Fatalf("parser: %#v", events)
	}
	if result.FirstEventMS == nil || *result.FirstEventMS > result.ElapsedMS {
		t.Fatal("missing incremental timing")
	}
}

func TestFailuresNeverBecomeSuccessOrExposePayload(t *testing.T) {
	for _, tc := range []struct {
		name, body, mime, want         string
		status                         int
		eventBytes, totalBytes, events int
	}{
		{name: "eof", body: "data: partial\n\n", want: "unexpected_eof"},
		{name: "unterminated_done", body: "data: [DONE]", want: "unexpected_eof"},
		{name: "server_error", body: "event: error\ndata: private-body\n\n", want: "event_error"},
		{name: "mime", body: "data: [DONE]\n\n", mime: "text/plain", want: "content_type"},
		{name: "status", body: "private-body", status: 503, want: "http_status"},
		{name: "frame", body: "data: " + strings.Repeat("x", 300) + "\n\n", eventBytes: 64, want: "event_bytes_limit"},
		{name: "total", body: strings.Repeat(": ping\n\n", 40), totalBytes: 64, want: "total_bytes_limit"},
		{name: "events", body: "data: one\n\ndata: two\n\ndata: [DONE]\n\n", events: 2, want: "events_limit"},
		{name: "utf8", body: "data: \xff\n\n", want: "invalid_utf8"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mime := tc.mime
				if mime == "" {
					mime = "text/event-stream"
				}
				status := tc.status
				if status == 0 {
					status = 200
				}
				w.Header().Set("Content-Type", mime)
				w.WriteHeader(status)
				fmt.Fprint(w, tc.body)
			}))
			defer server.Close()
			opts := options()
			if tc.eventBytes > 0 {
				opts.MaxEventBytes = tc.eventBytes
			}
			if tc.totalBytes > 0 {
				opts.MaxTotalBytes = tc.totalBytes
			}
			if tc.events > 0 {
				opts.MaxEvents = tc.events
			}
			result := Run(context.Background(), server.URL, opts, done, nil)
			if result.OK || !result.Started || !result.Closed || result.Reason != tc.want {
				t.Fatalf("result: %+v", result)
			}
			raw, _ := json.Marshal(result)
			if strings.Contains(string(raw), "private-body") || strings.Contains(string(raw), "local-fixture") || strings.Contains(string(raw), server.URL) {
				t.Fatal("private data in result")
			}
		})
	}
}

func TestRedirectNeverReceivesCredentials(t *testing.T) {
	var hits atomic.Int64
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hits.Add(1) }))
	defer target.Close()
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer source.Close()
	result := Run(context.Background(), source.URL, options(), done, nil)
	if result.Reason != "redirect" || result.OK || hits.Load() != 0 {
		t.Fatalf("redirect result: %+v, hits %d", result, hits.Load())
	}
}

func TestDeadlinesCancelServerAndCloseReader(t *testing.T) {
	for _, mode := range []string{"idle", "total", "context", "headers"} {
		t.Run(mode, func(t *testing.T) {
			closed := make(chan struct{})
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = io.Copy(io.Discard, r.Body)
				defer close(closed)
				if mode != "headers" {
					eventHeader(w)
				}
				ticker := time.NewTicker(15 * time.Millisecond)
				defer ticker.Stop()
				for {
					select {
					case <-r.Context().Done():
						return
					case <-ticker.C:
						if mode == "total" {
							fmt.Fprint(w, "data: waiting\n\n")
							w.(http.Flusher).Flush()
						}
						if mode == "idle" {
							fmt.Fprint(w, ": keepalive\n\n")
							w.(http.Flusher).Flush()
						}
					}
				}
			}))
			defer server.Close()
			opts := options()
			opts.IdleMS = 55
			opts.TotalMS = 100
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "context" {
				go func() { time.Sleep(25 * time.Millisecond); cancel() }()
			}
			result := Run(ctx, server.URL, opts, done, nil)
			want := "idle_timeout"
			if mode == "total" {
				want = "total_timeout"
			}
			if mode == "context" {
				want = "cancelled"
			}
			if result.OK || !result.Closed || result.Reason != want || result.ElapsedMS > 500 {
				t.Fatalf("result %+v", result)
			}
			select {
			case <-closed:
			case <-time.After(time.Second):
				t.Fatal("server request not cancelled")
			}
		})
	}
}

func TestCallbackErrorsAndInvalidOptionsAreSafe(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { eventHeader(w); fmt.Fprint(w, "data: value\n\n") }))
	defer server.Close()
	result := Run(context.Background(), server.URL, options(), func(Event) (string, error) { return "", errors.New("private-body") }, nil)
	if result.Reason != "callback_error" || !result.Closed {
		t.Fatalf("callback: %+v", result)
	}
	opts := options()
	opts.TotalMS = 0
	result = Run(context.Background(), server.URL, opts, done, nil)
	if result.Started || result.Reason != "invalid_options" {
		t.Fatalf("invalid: %+v", result)
	}
}

type readerCounter struct {
	reader io.Reader
	bytes  int
}

func (r *readerCounter) Read(p []byte) (int, error) {
	n, err := r.reader.Read(p)
	r.bytes += n
	return n, err
}
func (r *readerCounter) Close() error { return nil }

type fixtureTransport func(*http.Request) (*http.Response, error)

func (f fixtureTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestTotalBudgetAlsoBoundsReaderPrefetch(t *testing.T) {
	body := &readerCounter{reader: strings.NewReader("data: " + strings.Repeat("x", 8192))}
	transport := fixtureTransport(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"text/event-stream"}}, Body: body}, nil
	})
	opts := options()
	opts.MaxTotalBytes = 8
	result := Run(context.Background(), "http://fixture.invalid/stream", opts, done, transport)
	if result.Reason != "total_bytes_limit" || body.bytes > 9 {
		t.Fatalf("unbounded prefetch: reason=%s read=%d", result.Reason, body.bytes)
	}
}

func TestDuplicateCaseInsensitiveHeadersRejected(t *testing.T) {
	opts := options()
	opts.Headers = map[string]string{"Authorization": "one", "authorization": "two"}
	if validOptions("http://fixture.invalid", opts) {
		t.Fatal("ambiguous duplicate header accepted")
	}
}

func TestCallbackCannotOutliveIdleDeadline(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { eventHeader(w); fmt.Fprint(w, "data: [DONE]\n\n") }))
	defer server.Close()
	for _, action := range []string{"complete", "continue"} {
		t.Run(action, func(t *testing.T) {
			opts := options()
			opts.IdleMS = 20
			opts.TotalMS = 300
			result := Run(context.Background(), server.URL, opts, func(Event) (string, error) { time.Sleep(60 * time.Millisecond); return action, nil }, nil)
			if result.OK || result.Reason != "idle_timeout" || !result.Closed {
				t.Fatalf("callback outlived idle: %+v", result)
			}
		})
	}
	opts := options()
	opts.IdleMS = 20
	opts.TotalMS = 40
	result := Run(context.Background(), server.URL, opts, func(Event) (string, error) { time.Sleep(60 * time.Millisecond); return "complete", nil }, nil)
	if result.OK || result.Reason != "total_timeout" {
		t.Fatalf("total deadline must take priority: %+v", result)
	}
}
