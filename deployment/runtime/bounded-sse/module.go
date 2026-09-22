package boundsse

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/grafana/sobek"
	"go.k6.io/k6/v2/js/modules"
)

const Version = "testhub-sse/1"

type RootModule struct{}
type ModuleInstance struct{ vu modules.VU }

func init()                                                          { modules.Register("k6/x/testhub-sse", new(RootModule)) }
func (*RootModule) NewModuleInstance(vu modules.VU) modules.Instance { return &ModuleInstance{vu: vu} }
func (m *ModuleInstance) Exports() modules.Exports {
	return modules.Exports{Default: map[string]any{"version": Version, "open": m.Open}}
}

// Open stays on the VU goroutine; only transport reads execute in a worker.
func (m *ModuleInstance) Open(target string, raw sobek.Value, handler sobek.Value) map[string]any {
	invalid := Result{Closed: true, Reason: "invalid_options"}
	callback, ok := sobek.AssertFunction(handler)
	if !ok || raw == nil || sobek.IsNull(raw) || sobek.IsUndefined(raw) {
		return resultObject(invalid)
	}
	var body []byte
	var err error
	exception := m.vu.Runtime().Try(func() { body, err = json.Marshal(raw.Export()) })
	if err != nil || exception != nil {
		return resultObject(invalid)
	}
	var opts Options
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&opts) != nil || !validOptions(target, opts) {
		return resultObject(invalid)
	}
	state := m.vu.State()
	if state == nil || state.Dialer == nil {
		return resultObject(Result{Closed: true, Reason: "init_context"})
	}
	transport := &http.Transport{DialContext: state.Dialer.DialContext, DisableKeepAlives: true, DisableCompression: true,
		MaxResponseHeaderBytes: 32768, TLSHandshakeTimeout: time.Duration(opts.TotalMS) * time.Millisecond}
	if state.TLSConfig != nil {
		transport.TLSClientConfig = state.TLSConfig.Clone()
		transport.TLSClientConfig.NextProtos = []string{"http/1.1"}
	}
	defer transport.CloseIdleConnections()
	result := Run(m.vu.Context(), target, opts, func(event Event) (string, error) {
		value, err := callback(sobek.Undefined(), m.vu.Runtime().ToValue(map[string]any{"name": event.Name, "id": event.ID, "data": event.Data}))
		if err != nil || value == nil {
			return "", errors.New("callback failed")
		}
		action, ok := value.(sobek.String)
		if !ok {
			return "", errors.New("callback must return an action")
		}
		return action.String(), nil
	}, transport)
	return resultObject(result)
}

func resultObject(result Result) map[string]any {
	var first any
	if result.FirstEventMS != nil {
		first = *result.FirstEventMS
	}
	return map[string]any{"ok": result.OK, "started": result.Started, "closed": result.Closed, "reason": result.Reason,
		"status": result.Status, "events": result.Events, "bytes": result.Bytes, "elapsed_ms": result.ElapsedMS, "first_event_ms": first}
}
