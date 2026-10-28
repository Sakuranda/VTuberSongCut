"""Fill a small background preview queue without starving interactive requests."""
import logging
from threading import Event, Thread
from .service import source_signature


class PreviewMaintenance:
    def __init__(self, studio, training):
        self.studio, self.training = studio, training
        self.stop = Event()
        self.thread = None

    def sweep(self):
        runner = self.studio.runner
        if any(j['status'] in {'queued', 'running'} for j in self.training.jobs()):
            return
        budget = max(1, runner.policy.max_pending - min(4, runner.policy.max_pending // 2))
        jobs = self.studio.store.jobs(limit=None)
        for project in reversed(self.studio.store.projects()):
            if self.stop.is_set():
                return
            with runner.lock:
                if len(runner._jobs) >= budget:
                    return
            if not project.has_video:
                continue
            try:
                if self.studio.preview_target(project).is_file():
                    continue
                signature = source_signature(project)
                previous = next((j for j in jobs if j.kind == 'preview' and j.task
                                 and j.task.get('source') == signature and j.task.get('profile') == 'low'), None)
                if previous and previous.status in {'failed', 'cancelled'}:
                    continue  # Explicit user retry; don't loop over broken inputs.
                self.studio.preview(project.id)
            except (ValueError, OSError):
                continue
            except Exception:
                logging.getLogger(__name__).exception('Preview scheduling failed')

    def start(self):
        def run():
            while not self.stop.is_set():
                self.sweep()
                if self.stop.wait(15):
                    break
        self.thread = Thread(target=run, name='songcut-previews', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
