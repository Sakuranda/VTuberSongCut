import io
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from studio.inference_worker import pcm_windows, window_settings, cpu_threads, decoded_audio, predict_stream
from studio.runtime import worker_command


class Fragmented(io.BytesIO):
    def read(self, size=-1):
        assert 0 < size <= 65536
        return super().read(min(7, size))


@pytest.mark.parametrize('count', [0, 1, 8, 11, 12, 13, 20, 21, 29])
def test_pcm_windows_match_whole_audio_slices_without_duplicates(count):
    values = bytes(range(count * 4))
    actual = list(pcm_windows(Fragmented(values), 12, 4))
    expected = []
    for start in range(0, count, 8):
        expected.append((start, values[start * 4:(start + 12) * 4]))
        if start + 12 >= count:
            break
    assert actual == expected
    assert all(len(data) <= 48 for _, data in actual)


def test_truncated_sample_and_invalid_windows_are_rejected():
    with pytest.raises(ValueError, match='incomplete'):
        list(pcm_windows(io.BytesIO(b'12345'), 10, 0))
    for chunk, overlap in [(0, 0), (5, 5), (5, -1)]:
        with pytest.raises(ValueError):
            list(pcm_windows(io.BytesIO(), chunk, overlap))


def test_default_context_and_env_limits(monkeypatch):
    monkeypatch.delenv('SONGCUT_INFER_CHUNK_SECONDS', raising=False)
    monkeypatch.delenv('SONGCUT_INFER_OVERLAP_SECONDS', raising=False)
    assert window_settings('crnn', 16000, 320) == (120 * 16000, 30 * 16000)
    assert window_settings('tcn', 16000, 320) == (600 * 16000, 30 * 16000)
    monkeypatch.setenv('SONGCUT_INFER_CHUNK_SECONDS', 'nan')
    with pytest.raises(ValueError):
        window_settings('crnn', 16000, 320)
    monkeypatch.setenv('SONGCUT_INFER_CHUNK_SECONDS', '20')
    with pytest.raises(ValueError):
        window_settings('crnn', 16000, 320)
    monkeypatch.setenv('SONGCUT_CPU_THREADS', '0')
    with pytest.raises(ValueError):
        cpu_threads()


def test_stream_worker_packaged_and_external_commands(monkeypatch, tmp_path):
    command = worker_command('infer-stream', ['--input', 'test.mp4'], repository=tmp_path, python='model-python')
    assert command[0] == 'model-python'
    assert command[2].endswith('inference_worker.py')
    assert command[3:] == ['--repository', str(tmp_path), '--input', 'test.mp4']
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert worker_command('infer-stream', ['--input', 'test.mp4'], repository=tmp_path) == [
        sys.executable, '--worker', 'infer-stream', '--repository', str(tmp_path), '--input', 'test.mp4']


def optional_model_environment():
    # Core CI intentionally does not install large ML dependencies or upstream.
    torch = pytest.importorskip('torch')
    pytest.importorskip('torchaudio')
    upstream = Path(__file__).resolve().parents[3] / 'upstream/AutoSlicer-v2/src'
    if not (upstream / 'infer.py').is_file():
        pytest.skip('fixed upstream model source is not installed')
    return torch, upstream


@pytest.mark.parametrize('multiclass', [False, True])
def test_streaming_matches_upstream_across_overlap_and_fractional_tail(monkeypatch, multiclass):
    torch, upstream = optional_model_environment()
    monkeypatch.syspath_prepend(str(upstream))
    import numpy as np
    from features import FeatureConfig, LogMel
    from infer import predict_probs
    from models import build_model
    from postprocess import PostProcessConfig, probs_to_segments
    torch.set_num_threads(1)
    torch.manual_seed(17)
    config = FeatureConfig()
    # Small real CRNN, real log-mel frontend. Multiple windows and a fractional tail.
    model = build_model('crnn', channels=[2, 2, 2, 2], rnn_hidden=4, rnn_layers=1,
                        dropout=0, n_out=4 if multiclass else 1).eval()
    wav = torch.randn(16000 * 15 + 731) * .03
    expected = predict_probs(wav, model, torch.device('cpu'), config, chunk_s=6, overlap_s=2)
    actual, samples, _ = predict_stream(io.BytesIO(wav.numpy().astype('<f4').tobytes()), model, config,
                                      LogMel(config), chunk=96000, overlap=32000)
    assert samples == len(wav)
    np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)
    settings = PostProcessConfig(min_song_s=1, min_blip_s=.1, pad_before_s=0, pad_after_s=0)
    assert probs_to_segments(actual, settings, total_duration_s=samples / 16000) == probs_to_segments(expected, settings, total_duration_s=samples / 16000)


def test_nonfinite_pcm_and_prediction_do_not_produce_a_result():
    torch = pytest.importorskip('torch')
    import numpy as np
    config = SimpleNamespace(n_fft=2, hop_length=1, time_pool=1, sample_rate=4, output_frame_rate=4)
    class Frontend(torch.nn.Module):
        def forward(self, value):
            return value[None]
    class Invalid(torch.nn.Module):
        def forward(self, value):
            return value * float('nan')
    with pytest.raises(ValueError, match='non-finite samples'):
        predict_stream(io.BytesIO(np.array([0, np.nan, 1], dtype='<f4').tobytes()), Frontend(), config, Frontend(), 4, 1)
    with pytest.raises(ValueError, match='invalid probabilities'):
        predict_stream(io.BytesIO(np.ones(3, dtype='<f4').tobytes()), Invalid(), config, Frontend(), 4, 1)


def test_decoder_failure_and_exception_reap_owned_process(monkeypatch, tmp_path):
    import studio.inference_worker as module
    actual_popen = subprocess.Popen
    children = []
    def fake(command, **kwargs):
        assert command[command.index('-threads') + 1] == '1'
        script = "import sys; sys.stderr.write('decoder error'); sys.exit(3)"
        p = actual_popen([sys.executable, '-c', script], **kwargs)
        children.append(p)
        return p
    monkeypatch.setattr(module.subprocess, 'Popen', fake)
    with pytest.raises(RuntimeError, match='decoder error'):
        with decoded_audio(tmp_path / 'source', 16000, 1) as stream:
            stream.read()
    assert children[0].poll() is not None

    def long_child(command, **kwargs):
        p = actual_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
        children.append(p)
        return p
    monkeypatch.setattr(module.subprocess, 'Popen', long_child)
    with pytest.raises(ValueError, match='model failed'):
        with decoded_audio(tmp_path / 'source', 16000, 1):
            raise ValueError('model failed')
    assert children[-1].poll() is not None
    with pytest.raises(TimeoutError):
        with decoded_audio(tmp_path / 'source', 16000, 1, timeout=.05) as stream:
            stream.read()
    assert children[-1].poll() is not None


def test_real_decoder_streaming_reads_fractional_audio(monkeypatch, tmp_path):
    import wave
    import struct
    imageio = pytest.importorskip('imageio_ffmpeg')
    monkeypatch.setenv('AUTOSLICER_FFMPEG', imageio.get_ffmpeg_exe())
    source = tmp_path / 'fractional.wav'
    frames = 32000 + 731
    with wave.open(str(source), 'wb') as file:
        file.setparams((1, 2, 16000, frames, 'NONE', ''))
        file.writeframes(struct.pack('<h', 1000) * frames)
    with decoded_audio(source, 16000, 1) as stream:
        windows = list(pcm_windows(stream, 16000, 4000))
    assert max(len(raw) for _, raw in windows) == 64000
    assert windows[-1][0] + len(windows[-1][1]) // 4 == frames


def test_standalone_cpu_worker_full_checkpoint_to_segments(monkeypatch, tmp_path):
    torch, upstream = optional_model_environment()
    monkeypatch.syspath_prepend(str(upstream))
    import json
    import wave
    import struct
    imageio = pytest.importorskip('imageio_ffmpeg')
    from models import build_model
    kwargs = {'channels': [2, 2, 2, 2], 'rnn_hidden': 4, 'rnn_layers': 1, 'dropout': 0}
    model = build_model('crnn', **kwargs)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.head[-1].bias.fill_(6)
    checkpoint = tmp_path / 'test.pt'
    torch.save({'model': model.state_dict(), 'args': {'model': 'crnn', 'model_kwargs': json.dumps(kwargs),
                                                    'input_type': 'mel', 'out_fps': 3.125}}, checkpoint)
    source = tmp_path / 'recording.wav'
    frames = 16000 * 65 + 731
    with wave.open(str(source), 'wb') as file:
        file.setparams((1, 2, 16000, frames, 'NONE', ''))
        file.writeframes(struct.pack('<h', 1000) * frames)
    output = tmp_path / 'result'
    command = worker_command('infer-stream', ['--input', str(source), '--checkpoint', str(checkpoint),
                                            '--out-dir', str(output), '--device', 'cpu'], repository=upstream.parent)
    environment = {**os.environ, 'AUTOSLICER_FFMPEG': imageio.get_ffmpeg_exe(), 'SONGCUT_CPU_THREADS': '1',
                   'SONGCUT_INFER_CHUNK_SECONDS': '6', 'SONGCUT_INFER_OVERLAP_SECONDS': '2'}
    result = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    metadata = json.loads((output / 'recording.segments.json').read_text())
    assert metadata['device'] == 'cpu' and metadata['streaming'] is True
    assert metadata['cpu_threads'] == 1 and metadata['chunk_s'] == 6
    assert metadata['duration_s'] == frames / 16000
    assert len(metadata['segments']) == 1
    assert metadata['segments'][0]['cut_start'] == 0
    assert abs(metadata['segments'][0]['cut_end'] - frames / 16000) < .01
    assert (output / 'recording.probs.npy').is_file()
