import os
import subprocess
import sys
from threading import Event, Timer
import time
import pytest
from studio.execution import TaskCancelled, checkpoint, run_process, task_scope


def test_success_capture_and_nonzero_exit_contract():
    result = run_process([sys.executable, '-c', 'print("ok")'], capture_output=True, text=True, timeout=5)
    assert result.stdout.strip() == 'ok' and result.returncode == 0
    with pytest.raises(subprocess.CalledProcessError):
        run_process([sys.executable, '-c', 'raise SystemExit(7)'], check=True, timeout=5)


def test_cancelled_before_spawn_and_context_cleans_up(tmp_path):
    cancel = Event(); cancel.set()
    output = tmp_path / 'should-not-exist'
    with pytest.raises(TaskCancelled):
        with task_scope(cancel, 5):
            run_process([sys.executable, '-c', f'open({str(output)!r},"w").write("bad")'])
    assert not output.exists()
    checkpoint()


@pytest.mark.parametrize('mode', ['cancel', 'deadline', 'timeout'])
def test_running_process_is_reaped_on_cancel_or_timeout(tmp_path, mode):
    pid_file = tmp_path / 'pid'
    script = f'import os,time; open({str(pid_file)!r},"w").write(str(os.getpid())); time.sleep(60)'
    cancel = Event()
    timer = Timer(1.2, cancel.set); timer.start()
    try:
        expected = TaskCancelled if mode == 'cancel' else TimeoutError if mode == 'deadline' else subprocess.TimeoutExpired
        started = time.monotonic()
        with pytest.raises(expected):
            if mode == 'timeout': run_process([sys.executable, '-c', script], timeout=1)
            else:
                with task_scope(cancel if mode == 'cancel' else Event(), 1 if mode == 'deadline' else 5):
                    run_process([sys.executable, '-c', script], timeout=5)
        assert time.monotonic() - started < 6
        pid = int(pid_file.read_text())
        if os.name != 'nt':
            with pytest.raises(ProcessLookupError): os.kill(pid, 0)
    finally: timer.cancel()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group assertion')
def test_cancellation_stops_owned_child_tree(tmp_path):
    parent_file, child_file = tmp_path / 'parent', tmp_path / 'child'
    child = f'import os,time; open({str(child_file)!r},"w").write(str(os.getpid())); time.sleep(60)'
    script = f'''import os,subprocess,sys,time,signal
open({str(parent_file)!r},'w').write(str(os.getpid()))
child = subprocess.Popen([sys.executable,'-c',{child!r}])
def stop(signum, frame):
    child.wait(timeout=1)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
time.sleep(60)
'''
    cancel = Event()
    timer = Timer(1.5, cancel.set); timer.start()
    try:
        with pytest.raises(TaskCancelled):
            with task_scope(cancel, 5):
                run_process([sys.executable, '-c', script], capture_output=True, timeout=5)
        for path in (parent_file, child_file):
            with pytest.raises(ProcessLookupError): os.kill(int(path.read_text()), 0)
    finally: timer.cancel()
