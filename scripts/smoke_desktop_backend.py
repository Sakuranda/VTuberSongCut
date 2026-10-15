#!/usr/bin/env python3
"""Exercise the frozen runtime outside the checkout, without Python/FFmpeg on PATH."""
import argparse
from array import array
import hashlib
import math
import wave
import json
import os
from pathlib import Path
import subprocess
import socket
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    with tempfile.TemporaryDirectory(prefix="songcut-standalone-") as folder:
        folder = Path(folder)
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        env = {**os.environ, "SONGCUT_PORT": str(port), "SONGCUT_SERVER": "0", "SONGCUT_DATA": str(folder / "data"), "SONGCUT_SHUTDOWN_FILE": str(folder / "stop")}
        # Retain Windows system utilities needed by process supervision only.
        env["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32") if os.name == "nt" else "/usr/bin:/bin"
        env.pop("PYTHONPATH", None)
        env.pop("SONGCUT_MODEL_PYTHON", None)
        info = json.loads(subprocess.check_output([str(executable), "--runtime-info"], cwd=folder, env=env, text=True, encoding="utf-8", errors="replace"))
        repository = info["upstream"]
        probe = subprocess.run([str(executable), "--worker", "probe", "--repository", repository], cwd=folder,
                               env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=True)
        assert json.loads(probe.stdout.strip().splitlines()[-1])["automatic_device"]
        for worker in ("infer", "train"):
            subprocess.run([str(executable), "--worker", worker, "--repository", repository, "--help"],
                           cwd=folder, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=True)
        resources = Path(info["resources"])
        suffix = ".exe" if os.name == "nt" else ""
        for tool in ("ffmpeg", "ffprobe"):
            subprocess.run([str(resources / "bin" / (tool + suffix)), "-version"], cwd=folder,
                           env=env, capture_output=True, check=True, timeout=30)
        # Exercise the frozen prepare -> original train -> probe -> infer chain,
        # including real log-mel extraction, without shipping any test model.
        samples = []
        for index, split in enumerate(("train", "validation")):
            audio_path = folder / f"{split}.wav"
            samples_data = array("h", (int(.2 * math.sin(2 * math.pi * (220 + index * 80) * n / 16000) * 32767)
                                        for n in range(16000 * 4)))
            with wave.open(str(audio_path), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(samples_data.tobytes())
            samples.append(dict(id=str(index), path=str(audio_path), name=audio_path.name, duration_ms=4000,
                                sha256=hashlib.sha256(audio_path.read_bytes()).hexdigest(), split=split, reviewed=True,
                                regions=[dict(start_ms=0, end_ms=1600, label="target"),
                                         dict(start_ms=1600, end_ms=3000, label="other")]))
        snapshot = dict(upstream=repository, dataset=dict(samples=samples), settings=dict(
            device="cpu", steps=2, batch_size=1, num_workers=0, window_s=2.56,
            val_every=1, lr=.0003, seed=42, warmup=0),
            _test_model_kwargs=dict(channels=[2, 2, 2, 2], rnn_hidden=4, rnn_layers=1, dropout=0))
        snapshot_path = folder / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        subprocess.run([str(executable), "--worker", "prepare", str(snapshot_path)], cwd=folder, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=True)
        result = json.loads((folder / "result.json").read_text())
        assert result["verified_finite"]
        subprocess.run([str(executable), "--worker", "probe", "--repository", repository,
                        "--checkpoint", result["checkpoint_path"]], cwd=folder, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=True)
        subprocess.run([str(executable), "--worker", "infer", "--repository", repository,
                        "--input", samples[1]["path"], "--checkpoint", result["checkpoint_path"],
                        "--out-dir", str(folder / "inference"), "--device", "cpu"], cwd=folder, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=True)
        assert (folder / "inference/validation.segments.json").is_file()
        with (folder / "server.log").open("w") as log:
            process = subprocess.Popen([str(executable)], cwd=folder, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError((folder / "server.log").read_text())
                    try:
                        with urllib.request.urlopen(base_url + "/api/health", timeout=2) as response:
                            assert json.load(response)["application"] == "VTuberSongCut"
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.25)
                with urllib.request.urlopen(base_url + "/", timeout=10) as response:
                    assert b"<html" in response.read().lower()
                with urllib.request.urlopen(base_url + "/help/faq", timeout=10) as response:
                    assert response.status == 200
            finally:
                (folder / "stop").touch()
                try:
                    code = process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise
                if code:
                    raise RuntimeError((folder / "server.log").read_text())
    print("Standalone training/inference, FFmpeg/ffprobe, web/help and graceful shutdown passed")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stdout or "")
        print(error.stderr or "")
        raise
