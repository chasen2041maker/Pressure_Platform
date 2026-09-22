"""Owned performance-worker reaping, without Django or an application database."""
import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock


class ChildProcessTests(unittest.TestCase):
    def module(self):
        return importlib.import_module('apps.perf_testing.services.child_process')

    def wait_for_reaper(self, process):
        # Reading returncode does not poll/wait/reap; only the production waiter can set it.
        deadline = time.monotonic() + 5
        while process.returncode is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(process.returncode, 'The last child must be reaped without another Popen')

    def test_last_child_is_reaped_without_a_future_spawn(self):
        module = self.module()
        process = module.start_process([sys.executable, '-I', '-B', '-c', 'raise SystemExit(7)'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for_reaper(process)
            self.assertEqual(process.returncode, 7)
            if os.name == 'posix':
                with self.assertRaises(ChildProcessError): os.waitpid(process.pid, os.WNOHANG)
        finally:
            if process.returncode is None: process.kill(); process.wait(timeout=5)

    def test_live_child_returns_promptly_and_is_not_terminated(self):
        module = self.module()
        process = module.start_process([sys.executable, '-I', '-B', '-c', 'import sys; sys.stdin.buffer.read(1)'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertIsNone(process.returncode)
            process.stdin.write(b'x'); process.stdin.flush(); process.stdin.close()
            self.wait_for_reaper(process)
            self.assertEqual(process.returncode, 0)
        finally:
            if not process.stdin.closed: process.stdin.close()
            if process.returncode is None: process.kill(); process.wait(timeout=5)

    def test_thread_start_failure_never_creates_child(self):
        module = self.module()
        with mock.patch.object(module.threading.Thread, 'start', side_effect=RuntimeError('thread_limit')), \
                mock.patch.object(module.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'thread_limit'): module.start_process(['synthetic'])
            popen.assert_not_called()

    def test_popen_failure_releases_waiter_and_preserves_original_exception(self):
        module = self.module(); real_thread = threading.Thread; workers = []
        def record_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs); workers.append(thread); return thread
        with mock.patch.object(module.threading, 'Thread', side_effect=record_thread), \
                mock.patch.object(module.subprocess, 'Popen', side_effect=OSError('synthetic_spawn_failure')):
            with self.assertRaisesRegex(OSError, 'synthetic_spawn_failure'): module.start_process(['synthetic'])
        self.assertEqual(len(workers), 1)
        self.assertTrue(workers[0].daemon)
        workers[0].join(2); self.assertFalse(workers[0].is_alive())

    def test_waiter_holds_and_waits_only_its_exact_child(self):
        module = self.module(); entered = threading.Event(); released = threading.Event(); finished = threading.Event()
        def wait():
            entered.set(); released.wait(2); finished.set(); return 0
        process = mock.Mock(pid=100, wait=mock.Mock(side_effect=wait))
        other = mock.Mock(pid=101)
        with mock.patch.object(module.subprocess, 'Popen', return_value=process):
            returned = module.start_process(['synthetic'], start_new_session=True)
            self.assertIs(returned, process)
            self.assertTrue(entered.wait(2))
            process.kill.assert_not_called(); process.terminate.assert_not_called()
            other.wait.assert_not_called()
            released.set(); self.assertTrue(finished.wait(2))
            process.wait.assert_called_once_with()

    def test_executor_registers_child_before_pid_persistence_and_keeps_spawn_contract(self):
        path = Path(__file__).resolve().parents[1] / 'services' / 'executor.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'spawn_execution')
        start = mock.Mock(return_value=SimpleNamespace(pid=12345))
        execution = mock.Mock(id=8, execution_no='synthetic-execution', load_snapshot={})
        execution.save.side_effect = RuntimeError('pid_save_failure')
        with tempfile.TemporaryDirectory() as directory:
            scope = {'os': os, 'sys': sys, 'subprocess': subprocess, 'start_process': start,
                'settings': SimpleNamespace(BASE_DIR=Path(directory)), 'timezone': mock.Mock(),
                '_is_k6_execution': lambda _: False, 'abs_artifact_dir': lambda _: directory, 'logger': mock.Mock()}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), scope)
            with mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('unowned_direct_popen')):
                with self.assertRaisesRegex(RuntimeError, 'pid_save_failure'): scope['spawn_execution'](execution)
            start.assert_called_once()
            self.assertEqual(start.call_args.args[0], [sys.executable, str(Path(directory) / 'manage.py'), 'run_perf_execution', '8'])
            self.assertTrue(start.call_args.kwargs['start_new_session'])
            self.assertEqual(start.call_args.kwargs['stderr'], subprocess.STDOUT)
            self.assertEqual(execution.process_pid, 12345)
            execution.save.assert_called_once_with(update_fields=['process_pid', 'heartbeat_at'])


if __name__ == '__main__': unittest.main()
