"""One configurable budget for a small server; desktop remains full-featured."""
from dataclasses import dataclass
import os
from pathlib import Path
import shutil


class ResourceLimitError(ValueError):
    def __init__(self, message, status=429):
        super().__init__(message)
        self.status = status


def integer(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except ValueError as exc:
        raise ValueError(f'{name} must be an integer') from exc
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value


@dataclass(frozen=True)
class ResourcePolicy:
    low: bool = False
    max_pending: int = 20
    per_user: int = 2
    upload_bytes: int = 16 * 1024**3
    reserve_bytes: int = 1024**3
    export_seconds: int = 1800
    task_seconds: int = 7200
    threads: int = 1

    @classmethod
    def load(cls, server=False):
        low = os.environ.get('SONGCUT_LOW_RESOURCE', '1' if server else '0') == '1'
        return cls(low, integer('SONGCUT_QUEUE_LIMIT', 20, 1, 1000),
                   integer('SONGCUT_USER_QUEUE_LIMIT', 2, 1, 100),
                   integer('SONGCUT_UPLOAD_LIMIT_MB', 16384, 1, 1048576) * 1024**2,
                   integer('SONGCUT_DISK_RESERVE_MB', 1024 if server else 128, 0, 1048576) * 1024**2,
                   integer('SONGCUT_EXPORT_MAX_SECONDS', 1800 if low else 86400, 1, 86400),
                   integer('SONGCUT_TASK_TIMEOUT_SECONDS', 7200, 30, 86400),
                   integer('SONGCUT_CPU_THREADS', 1 if low else 2, 1, 32))

    def check_disk(self, directory: Path, required=0):
        directory.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(directory).free < self.reserve_bytes + required:
            raise ResourceLimitError('磁盘剩余空间不足，请清理缓存或增加存储后重试', 507)

    def copy_upload(self, source, destination):
        count = 0
        self.check_disk(destination.parent)
        with destination.open('wb') as output:
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                if count > self.upload_bytes:
                    raise ResourceLimitError(f'录播超过上传限制（{self.upload_bytes // 1024**2} MB）', 413)
                self.check_disk(destination.parent, len(chunk))
                output.write(chunk)

    def validate_export(self, project, clip):
        if (clip.end_ms - clip.start_ms) > self.export_seconds * 1000:
            raise ResourceLimitError(f'单次导出最长 {self.export_seconds // 60} 分钟，请拆分片段', 422)
        settings = clip.export_settings
        if settings.mode == 'fast' and settings.video and (clip.subtitle.enabled or settings.resolution != 'source' or settings.fps is not None):
            raise ResourceLimitError('快速导出需关闭字幕，并保留原分辨率和帧率', 422)
        if self.low and settings.video:
            if settings.resolution == '2160p' or settings.video_codec != 'h264' or (settings.fps or project.source_fps or 30) > 30:
                raise ResourceLimitError('低配服务器仅支持最高1080p、30fps、H.264导出', 422)
            if settings.resolution == 'source' and (project.source_height or 0) > 1080:
                raise ResourceLimitError('原素材高于1080p，请将导出分辨率设为1080p或720p', 422)
