from dataclasses import replace
from threading import Event
import time

import pytest
from studio.domain import Job
from studio.execution import checkpoint
from studio.jobs import JobRunner
from studio.resources import ResourcePolicy, ResourceLimitError
from studio.store import Store


def finish(store, ident):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        job = store.get_job(ident)
        if job.status not in ('queued', 'running'):
            return job
        time.sleep(.01)
    raise AssertionError('job did not finish')


def test_bounded_queue_per_user_dedupe_and_fair_scheduling(tmp_path):
    store = Store(tmp_path / 'studio.sqlite3')
    runner = JobRunner(store, ResourcePolicy(reserve_bytes=0, max_pending=3, per_user=2))
    started, release = Event(), Event()
    order = []
    def first(_):
        started.set()
        assert release.wait(4)
        order.append('alice-first')
    try:
        one = runner.submit('p1', 'test', first, owner='alice', cache_key='same')
        assert started.wait(2)
        assert runner.submit('other', 'test', first, owner='alice', cache_key='same').id == one.id
        two = runner.submit('p2', 'test', lambda _: order.append('alice-second'), owner='alice')
        with pytest.raises(ResourceLimitError):
            runner.submit('p3', 'test', lambda _: [], owner='alice')
        three = runner.submit('p3', 'test', lambda _: order.append('bob'), owner='bob')
        with pytest.raises(ResourceLimitError):
            runner.submit('p4', 'test', lambda _: [], owner='charlie')
        release.set()
        assert finish(store, two.id).status == 'succeeded'
        assert finish(store, three.id).status == 'succeeded'
        assert order == ['alice-first', 'bob', 'alice-second']
    finally:
        release.set()
        runner.close()


def test_cancel_queued_and_running_work(tmp_path):
    store = Store(tmp_path / 'studio.sqlite3')
    runner = JobRunner(store, ResourcePolicy(reserve_bytes=0))
    entered = Event()
    def operation(_):
        entered.set()
        while True:
            checkpoint()
            time.sleep(.01)
    try:
        running = runner.submit('one', 'test', operation)
        assert entered.wait(2)
        queued = runner.submit('two', 'test', lambda _: pytest.fail('cancelled job executed'))
        assert runner.cancel(queued.id).status == 'cancelled'
        runner.cancel(running.id)
        assert finish(store, running.id).status == 'cancelled'
        assert not runner.active
    finally:
        runner.close()


def test_structured_jobs_restore_after_restart(tmp_path):
    store = Store(tmp_path / 'studio.sqlite3')
    record = Job(project_id='one', kind='test', task={'saved': 'value'}, status='running', owner='alice')
    store.save_job(record)
    legacy = Job(project_id='legacy', kind='old', status='queued')
    store.save_job(legacy)
    runner = JobRunner(store, ResourcePolicy(reserve_bytes=0))
    seen = []
    runner.register('test', lambda ident, task: seen.append((ident, task)) or ['finished.txt'])
    try:
        runner.restore()
        result = finish(store, record.id)
        assert result.status == 'succeeded' and result.artifacts == ['finished.txt']
        assert seen == [(record.id, {'saved': 'value'})]
        assert store.get_job(legacy.id).status == 'interrupted'
    finally:
        runner.close()


def test_shutdown_preserves_structured_work_and_restart_resumes(tmp_path):
    store = Store(tmp_path / 'studio.sqlite3')
    runner = JobRunner(store, ResourcePolicy(reserve_bytes=0))
    entered = Event()
    def blocking(ident, task):
        entered.set()
        while True:
            checkpoint()
            time.sleep(.01)
    runner.register('test', blocking)
    first = runner.submit('one', 'test', task={'which': 'active'})
    assert entered.wait(2)
    second = runner.submit('two', 'test', task={'which': 'queued'})
    started = time.monotonic()
    runner.close(preserve_pending=True)
    assert time.monotonic() - started < 2
    assert store.get_job(first.id).status == 'queued'
    assert store.get_job(second.id).status == 'queued'
    restarted = JobRunner(store, ResourcePolicy(reserve_bytes=0))
    seen = []
    restarted.register('test', lambda ident, task: seen.append(task['which']) or [])
    try:
        restarted.restore()
        assert finish(store, first.id).status == 'succeeded'
        assert finish(store, second.id).status == 'succeeded'
        assert sorted(seen) == ['active', 'queued']
    finally:
        restarted.close()
