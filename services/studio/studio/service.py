"""Application operations shared by browser, recorder import and desktop."""
import hashlib
import json
import os
import shutil
from pathlib import Path
from .domain import Project, Clip
from .store import Store
from .jobs import JobRunner
from .ports import SingingDetector
from .adapters.media import FFmpegMedia, PREVIEW_PROFILES
from .adapters.lyrics import parse_lrc
from .resources import ResourcePolicy
from .execution import checkpoint


def source_signature(project):
    path = Path(project.source_file).resolve()
    try:
        stat = path.stat()
    except OSError as exc:
        raise ValueError('原始素材不可读取，请重新导入') from exc
    return hashlib.sha256(f'{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()


def key_for(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Studio:
    def __init__(self, data: Path, store: Store, runner: JobRunner, detector: SingingDetector, media: FFmpegMedia, policy=None):
        self.data, self.store, self.runner, self.detector, self.media = data, store, runner, detector, media
        self.policy = policy or ResourcePolicy()
        self.detector_factory = None
        runner.register('preview', self._preview)
        runner.register('export', self._export)
        runner.register('detect', self._detect)

    def preview_target(self, project, profile='low'):
        return self.data / 'previews' / f'{source_signature(project)}-{PREVIEW_PROFILES[profile]}.mp4'

    def with_previews(self, project):
        project = project.model_copy(deep=True)
        try:
            low = self.preview_target(project)
            high = self.preview_target(project, 'high')
            if low.is_file():
                project.preview_file, project.preview_profile = str(low), PREVIEW_PROFILES['low']
            if high.is_file():
                project.high_preview_file = str(high)
            elif project.source_browser_compatible and (project.source_height or 0) <= 1080 and (project.source_fps or 0) <= 30:
                project.high_preview_file = project.source_file
        except (OSError, ValueError):
            pass
        return project

    def _cached_job(self, cache_key):
        return self.store.cached_job(cache_key)

    def detect(self, ident, detector=None, model_info=None, owner=None):
        project = self.store.get(ident)
        selected = detector or self.detector
        if selected is None:
            raise ValueError('请先选择主播声线模型')
        signature = source_signature(project)
        key = key_for(['detect', signature, model_info, 'stream-v1', os.environ.get('SONGCUT_INFER_CHUNK_SECONDS'), os.environ.get('SONGCUT_INFER_OVERLAP_SECONDS')])
        cache_file = self.data / 'detections' / (key + '.json')
        task = {'project_id': ident, 'source': signature, 'model': model_info, 'cache': str(cache_file)}
        if self.detector_factory is None:
            return self.runner.submit(ident, 'detect', lambda job_id: self._detect(job_id, task, selected), project.revision, model_info, owner=owner)
        return self.runner.submit(ident, 'detect', input_revision=project.revision, model_info=model_info,
                                  task=task, owner=owner, cache_key=key_for([key, ident]))

    def _detect(self, job_id, task, selected=None):
        project = self.store.get(task['project_id'])
        if source_signature(project) != task['source']:
            raise ValueError('原始素材已变化，请重新导入后检测')
        cache = Path(task['cache'])
        if cache.is_file():
            clips = [Clip.model_validate(c) for c in json.loads(cache.read_text())]
        else:
            selected = selected or self.detector_factory(task['model'])
            clips = selected.detect(Path(project.source_file), self.data / 'jobs' / job_id, project.duration_ms)
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix('.tmp')
            temporary.write_text(json.dumps([c.model_dump() for c in clips], ensure_ascii=False))
            temporary.replace(cache)
        checkpoint()
        current = self.store.get(project.id)
        for clip in clips:
            if not any(min(clip.end_ms, c.end_ms) > max(clip.start_ms, c.start_ms) for c in current.clips):
                current.clips.append(clip)
        self.store.save(current, current.revision)
        return []

    def preview(self, ident, profile='low', owner=None):
        project = self.store.get(ident)
        key = key_for(['preview', source_signature(project), PREVIEW_PROFILES[profile]])
        cached = self._cached_job(key)
        if cached and self.preview_target(project, profile).is_file():
            return cached
        return self.runner.submit(ident, 'preview', input_revision=project.revision, owner=owner, cache_key=key,
                                  task={'project_id': ident, 'source': source_signature(project), 'profile': profile})

    def _preview(self, job_id, task):
        project = self.store.get(task['project_id'])
        if source_signature(project) != task['source']:
            raise ValueError('原始素材已变化，请重新生成预览')
        profile = task['profile']
        target = self.preview_target(project, profile)
        if not target.is_file():
            self.runner.update_progress(job_id, 0, '正在生成320p预览' if profile == 'low' else '正在生成高清预览')
            self.media.preview(Path(project.source_file), target, profile=profile, duration_ms=project.duration_ms,
                               on_progress=lambda value: self.runner.update_progress(job_id, value))
        # Do not bump edit revisions for background media preparation.
        self.store.update_media(project.id, **({'preview_file': str(target), 'preview_profile': PREVIEW_PROFILES['low']}
                                if profile == 'low' else {'high_preview_file': str(target)}))
        return []

    def find_clip(self, project: Project, clip_id: str) -> Clip:
        for clip in project.clips:
            if clip.id == clip_id:
                return clip
        raise KeyError(clip_id)

    def export(self, ident, clip_id, owner=None):
        project = self.store.get(ident)
        clip = self.find_clip(project, clip_id)
        if self.policy.low and project.has_video and (project.source_height is None or project.source_fps is None):
            info = self.media.metadata(Path(project.source_file))
            project.source_height, project.source_fps = info['height'], info['fps']
            if not project.source_height or not project.source_fps:
                raise ValueError('无法确定原素材规格，请重新导入后导出')
        self.policy.validate_export(project, clip)
        signature = source_signature(project)
        render_settings = clip.model_dump(exclude={'id'})
        render_key = key_for(['export-v2', signature, render_settings, os.environ.get('SONGCUT_FONT_FILE'), os.environ.get('SONGCUT_FONT_FAMILY')])
        key = key_for([render_key, ident])
        cached = self._cached_job(key)
        if cached and all((self.data / 'jobs' / cached.id / f).is_file() for f in cached.artifacts):
            return cached
        return self.runner.submit(ident, 'export', input_revision=project.revision, owner=owner, cache_key=key,
                                  task={'project': project.model_dump(), 'clip_id': clip_id, 'source': signature, 'render_key': render_key})

    def _export(self, job_id, task):
        project = Project.model_validate(task['project'])
        if source_signature(project) != task['source']:
            raise ValueError('原始素材已变化，请重新导入后导出')
        clip = self.find_clip(project, task['clip_id'])
        self.policy.validate_export(project, clip)
        output = self.data / 'jobs' / job_id
        reusable = self.store.cached_job(task['render_key'], 'render_key') if task.get('render_key') else None
        if reusable and (reusable.id == job_id or not all((self.data / 'jobs' / reusable.id / f).is_file() for f in reusable.artifacts)):
            reusable = None
        if reusable:
            output.mkdir(parents=True, exist_ok=True)
            files = []
            for name in reusable.artifacts:
                if name == 'project.snapshot.json':
                    continue
                target = output / name
                target.unlink(missing_ok=True)
                source = self.data / 'jobs' / reusable.id / name
                try:
                    target.hardlink_to(source)
                except OSError:
                    shutil.copy2(source, target)
                files.append(target)
        else:
            files = self.media.render(project, clip, output)
        (output / 'project.snapshot.json').write_text(project.model_dump_json(indent=2))
        return [p.name for p in files] + ['project.snapshot.json']

    def lyrics(self, ident, clip_id, text, offset_ms):
        project = self.store.get(ident)
        clip = self.find_clip(project, clip_id)
        clip.lyrics = parse_lrc(text, clip.start_ms + offset_ms, project.duration_ms)
        return self.store.save(project, project.revision)
