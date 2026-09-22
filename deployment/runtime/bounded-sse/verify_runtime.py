"""Verify a candidate only against loopback HTTP, WebSocket and SSE fixtures."""
import argparse
import base64
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time


def verify(binary):
    counts = Counter()
    first_seen = threading.Event()
    cancelled = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def headers_for(self, status=200, mime='text/event-stream'):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True

        def do_GET(self):
            counts[self.path] += 1
            if self.path == '/slow':
                time.sleep(.16)
            if self.path == '/ws':
                key = self.headers.get('Sec-WebSocket-Key', '')
                accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                self.send_response(101)
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', accept)
                self.end_headers()
                self.wfile.write(b'\x81\x05ready')
                self.wfile.flush()
                self.connection.settimeout(3)
                try:
                    self.connection.recv(4096)
                    self.wfile.write(b'\x88\x02\x03\xe8')
                    self.wfile.flush()
                except OSError:
                    pass
                self.close_connection = True
                return
            if self.path == '/ack':
                first_seen.set()
            self.headers_for(mime='application/json')
            self.wfile.write(b'{"ok":true}')

        def do_POST(self):
            counts[self.path] += 1
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if body != b'{"fixture":true}' or self.headers.get('Authorization') != 'Bearer fixture-only':
                self.headers_for(400)
                return
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/redirect-destination')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.headers_for(mime='application/json' if self.path == '/mime' else 'text/event-stream')
            try:
                if self.path == '/normal':
                    self.wfile.write(b'data: private-segment\r\n\r\n')
                    self.wfile.flush()
                    if first_seen.wait(2):
                        counts['incremental_ack'] += 1
                        self.wfile.write(b'data: [DONE]\r\n\r\n')
                elif self.path == '/error':
                    self.wfile.write(b'event: error\ndata: private-error\n\n')
                elif self.path == '/bytes':
                    self.wfile.write(b'data: ' + b'x' * 512 + b'\n\n')
                elif self.path == '/total':
                    for _ in range(100):
                        self.wfile.write(b'data: pending\n\n')
                        self.wfile.flush()
                        time.sleep(.015)
                elif self.path in ('/idle', '/cancel'):
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    self.connection.settimeout(4)
                    if self.connection.recv(1) == b'':
                        counts[self.path + '_closed'] += 1
                        if self.path == '/cancel':
                            cancelled.set()
                else:
                    self.wfile.write(b'data: unfinished\n\n')
                self.wfile.flush()
            except (OSError, ConnectionError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    common = """
import http from 'k6/http';
import { WebSocket } from 'k6/websockets';
import { check } from 'k6';
import sse from 'k6/x/testhub-sse';
const base = __ENV.FIXTURE_URL;
const opts = {method:'POST',headers:{Authorization:'Bearer fixture-only','Content-Type':'application/json'},body:'{"fixture":true}',total_ms:3000,idle_ms:2000,max_event_bytes:1024,max_total_bytes:8192,max_events:16};
"""
    smoke = common + """
export const options = {vus:1,iterations:1,thresholds:{checks:['rate==1']}};
export default function(){
  check(http.get(base+'/health'), {'http before stream':r=>r.status===200});
  const result=sse.open(base+'/normal',opts,event=>{
    if(event.data==='private-segment') {
      check(http.get(base+'/ack'), {'incremental callback before terminal':r=>r.status===200});
      return 'continue';
    }
    return event.data==='[DONE]'?'complete':'error';
  });
  check(result, {'native stream success':r=>r.ok&&r.closed&&r.started&&r.events===2&&r.status===200});
  for (const action of ['complete','continue']) {
    const overdue=sse.open(base+'/callback',{...opts,idle_ms:40},()=>{
      http.get(base+'/slow');
      return action;
    });
    check(overdue, {[`callback ${action} obeys idle deadline`]:r=>!r.ok&&r.closed&&r.reason==='idle_timeout'});
  }
  const ws=new WebSocket(base.replace('http:','ws:')+'/ws');
  ws.onmessage=event=>{
    check(event, {'websocket after stream':e=>e.data==='ready'});
    check(http.get(base+'/after'), {'http after websocket':r=>r.status===200});
    ws.close();
  };
  for(const [path,reason,overrides] of [
    ['/eof','unexpected_eof',{}],['/error','event_error',{}],['/mime','content_type',{}],
    ['/redirect','redirect',{}],['/bytes','event_bytes_limit',{max_event_bytes:40}],
    ['/idle','idle_timeout',{idle_ms:80}],['/total','total_timeout',{total_ms:160,idle_ms:80}],
  ]){
    const failed=sse.open(base+path,{...opts,...overrides},()=> 'continue');
    check(failed, {[`native ${reason}`]:r=>!r.ok&&r.closed&&r.started&&r.reason===reason});
  }
}
"""
    cancel = common + """
export const options={scenarios:{deadline:{executor:'constant-vus',vus:1,duration:'1s',gracefulStop:'0s'}}};
export default function(){
  const result=sse.open(base+'/cancel',opts,()=> 'continue');
  if(result.ok) throw new Error('cancelled stream incorrectly succeeded');
}
"""
    outputs = {}
    try:
        with tempfile.TemporaryDirectory(prefix='bounded-sse-verify-') as folder:
            for name, source in [('smoke', smoke), ('cancel', cancel)]:
                script = Path(folder) / (name + '.js')
                script.write_text(source, encoding='utf-8')
                result = subprocess.run([str(binary), 'run', '--no-color', '--quiet', '--no-usage-report',
                                         '--env', 'FIXTURE_URL=' + base, str(script)],
                                        capture_output=True, timeout=15)
                outputs[name] = {'exit_code': result.returncode,
                                 'output_sha256': hashlib.sha256(result.stdout + result.stderr).hexdigest()}
                if result.returncode != 0:
                    raise AssertionError(f'{name} fixture execution failed; exit {result.returncode}')
            if not cancelled.wait(2):
                raise AssertionError('Executor cancellation did not close the stream')
        required = ['/health', '/after', '/ack', '/ws', '/normal', 'incremental_ack', '/idle_closed', '/cancel_closed']
        if any(counts[key] != 1 for key in required) or counts['/redirect-destination']:
            raise AssertionError('Fixture sequence, closure or redirect check failed')
        return {'native_runtime_verified': True, 'supplier_calls': 0,
                'counts': dict(counts), 'runs': outputs,
                'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.binary.resolve()), indent=2))
