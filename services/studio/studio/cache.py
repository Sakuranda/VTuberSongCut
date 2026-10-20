"""Reclaim reproducible runtime caches; never remove source media or checkpoints."""
import json
import logging
from pathlib import Path
import re
from threading import Event, Lock, Thread
import time

LOG = logging.getLogger(__name__)
RETENTION_DAYS = 7
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
ID = re.compile(r'^[0-9a-f]{32}$')
LABELS = {'features': '已结束训练的特征缓存', 'detection': '演唱检测中间数据',
          'previews': '不再使用的兼容预览', 'temporary': '中断的预览临时文件'}


class RuntimeCache:
    def __init__(self, data, store):
        self.data = Path(data).resolve()
        self.store = store
        self.lock = Lock()
        self.stop = Event()
        self.thread = None

    def safe_file(self, path):
        try:
            path.relative_to(self.data)
            # Resolving links/junctions must not change even an in-root target.
            if path.resolve() != path or path.is_symlink():
                return False
            return path.is_file()
        except (OSError, ValueError):
            return False

    def candidates(self):
        with self.store.connect() as db:
            projects = [json.loads(r[0]) for r in db.execute('SELECT data FROM projects')]
            jobs = [json.loads(r[0]) for r in db.execute('SELECT data FROM jobs')]
            training = [json.loads(r[0]) for r in db.execute('SELECT data FROM training_jobs')]
            datasets = [json.loads(r[0]) for r in db.execute('SELECT data FROM training_datasets')]
        # Protect every referenced source, even if imported through another API.
        protected = {str(Path(p[key]).resolve()) for p in projects for key in ('source_file', 'preview_file') if p.get(key)}
        protected.update(str(Path(s['path']).resolve()) for d in datasets for s in d['samples'])
        samples = {s['id'] for d in datasets for s in d['samples']}
        known = {j['id']: j for j in jobs}
        found = {}

        def add(path, category, job=None):
            if self.safe_file(path) and str(path) not in protected:
                found[path] = (category, job)

        for job in jobs:
            if job['status'] not in TERMINAL or not ID.fullmatch(job['id']):
                continue
            directory = self.data / 'jobs' / job['id']
            if directory.resolve() != directory:
                continue
            if job['kind'] == 'detect':
                for path in directory.glob('*.probs.npy'):
                    add(path, 'detection')
            elif job['kind'] == 'preview':
                for path in directory.glob('*.mp4'):
                    # Preserve sources and active preview references; only this
                    # producer writes MP4s here. Export jobs are never candidates.
                    add(path, 'temporary' if path.name.startswith('.') else 'previews', job)
        for job in training:
            if job['status'] not in TERMINAL or not ID.fullmatch(job['id']):
                continue
            directory = self.data / 'training/jobs' / job['id'] / 'features'
            if directory.resolve() != directory:
                continue
            for path in directory.glob('*.npy'):
                add(path, 'features')
        previews = self.data / 'training/previews'
        if previews.resolve() == previews:
            for path in previews.glob('*.mp4'):
                # Completed previews only become disposable after sample removal.
                if re.fullmatch(r'[0-9a-f]{32}\.mp4', path.name):
                    sid = path.stem
                    active = any(j['project_id'] == 'training-media:' + sid and j['status'] not in TERMINAL for j in jobs)
                    if sid not in samples and not active:
                        add(path, 'previews')
                else:
                    # Includes FFmpeg's hidden intermediate wrapping a pending file.
                    match = re.search(r'([0-9a-f]{32})\.([0-9a-f]{32})\.pending', path.name)
                    if match and (job := known.get(match[2])) and job['kind'] == 'training_preview' and job['status'] in TERMINAL:
                        add(path, 'temporary')
        return found

    def report(self, candidates):
        categories = {key: {'key': key, 'label': label, 'bytes': 0, 'files': 0} for key, label in LABELS.items()}
        for path, (category, _) in candidates.items():
            try:
                categories[category]['bytes'] += path.stat().st_size
                categories[category]['files'] += 1
            except FileNotFoundError:
                pass
        return {'categories': list(categories.values()), 'bytes': sum(x['bytes'] for x in categories.values()),
                'files': sum(x['files'] for x in categories.values()), 'retention_days': RETENTION_DAYS}

    def inspect(self):
        with self.lock:
            return self.report(self.candidates())

    def clean(self, min_age_days=0):
        with self.lock:
            candidates = self.candidates()  # Never trust a previous UI scan.
            removed_bytes = removed_files = failed_files = 0
            cutoff = time.time() - min_age_days * 86400
            for path, (_, job) in candidates.items():
                try:
                    if not self.safe_file(path):
                        continue
                    stat = path.stat()
                    if min_age_days and stat.st_mtime > cutoff:
                        continue
                    path.unlink()
                    removed_bytes += stat.st_size
                    removed_files += 1
                    if job and path.name in job['artifacts']:
                        # Only a terminal preview job; project revisions/edits stay untouched.
                        current = self.store.get_job(job['id'])
                        current.artifacts = [name for name in current.artifacts if name != path.name]
                        self.store.save_job(current)
                except FileNotFoundError:
                    continue
                except OSError:
                    failed_files += 1
                    LOG.warning('Cache cleanup could not remove %s', path, exc_info=True)
            return {**self.report(self.candidates()), 'removed_bytes': removed_bytes,
                    'removed_files': removed_files, 'failed_files': failed_files}

    def start(self):
        def sweep():
            while not self.stop.is_set():
                try:
                    self.clean(RETENTION_DAYS)
                except Exception:
                    LOG.exception('Runtime cache sweep failed; will retry later')
                if self.stop.wait(3600):
                    break
        self.thread = Thread(target=sweep, name='songcut-cache', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
