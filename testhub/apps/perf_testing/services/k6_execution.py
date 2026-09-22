"""Private, immutable execution inputs and single-host K6 process coordination."""
import errno
import json
import os
import re
import threading
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1
ADAPTER_VERSION = 1
SOURCE_REVISION = '272ab153c6381b8eb726847dee3b2d819f2574ca'


class K6ExecutionError(RuntimeError):
    """A safe, user-facing execution configuration error."""


class K6LeaseBusy(K6ExecutionError):
    """Only an OS locking conflict, not a filesystem or execution failure."""


def safe_engine_error(error: Exception) -> str:
    """Only adapter-owned runtime diagnoses may enter public reports."""
    message = str(error)
    static = {
        'k6 运行时变量或请求配置失败，已保留已有统计；请检查场景关联规则',
        '未产生任何已完成的业务请求，不能判定压测成功；请检查超时、时长和业务配置',
        '未完成指定的用户轮次，可能达到最长时间；请查看已获得的数据',
        '未找到 k6 可执行文件，请设置 K6_BIN',
        'k6 发压窗口起点不一致，不能判定执行成功',
        '执行组缺少终态，已保留实际请求与未完成组数据',
    }
    numeric = (
        r'k6 异常退出（退出码 -?\d+），已保留收到的请求统计',
        r'\d+ 个用户前置登录或提取失败，未发送这些用户的业务请求',
        r'\d+ 个用户在发压到期时尚未完成全部前置步骤',
    )
    if message in static or any(re.fullmatch(pattern, message) for pattern in numeric):
        return message
    return 'K6 执行失败，请检查预检结果和本次报告'


def _directory(root: str | Path, relative: str) -> Path:
    path = Path(root).resolve() / relative
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def save_snapshot(root: str | Path, execution_id: int, snapshot: dict) -> Path:
    path = _directory(root, f'executions/{int(execution_id)}') / 'snapshot.json'
    envelope = {
        'schema_version': SCHEMA_VERSION,
        'adapter_version': ADAPTER_VERSION,
        'source_revision': SOURCE_REVISION,
        'snapshot': snapshot,
    }
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(envelope, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        if os.name != 'nt':
            parent_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
    except (OSError, ValueError, TypeError) as exc:
        raise K6ExecutionError('无法创建私密执行快照；禁止覆盖已有快照') from exc
    return path


def load_snapshot(root: str | Path, execution_id: int) -> dict:
    path = Path(root).resolve() / 'executions' / str(int(execution_id)) / 'snapshot.json'
    try:
        with path.open(encoding='utf-8') as source:
            envelope = json.load(source)
        if (envelope.get('schema_version') != SCHEMA_VERSION
                or envelope.get('adapter_version') != ADAPTER_VERSION
                or not isinstance(envelope.get('snapshot'), dict)):
            raise ValueError('incompatible snapshot')
        return envelope['snapshot']
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise K6ExecutionError('私密执行快照缺失、损坏或版本不兼容；请重新创建执行') from exc


class FileLease:
    """An OS-held nonblocking lock; a crashed owner cannot leave a stale lock."""

    def __init__(self, root: str | Path, name: str) -> None:
        if name not in ('start', 'run', 'recovery', 'recovery-state'):
            raise ValueError('unknown K6 lock')
        self.path = _directory(root, 'locks') / f'k6-{name}.lock'
        self.fd: int | None = None

    def __enter__(self) -> 'FileLease':
        fd = None
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise K6LeaseBusy('已有 K6 任务正在启动或运行；本地版串行执行以避免账号池冲突') from exc
                raise
        except K6LeaseBusy:
            os.close(fd)
            raise
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            raise K6ExecutionError('无法获取 K6 执行租约，请检查私密目录与文件系统状态') from exc
        self.fd = fd
        return self

    def __exit__(self, *_args: object) -> None:
        if self.fd is not None:
            fd, self.fd = self.fd, None
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


class StopMonitor:
    def __init__(self, read_status: Callable[[], str], stop: Callable[[], None],
                 interval: float = 0.25) -> None:
        self.read_status = read_status
        self.stop = stop
        self.interval = interval
        self.done = threading.Event()
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._watch, name='k6-stop-monitor', daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.done.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=15)

    def _watch(self) -> None:
        while not self.done.wait(self.interval):
            try:
                status = self.read_status()
            except Exception:
                self.error = K6ExecutionError('无法读取执行状态，已请求停止 K6 发压')
                self._stop_safely()
                return
            if status != 'RUNNING':
                self._stop_safely()
                return

    def _stop_safely(self) -> None:
        try:
            self.stop()
        except Exception:
            self.error = K6ExecutionError('无法读取执行状态且 K6 停止清理失败，请检查本次压力进程'
                if self.error else 'K6 停止清理失败，请检查本次压力进程')
            try:
                self.stop()
            except Exception:
                pass  # The first cleanup failure remains a failed execution.


def drive_engine(engine: object, read_status: Callable[[], str],
                 on_started: Callable[[], None]) -> tuple[dict, Exception | None]:
    """Always stop on failure and collect partial results after the process exits."""
    monitor = StopMonitor(read_status, engine.stop)
    result, error = {}, None
    try:
        engine.prepare()
        if read_status() == 'STOPPING':
            engine.stop()
        else:
            if on_started() is False:
                engine.stop()
            else:
                monitor.start()
                engine.run()
    except Exception as exc:
        error = exc
        try:
            engine.stop()
        except Exception:
            error = K6ExecutionError('K6 执行失败且停止失败，请检查压力进程')
    finally:
        monitor.close()
        error = error or monitor.error
        try:
            result = engine.collect() or {}
        except Exception as exc:
            error = error or exc
    return result, error
