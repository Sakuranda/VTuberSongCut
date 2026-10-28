"""CPU inference with bounded PCM windows, using AutoSlicer's original model/frontend.

Only the low-rate probability timeline grows with recording duration. Source audio
is decoded once through a back-pressured FFmpeg pipe, never loaded in full.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from threading import Timer
import time


def cpu_threads() -> int:
    value = int(os.environ.get('SONGCUT_CPU_THREADS', '1'))
    if not 1 <= value <= 32:
        raise ValueError('SONGCUT_CPU_THREADS must be between 1 and 32')
    return value


def window_settings(architecture: str, sample_rate: int, hop: int):
    # TCN's long dilated context must not silently shrink with the CPU migration.
    default = '600' if architecture == 'tcn' else '120'
    chunk_s = float(os.environ.get('SONGCUT_INFER_CHUNK_SECONDS', default))
    overlap_s = float(os.environ.get('SONGCUT_INFER_OVERLAP_SECONDS', '30'))
    if not math.isfinite(chunk_s) or not 1 <= chunk_s <= 600:
        raise ValueError('Inference chunk must be between 1 and 600 seconds')
    if not math.isfinite(overlap_s) or not 0 <= overlap_s < chunk_s:
        raise ValueError('Inference overlap must be nonnegative and smaller than the chunk')
    chunk = int(chunk_s * sample_rate // hop) * hop
    overlap = int(overlap_s * sample_rate // hop) * hop
    if chunk <= overlap:
        raise ValueError('Inference window has no forward progress')
    return chunk, overlap


def _read_up_to(stream, count):
    data = bytearray()
    while len(data) < count:
        block = stream.read(min(65536, count - len(data)))
        if not block:
            break
        data.extend(block)
    if len(data) % 4:
        raise ValueError('Decoder returned an incomplete float32 sample')
    return data


def pcm_windows(stream, chunk: int, overlap: int):
    """Yield (absolute sample offset, bounded PCM bytes), including one short tail."""
    if chunk <= 0 or not 0 <= overlap < chunk:
        raise ValueError('Invalid PCM window')
    buffer = _read_up_to(stream, chunk * 4)
    offset = 0
    while buffer:
        yield offset, buffer
        if len(buffer) < chunk * 4:
            return
        extra = _read_up_to(stream, (chunk - overlap) * 4)
        if not extra:
            return
        buffer = buffer[-overlap * 4:] + extra if overlap else extra
        offset += chunk - overlap


@contextmanager
def decoded_audio(source: Path, sample_rate: int, threads: int, timeout: float = 6 * 3600):
    ffmpeg = os.environ.get('AUTOSLICER_FFMPEG') or os.environ.get('SONGCUT_FFMPEG') or 'ffmpeg'
    command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-threads', str(threads),
               '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn',
               '-f', 'f32le', '-acodec', 'pcm_f32le', '-ac', '1', '-ar', str(sample_rate), 'pipe:1']
    # Keep FFmpeg in the worker's process group so the host can cancel the whole tree.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        expired = []
        def expire():
            expired.append(True)
            try:
                process.kill()
            except OSError:
                pass
        timer = Timer(timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            yield process.stdout
            returncode = process.wait()
            if expired:
                raise TimeoutError('Audio decoding/inference exceeded its time limit')
            if returncode:
                errors.seek(0, 2)
                errors.seek(max(0, errors.tell() - 2000))
                raise RuntimeError('Audio decoding failed: ' + errors.read().decode('utf-8', errors='replace'))
        finally:
            timer.cancel()
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            process.wait()


def predict_stream(stream, model, fcfg, frontend, chunk: int, overlap: int):
    """Match infer.predict_probs arithmetic for the same PCM, window and overlap."""
    import numpy as np
    import torch
    model = model.to('cpu').eval()
    frontend = frontend.to('cpu')
    probs = np.zeros(0, dtype=np.float32)
    weight = np.zeros(0, dtype=np.float32)
    total_samples = 0
    infer_seconds = 0.0
    minimum_samples = max(fcfg.n_fft, fcfg.hop_length * (fcfg.time_pool - 1))
    with torch.inference_mode():
        for start, raw in pcm_windows(stream, chunk, overlap):
            count = len(raw) // 4
            total_samples = start + count
            if count < minimum_samples:
                continue
            wav = torch.frombuffer(raw, dtype=torch.float32)
            if not torch.isfinite(wav).all():
                raise ValueError('Decoded audio contains non-finite samples')
            then = time.monotonic()
            logits = model(frontend(wav))[0].float().cpu().numpy()
            infer_seconds += time.monotonic() - then
            if logits.ndim not in (1, 2) or not np.isfinite(logits).all():
                raise ValueError('Model returned invalid probabilities')
            if logits.ndim == 2:
                if logits.shape[1] < 2:
                    raise ValueError('Unsupported multiclass model output')
                exp = np.exp(logits - logits.max(-1, keepdims=True))
                prediction = exp[:, 1] / exp.sum(-1)
            else:
                # Stable sigmoid; clipping is immaterial at float32 precision.
                prediction = 1 / (1 + np.exp(-np.clip(logits, -80, 80)))
            offset = int(start / fcfg.hop_length / fcfg.time_pool)
            end = offset + len(prediction)
            if end > len(probs):
                probs = np.pad(probs, (0, end - len(probs)))
                weight = np.pad(weight, (0, end - len(weight)))
            probs[offset:end] += prediction
            weight[offset:end] += 1
    if total_samples < minimum_samples:
        raise ValueError('Audio is too short for model inference')
    size = int(total_samples / fcfg.sample_rate * fcfg.output_frame_rate) + 1
    if size > len(probs):
        probs = np.pad(probs, (0, size - len(probs)))
        weight = np.pad(weight, (0, size - len(weight)))
    return probs[:size] / np.maximum(weight[:size], 1), total_samples, infer_seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--device', default='cpu')
    args = parser.parse_args()
    threads = cpu_threads()
    # Set before importing Torch, NumPy or upstream modules.
    for name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = str(threads)
    sys.path.insert(0, str(Path(args.repository) / 'src'))
    import numpy as np
    import torch
    from devices import resolve_device
    device = resolve_device(args.device)
    if device.type != 'cpu':
        script = Path(args.repository) / 'src/infer.py'
        sys.argv = [str(script), '--input', args.input, '--checkpoint', args.checkpoint,
                    '--out-dir', args.out_dir, '--device', str(device)]
        runpy.run_path(str(script), run_name='__main__')
        return
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    from features import FeatureConfig, LogMel
    from models import build_model
    from postprocess import PostProcessConfig, probs_to_segments
    checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=True)
    config = checkpoint['args']
    architecture = config['model']
    if architecture not in {'crnn', 'tcn'} or config.get('input_type', 'mel') != 'mel' or config.get('out_fps') not in (None, 3.125):
        raise ValueError('Unsupported AutoSlicer checkpoint configuration')
    model = build_model(architecture, **json.loads(config.get('model_kwargs', '{}')))
    model.load_state_dict(checkpoint['model'], strict=True)
    if any(not torch.isfinite(t).all() for t in model.state_dict().values() if t.is_floating_point()):
        raise ValueError('Checkpoint contains non-finite model weights')
    fcfg = FeatureConfig()
    chunk, overlap = window_settings(architecture, fcfg.sample_rate, fcfg.hop_length)
    source = Path(args.input)
    started = time.monotonic()
    with decoded_audio(source, fcfg.sample_rate, threads) as stream:
        probabilities, samples, inference_seconds = predict_stream(stream, model, fcfg, LogMel(fcfg), chunk, overlap)
    elapsed = time.monotonic() - started
    duration = samples / fcfg.sample_rate
    segments = probs_to_segments(probabilities, PostProcessConfig(frame_rate=fcfg.output_frame_rate), total_duration_s=duration)
    result = {'input': str(source), 'duration_s': duration, 'model': architecture, 'checkpoint': args.checkpoint,
              'device': 'cpu', 'streaming': True, 'cpu_threads': threads,
              'chunk_s': chunk / fcfg.sample_rate, 'overlap_s': overlap / fcfg.sample_rate,
              'decode_time_s': round(max(0, elapsed - inference_seconds), 3),
              'inference_time_s': round(inference_seconds, 3), 'rtf': round(elapsed / max(1, duration), 4), 'segments': segments}
    directory = Path(args.out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    probability_file = directory / f'{source.stem}.probs.npy'
    with probability_file.open('wb') as output:
        np.save(output, probabilities)
    (directory / f'{source.stem}.segments.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
