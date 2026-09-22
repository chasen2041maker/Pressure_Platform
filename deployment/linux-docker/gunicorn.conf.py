"""Exactly one management process; pressure execution remains its own subprocess."""
import fcntl
import os
from pathlib import Path

bind = '0.0.0.0:8000'
workers = 1
worker_class = 'gthread'
threads = 4
preload_app = False
timeout = 120
graceful_timeout = 30
keepalive = 5
max_requests = 0
accesslog = None
errorlog = '-'
capture_output = True
worker_tmp_dir = '/tmp'


def on_starting(server):
    lock = (Path(os.environ['PERF_PRIVATE_ROOT']) / 'intranet-backend.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('Another backend owns this runtime; multiple instances are not supported') from None
    server.pressure_instance_lock = lock
