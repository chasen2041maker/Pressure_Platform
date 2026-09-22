"""Keep the reminder recovery daemon independent of web workers and execution children."""
import signal
import subprocess
import threading
import time


def terminate(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def serve(web_command, recovery_command, *, cwd, popen=subprocess.Popen, stopped=None):
    stopped=stopped or threading.Event()
    for signum in (signal.SIGINT,signal.SIGTERM):
        signal.signal(signum,lambda *_:stopped.set())
    processes=[]
    try:
        daemon=popen(recovery_command,cwd=cwd)
        processes.append(daemon)
        web=popen(web_command,cwd=cwd)
        processes.append(web)
        failures=0
        since=time.monotonic()
        while not stopped.wait(0.25):
            code=web.poll()
            if code is not None:
                return code or 1
            if daemon.poll() is not None:
                failures=failures+1 if time.monotonic()-since < 60 else 1
                if failures >= 3:
                    return 1
                if stopped.wait(1):
                    break
                daemon=popen(recovery_command,cwd=cwd)
                processes.append(daemon)
                since=time.monotonic()
        return 0
    finally:
        for process in reversed(processes):
            terminate(process)
