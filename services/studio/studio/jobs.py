"""Bounded, fair, durable work queue: one heavy task per workspace process."""
import math
from threading import Condition, Event, Thread
from .domain import Job
from .store import Store, ConflictError
from .resources import ResourcePolicy, ResourceLimitError
from .execution import TaskCancelled, task_scope


class JobRunner:
    def __init__(self, store: Store, policy=None):
        self.store = store
        self.policy = policy or ResourcePolicy()
        self.active = set()
        self._jobs = {}
        self.operations = {}
        self.handlers = {}
        self.pending = []
        self.cancel_events = {}
        self.lock = Condition()
        self.stopping = False
        self.preserve_pending = False
        self.last_owner = None
        self.thread = Thread(target=self._worker, name='songcut', daemon=True)
        # Old closure-only jobs cannot be replayed; structured jobs restore later.
        for job in store.jobs(limit=None):
            if job.status in ('queued', 'running') and not job.task:
                job.status, job.message = 'interrupted', '服务已重启；请重新发起此任务'
                store.save_job(job)
        self.thread.start()

    def register(self, kind, handler):
        self.handlers[kind] = handler

    def restore(self):
        with self.lock:
            for job in reversed(self.store.jobs(limit=None)):
                if job.status not in ('queued', 'running') or not job.task or job.id in self._jobs:
                    continue
                if job.kind not in self.handlers or len(self.pending) >= self.policy.max_pending:
                    job.status, job.message = 'interrupted', '任务未恢复，请重新发起'
                    self.store.save_job(job)
                    continue
                job.status, job.message, job.progress = 'queued', '服务重启，等待继续处理', 0
                self._enqueue(job)
            self.lock.notify_all()

    def _enqueue(self, job, operation=None):
        self.store.save_job(job)
        self._jobs[job.id] = job
        self.active.add(job.project_id)
        self.cancel_events[job.id] = Event()
        self.pending.append(job.id)
        if operation:
            self.operations[job.id] = operation

    def submit(self, project_id, kind, operation=None, input_revision=None, model_info=None, *, task=None, owner=None, cache_key=None):
        with self.lock:
            if self.stopping:
                raise ConflictError('服务正在关闭，请稍后重试')
            if cache_key:
                existing = next((j for j in self._jobs.values() if j.cache_key == cache_key), None)
                if existing:
                    return existing
            if project_id in self.active:
                raise ConflictError('这个项目已有任务在处理，请稍后再试')
            if len(self._jobs) >= self.policy.max_pending:
                raise ResourceLimitError('服务器任务队列已满，请稍后重试')
            if owner and sum(j.owner == owner for j in self._jobs.values()) >= self.policy.per_user:
                raise ResourceLimitError('你已有任务在排队或处理中，请完成后再提交')
            job = Job(project_id=project_id, kind=kind, input_revision=input_revision, owner=owner, task=task,
                      cache_key=cache_key, **(model_info or {}))
            self._enqueue(job, operation)
            self.lock.notify_all()
            return job

    def _worker(self):
        while True:
            with self.lock:
                self.lock.wait_for(lambda: self.pending or self.stopping)
                if self.stopping and (self.preserve_pending or not self.pending):
                    return
                index = next((i for i, ident in enumerate(self.pending) if self._jobs[ident].owner != self.last_owner), 0)
                ident = self.pending.pop(index)
                job = self._jobs[ident]
                cancel = self.cancel_events[ident]
                self.last_owner = job.owner
                job.status, job.message = 'running', '正在处理'
                self.store.save_job(job)
            try:
                with task_scope(cancel, self.policy.task_seconds):
                    self.policy.check_disk(self.store.path.parent)
                    operation = self.operations.get(ident)
                    artifacts = operation(ident) if operation else self.handlers[job.kind](ident, job.task)
                    job.artifacts = artifacts or []
                job.status, job.message, job.progress = 'succeeded', '处理完成', 1
            except TaskCancelled:
                job.status, job.message = ('queued', '等待服务重启后继续') if self.stopping and job.task else ('cancelled', '任务已取消')
            except Exception as exc:
                if cancel.is_set():
                    job.status, job.message = ('queued', '等待服务重启后继续') if self.stopping and job.task else ('cancelled', '任务已取消')
                else:
                    job.status, job.message = 'failed', str(exc)[-2000:]
            finally:
                with self.lock:
                    self.store.save_job(job)
                    self._jobs.pop(ident, None)
                    self.operations.pop(ident, None)
                    self.cancel_events.pop(ident, None)
                    self.active.discard(job.project_id)
                    self.lock.notify_all()

    def cancel(self, ident):
        with self.lock:
            job = self._jobs.get(ident)
            if job is None:
                return self.store.get_job(ident)
            self.cancel_events[ident].set()
            if job.status == 'queued':
                self.pending.remove(ident)
                job.status, job.message = 'cancelled', '已取消排队'
                self.store.save_job(job)
                self._jobs.pop(ident, None)
                self.operations.pop(ident, None)
                self.cancel_events.pop(ident, None)
                self.active.discard(job.project_id)
            return job

    def update_progress(self, job_id, progress, message=None):
        if not math.isfinite(progress):
            raise ValueError('任务进度必须为有限数值')
        with self.lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != 'running':
                return
            job.progress = max(job.progress, min(.99, max(0, progress)))
            if message:
                job.message = message
            self.store.save_job(job)

    def reset_progress(self, job_id, message):
        with self.lock:
            job = self._jobs.get(job_id)
            if job and job.status == 'running':
                job.progress, job.message = 0, message
                self.store.save_job(job)

    def close(self, preserve_pending=False):
        # Active work finishes on normal shutdown; queued structured jobs remain
        # persisted for the next start rather than extending shutdown indefinitely.
        with self.lock:
            self.stopping = True
            self.preserve_pending = preserve_pending
            if preserve_pending:
                for ident, job in self._jobs.items():
                    if job.status == 'running':
                        self.cancel_events[ident].set()
            self.lock.notify_all()
        self.thread.join()
