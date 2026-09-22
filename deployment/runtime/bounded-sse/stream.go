// Package boundsse provides a bounded SSE session without retaining response bodies.
package boundsse

import (
	"bufio"
	"context"
	"errors"
	"io"
	"mime"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

type Options struct {
	Method        string            `json:"method"`
	Headers       map[string]string `json:"headers"`
	Body          string            `json:"body"`
	TotalMS       int               `json:"total_ms"`
	IdleMS        int               `json:"idle_ms"`
	MaxEventBytes int               `json:"max_event_bytes"`
	MaxTotalBytes int               `json:"max_total_bytes"`
	MaxEvents     int               `json:"max_events"`
}

type Event struct {
	Name string `json:"name"`
	ID   string `json:"id"`
	Data string `json:"data"`
}
type Result struct {
	OK           bool     `json:"ok"`
	Started      bool     `json:"started"`
	Closed       bool     `json:"closed"`
	Reason       string   `json:"reason"`
	Status       int      `json:"status"`
	Events       int      `json:"events"`
	Bytes        int64    `json:"bytes"`
	ElapsedMS    float64  `json:"elapsed_ms"`
	FirstEventMS *float64 `json:"first_event_ms"`
}

var headerName = regexp.MustCompile("^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

func validOptions(target string, opts Options) bool {
	parsed, err := url.Parse(target)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" || len(target) > 8192 {
		return false
	}
	if opts.Method != "POST" && opts.Method != "GET" {
		return false
	}
	if opts.TotalMS < 1 || opts.TotalMS > 300000 || opts.IdleMS < 1 || opts.IdleMS > opts.TotalMS || opts.MaxEventBytes < 1 || opts.MaxEventBytes > 1024*1024 || opts.MaxTotalBytes < 1 || opts.MaxTotalBytes > 16*1024*1024 || opts.MaxEvents < 1 || opts.MaxEvents > 4096 || len(opts.Body) > 1024*1024 || len(opts.Headers) > 64 {
		return false
	}
	if opts.Method == "GET" && opts.Body != "" {
		return false
	}
	headerBytes := 0
	seenHeaders := make(map[string]bool, len(opts.Headers))
	for name, value := range opts.Headers {
		key := strings.ToLower(name)
		if seenHeaders[key] {
			return false
		}
		seenHeaders[key] = true
		if !headerName.MatchString(name) {
			return false
		}
		for _, ch := range value {
			if ch < 32 && ch != '\t' || ch == 127 {
				return false
			}
		}
		headerBytes += len(name) + len(value)
		switch strings.ToLower(name) {
		case "host", "content-length", "transfer-encoding", "connection", "proxy-authorization", "accept-encoding":
			return false
		}
	}
	return headerBytes <= 32768
}

type message struct {
	status int
	event  *Event
	reason string
}

// Run calls the callback on its calling goroutine, never from the I/O goroutine.
// Only explicit completion succeeds; EOF, timeout and server errors remain failures.
func Run(parent context.Context, target string, opts Options, callback func(Event) (string, error), transport http.RoundTripper) (result Result) {
	started := time.Now()
	result.Closed = true
	defer func() { result.ElapsedMS = float64(time.Since(started).Microseconds()) / 1000 }()
	if !validOptions(target, opts) || callback == nil {
		result.Reason = "invalid_options"
		return
	}
	if parent.Err() != nil {
		result.Reason = "cancelled"
		return
	}
	ctx, cancel := context.WithTimeout(parent, time.Duration(opts.TotalMS)*time.Millisecond)
	defer cancel()
	if transport == nil {
		local := http.DefaultTransport.(*http.Transport).Clone()
		local.DisableKeepAlives = true
		local.DisableCompression = true
		local.Proxy = nil
		local.MaxResponseHeaderBytes = 32768
		transport = local
		defer local.CloseIdleConnections()
	}
	request, err := http.NewRequestWithContext(ctx, opts.Method, target, strings.NewReader(opts.Body))
	if err != nil {
		result.Reason = "invalid_options"
		return
	}
	for name, value := range opts.Headers {
		request.Header.Set(name, value)
	}
	request.Header.Set("Accept", "text/event-stream")
	request.Header.Set("Accept-Encoding", "identity")
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	incoming := make(chan message)
	finished := make(chan struct{})
	var byteCount atomic.Int64
	send := func(value message) bool {
		select {
		case incoming <- value:
			return true
		case <-ctx.Done():
			return false
		}
	}
	result.Started = true
	result.Closed = false
	go func() {
		defer close(finished)
		response, err := client.Do(request)
		if err != nil {
			send(message{reason: "transport_error"})
			return
		}
		defer response.Body.Close()
		if !send(message{status: response.StatusCode}) {
			return
		}
		if response.StatusCode >= 300 && response.StatusCode < 400 {
			send(message{reason: "redirect"})
			return
		}
		if response.StatusCode != http.StatusOK {
			send(message{reason: "http_status"})
			return
		}
		media, params, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
		if err != nil || media != "text/event-stream" || (params["charset"] != "" && !strings.EqualFold(params["charset"], "utf-8")) || (response.Header.Get("Content-Encoding") != "" && response.Header.Get("Content-Encoding") != "identity") {
			send(message{reason: "content_type"})
			return
		}
		parser := eventReader{reader: bufio.NewReaderSize(io.LimitReader(response.Body, int64(opts.MaxTotalBytes)+1), 4096), opts: opts, bytes: &byteCount, firstLine: true}
		for {
			event, reason := parser.next()
			if reason != "" {
				send(message{reason: reason})
				return
			}
			if !send(message{event: &event}) {
				return
			}
		}
	}()
	// Waiting for the pump after cancellation prevents orphan response readers.
	defer func() { cancel(); <-finished; result.Closed = true; result.Bytes = byteCount.Load() }()
	idle := time.NewTimer(time.Duration(opts.IdleMS) * time.Millisecond)
	idleDeadline := time.Now().Add(time.Duration(opts.IdleMS) * time.Millisecond)
	totalDeadline, _ := ctx.Deadline()
	defer idle.Stop()
	deadlineReason := func() string {
		if parent.Err() != nil {
			return "cancelled"
		}
		return "total_timeout"
	}
	expiredReason := func() string {
		if ctx.Err() != nil || !time.Now().Before(totalDeadline) {
			return deadlineReason()
		}
		if !time.Now().Before(idleDeadline) {
			return "idle_timeout"
		}
		return ""
	}
	for {
		select {
		case <-ctx.Done():
			result.Reason = deadlineReason()
			return
		case <-idle.C:
			result.Reason = expiredReason()
			if result.Reason == "" {
				result.Reason = "idle_timeout"
			}
			return
		case value := <-incoming:
			if reason := expiredReason(); reason != "" {
				result.Reason = reason
				return
			}
			if value.status != 0 {
				result.Status = value.status
				continue
			}
			if value.reason != "" {
				result.Reason = value.reason
				return
			}
			if value.event == nil {
				result.Reason = "protocol_error"
				return
			}
			result.Events++
			if result.Events > opts.MaxEvents {
				result.Reason = "events_limit"
				return
			}
			if result.FirstEventMS == nil {
				ms := float64(time.Since(started).Microseconds()) / 1000
				result.FirstEventMS = &ms
			}
			if value.event.Name == "error" {
				result.Reason = "event_error"
				return
			}
			if !idle.Stop() {
				select {
				case <-idle.C:
				default:
				}
			}
			idle.Reset(time.Duration(opts.IdleMS) * time.Millisecond)
			idleDeadline = time.Now().Add(time.Duration(opts.IdleMS) * time.Millisecond)
			action, err := callback(*value.event)
			if reason := expiredReason(); reason != "" {
				result.Reason = reason
				return
			}
			if err != nil {
				result.Reason = "callback_error"
				return
			}
			switch action {
			case "continue":
			case "complete":
				result.OK = true
				result.Reason = "completed"
				return
			case "error":
				result.Reason = "business_error"
				return
			default:
				result.Reason = "callback_error"
				return
			}
		}
	}
}

type eventReader struct {
	reader     *bufio.Reader
	opts       Options
	bytes      *atomic.Int64
	frameBytes int
	skipLF     bool
	firstLine  bool
	lastID     string
}

func (p *eventReader) line() (string, string) {
	var line strings.Builder
	for {
		ch, err := p.reader.ReadByte()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return "", "unexpected_eof"
			}
			return "", "read_error"
		}
		if p.bytes.Add(1) > int64(p.opts.MaxTotalBytes) {
			return "", "total_bytes_limit"
		}
		if p.skipLF {
			p.skipLF = false
			if ch == '\n' {
				continue
			}
		}
		p.frameBytes++
		if p.frameBytes > p.opts.MaxEventBytes {
			return "", "event_bytes_limit"
		}
		if ch == '\r' {
			p.skipLF = true
			break
		}
		if ch == '\n' {
			break
		}
		line.WriteByte(ch)
	}
	value := line.String()
	if p.firstLine {
		value = strings.TrimPrefix(value, "\xef\xbb\xbf")
		p.firstLine = false
	}
	if !utf8.ValidString(value) {
		return "", "invalid_utf8"
	}
	return value, ""
}

func (p *eventReader) next() (Event, string) {
	name := "message"
	var data strings.Builder
	hasData := false
	for {
		line, reason := p.line()
		if reason != "" {
			return Event{}, reason
		}
		if line == "" {
			p.frameBytes = 0
			if hasData {
				return Event{Name: name, ID: p.lastID, Data: strings.TrimSuffix(data.String(), "\n")}, ""
			}
			name = "message"
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		field, value, found := strings.Cut(line, ":")
		if found {
			value = strings.TrimPrefix(value, " ")
		}
		switch field {
		case "data":
			data.WriteString(value)
			data.WriteByte('\n')
			hasData = true
		case "event":
			name = value
			if name == "" {
				name = "message"
			}
		case "id":
			if !strings.ContainsRune(value, 0) {
				p.lastID = value
			}
			// retry and unknown fields cannot trigger reconnection or raw logging.
		}
	}
}
