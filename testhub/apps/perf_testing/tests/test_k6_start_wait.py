"""Bounded start admission; real OS contention, no business or database writes."""
import errno
import multiprocessing
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock


def _hold_lease(root, name, acquired, release, finished):
    from apps.perf_testing.services.k6_execution import FileLease
    try:
        with FileLease(root, name):
            acquired.set()
            if not release.wait(10):
                raise RuntimeError('test owner release timed out')
    finally:
        finished.set()


class K6StartWaitTests(unittest.TestCase):
    def setUp(self):
        from apps.perf_testing.models import PerfExecution
        from apps.perf_testing.services import executor, k6_execution
        self.executor, self.leases = executor, k6_execution
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = self.stack.enter_context(tempfile.TemporaryDirectory())
        from django.test import override_settings
        self.stack.enter_context(override_settings(PERF_PRIVATE_ROOT=self.root))
        self.stack.enter_context(mock.patch.object(executor, '_resolve_environment_inputs', return_value={}))
        self.stack.enter_context(mock.patch.object(executor, 'preflight', return_value={'passed': True, 'errors': []}))
        self.objects = self.stack.enter_context(mock.patch.object(PerfExecution, 'objects'))
        self.active = self.objects.filter.return_value.filter.return_value.exists
        self.active.return_value = False
        self.created = SimpleNamespace(id=1)
        self.create = self.stack.enter_context(mock.patch.object(executor, 'create_execution', return_value=self.created))
        self.spawn = self.stack.enter_context(mock.patch.object(executor, 'spawn_execution'))
        self.scenario = SimpleNamespace(engine='K6', steps=mock.Mock())
        self.scenario.steps.exclude.return_value.exists.return_value = False

    @contextmanager
    def run_owner(self, name='run'):
        context = multiprocessing.get_context('spawn')
        acquired, release, finished = (context.Event() for _ in range(3))
        owner = context.Process(target=_hold_lease, args=(self.root, name, acquired, release, finished))
        owner.start()
        try:
            self.assertTrue(acquired.wait(5), 'OS owner did not acquire the lease')
            yield release, finished
        finally:
            release.set()
            owner.join(5)
            if owner.is_alive():
                owner.terminate()
                owner.join(5)
            self.assertEqual(owner.exitcode, 0)

    def start(self):
        return self.executor.start_execution(self.scenario)

    def test_other_process_releases_run_then_create_and_spawn_once(self):
        with self.run_owner() as (release, finished):
            timer = threading.Timer(0.2, release.set)
            timer.start()
            try:
                execution, check = self.start()
            finally:
                timer.join(2)
            self.assertIs(execution, self.created)
            self.assertTrue(check['passed'])
            self.assertTrue(finished.wait(2))
            self.create.assert_called_once()
            self.spawn.assert_called_once_with(self.created)
        with self.leases.FileLease(self.root, 'run'), self.leases.FileLease(self.root, 'start'):
            pass

    def test_busy_deadline_is_three_seconds_and_creates_nothing(self):
        clock = [0.0]
        pauses = []
        def sleep(delay):
            self.assertGreater(delay, 0)
            self.assertLessEqual(delay, 0.05)
            pauses.append(delay)
            clock[0] += delay
        with self.run_owner(), mock.patch.object(self.executor.time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(self.executor.time, 'sleep', side_effect=sleep):
            execution, check = self.start()
        self.assertIsNone(execution)
        self.assertFalse(check['passed'])
        self.assertAlmostEqual(clock[0], 3.0)
        self.assertLessEqual(len(pauses), 61)
        self.create.assert_not_called()
        self.spawn.assert_not_called()

    def test_active_execution_appears_while_waiting(self):
        active = [False]
        self.active.side_effect = lambda: active[0]
        with self.run_owner(), mock.patch.object(self.executor.time, 'sleep', side_effect=lambda _: active.__setitem__(0, True)) as pause:
            execution, check = self.start()
        self.assertIsNone(execution)
        self.assertFalse(check['passed'])
        pause.assert_called_once()
        self.create.assert_not_called()
        self.spawn.assert_not_called()

    def test_active_rechecked_after_run_acquisition(self):
        active = [False]
        self.active.side_effect = lambda: active[0]
        original_enter = self.leases.FileLease.__enter__
        def enter(lease):
            result = original_enter(lease)
            if lease.path.name == 'k6-run.lock':
                active[0] = True
            return result
        with mock.patch.object(self.leases.FileLease, '__enter__', enter):
            execution, check = self.start()
        self.assertIsNone(execution)
        self.assertFalse(check['passed'])
        self.create.assert_not_called()
        self.spawn.assert_not_called()
        with self.leases.FileLease(self.root, 'run'):
            pass

    def test_active_rejects_without_waiting_even_when_start_is_busy(self):
        self.active.return_value = True
        for held_start in (False, True):
            with self.subTest(held_start=held_start), ExitStack() as contexts:
                if held_start:
                    contexts.enter_context(self.leases.FileLease(self.root, 'start'))
                pause = contexts.enter_context(mock.patch.object(self.executor.time, 'sleep'))
                execution, check = self.start()
                self.assertIsNone(execution)
                self.assertFalse(check['passed'])
                self.assertIn('待执行', check['errors'][0])
                pause.assert_not_called()
        self.create.assert_not_called()
        self.spawn.assert_not_called()

    def test_other_process_releases_start_then_creates_and_spawns_once(self):
        with self.run_owner('start') as (release, finished):
            timer = threading.Timer(0.2, release.set)
            timer.start()
            try:
                execution, check = self.start()
            finally:
                timer.join(2)
            self.assertIs(execution, self.created)
            self.assertTrue(check['passed'])
            self.assertTrue(finished.wait(2))
        self.create.assert_called_once()
        self.spawn.assert_called_once_with(self.created)

    def test_start_busy_times_out_with_no_create_or_spawn(self):
        clock = [0.0]
        def sleep(delay):
            self.assertGreater(delay, 0)
            self.assertLessEqual(delay, 0.05)
            clock[0] += delay
        with self.run_owner('start'), mock.patch.object(self.executor.time, 'monotonic', side_effect=lambda: clock[0]), \
                mock.patch.object(self.executor.time, 'sleep', side_effect=sleep):
            execution, check = self.start()
        self.assertIsNone(execution)
        self.assertFalse(check['passed'])
        self.assertAlmostEqual(clock[0], 3.0)
        self.create.assert_not_called()
        self.spawn.assert_not_called()

    def test_start_wait_consumes_run_wait_budget(self):
        clock = [0.0]
        waits = {'start': 0.0, 'run': 0.0}
        with self.run_owner(), self.leases.FileLease(self.root, 'start') as owner:
            def sleep(delay):
                self.assertGreater(delay, 0)
                self.assertLessEqual(delay, 0.05)
                waits['start' if owner.fd is not None else 'run'] += delay
                clock[0] += delay
                if clock[0] >= 2.0:
                    owner.__exit__(None, None, None)
            with mock.patch.object(self.executor.time, 'monotonic', side_effect=lambda: clock[0]), \
                    mock.patch.object(self.executor.time, 'sleep', side_effect=sleep):
                execution, check = self.start()
        self.assertIsNone(execution)
        self.assertFalse(check['passed'])
        self.assertAlmostEqual(clock[0], 3.0)
        self.assertAlmostEqual(waits['start'], 2.0)
        self.assertAlmostEqual(waits['run'], 1.0)
        self.create.assert_not_called()
        self.spawn.assert_not_called()

    def test_create_and_spawn_errors_never_retry_even_for_busy_type(self):
        busy = getattr(self.leases, 'K6LeaseBusy', self.leases.K6ExecutionError)
        for stage, error in (('create', busy('creation busy')),
                             ('create', RuntimeError('creation failed')),
                             ('spawn', busy('spawn outcome unknown')),
                             ('spawn', OSError('spawn outcome unknown'))):
            with self.subTest(stage=stage, error=type(error).__name__):
                self.create.reset_mock(side_effect=True)
                self.create.return_value = self.created
                self.spawn.reset_mock(side_effect=True)
                getattr(self, stage).side_effect = error
                with mock.patch.object(self.executor.time, 'sleep') as pause:
                    if isinstance(error, self.leases.K6ExecutionError):
                        execution, check = self.start()
                        self.assertIsNone(execution)
                        self.assertFalse(check['passed'])
                    else:
                        with self.assertRaises(type(error)):
                            self.start()
                    pause.assert_not_called()
                self.create.assert_called_once()
                self.assertEqual(self.spawn.call_count, int(stage == 'spawn'))
                with self.leases.FileLease(self.root, 'run'), self.leases.FileLease(self.root, 'start'):
                    pass

    def test_only_os_lock_contention_has_busy_type(self):
        busy = getattr(self.leases, 'K6LeaseBusy', None)
        self.assertIsNotNone(busy, 'OS contention must be distinguishable from I/O failure')
        with self.run_owner():
            with self.assertRaises(busy):
                with self.leases.FileLease(self.root, 'run'):
                    self.fail('second process acquired run')
        lock = self.leases.FileLease(self.root, 'run')
        if os.name == 'nt':
            target = 'msvcrt.locking'
        else:
            target = 'fcntl.flock'
        with mock.patch(target, side_effect=OSError(errno.EIO, 'private device detail')):
            with self.assertRaises(self.leases.K6ExecutionError) as caught:
                lock.__enter__()
        self.assertNotIsInstance(caught.exception, busy)
        self.assertNotIn('private device detail', str(caught.exception))
        self.assertIsNone(lock.fd)
        with mock.patch.object(self.leases.os, 'fstat', side_effect=OSError(errno.EACCES, 'private metadata detail')):
            with self.assertRaises(self.leases.K6ExecutionError) as caught:
                lock.__enter__()
        self.assertNotIsInstance(caught.exception, busy)
        with self.leases.FileLease(self.root, 'run'):
            pass

    def test_nonbusy_start_or_run_io_error_does_not_wait_or_create(self):
        original_enter = self.leases.FileLease.__enter__
        for name in ('start', 'run'):
            def enter(lease):
                if lease.path.name == f'k6-{name}.lock':
                    raise self.leases.K6ExecutionError('无法获取 K6 执行租约')
                return original_enter(lease)
            with self.subTest(name=name), mock.patch.object(self.leases.FileLease, '__enter__', enter), \
                    mock.patch.object(self.executor.time, 'sleep') as pause:
                execution, check = self.start()
            self.assertIsNone(execution)
            self.assertFalse(check['passed'])
            pause.assert_not_called()
        self.create.assert_not_called()
        self.spawn.assert_not_called()
