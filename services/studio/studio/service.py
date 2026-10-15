"""Application operations shared by every controller (HTTP, desktop, future CLI)."""
from pathlib import Path
from .domain import Project, Clip
from .store import Store
from .jobs import JobRunner
from .ports import SingingDetector
from .adapters.media import FFmpegMedia
from .adapters.lyrics import parse_lrc


class Studio:
    def __init__(self, data: Path, store: Store, runner: JobRunner, detector: SingingDetector, media: FFmpegMedia):
        self.data, self.store, self.runner, self.detector, self.media = data, store, runner, detector, media

    def detect(self, ident: str, detector: SingingDetector | None = None, model_info: dict | None = None):
        project = self.store.get(ident)
        selected_detector = detector or self.detector
        if selected_detector is None:
            raise ValueError("请先选择主播声线模型")
        def operation(job_id):
            clips = selected_detector.detect(Path(project.source_file), self.data / "jobs" / job_id, project.duration_ms)
            # Keep manual edits: append to the newest revision, still reject concurrent writes.
            current = self.store.get(ident)
            for clip in clips:
                # Re-running detection should not duplicate already reviewed matching spans.
                if not any(min(clip.end_ms, c.end_ms) > max(clip.start_ms, c.start_ms) for c in current.clips):
                    current.clips.append(clip)
            self.store.save(current, current.revision)
            return []
        return self.runner.submit(ident, "detect", operation, project.revision, model_info)

    def preview(self, ident: str):
        project = self.store.get(ident)
        if not project.has_video:
            raise ValueError("音频文件不需要视频预览代理")
        def operation(job_id):
            output = self.data / "jobs" / job_id
            output.mkdir(parents=True, exist_ok=True)
            target = output / "preview.mp4"
            self.media.preview(Path(project.source_file), target)
            current = self.store.get(ident)
            current.preview_file = str(target)
            self.store.save(current, current.revision)
            return [target.name]
        return self.runner.submit(ident, "preview", operation, project.revision)

    def find_clip(self, project: Project, clip_id: str) -> Clip:
        for clip in project.clips:
            if clip.id == clip_id:
                return clip
        raise KeyError(clip_id)

    def export(self, ident: str, clip_id: str):
        # Immutable snapshot: later editing cannot change an already queued render.
        project = self.store.get(ident)
        clip = self.find_clip(project, clip_id)
        def operation(job_id):
            output = self.data / "jobs" / job_id
            files = self.media.render(project, clip, output)
            (output / "project.snapshot.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
            return [p.name for p in files] + ["project.snapshot.json"]
        return self.runner.submit(ident, "export", operation, project.revision)

    def lyrics(self, ident: str, clip_id: str, text: str, offset_ms: int):
        project = self.store.get(ident)
        clip = self.find_clip(project, clip_id)
        clip.lyrics = parse_lrc(text, clip.start_ms + offset_ms, project.duration_ms)
        return self.store.save(project, project.revision)
