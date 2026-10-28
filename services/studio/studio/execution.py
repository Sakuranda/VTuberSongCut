"""Cooperative task cancellation and deadlines for owned subprocess trees."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import signal
import subprocess
from threading import Event, Thread
import time

_CURRENT = ContextVar('songcut_execution', default=None)


class TaskCancelled(Exception):
    pass


@contextmanager
def task_scope(cancel, seconds):
    token = _CURRENT.set((cancel, time.monotonic() + seconds))
    try:
        yield
        checkpoint()
    finally:
        _CURRENT.reset(token)


def checkpoint():
    state = _CURRENT.get()
    if state:
        if state[0].is_set():
            raise TaskCancelled('任务已取消')
        if time.monotonic() >= state[1]:
            raise TimeoutError('任务超过服务器处理时限')


def stop_process(process):
    if os.name == 'nt':
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@contextmanager
def watch_process(process):
    state = _CURRENT.get()
    stopped = Event()
    def watch():
        while not stopped.wait(.2):
            if state and (state[0].is_set() or time.monotonic() >= state[1]):
                stop_process(process)
                return
    thread = Thread(target=watch, daemon=True) if state else None
    if thread:
        thread.start()
    try:
        yield process
        checkpoint()
    finally:
        stopped.set()
        if thread:
            thread.join(timeout=12)


def run_process(command, *, timeout=None, check=False, capture_output=False, **kwargs):
    checkpoint()
    if capture_output:
        kwargs['stdout'] = subprocess.PIPE
        kwargs['stderr'] = subprocess.PIPE
    if os.name != 'nt':
        kwargs['start_new_session'] = True
    else:
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    with subprocess.Popen(command, **kwargs) as process:
        with watch_process(process):
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except BaseException:
                stop_process(process)
                process.communicate()
                raise
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
