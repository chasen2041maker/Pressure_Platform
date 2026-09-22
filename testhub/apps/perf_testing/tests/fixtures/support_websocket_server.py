"""Loopback-only RFC6455 fixture. Tokens and messages are synthetic test data."""
import base64
import hashlib
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SupportWebSocketServer:
    def __init__(self, mode: str = 'success'):
        self.mode = mode
        self.frames = []
        self.http_calls = []
        self.headers = []
        self.closed = 0
        self.received_close = 0
        self.server = None
        self.thread = None

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def handle(self):
                try:
                    super().handle()
                except (OSError, EOFError):
                    pass

            def read_exact(self, size):
                data = self.rfile.read(size)
                if len(data) != size:
                    raise EOFError
                return data

            def frame(self):
                head = self.read_exact(2)
                size = head[1] & 127
                if size == 126:
                    size = struct.unpack('!H', self.read_exact(2))[0]
                elif size == 127:
                    size = struct.unpack('!Q', self.read_exact(8))[0]
                if size > 128 * 1024:
                    raise EOFError
                mask = self.read_exact(4) if head[1] & 128 else None
                payload = self.read_exact(size)
                if mask:
                    payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
                return head[0] & 15, payload

            def send_frame(self, value, opcode=1):
                raw = value if isinstance(value, bytes) else json.dumps(value).encode()
                head = bytes([128 | opcode])
                head += bytes([len(raw)]) if len(raw) < 126 else b'\x7e' + struct.pack('!H', len(raw))
                self.wfile.write(head + raw)
                self.wfile.flush()

            def do_GET(self):
                owner.headers.append(dict(self.headers))
                if self.path != '/socket':
                    owner.http_calls.append(self.path)
                    raw = b'{"ok":true}'
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if owner.mode == 'no_handshake':
                    self.connection.settimeout(3)
                    try:
                        self.rfile.read(1)
                    except socket.timeout:
                        pass
                    owner.closed += 1
                    return
                if owner.mode in ('push_auth_failed', 'push_redirect'):
                    self.send_response(302 if owner.mode == 'push_redirect' else 401)
                    if owner.mode == 'push_redirect':
                        self.send_header('Location', '/redirect-must-not-follow')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if owner.mode == 'delayed_handshake':
                    time.sleep(0.6)
                key = self.headers['Sec-WebSocket-Key']
                accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                self.send_response(101)
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', accept)
                self.end_headers()
                self.connection.settimeout(4)
                token = None
                if owner.mode.startswith('push_'):
                    token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                    if not token.startswith('FAKE_TOKEN_') or self.headers.get('Cookie'):
                        self.send_frame(b'\x03\xe8', 8)
                        return
                    if owner.mode in ('push_initial', 'push_reminder'):
                        self.send_frame({('event_type' if owner.mode == 'push_reminder' else 'type'): 'quote.snapshot', 'cycle_id': 'fixture-cycle',
                                         'quotes': [{'code': 'sz000001'}]})
                while True:
                    opcode, payload = self.frame()
                    if opcode == 8:
                        owner.received_close += 1
                        if owner.mode == 'ignore_close':
                            self.connection.settimeout(10)
                            while self.rfile.read(1):
                                pass
                            owner.closed += 1
                            self.close_connection = True
                            return
                        self.send_frame(payload, 8)
                        owner.closed += 1
                        self.close_connection = True
                        return
                    frame = json.loads(payload)
                    owner.frames.append(frame)
                    if owner.mode.startswith('push_'):
                        if owner.mode == 'push_timeout':
                            continue
                        self.send_frame({'type': 'unrelated', 'secret': 'SECRET_IGNORED'})
                        self.send_frame({'type': 'quote.error' if owner.mode == 'push_error' else 'quote.snapshot',
                                         'cycle_id': 'fixture-cycle', 'quotes': [{'code': 'sz000001'}]})
                        if owner.mode in ('push_hold_error', 'push_hold_more'):
                            time.sleep(0.03)
                            self.send_frame({'type': 'quote.error' if owner.mode == 'push_hold_error' else 'quote.snapshot'})
                        continue
                    action = frame['action']
                    if action == 'auth':
                        token = frame['payload'].get('token')
                        ok = owner.mode != 'auth_failed' and token.startswith('FAKE_TOKEN_')
                        self.send_frame({'id': frame['id'], 'ok': ok, 'data': {'user': token}})
                        continue
                    if owner.mode == 'timeout':
                        continue
                    if owner.mode == 'delayed_command' or (owner.mode == 'delayed_first_command' and action == 'timeline.list'):
                        time.sleep(0.6)
                    if owner.mode == 'close':
                        self.send_frame(b'\x03\xe8SECRET_CLOSE', 8)
                        self.close_connection = True
                        return
                    if owner.mode == 'malformed':
                        self.send_frame(b'SECRET_INVALID_JSON')
                        continue
                    self.send_frame({'event': 'changed', 'data': 'SECRET_EVENT'})
                    self.send_frame({'id': 'wrong', 'ok': True, 'data': 'SECRET_WRONG_ID'})
                    if owner.mode == 'wrong_id':
                        continue
                    self.send_frame({'id': frame['id'], 'ok': True, 'data': {'next': 42, 'secret': 'SECRET_RESPONSE'}})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
