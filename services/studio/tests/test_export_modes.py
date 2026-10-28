import json
from pathlib import Path
import subprocess
import pytest
from studio.adapters.media import FFmpegMedia
from studio.domain import Project, Clip, ExportSettings, SubtitleStyle, LyricCue


@pytest.fixture
def media_source(tmp_path):
    media = FFmpegMedia()
    source = tmp_path/'source.mp4'
    subprocess.run([media.ffmpeg, '-v','error','-y','-f','lavfi','-i','testsrc2=size=128x72:rate=10',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','5','-c:v','libx264',
                    '-g','20','-keyint_min','20','-sc_threshold','0','-c:a','aac',str(source)],check=True)
    project=Project(name='test',source_name=source.name,source_file=str(source),duration_ms=5000,source_fps=10)
    return media,project


def packet_hashes(media, path):
    result=subprocess.run([media.ffprobe,'-v','error','-select_streams','v','-show_packets','-show_data_hash','sha256',
                           '-show_entries','packet=data_hash','-of','json',str(path)],capture_output=True,text=True,check=True)
    return [p['data_hash'] for p in json.loads(result.stdout)['packets']]


def test_fast_reuses_encoded_packets_while_precise_keeps_requested_duration(media_source,tmp_path):
    media,project=media_source
    hashes=packet_hashes(media,project.source_file)
    for mode in ('fast','precise'):
        clip=Clip(start_ms=1000,end_ms=3000,export_settings=ExportSettings(mode=mode,audio=False))
        output=media.render(project,clip,tmp_path/mode)[0]
        duration,video=media.probe(output)
        assert video
        if mode=='fast':
            actual=packet_hashes(media,output)
            assert actual and all(h in hashes for h in actual)
            assert 1900 <= duration <= 4200  # Keyframe-aligned copy is deliberately approximate.
        else:
            assert abs(duration-2000)<120


def test_fast_rejects_transforms_instead_of_silently_ignoring_them(media_source,tmp_path):
    media,project=media_source
    for settings in (ExportSettings(mode='fast',resolution='720p'),ExportSettings(mode='fast',fps=30)):
        with pytest.raises(ValueError,match='快速导出'):
            media.render(project,Clip(start_ms=0,end_ms=1000,export_settings=settings),tmp_path/'invalid')
