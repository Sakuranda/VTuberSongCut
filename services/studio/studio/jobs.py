from concurrent.futures import ThreadPoolExecutor
import math
from threading import Lock
from .domain import Job
from .store import Store, ConflictError


class JobRunner:
    """One heavy operation per project, one GPU worker per local service."""
    def __init__(self, store: Store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="songcut")
        self.active: set[str] = set()
        self._jobs: dict[str, Job] = {}
        self.lock = Lock()
        store.recover()

    def submit(self, project_id: str, kind: str, operation, input_revision: int | None = None, model_info: dict | None = None) -> Job:
        with self.lock:
            if project_id in self.active:
                raise ConflictError("这个项目已有任务在处理，请稍后再试")
            job = Job(project_id=project_id, kind=kind, input_revision=input_revision, **(model_info or {}))
            self.store.save_job(job)
            self.active.add(project_id)
            self._jobs[job.id] = job

        def run():
            try:
                with self.lock:
                    job.status, job.message = "running", "正在处理"
                    self.store.save_job(job)
                job.artifacts = operation(job.id) or []
                job.status, job.message, job.progress = "succeeded", "处理完成", 1
            except Exception as exc:
                job.status, job.message = "failed", str(exc)[-2000:]
            finally:
                with self.lock:
                    self.store.save_job(job)
                    self._jobs.pop(job.id, None)
                    self.active.discard(project_id)
        self.pool.submit(run)
        return job

    def update_progress(self, job_id: str, progress: float, message: str | None = None):
        """Persist real worker progress; only successful completion may reach 100%."""
        if not math.isfinite(progress):
            raise ValueError("任务进度必须为有限数值")
        with self.lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return
            job.progress = max(job.progress, min(.99, max(0, progress)))
            if message:
                job.message = message
            self.store.save_job(job)

    def close(self):
        self.pool.shutdown(wait=True)
