"""Independent bounded portfolio recovery worker; supervised separately from gunicorn."""
import os
import signal
import threading
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.perf_testing.services.k6_execution import FileLease, K6ExecutionError
from apps.perf_testing.services.reminder_recovery import configured_store, recovery_tick


class RecoveryProgressGuard:
    """Kill the independent daemon if network/filesystem work stops making progress."""

    def __init__(self, *, timeout=30, clock=time.monotonic, abort=os._exit):
        self.timeout=timeout
        self.clock=clock
        self.abort=abort
        self.last=clock()
        self.lock=threading.Lock()
        self.stopped=threading.Event()
        self.worker=threading.Thread(target=self.watch,daemon=True)

    def checkpoint(self):
        with self.lock:
            self.last=self.clock()

    def expired(self):
        with self.lock:
            return self.clock()-self.last > self.timeout

    def watch(self):
        while not self.stopped.wait(min(1,self.timeout/4)):
            with self.lock:
                if self.clock()-self.last > self.timeout:
                    # Do not unwind blocking I/O or leave a DELETE running in another thread.
                    self.abort(74)
                    return

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self,*_):
        self.stopped.set()
        self.worker.join(timeout=2)


class Command(BaseCommand):
    help = '恢复已终止压测的 portfolio_reminder 资源，保留冲突与未知结果'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        stopped=threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum,lambda *_:stopped.set())
        try:
            with RecoveryProgressGuard() as progress:
                store=configured_store()
                with FileLease(store.root,'recovery'):
                    while not stopped.is_set():
                        close_old_connections()
                        try:
                            from apps.perf_testing.services.cleanup import reap_stale_executions
                            reap_stale_executions()
                            recovery_tick(store=store,progress=progress.checkpoint)
                        except Exception as exc:
                            # Supervisor restarts a failed daemon. Never continue advertising readiness.
                            raise CommandError('提醒恢复守护状态不可用，已停止并保留原意图') from exc
                        if options['once']:
                            break
                        stopped.wait(1)
        except K6ExecutionError as exc:
            raise CommandError('另一个提醒恢复守护进程已持有租约') from exc
