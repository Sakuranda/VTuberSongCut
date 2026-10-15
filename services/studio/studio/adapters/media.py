import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from ..domain import Project, Clip
from .lyrics import render_ass


class FFmpegMedia:
    def __init__(self):
        import imageio_ffmpeg
        self.ffmpeg = os.environ.get("SONGCUT_FFMPEG") or imageio_ffmpeg.get_ffmpeg_exe()
        self.ffprobe = os.environ.get("SONGCUT_FFPROBE", "ffprobe")

    def run(self, args: list[str], cwd: Path | None = None, timeout: int = 6*3600):
        result = subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
                                cwd=cwd, capture_output=True, text=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise RuntimeError("媒体处理失败：" + result.stderr[-1500:])

    def probe(self, source: Path) -> tuple[int, bool]:
        result = subprocess.run([self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
                                capture_output=True, text=True, check=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        data = json.loads(result.stdout)
        if not any(s["codec_type"] == "audio" for s in data["streams"]):
            raise ValueError("录播需要包含音轨")
        duration_ms = round(float(data["format"]["duration"])*1000)
        if duration_ms <= 0:
            raise ValueError("无法读取媒体时长")
        return duration_ms, any(s["codec_type"] == "video" for s in data["streams"])

    def source_fps(self, source: Path) -> float | None:
        from fractions import Fraction
        try:
            result = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "json", str(source)],
                                    capture_output=True, text=True, check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            streams = json.loads(result.stdout).get("streams", [])
            rate = float(Fraction(streams[0]["avg_frame_rate"])) if streams else 0
            return rate if rate > 0 else None
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, ZeroDivisionError):
            return None

    def capabilities(self):
        available = bool(shutil.which(self.ffmpeg))
        subtitle = False
        if available:
            result = subprocess.run([self.ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            subtitle = " ass " in result.stdout or " subtitles " in result.stdout
        return {"ffmpeg": available, "ffprobe": bool(shutil.which(self.ffprobe)), "subtitle_burn": subtitle}

    def preview(self, source: Path, output: Path):
        self.run(["-i", str(source), "-map", "0:v:0", "-map", "0:a:0", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2",
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(output)])

    def render(self, project: Project, clip: Clip, output: Path) -> list[Path]:
        output.mkdir(parents=True, exist_ok=True)
        settings = clip.export_settings
        if not project.has_video and not settings.audio:
            raise ValueError("源文件没有视频画面，请勾选 MP3 导出")
        if settings.video and clip.subtitle.enabled and not clip.lyrics:
            raise ValueError("已开启歌词，但没有歌词内容")
        if settings.video and clip.subtitle.enabled and project.has_video and not self.capabilities()["subtitle_burn"]:
            raise RuntimeError("当前 FFmpeg 未包含 libass 字幕渲染，请安装支持 libass 的版本")
        source = str(Path(project.source_file).resolve())
        trim = ["-ss", f"{clip.start_ms/1000:.3f}", "-i", source, "-t", f"{(clip.end_ms-clip.start_ms)/1000:.3f}"]
        metadata = ["-metadata", f"title={clip.title}", "-metadata", f"artist={clip.performer or clip.original_artist}"]
        outputs = []
        if settings.video and project.has_video and clip.subtitle.enabled:
            ass = output / "lyrics.ass"
            font_family = os.environ.get("SONGCUT_FONT_FAMILY") or ("Hiragino Sans GB" if sys.platform == "darwin" else "Microsoft YaHei" if sys.platform == "win32" else "Noto Sans CJK SC")
            ass.write_text(render_ass(clip, font_family), encoding="utf-8")
            outputs.append(ass)
        if project.has_video and settings.video:
            video = output / "clip.mp4"
            filter_chain = []
            dimensions = {"720p": (1280, 720), "1080p": (1920, 1080), "2160p": (3840, 2160)}
            if settings.resolution in dimensions:
                width, height = dimensions[settings.resolution]
                filter_chain.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2")
            else:
                filter_chain.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
            if settings.fps:
                filter_chain.append(f"fps={settings.fps}")
            if clip.subtitle.enabled:
                # Supply a local CJK font explicitly: bundled FFmpeg may lack system font discovery.
                font_candidates = [Path(os.environ.get("SONGCUT_FONT_FILE", "__not_configured__")),
                    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
                    Path("C:/Windows/Fonts/msyh.ttc"),
                    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")]
                font = next((p for p in font_candidates if p.is_file()), None)
                if font:
                    fonts = output / "fonts"
                    fonts.mkdir(exist_ok=True)
                    target_font = fonts / ("subtitle" + font.suffix)
                    if not target_font.exists():
                        try:
                            target_font.symlink_to(font.resolve())
                        except OSError:
                            shutil.copyfile(font, target_font)
                filter_chain.append("ass=lyrics.ass:fontsdir=fonts" if font else "ass=lyrics.ass")
            codec = "libx264" if settings.video_codec == "h264" else "libx265"
            rate = ["-crf", str(settings.crf)] if settings.rate_control == "quality" else ["-b:v", f"{settings.video_bitrate_kbps}k"]
            tag = ["-tag:v", "hvc1"] if settings.video_codec == "hevc" else []
            self.run([*trim, "-map", "0:v:0", "-map", "0:a:0", "-vf", ",".join(filter_chain), "-c:v", codec, "-preset", "fast", *rate, *tag,
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{settings.audio_bitrate_kbps}k", "-movflags", "+faststart", *metadata, str(video)], cwd=output)
            outputs.append(video)
        if settings.audio:
            audio = output / "clip.mp3"
            self.run([*trim, "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", f"{settings.audio_bitrate_kbps}k", *metadata, str(audio)])
            outputs.append(audio)
        return outputs
