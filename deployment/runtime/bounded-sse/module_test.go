package boundsse

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.k6.io/k6/v2/js/modulestest"
	"go.k6.io/k6/v2/lib"
)

func TestJSModulePreservesPrivateEventsAndReturnsSafeCounters(t *testing.T) {
	runtime := modulestest.NewRuntime(t)
	runtime.MoveToVUContext(&lib.State{Dialer: &net.Dialer{}})
	module := (&RootModule{}).NewModuleInstance(runtime.VU)
	if err := runtime.VU.Runtime().Set("sse", module.Exports().Default); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		eventHeader(w)
		fmt.Fprint(w, "data: private-body\n\ndata: [DONE]\n\n")
	}))
	defer server.Close()
	raw, _ := json.Marshal(options())
	endpoint, _ := json.Marshal(server.URL)
	value, err := runtime.RunOnEventLoop(`const events=[]; const result=sse.open(` + string(endpoint) + `,` + string(raw) + `, e=>{events.push(e.data);return e.data==='[DONE]'?'complete':'continue'}); JSON.stringify({result,events});`)
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Result Result   `json:"result"`
		Events []string `json:"events"`
	}
	if err = json.Unmarshal([]byte(value.String()), &got); err != nil {
		t.Fatal(err)
	}
	if !got.Result.OK || got.Result.Events != 2 || len(got.Events) != 2 || got.Events[0] != "private-body" {
		t.Fatalf("bad module counters %+v", got.Result)
	}
	safe, _ := json.Marshal(got.Result)
	if strings.Contains(string(safe), "private-body") {
		t.Fatal("result leaked payload")
	}
}

func TestJSModuleRejectsUnknownOptionsAndCallbackExceptions(t *testing.T) {
	runtime := modulestest.NewRuntime(t)
	runtime.MoveToVUContext(&lib.State{Dialer: &net.Dialer{}})
	runtime.VU.Runtime().Set("sse", (&RootModule{}).NewModuleInstance(runtime.VU).Exports().Default)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { eventHeader(w); fmt.Fprint(w, "data: value\n\n") }))
	defer server.Close()
	raw, _ := json.Marshal(options())
	endpoint, _ := json.Marshal(server.URL)
	for _, tc := range []struct{ opts, callback, reason string }{
		{`{...` + string(raw) + `,redirects:1}`, `()=>'complete'`, "invalid_options"},
		{`Object.defineProperty(` + string(raw) + `,'body',{get(){throw new Error('private-exception')}})`, `()=>'complete'`, "invalid_options"},
		{string(raw), `()=>{throw new Error('private-exception')}`, "callback_error"},
		{string(raw), `()=>({toString:()=> 'complete'})`, "callback_error"},
	} {
		value, err := runtime.RunOnEventLoop(`JSON.stringify(sse.open(` + string(endpoint) + `,` + tc.opts + `,` + tc.callback + `))`)
		if err != nil {
			t.Fatal("module must not throw private exceptions")
		}
		var result Result
		json.Unmarshal([]byte(value.String()), &result)
		if result.OK || result.Reason != tc.reason || strings.Contains(value.String(), "private-exception") {
			t.Fatalf("result %+v", result)
		}
	}
}
