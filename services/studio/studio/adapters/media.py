import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event, Timer
import time
from fractions import Fraction
from typing import Callable, Literal
from ..domain import Project, Clip
from ..execution import run_process, watch_process
from .lyrics import render_ass


PREVIEW_PROFILES = {"low": "low320-v1", "high": "high1080-v1", "compatible": "compatible-v1"}


class _PreviewEncodingError(RuntimeError):
    """An FFmpeg failure that permits a stream-copy attempt to fall back."""


class FFmpegMedia:
    def __init__(self):
        import imageio_ffmpeg
        self.ffmpeg = os.environ.get("SONGCUT_FFMPEG") or imageio_ffmpeg.get_ffmpeg_exe()
        self.ffprobe = os.environ.get("SONGCUT_FFPROBE", "ffprobe")
        try:
            self.cpu_threads = int(os.environ.get("SONGCUT_CPU_THREADS", "1"))
        except ValueError as exc:
            raise ValueError("SONGCUT_CPU_THREADS 必须是 1–32 的整数") from exc
        if not 1 <= self.cpu_threads <= 32:
            raise ValueError("SONGCUT_CPU_THREADS 必须是 1–32 的整数")

    def _command(self, args: list[str]):
        threads = str(self.cpu_threads)
        # Input decoder, simple/complex filters and each encoder have distinct
        # pools. Bound all of them; x265 also has its own worker-pool setting.
        encoder_options = ["-threads:v", threads, "-threads:a", threads]
        if "libx265" in args:
            encoder_options += ["-x265-params", f"pools={threads}:frame-threads={threads}"]
        return [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-threads", threads, "-filter_threads", threads, "-filter_complex_threads", threads,
                *args[:-1], *encoder_options, args[-1]]

    def run(self, args: list[str], cwd: Path | None = None, timeout: int = 6*3600):
        result = run_process(self._command(args),
                                cwd=cwd, capture_output=True, text=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise RuntimeError("媒体处理失败：" + result.stderr[-1500:])

    def _probe_data(self, source: Path):
        try:
            result = run_process([self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
                                    capture_output=True, text=True, check=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.CalledProcessError as exc:
            raise ValueError("无法读取媒体文件，请检查素材是否损坏或格式是否受支持：" + (exc.stderr or "")[-500:]) from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError("检查媒体超时，请确认素材文件可读取后重试") from exc
        except OSError as exc:
            raise ValueError("无法启动媒体检查工具 ffprobe，请确认媒体工具可用：" + str(exc)) from exc
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ValueError("无法读取媒体信息，请检查素材是否完整") from exc

    def probe(self, source: Path) -> tuple[int, bool]:
        data = self._probe_data(source)
        try:
            duration_ms = round(float(data["format"]["duration"])*1000)
            streams = data["streams"]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("无法读取媒体时长，请检查素材是否完整") from exc
        if not any(s.get("codec_type") == "audio" for s in streams):
            raise ValueError("录播需要包含音轨")
        if duration_ms <= 0:
            raise ValueError("无法读取媒体时长")
        return duration_ms, any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in streams)

    @staticmethod
    def _fps(stream):
        for name in ("avg_frame_rate", "r_frame_rate"):
            try:
                rate = Fraction(stream.get(name, "0/0"))
                if rate > 0:
                    return rate
            except (ValueError, ZeroDivisionError, TypeError):
                pass
        return None

    @staticmethod
    def _streams(data):
        streams = data.get("streams", [])
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        video = next((s for s in streams if s.get("codec_type") == "video"
                      and not s.get("disposition", {}).get("attached_pic")), None)
        return video, audio

    @staticmethod
    def _dimensions(video):
        if not video:
            return None, None
        width, height = video.get("width"), video.get("height")
        rotation = next((item.get("rotation", 0) for item in video.get("side_data_list", [])
                         if "rotation" in item), video.get("tags", {}).get("rotate", 0))
        try:
            if abs(float(rotation)) % 180 == 90:
                width, height = height, width
        except (TypeError, ValueError):
            pass
        return width, height

    @staticmethod
    def _copy_audio(audio):
        return bool(audio and audio.get("codec_name") == "aac" and audio.get("profile") == "LC"
                    and audio.get("channels") in (1, 2))

    @staticmethod
    def _copy_video(video):
        return video is None or (video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
                                and video.get("profile") in {"Baseline", "Constrained Baseline", "Main", "High"})

    def metadata(self, source: Path):
        data = self._probe_data(source)
        video, audio = self._streams(data)
        width, height = self._dimensions(video)
        rate = self._fps(video) if video else None
        containers = set(data.get("format", {}).get("format_name", "").split(","))
        return {"width": width, "height": height, "fps": float(rate) if rate else None,
                "video_codec": video.get("codec_name") if video else None,
                "audio_codec": audio.get("codec_name") if audio else None,
                "browser_compatible": bool(containers & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})
                    and self._copy_audio(audio) and self._copy_video(video)}

    def source_fps(self, source: Path) -> float | None:
        try:
            return self.metadata(source)["fps"]
        except (OSError, ValueError, RuntimeError):
            return None

    def capabilities(self):
        available = bool(shutil.which(self.ffmpeg))
        subtitle = False
        if available:
            result = run_process([self.ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            subtitle = " ass " in result.stdout or " subtitles " in result.stdout
        return {"ffmpeg": available, "ffprobe": bool(shutil.which(self.ffprobe)), "subtitle_burn": subtitle}

    def preview(self, source: Path, output: Path, *, duration_ms: int | None = None,
                on_progress: Callable[[float], None] | None = None,
                on_fallback: Callable[[], None] | None = None,
                profile: Literal["low", "high", "compatible"] = "compatible"):
        if profile not in PREVIEW_PROFILES:
            raise ValueError("未知的预览规格")
        if source.resolve() == output.resolve():
            raise ValueError("兼容预览不能覆盖原素材")
        data = self._probe_data(source)
        video, audio = self._streams(data)
        if audio is None:
            raise ValueError("素材需要包含音轨")
        if duration_ms is None:
            try:
                duration_ms = round(float(data["format"]["duration"]) * 1000)
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError("无法读取媒体时长，请检查素材是否完整") from exc
        if duration_ms <= 0:
            raise ValueError("无法读取媒体时长")
        report = on_progress or (lambda value: None)
        audio_copy = self._copy_audio(audio)
        video_copy = self._copy_video(video)
        if profile == "low":
            # Low means a real bandwidth cap, even for an already-playable input.
            video_copy = False
            audio_copy = False
        elif profile == "high" and video is not None:
            width, height = self._dimensions(video)
            rate = self._fps(video)
            video_copy = bool(video_copy and width and height and width <= 1920 and height <= 1080
                              and rate and rate <= 30)
        # New quality profiles share the original source timeline, including a
        # positive start offset; switching quality must not move edit markers.
        mapped = (["-copyts"] if profile != "compatible" else []) + ["-i", str(source)]
        if video is not None:
            mapped += ["-map", f"0:{video['index']}"]
        mapped += ["-map", f"0:{audio['index']}", "-sn", "-dn"]
        # Retain mono and low sample rates; only downmix/resample sources
        # beyond the compatibility target instead of upsampling small audio.
        sample_rate = min(int(audio.get("sample_rate") or 48000), 48000)
        encoded_audio = ["-c:a", "aac", "-profile:a", "aac_low", "-ac", "1" if audio.get("channels") == 1 else "2",
                         "-ar", str(sample_rate)]
        if profile != "compatible":
            encoded_audio += ["-b:a", "64k" if profile == "low" else "128k"]
        output.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}-", suffix=".mp4", dir=output.parent)
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            if video_copy and (video is not None or audio_copy):
                copied = ["-c", "copy"] if audio_copy else ["-c:v", "copy", *encoded_audio]
                try:
                    self._run_preview([*mapped, *copied, "-movflags", "+faststart", str(temporary)], duration_ms, report)
                except _PreviewEncodingError:
                    # A container can still reject timestamps or extradata even
                    # when its declared codecs are suitable for a browser.
                    temporary.unlink(missing_ok=True)
                    if on_fallback:
                        on_fallback()
                    elif on_progress:
                        on_progress(0)
                else:
                    temporary.replace(output)
                    return
            encoded = list(mapped)
            if video is None:
                encoded += ["-vn"]
            else:
                if profile == "compatible":
                    filters = "scale=w='min(iw,1280)':h='min(ih,720)':force_original_aspect_ratio=decrease:force_divisible_by=2"
                    rate_control = ["-crf", "25"]
                else:
                    width, height, limit = (570, 320, 24) if profile == "low" else (1920, 1080, 30)
                    rate = min(self._fps(video) or Fraction(limit), Fraction(limit))
                    filters = (f"scale=w='min(iw,{width})':h='min(ih,{height})':"
                               f"force_original_aspect_ratio=decrease:force_divisible_by=2,fps={rate}")
                    rate_control = (["-b:v", "300k", "-maxrate", "400k", "-bufsize", "800k"] if profile == "low"
                                    else ["-b:v", "2500k", "-maxrate", "4000k", "-bufsize", "8000k"])
                encoded += ["-vf", filters, "-c:v", "libx264", "-preset", "veryfast", *rate_control,
                            "-pix_fmt", "yuv420p", "-profile:v", "main"]
            encoded += [*encoded_audio, "-movflags", "+faststart", str(temporary)]
            self._run_preview(encoded, duration_ms, report)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)

    def _run_preview(self, arguments, duration_ms, on_progress, timeout=6*3600):
        command = self._command(["-nostats", "-stats_period", "0.25", "-progress", "pipe:1", *arguments])
        # stderr must be drained independently from machine-readable stdout.
        # A temporary file also prevents large decoder error logs filling a pipe.
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
                                       text=True, encoding="utf-8", errors="replace",
                                       start_new_session=os.name != "nt",
                                       creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0)
            guard = watch_process(process)
            guard.__enter__()
            expired = Event()
            def expire():
                expired.set()
                if process.poll() is None:
                    process.kill()
            timer = Timer(timeout, expire)
            timer.daemon = True
            timer.start()
            last_report, last_progress, latest_progress = float("-inf"), -1., 0.
            try:
                for line in process.stdout:
                    key, separator, value = line.strip().partition("=")
                    if separator and key == "progress" and value == "end" and latest_progress > last_progress:
                        on_progress(latest_progress)
                        last_progress = latest_progress
                    if not separator or key != "out_time_us":
                        continue
                    try:
                        progress = min(.99, max(0., int(value) / (duration_ms * 1000)))
                    except ValueError:
                        continue
                    latest_progress = max(latest_progress, progress)
                    now = time.monotonic()
                    if progress > last_progress and (now - last_report >= .5 or progress == .99):
                        on_progress(progress)
                        last_report, last_progress = now, progress
                code = process.wait()
                if expired.is_set():
                    raise TimeoutError("生成兼容预览超时，请检查素材后重试")
                if code:
                    errors.seek(0, os.SEEK_END)
                    errors.seek(max(0, errors.tell() - 6000))
                    raise _PreviewEncodingError("生成兼容预览失败：" + errors.read().decode("utf-8", errors="replace")[-1500:])
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                guard.__exit__(None, None, None)

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
            if settings.mode == "fast":
                if clip.subtitle.enabled or settings.resolution != "source" or settings.fps is not None:
                    raise ValueError("快速导出需关闭字幕，并保留原分辨率和帧率")
                info = self.metadata(Path(source))
                if info["video_codec"] != "h264" or info["audio_codec"] != "aac":
                    raise ValueError("该素材不适合快速导出，请使用精确导出")
                self.run([*trim, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                          "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", *metadata, str(video)], cwd=output)
            else:
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
