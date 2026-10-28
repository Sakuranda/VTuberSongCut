"""Real low/high media profiles preserve source edit time and bound output cost."""
from array import array
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from studio.adapters.media import FFmpegMedia, PREVIEW_PROFILES


@pytest.fixture
def media(monkeypatch):
    monkeypatch.setenv('SONGCUT_CPU_THREADS', '1')
    value = FFmpegMedia()
    if not shutil.which(value.ffprobe) or not Path(value.ffmpeg).is_file():
        pytest.skip('real FFmpeg / ffprobe unavailable')
    return value


def fixture_video(media, path, size='1280x720', rate='30', duration='4.375'):
    media.run(['-f', 'lavfi', '-i', f'testsrc2=size={size}:rate={rate}',
               '-f', 'lavfi', '-i', 'sine=frequency=587:sample_rate=48000:duration=1,adelay=1275,apad',
               '-t', duration, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-b:a', '192k', str(path)])


def streams(media, path):
    data = media._probe_data(path)
    return data, {s['codec_type']: s for s in data['streams']}


def packets(media, path):
    result = subprocess.run([media.ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_packets',
                             '-show_data_hash', 'sha256', '-show_entries', 'packet=data_hash', '-of', 'json', str(path)],
                            check=True, capture_output=True, text=True)
    return [p['data_hash'] for p in json.loads(result.stdout)['packets']]


def first_tone(media, path):
    result = subprocess.run([media.ffmpeg, '-v', 'error', '-threads', '1', '-i', str(path), '-vn',
                             '-ac', '1', '-ar', '8000', '-f', 's16le', 'pipe:1'], capture_output=True, check=True)
    signal = array('h', result.stdout)
    if sys.byteorder != 'little':
        signal.byteswap()
    return next(index / 8000 for index, value in enumerate(signal) if abs(value) > 500)


def test_low_always_encodes_compatible_video_and_preserves_edit_timing(media, tmp_path):
    source, target = tmp_path / 'source.mp4', tmp_path / 'low.mp4'
    fixture_video(media, source, duration='6.375')
    original = hashlib.sha256(source.read_bytes()).digest()
    progress = []
    media.preview(source, target, profile='low', on_progress=progress.append)
    info, selected = streams(media, target)
    video, audio = selected['video'], selected['audio']
    assert video['codec_name'] == 'h264' and video['pix_fmt'] == 'yuv420p'
    assert video['height'] == 320 and video['width'] % 2 == 0
    assert Fraction(video['avg_frame_rate']) <= 24
    assert audio['codec_name'] == 'aac' and audio['profile'] == 'LC'
    assert int(info['format']['bit_rate']) < 550_000  # container and AAC overhead included
    assert abs(float(info['format']['duration']) - media.probe(source)[0] / 1000) < .085
    assert abs(first_tone(media, source) - first_tone(media, target)) < .04
    assert hashlib.sha256(source.read_bytes()).digest() == original
    assert packets(media, source) != packets(media, target)
    assert progress and all(0 <= p < 1 for p in progress)
    assert target.read_bytes().find(b'moov') < target.read_bytes().find(b'mdat')
    assert not list(tmp_path.glob('.low-*.mp4'))


def test_low_does_not_upscale_small_video_or_raise_frame_rate(media, tmp_path):
    source, target = tmp_path / 'small.mp4', tmp_path / 'low.mp4'
    fixture_video(media, source, size='320x180', rate='15', duration='1.5')
    media.preview(source, target, profile='low')
    _, selected = streams(media, target)
    assert (selected['video']['width'], selected['video']['height']) == (320, 180)
    assert Fraction(selected['video']['avg_frame_rate']) == 15


def test_high_reuses_compatible_source_packets_without_labeling_it_1080(media, tmp_path):
    source, target = tmp_path / 'source.flv', tmp_path / 'high.mp4'
    fixture_video(media, source, size='640x360', rate='25', duration='1.5')
    metadata = media.metadata(source)
    assert metadata['height'] == 360 and metadata['fps'] == 25
    assert metadata['browser_compatible'] is False  # FLV requires remux first
    media.preview(source, target, profile='high')
    assert packets(media, source) == packets(media, target)
    assert media.metadata(target)['height'] == 360
    assert media.metadata(target)['browser_compatible'] is True


def test_high_caps_resolution_and_framerate_when_encoding_is_required(media, tmp_path):
    source, target = tmp_path / 'large.mp4', tmp_path / 'high.mp4'
    fixture_video(media, source, size='1920x1200', rate='60', duration='1.4')
    media.preview(source, target, profile='high')
    _, selected = streams(media, target)
    video = selected['video']
    assert video['width'] <= 1920 and video['height'] == 1080
    assert Fraction(video['avg_frame_rate']) <= 30
    assert video['codec_name'] == 'h264'
    assert packets(media, source) != packets(media, target)


def test_profiles_preserve_a_positive_source_timestamp_offset(media, tmp_path):
    original, source = tmp_path / 'original.mp4', tmp_path / 'offset.mp4'
    fixture_video(media, original, size='320x180', rate='24', duration='2.4')
    media.run(['-i', str(original), '-c', 'copy', '-output_ts_offset', '2.5', str(source)])
    before, _ = streams(media, source)
    for profile in ('low', 'high'):
        target = tmp_path / f'{profile}.mp4'
        media.preview(source, target, profile=profile)
        after, _ = streams(media, target)
        assert abs(float(after['format']['start_time']) - float(before['format']['start_time'])) < .085
        assert abs(float(after['format']['duration']) - float(before['format']['duration'])) < .085


def test_audio_only_low_has_no_dummy_video_and_remains_low_bitrate(media, tmp_path):
    source, target = tmp_path / 'voice.flac', tmp_path / 'low.mp4'
    media.run(['-f', 'lavfi', '-i', 'sine=sample_rate=16000', '-t', '2', '-c:a', 'flac', str(source)])
    media.preview(source, target, profile='low')
    _, selected = streams(media, target)
    assert set(selected) == {'audio'}
    assert selected['audio']['codec_name'] == 'aac'
    assert int(selected['audio']['sample_rate']) == 16000
    assert selected['audio']['channels'] == 1


def test_unknown_profile_rejected_before_creating_output(media, tmp_path):
    with pytest.raises(ValueError, match='未知'):
        media.preview(tmp_path / 'source', tmp_path / 'output', profile='typo')
    assert not (tmp_path / 'output').exists()
    assert set(PREVIEW_PROFILES) == {'low', 'high', 'compatible'}


@pytest.mark.parametrize('value', ['0', '33', '-1', 'auto'])
def test_unbounded_or_invalid_thread_configuration_fails_fast(monkeypatch, value):
    monkeypatch.setenv('SONGCUT_CPU_THREADS', value)
    with pytest.raises(ValueError, match='SONGCUT_CPU_THREADS'):
        FFmpegMedia()


def test_thread_limits_cover_decoding_filtering_and_h265_private_pools(media):
    command = media._command(['-i', 'source.mp4', '-c:v', 'libx265', 'output.mp4'])
    for option in ('-threads', '-filter_threads', '-filter_complex_threads', '-threads:v', '-threads:a'):
        assert command[command.index(option) + 1] == '1'
    assert command[command.index('-x265-params') + 1] == 'pools=1:frame-threads=1'
