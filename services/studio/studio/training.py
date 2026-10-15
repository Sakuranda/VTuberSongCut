"""Persisted, isolated training orchestration. The web process never imports torch."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from .store import ConflictError
from .runtime import worker_command

OUT_FPS = 3.125
SCOPE = "target-vs-rest"
DEFAULT_SETTINGS = {"device": "auto", "steps": 2000, "batch_size": 2, "num_workers": 0,
                    "window_s": 32.0, "val_every": 100, "lr": 0.0003, "seed": 42, "warmup": 100}


def now():
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_regions(regions, duration_ms):
    if not isinstance(regions, list):
        raise ValueError("标注必须是区间列表")
    result = []
    for region in regions:
        if not isinstance(region, dict) or set(region) - {"start_ms", "end_ms", "label"}:
            raise ValueError("标注区间格式错误")
        start, end, label = region.get("start_ms"), region.get("end_ms"), region.get("label")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= duration_ms:
            raise ValueError("标注起止时间必须位于素材范围内，且结束晚于开始")
        if label not in ("target", "other"):
            raise ValueError("标注类型必须是目标演唱或其他人演唱")
        result.append({"start_ms": start, "end_ms": end, "label": label})
    result.sort(key=lambda region: (region["start_ms"], region["end_ms"]))
    if any(left["end_ms"] > right["start_ms"] for left, right in zip(result, result[1:])):
        raise ValueError("标注区间不能重叠，请合并或调整时间")
    return result


def frame_labels(sample, n_frames):
    """Frame centres align to the upstream 0.32-second output grid.

    A reviewed file's unmarked frames and explicit other singers are negatives.
    Unreviewed files must never be passed to this function for training.
    """
    if not sample.get("reviewed"):
        raise ValueError("素材尚未确认完成标注")
    labels = [0.0] * n_frames
    for region in validate_regions(sample["regions"], sample["duration_ms"]):
        if region["label"] == "target":
            start = max(0, math.ceil(region["start_ms"] * OUT_FPS / 1000 - .5))
            end = min(n_frames, math.ceil(region["end_ms"] * OUT_FPS / 1000 - .5))
            labels[start:end] = [1.0] * max(0, end - start)
    return labels


def validate_training_dataset(dataset):
    samples = dataset["samples"]
    if not samples or any(not sample["reviewed"] for sample in samples):
        raise ValueError("请完成并确认所有素材的标注；确认后未标注部分将作为非目标演唱")
    hashes = {}
    for sample in samples:
        previous = hashes.setdefault(sample["sha256"], sample["split"])
        if previous != sample["split"]:
            raise ValueError("训练集和验证集包含同一素材，存在数据泄漏，请移除重复文件")
    for split, title in (("train", "训练集"), ("validation", "验证集")):
        selected = [sample for sample in samples if sample["split"] == split]
        if not selected:
            raise ValueError(f"{title}至少需要一个独立素材")
        labels = [label for sample in selected for label in frame_labels(sample, int(sample["duration_ms"] * OUT_FPS / 1000))]
        if 1.0 not in labels or 0.0 not in labels:
            raise ValueError(f"{title}必须同时包含目标演唱和非目标演唱（标注至少覆盖一个 0.32 秒帧）")


def normalize_settings(settings):
    if not isinstance(settings, dict) or set(settings) - set(DEFAULT_SETTINGS):
        raise ValueError("包含不支持的训练设置")
    values = {**DEFAULT_SETTINGS, **settings}
    if values["device"] not in ("auto", "cpu", "mps", "cuda"):
        raise ValueError("训练设备必须为 auto、cpu、mps 或 cuda")
    for name, lower, upper in (("steps", 1, 50000), ("batch_size", 1, 32), ("num_workers", 0, 8),
                               ("val_every", 1, 10000), ("seed", 0, 2**31 - 1), ("warmup", 0, 10000)):
        if type(values[name]) is not int or not lower <= values[name] <= upper:
            raise ValueError(f"训练设置 {name} 必须是 {lower} 到 {upper} 的整数")
    for name, lower, upper in (("window_s", 1.28, 192), ("lr", 1e-7, .1)):
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f"训练设置 {name} 必须在 {lower} 到 {upper} 之间")
    return values


class TrainingService:
    def __init__(self, settings, store, register_model=None):
        self.settings, self.store, self.register_model = settings, store, register_model
        self.root = (settings.data / "training").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.processes = {}
        self.cancelled = set()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="songcut-training")
        self.closed = False
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS training_datasets (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS training_jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        for job in self.jobs():
            if job["status"] in ("queued", "running"):
                # A previous worker may still be alive after a server crash. Its PID and
                # private process group are recorded so restart cannot orphan training.
                if self._stop_recorded_process(job):
                    job.update(status="interrupted", stage="finished", message="服务已重启，训练已中断；请重新发起", updated_at=now())
                else:
                    job.update(status="running", message="上次训练进程尚未停止，请再次取消后再开始其他计算任务")
                self._save_job(job)

    def _save_job(self, job):
        job["updated_at"] = now()
        with self.store.connect() as db:
            db.execute("INSERT INTO training_jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (job["id"], json.dumps(job, ensure_ascii=False, allow_nan=False)))

    def datasets(self):
        with self.store.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM training_datasets ORDER BY rowid DESC")]

    def create_dataset(self, name, target_name):
        if not isinstance(name, str) or not name.strip() or not isinstance(target_name, str) or not target_name.strip():
            raise ValueError("请填写数据集名称和目标演唱者")
        if len(name) > 200 or len(target_name) > 200:
            raise ValueError("名称不能超过 200 字符")
        dataset = dict(id=uuid4().hex, name=name.strip(), target_name=target_name.strip(), revision=0,
                       samples=[], scope=SCOPE, created_at=now(), updated_at=now())
        with self.store.connect() as db:
            db.execute("INSERT INTO training_datasets VALUES (?,?,?)", (dataset["id"], 0, json.dumps(dataset, ensure_ascii=False)))
        return dataset

    def get_dataset(self, ident):
        with self.store.connect() as db:
            row = db.execute("SELECT data FROM training_datasets WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return json.loads(row[0])

    def _save_dataset(self, dataset, expected_revision):
        if expected_revision is None:
            expected_revision = dataset["revision"]
        dataset["revision"], dataset["updated_at"] = expected_revision + 1, now()
        with self.store.connect() as db:
            changed = db.execute("UPDATE training_datasets SET revision=?,data=? WHERE id=? AND revision=?",
                                 (dataset["revision"], json.dumps(dataset, ensure_ascii=False), dataset["id"], expected_revision)).rowcount
        if changed != 1:
            raise ConflictError("数据集已更新，请重新载入后再保存")
        return dataset

    def add_sample(self, ident, path, name, duration_ms, sha256=None, split="train", expected_revision=None):
        dataset = self.get_dataset(ident)
        path = Path(path).expanduser().resolve()
        if not path.is_file() or type(duration_ms) is not int or duration_ms < 640:
            raise ValueError("训练素材必须存在且长度至少为 0.64 秒")
        if split not in ("train", "validation"):
            raise ValueError("素材分组必须为训练集或验证集")
        actual_hash = file_sha256(path)
        if sha256 and actual_hash != sha256:
            raise ValueError("素材校验值不匹配，请重新上传")
        if any(sample["sha256"] == actual_hash for sample in dataset["samples"]):
            raise ValueError("该素材已在此数据集中，重复文件不能用于训练或验证")
        dataset["samples"].append(dict(id=uuid4().hex, name=str(name)[:240], path=str(path), duration_ms=duration_ms,
                                       sha256=actual_hash, split=split, reviewed=False, regions=[]))
        return self._save_dataset(dataset, expected_revision)

    def update_sample(self, ident, sample_id, changes, expected_revision=None):
        dataset = self.get_dataset(ident)
        sample = next((sample for sample in dataset["samples"] if sample["id"] == sample_id), None)
        if sample is None:
            raise KeyError(sample_id)
        if not isinstance(changes, dict) or set(changes) - {"regions", "split", "reviewed"}:
            raise ValueError("只能修改标注、分组和确认状态")
        if "split" in changes:
            if changes["split"] not in ("train", "validation"):
                raise ValueError("素材分组必须为训练集或验证集")
            sample["split"] = changes["split"]
        if "regions" in changes:
            sample["regions"] = validate_regions(changes["regions"], sample["duration_ms"])
            sample["reviewed"] = False
        if "reviewed" in changes:
            if type(changes["reviewed"]) is not bool:
                raise ValueError("标注确认状态必须是布尔值")
            sample["reviewed"] = changes["reviewed"]
        return self._save_dataset(dataset, expected_revision)

    def remove_sample(self, ident, sample_id, expected_revision=None):
        dataset = self.get_dataset(ident)
        if not any(sample["id"] == sample_id for sample in dataset["samples"]):
            raise KeyError(sample_id)
        dataset["samples"] = [sample for sample in dataset["samples"] if sample["id"] != sample_id]
        # Snapshots can refer to removed samples; retain source files.
        return self._save_dataset(dataset, expected_revision)

    def jobs(self, dataset_id=None):
        with self.store.connect() as db:
            jobs = [json.loads(row[0]) for row in db.execute("SELECT data FROM training_jobs ORDER BY rowid DESC")]
        return [job for job in jobs if dataset_id is None or job["dataset_id"] == dataset_id]

    def get_job(self, ident):
        with self.store.connect() as db:
            row = db.execute("SELECT data FROM training_jobs WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return json.loads(row[0])

    def start(self, dataset_id, settings=None, expected_revision=None):
        with self.lock:
            if self.closed:
                raise ConflictError("训练服务正在关闭")
            if any(job["status"] in ("queued", "running") for job in self.jobs()):
                raise ConflictError("已有训练任务正在运行，请完成或取消后再试")
            dataset = self.get_dataset(dataset_id)
            if expected_revision is not None and dataset["revision"] != expected_revision:
                raise ConflictError("数据集已更新，请重新载入后再开始训练")
            validate_training_dataset(dataset)
            values = normalize_settings(settings or {})
            ident = uuid4().hex
            directory = self.root / "jobs" / ident
            directory.mkdir(parents=True)
            snapshot = dict(dataset=dataset, settings=values, upstream=str(self.settings.upstream.resolve()),
                            job_id=ident, scope=SCOPE, scope_reason="二分类：目标演唱为 1，其他人演唱及已复核的其余区间为 0；与原版二分类验证一致")
            (directory / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            job = dict(id=ident, dataset_id=dataset_id, dataset_revision=dataset["revision"], status="queued", stage="queued",
                       message="等待准备训练数据", progress=0.0, step=0, total_steps=values["steps"], settings=values,
                       history=[], log_tail="", checkpoint_path=None, model=None, error=None, scope=SCOPE,
                       created_at=now(), updated_at=now(), directory=str(directory), pid=None)
            self._save_job(job)
            self.pool.submit(self._run, ident)
            return job.copy()

    def _observe(self, job):
        with self.lock:
            if job["id"] in self.cancelled:
                return
            self._observe_running(job)

    def _observe_running(self, job):
        directory = Path(job["directory"])
        try:
            progress = json.loads((directory / "progress.json").read_text(encoding="utf-8"))
            job.update(stage=progress["stage"], message=progress["message"], progress=max(job["progress"], progress["progress"]))
        except (OSError, ValueError, KeyError):
            pass
        try:
            with (directory / "training.log").open("rb") as log:
                log.seek(0, 2)
                log.seek(max(0, log.tell() - 16000))
                job["log_tail"] = log.read().decode("utf-8", errors="replace")
            observed = [int(step) for step in re.findall(r"(?:step |\[val @)(\d+)", job["log_tail"])]
            if observed:
                job["step"] = max(job["step"], *observed)
        except OSError:
            pass
        try:
            history = json.loads((directory / "runs/model/history.json").read_text(encoding="utf-8"))
            if isinstance(history, list):
                job["history"] = history
                job["step"] = max(job["step"], max((entry["step"] for entry in history), default=0))
        except (OSError, ValueError, KeyError):
            pass
        if job["stage"] == "training":
            job["progress"] = max(job["progress"], .2 + .75 * min(1, job["step"] / job["total_steps"]))
        self._save_job(job)

    def _run(self, ident):
        job = self.get_job(ident)
        directory = Path(job["directory"])
        process = None
        try:
            with self.lock:
                if ident in self.cancelled or job["status"] == "cancelled":
                    job.update(status="cancelled", stage="finished", message="训练已取消")
                    return
                job.update(status="running", stage="preparing", message="正在提取特征并准备标注")
                self._save_job(job)
                environment = os.environ.copy()
                environment.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
                # Desktop installs carry a bundled FFmpeg, even when absent from PATH.
                try:
                    import imageio_ffmpeg
                    environment.setdefault("AUTOSLICER_FFMPEG", imageio_ffmpeg.get_ffmpeg_exe())
                except ImportError:
                    pass
                options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                with (directory / "training.log").open("w", encoding="utf-8") as log:
                    process = subprocess.Popen(worker_command("prepare", [str(directory / "snapshot.json")], python=self.settings.model_python),
                                               cwd=directory, stdout=log, stderr=subprocess.STDOUT, env=environment, **options)
                self.processes[ident] = process
                job["pid"] = process.pid
                self._save_job(job)
            while process.poll() is None:
                self._observe(job)
                time.sleep(.35)
            self._observe(job)
            with self.lock:
                if ident in self.cancelled:
                    job.update(status="cancelled", stage="finished", message="训练已取消")
                elif process.returncode:
                    detail = job["log_tail"].strip().splitlines()[-1:] or ["请查看训练日志"]
                    raise RuntimeError("训练失败：" + detail[0])
                else:
                    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                    checkpoint = Path(result["checkpoint_path"])
                    if not checkpoint.is_file() or checkpoint.stat().st_size == 0 or not result.get("verified_finite"):
                        raise RuntimeError("训练未生成通过有限值检查的模型文件")
                    job.update(stage="registering", message="训练完成，正在登记模型", checkpoint_path=str(checkpoint))
                    self._save_job(job)
                    snapshot = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
                    metadata = dict(target_name=snapshot["dataset"]["target_name"], dataset_id=job["dataset_id"],
                                    revision=job["dataset_revision"], job_id=ident, metrics=result["metrics"],
                                    scope=SCOPE, classes=1, settings=job["settings"])
                    if self.register_model:
                        model = self.register_model(checkpoint, metadata)
                        job["model"] = model.model_dump() if hasattr(model, "model_dump") else model
                    job.update(status="succeeded", stage="finished", message="训练完成，模型已保存", progress=1.0, step=job["total_steps"])
        except Exception as exc:
            with self.lock:
                if ident in self.cancelled:
                    job.update(status="cancelled", stage="finished", message="训练已取消")
                else:
                    job.update(status="failed", stage="finished", message=str(exc)[-2000:], error=str(exc)[-2000:])
        finally:
            with self.lock:
                stopped = True
                if process is not None and (process.poll() is None or job["status"] in ("failed", "cancelled")):
                    # An observation/persistence error must not orphan the real trainer.
                    # Also clean up children if the worker itself exited unexpectedly.
                    stopped = self._stop_process(process)
                if stopped:
                    self.processes.pop(ident, None)
                else:
                    # Keep compute exclusion in force until the OS confirms termination.
                    job.update(status="running", message="训练进程尚未停止，请再次取消后再开始其他计算任务")
                self._save_job(job)

    def _stop_process(self, process):
        self._terminate_tree(process.pid)
        try:
            process.wait(timeout=5)
            return self._group_stopped(process.pid) if os.name != "nt" else True
        except subprocess.TimeoutExpired:
            return False

    def _group_stopped(self, pid):
        # Reaping the worker alone does not prove that its trainer/FFmpeg children
        # have exited. Dead zombies use no compute and are left to the OS reaper.
        deadline = time.monotonic() + 5
        while True:
            try:
                result = subprocess.run(["ps", "-e", "-o", "pgid=,stat="], capture_output=True, text=True, timeout=3)
                if result.returncode:
                    return False
                active = any(len(fields) >= 2 and fields[0] == str(pid) and not fields[1].startswith("Z")
                             for line in result.stdout.splitlines() if (fields := line.split()))
                if not active:
                    return True
            except (OSError, subprocess.SubprocessError):
                return False
            if time.monotonic() >= deadline:
                return False
            self._terminate_tree(pid)
            time.sleep(.05)

    @staticmethod
    def _terminate_tree(pid):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
            else:
                # Stop worker, trainer, and any FFmpeg child as one isolated group.
                # SIGKILL ensures cancellation cannot hang on a blocked native ML call.
                os.killpg(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            pass

    def _stop_recorded_process(self, job):
        pid = job.get("pid")
        if pid:
            # Verify identity before touching a recorded PID, which the OS may reuse.
            try:
                if os.name == "nt":
                    inspect = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                               f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"]
                else:
                    inspect = ["ps", "-ww", "-p", str(pid), "-o", "command="]
                command = subprocess.run(inspect, capture_output=True, text=True, timeout=3).stdout
                if ("training_worker.py" in command or "--worker prepare" in command) and str(job["id"]) in command:
                    self._terminate_tree(pid)
                    if os.name != "nt":
                        return self._group_stopped(pid)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        command = subprocess.run(inspect, capture_output=True, text=True, timeout=3).stdout
                        if ("training_worker.py" not in command and "--worker prepare" not in command) or str(job["id"]) not in command:
                            return True
                        time.sleep(.05)
                    return False
            except (OSError, subprocess.SubprocessError, ValueError):
                return False
        return True

    def cancel(self, ident):
        with self.lock:
            job = self.get_job(ident)
            if job["status"] not in ("queued", "running"):
                return job
            self.cancelled.add(ident)
            process = self.processes.get(ident)
            stopped = self._stop_process(process) if process is not None else self._stop_recorded_process(job)
            if not stopped:
                job.update(status="running", message="正在等待训练进程停止，请稍后或再次取消")
            else:
                job.update(status="cancelled", stage="finished", message="训练已取消")
                self.processes.pop(ident, None)
            self._save_job(job)
            return job

    def close(self):
        with self.lock:
            self.closed = True
            for job in self.jobs():
                if job["status"] in ("queued", "running"):
                    self.cancel(job["id"])
        self.pool.shutdown(wait=True)
