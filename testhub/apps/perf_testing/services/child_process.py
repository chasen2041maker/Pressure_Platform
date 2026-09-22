"""Keep each performance worker owned until its exit status is collected."""
import subprocess
import threading


def start_process(*args, **kwargs):
    ready = threading.Event()
    processes = []

    def reap():
        ready.wait()
        if processes:
            processes[0].wait()

    # Reserve the waiter before spawning, so thread exhaustion cannot orphan a child.
    waiter = threading.Thread(target=reap, name='perf-worker-reaper', daemon=True)
    waiter.start()
    try:
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process
    finally:
        ready.set()
