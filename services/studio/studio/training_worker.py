"""Separate ML interpreter: build original log-mel inputs, invoke original train.py."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio.runtime import worker_command


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def main(snapshot_path):
    snapshot_path = Path(snapshot_path).resolve()
    directory = snapshot_path.parent
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    upstream = Path(snapshot["upstream"])
    if not (upstream / "src/train.py").is_file():
        raise RuntimeError("找不到原版训练入口，请先完成 AutoSlicer 安装")
    sys.path.insert(0, str(upstream / "src"))
    import numpy as np
    import torch
    from features import extract_logmel_file
    # A modest thread count avoids oversubscription during local CPU training.
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    config, dataset = snapshot["settings"], snapshot["dataset"]
    manifests = {"train": [], "validation": []}
    cache = directory / "features"
    cache.mkdir(exist_ok=True)
    samples = dataset["samples"]
    for index, sample in enumerate(samples):
        atomic_json(directory / "progress.json", dict(stage="preparing", message=f"提取特征 {index + 1}/{len(samples)}：{sample['name']}", progress=.2 * index / len(samples)))
        source = Path(sample["path"])
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != sample["sha256"]:
            raise RuntimeError(f"素材已变化，请重新上传并复核：{sample['name']}")
        if not sample["reviewed"]:
            raise RuntimeError("训练快照含未复核素材")
        # CPU features are reproducible across CPU/MPS/CUDA training devices.
        mel = extract_logmel_file(source, device="cpu")
        if mel.ndim != 2 or mel.shape[0] != 80 or not np.isfinite(mel).all():
            raise RuntimeError(f"素材特征无效：{sample['name']}")
        n_frames = min(mel.shape[1] // 16, int(sample["duration_ms"] * 3.125 / 1000))
        if n_frames < 2:
            raise RuntimeError(f"素材太短，无法生成有效训练帧：{sample['name']}")
        # Cropping to a multiple of 16 keeps label and convolution frame grids exact.
        mel = mel[:, :n_frames * 16]
        centers = (np.arange(n_frames) + .5) * 1000 / 3.125
        labels = np.zeros(n_frames, dtype=np.float32)
        for region in sample["regions"]:
            if region["label"] == "target":
                labels[(centers >= region["start_ms"]) & (centers < region["end_ms"])] = 1
        mel_path, label_path = cache / f"{sample['id']}.mel.npy", cache / f"{sample['id']}.labels.npy"
        np.save(mel_path, mel)
        np.save(label_path, labels)
        manifests[sample["split"]].append(dict(mel=str(mel_path), label=str(label_path), vod=sample["id"]))
    for split, items in manifests.items():
        all_labels = np.concatenate([np.load(item["label"]) for item in items])
        if not (np.any(all_labels == 1) and np.any(all_labels == 0)):
            raise RuntimeError(f"{split} 特征对齐后缺少正样本或负样本，请扩大有效标注区间")
        atomic_json(directory / f"manifest_{split}.json", items)
    atomic_json(directory / "progress.json", dict(stage="training", message="正在学习目标主播的演唱特征", progress=.2))
    command = worker_command("train", ["--model", "crnn", "--classes", "1",
               "--run-name", "model", "--manifest", str(directory / "manifest_train.json"),
               "--val-manifest", str(directory / "manifest_validation.json"), "--input-type", "mel", "--out-fps", "3.125"], repository=upstream)
    for name in ("device", "steps", "batch_size", "num_workers", "window_s", "val_every", "lr", "seed", "warmup"):
        command.extend(["--" + name.replace("_", "-"), str(config[name])])
    # Available only to direct test fixtures, never accepted by the service API.
    if "_test_model_kwargs" in snapshot:
        command.extend(["--model-kwargs", json.dumps(snapshot["_test_model_kwargs"])])
    environment = os.environ.copy()
    environment.setdefault("OMP_NUM_THREADS", "4")
    environment.setdefault("MKL_NUM_THREADS", "4")
    result = subprocess.run(command, cwd=directory, env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"原版训练程序退出，代码 {result.returncode}")
    checkpoint = directory / "runs/model/best.pt"
    payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    state = payload.get("model", {})
    if not state or not all(torch.isfinite(tensor).all().item() for tensor in state.values()):
        raise RuntimeError("训练权重包含非有限数值，未登记模型")
    metrics = payload.get("metrics", {})
    if not all(not isinstance(value, (int, float)) or math.isfinite(value) for value in metrics.values()):
        raise RuntimeError("训练指标包含非有限数值，未登记模型")
    if payload.get("args", {}).get("classes") != 1:
        raise RuntimeError("训练模型分类语义不匹配")
    atomic_json(directory / "result.json", dict(checkpoint_path=str(checkpoint), metrics=metrics, verified_finite=True,
                                               best_step=payload.get("step"), scope="target-vs-rest"))


if __name__ == "__main__":
    main(sys.argv[1])
