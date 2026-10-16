"""Real media coverage for preview copy, fallback, and audio-only compatibility."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from threading import Event

import pytest

from studio.adapters.media import FFmpegMedia, _PreviewEncodingError
from studio.jobs import JobRunner
from studio.store import Store


@pytest.fixture
def media():
    adapter = FFmpegMedia()
    if not shutil.which(adapter.ffprobe) or not Path(adapter.ffmpeg).is_file():
        pytest.skip("real FFmpeg and ffprobe are not installed")
    return adapter


def video_fixture(media, path, video="libx264", audio="aac", sample_rate=16000):
    video_options = ["-preset", "veryfast"] if video == "libx264" else ["-q:v", "4"]
    media.run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15", "-f", "lavfi", "-i",
               f"sine=frequency=440:sample_rate={sample_rate}", "-t", "2", "-c:v", video, *video_options,
               "-pix_fmt", "yuv420p", "-c:a", audio, str(path)])


def packet_hashes(media, path, stream):
    result = subprocess.run([media.ffprobe, "-v", "error", "-select_streams", stream, "-show_packets",
                             "-show_data_hash", "sha256", "-show_entries", "packet=data_hash", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True)
    return [packet["data_hash"] for packet in json.loads(result.stdout)["packets"]]


def test_h264_aac_flv_remux_preserves_encoded_packets_and_source(media, tmp_path):
    source, output = tmp_path / "original.flv", tmp_path / "preview.mp4"
    video_fixture(media, source)
    original = hashlib.sha256(source.read_bytes()).digest()
    progress, fallback = [], []
    media.preview(source, output, on_progress=progress.append, on_fallback=lambda: fallback.append(True))
    assert not fallback
    assert hashlib.sha256(source.read_bytes()).digest() == original
    assert packet_hashes(media, source, "v:0") == packet_hashes(media, output, "v:0")
    assert packet_hashes(media, source, "a:0") == packet_hashes(media, output, "a:0")
    assert progress and all(0 <= value < 1 for value in progress)
    assert output.read_bytes().find(b"moov") < output.read_bytes().find(b"mdat")
    assert not list(tmp_path.glob(".preview-*.mp4"))


def test_h264_with_mp3_only_converts_audio_to_aac(media, tmp_path):
    source, output = tmp_path / "original.mkv", tmp_path / "preview.mp4"
    video_fixture(media, source, audio="libmp3lame", sample_rate=44100)
    media.preview(source, output)
    assert packet_hashes(media, source, "v:0") == packet_hashes(media, output, "v:0")
    audio = next(stream for stream in media._probe_data(output)["streams"] if stream["codec_type"] == "audio")
    assert audio["codec_name"] == "aac" and audio["profile"] == "LC"
    assert audio["channels"] == 1 and int(audio["sample_rate"]) == 44100


def test_small_incompatible_video_is_encoded_without_upscaling(media, tmp_path):
    source, output = tmp_path / "original.mkv", tmp_path / "preview.mp4"
    video_fixture(media, source, video="mpeg4")
    media.preview(source, output)
    video = next(stream for stream in media._probe_data(output)["streams"] if stream["codec_type"] == "video")
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert (video["width"], video["height"]) == (320, 180)


def test_actual_remux_error_falls_back_to_real_encoding_atomically(media, tmp_path, monkeypatch):
    source, output = tmp_path / "original.flv", tmp_path / "preview.mp4"
    video_fixture(media, source)
    original_source = source.read_bytes()
    output.write_bytes(b"previous preview must remain visible until success")
    previous = output.read_bytes()
    original_run = media._run_preview
    fallback, errors = [], []
    def execute(arguments, duration, report):
        assert output.read_bytes() == previous
        if "copy" in arguments:
            # Trigger a real MP4 muxer rejection, not a simulated Python error.
            arguments = [*arguments[:-1], "-tag:v", "zzzz", arguments[-1]]
            try:
                original_run(arguments, duration, report)
            except _PreviewEncodingError as exc:
                errors.append(str(exc))
                raise
            pytest.fail("the MP4 muxer unexpectedly accepted an incompatible codec tag")
        assert fallback == [True]
        return original_run(arguments, duration, report)
    monkeypatch.setattr(media, "_run_preview", execute)
    media.preview(source, output, on_fallback=lambda: fallback.append(True))
    assert len(errors) == 1 and "zzzz" in errors[0]
    assert source.read_bytes() == original_source
    assert packet_hashes(media, source, "v:0") != packet_hashes(media, output, "v:0")
    assert {stream["codec_name"] for stream in media._probe_data(output)["streams"]} == {"h264", "aac"}
    assert not list(tmp_path.glob(".preview-*.mp4"))


def test_real_failed_attempts_preserve_previous_preview_and_original(media, tmp_path, monkeypatch):
    source, output = tmp_path / "original.flv", tmp_path / "preview.mp4"
    video_fixture(media, source)
    original_source = source.read_bytes()
    output.write_bytes(b"previous preview")
    original_run = media._run_preview
    def fail(arguments, duration, report):
        return original_run([*arguments[:-1], "-c:a", "definitely_missing_encoder", arguments[-1]], duration, report)
    monkeypatch.setattr(media, "_run_preview", fail)
    with pytest.raises(_PreviewEncodingError, match="definitely_missing_encoder"):
        media.preview(source, output)
    assert output.read_bytes() == b"previous preview"
    assert source.read_bytes() == original_source
    assert not list(tmp_path.glob(".preview-*.mp4"))


@pytest.mark.parametrize("suffix,codec", [("wav", "pcm_s16le"), ("flac", "flac"), ("mp3", "libmp3lame"), ("aac", "aac")])
def test_audio_only_sources_make_playable_aac_mp4_without_upsampling(media, tmp_path, suffix, codec):
    source, output = tmp_path / ("original." + suffix), tmp_path / "preview.mp4"
    media.run(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "1", "-c:a", codec, str(source)])
    media.preview(source, output)
    streams = media._probe_data(output)["streams"]
    assert len(streams) == 1 and streams[0]["codec_type"] == "audio"
    assert streams[0]["codec_name"] == "aac" and streams[0]["profile"] == "LC"
    assert streams[0]["channels"] == 1 and int(streams[0]["sample_rate"]) == 16000
    media.run(["-i", str(output), "-f", "null", "-"])


def test_audio_only_aac_is_remuxed_without_reencoding(media, tmp_path):
    source, output = tmp_path / "original.m4a", tmp_path / "preview.mp4"
    media.run(["-f", "lavfi", "-i", "sine=sample_rate=16000", "-t", "1", "-c:a", "aac", str(source)])
    media.preview(source, output)
    assert packet_hashes(media, source, "a:0") == packet_hashes(media, output, "a:0")
    assert len(media._probe_data(output)["streams"]) == 1


def test_audio_cover_art_is_not_treated_as_video(media, tmp_path):
    source, output = tmp_path / "cover.mp3", tmp_path / "preview.mp4"
    media.run(["-f", "lavfi", "-i", "sine=sample_rate=16000", "-f", "lavfi", "-i", "color=red:size=16x16",
               "-t", "1", "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-c:v", "mjpeg",
               "-pix_fmt", "yuvj420p", "-frames:v", "1", "-disposition:v", "attached_pic", str(source)])
    assert media.probe(source)[1] is False
    media.preview(source, output)
    assert [stream["codec_type"] for stream in media._probe_data(output)["streams"]] == ["audio"]


def test_preview_cannot_replace_its_original_source(media, tmp_path):
    source = tmp_path / "original.mp4"
    source.write_bytes(b"original")
    with pytest.raises(ValueError, match="不能覆盖原素材"):
        media.preview(source, source)
    assert source.read_bytes() == b"original"


def test_fallback_progress_resets_explicitly_while_each_attempt_stays_monotonic(tmp_path):
    store = Store(tmp_path / "store.sqlite3")
    runner = JobRunner(store)
    entered, release = Event(), Event()
    def operation(ident):
        runner.update_progress(ident, .99)
        runner.reset_progress(ident, "正在转换兼容预览")
        assert store.get_job(ident).progress == 0
        runner.update_progress(ident, .2)
        runner.update_progress(ident, .1)
        entered.set()
        assert release.wait(5)
        return []
    try:
        job = runner.submit("sample", "preview", operation)
        assert entered.wait(5)
        current = store.get_job(job.id)
        assert current.progress == .2 and current.message == "正在转换兼容预览"
        assert current.status == "running"
    finally:
        release.set()
        runner.close()
    assert store.get_job(job.id).progress == 1
