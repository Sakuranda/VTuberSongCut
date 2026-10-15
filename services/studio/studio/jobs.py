from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from .domain import Job
from .store import Store, ConflictError


class JobRunner:
    """One heavy operation per project, one GPU worker per local service."""
    def __init__(self, store: Store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="songcut")
        self.active: set[str] = set()
        self.lock = Lock()
        store.recover()

    def submit(self, project_id: str, kind: str, operation, input_revision: int | None = None) -> Job:
        with self.lock:
            if project_id in self.active:
                raise ConflictError("这个项目已有任务在处理，请稍后再试")
            self.active.add(project_id)
        job = Job(project_id=project_id, kind=kind, input_revision=input_revision)
        self.store.save_job(job)

        def run():
            try:
                job.status, job.message = "running", "正在处理"
                self.store.save_job(job)
                job.artifacts = operation(job.id) or []
                job.status, job.message = "succeeded", "处理完成"
            except Exception as exc:
                job.status, job.message = "failed", str(exc)[-2000:]
            finally:
                self.store.save_job(job)
                with self.lock:
                    self.active.discard(project_id)
        self.pool.submit(run)
        return job

    def close(self):
        self.pool.shutdown(wait=True)
