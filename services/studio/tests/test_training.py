"""Training contracts and an opt-in real original-CLI smoke test."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import wave

import pytest

from studio.config import Settings
from studio.store import Store, ConflictError
from studio.training import TrainingService, frame_labels, normalize_settings, validate_regions, validate_training_dataset


@pytest.fixture
def training(tmp_path):
    settings = Settings(root=tmp_path, data=tmp_path / "data", upstream=tmp_path / "upstream", checkpoint=tmp_path / "unused.pt")
    service = TrainingService(settings, Store(tmp_path / "store.sqlite3"))
    yield service
    service.close()


def ready_dataset(service, tmp_path):
    dataset = service.create_dataset("可复用声线", "测试演唱者")
    for index, split in enumerate(("train", "validation")):
        source = tmp_path / f"source-{index}.wav"
        source.write_bytes(f"different audio {index}".encode())
        dataset = service.add_sample(dataset["id"], source, source.name, 4000, split=split, expected_revision=dataset["revision"])
        dataset = service.update_sample(dataset["id"], dataset["samples"][-1]["id"], {
            "regions": [{"start_ms": 0, "end_ms": 1280, "label": "target"},
                        {"start_ms": 1280, "end_ms": 2560, "label": "other"}], "reviewed": True,
        }, dataset["revision"])
    return dataset


def wait_finished(service, ident, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.get_job(ident)
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(.05)
    raise AssertionError("training task did not finish")


def test_reviewed_rest_and_other_singing_are_binary_negatives():
    sample = dict(duration_ms=1600, reviewed=True, regions=[
        {"start_ms": 0, "end_ms": 640, "label": "target"},
        {"start_ms": 640, "end_ms": 960, "label": "other"}])
    assert frame_labels(sample, 5) == [1, 1, 0, 0, 0]
    sample["regions"] = [{"start_ms": 160, "end_ms": 480, "label": "target"}]
    assert frame_labels(sample, 5) == [1, 0, 0, 0, 0]
    sample["reviewed"] = False
    with pytest.raises(ValueError, match="确认"):
        frame_labels(sample, 5)


@pytest.mark.parametrize("regions", [
    [{"start_ms": -1, "end_ms": 100, "label": "target"}],
    [{"start_ms": 100, "end_ms": 100, "label": "target"}],
    [{"start_ms": 0, "end_ms": 1001, "label": "target"}],
    [{"start_ms": 0, "end_ms": 100, "label": "unknown"}],
    [{"start_ms": 0, "end_ms": 600, "label": "target"}, {"start_ms": 500, "end_ms": 700, "label": "other"}],
])
def test_invalid_or_overlapping_annotations_rejected(regions):
    with pytest.raises(ValueError):
        validate_regions(regions, 1000)


def test_dataset_revision_review_persistence_and_duplicate_rejection(training, tmp_path):
    dataset = ready_dataset(training, tmp_path)
    original_revision = dataset["revision"]
    sample = dataset["samples"][0]
    with pytest.raises(ConflictError):
        training.update_sample(dataset["id"], sample["id"], {"reviewed": False}, original_revision - 1)
    with pytest.raises(ValueError, match="重复"):
        training.add_sample(dataset["id"], sample["path"], "same bytes renamed.wav", 4000, split="validation")
    saved = training.update_sample(dataset["id"], sample["id"], {"regions": []}, original_revision)
    assert saved["revision"] == original_revision + 1
    assert not saved["samples"][0]["reviewed"]
    assert training.get_dataset(dataset["id"]) == saved
    with pytest.raises(ValueError, match="确认"):
        validate_training_dataset(saved)


def test_validation_requires_both_splits_positives_negatives_and_no_leakage(training, tmp_path):
    dataset = ready_dataset(training, tmp_path)
    validate_training_dataset(dataset)
    dataset["samples"][1]["sha256"] = dataset["samples"][0]["sha256"]
    with pytest.raises(ValueError, match="泄漏"):
        validate_training_dataset(dataset)
    dataset = training.get_dataset(dataset["id"])
    dataset["samples"][1]["regions"] = []
    with pytest.raises(ValueError, match="验证集"):
        validate_training_dataset(dataset)
    dataset["samples"][1]["split"] = "train"
    with pytest.raises(ValueError, match="验证集"):
        validate_training_dataset(dataset)


def test_start_uses_immutable_snapshot_and_cancelled_queue_never_trains(training, tmp_path, monkeypatch):
    dataset = ready_dataset(training, tmp_path)
    calls = []
    monkeypatch.setattr(training.pool, "submit", lambda *args: calls.append(args))
    job = training.start(dataset["id"], {"steps": 2}, dataset["revision"])
    with pytest.raises(ConflictError):
        training.start(dataset["id"], {"steps": 2})
    training.update_sample(dataset["id"], dataset["samples"][0]["id"], {"regions": [], "reviewed": False}, dataset["revision"])
    snapshot = json.loads((Path(job["directory"]) / "snapshot.json").read_text())
    assert snapshot["dataset"] == dataset
    assert snapshot["settings"]["steps"] == 2
    assert snapshot["scope"] == "target-vs-rest"
    assert training.cancel(job["id"])["status"] == "cancelled"
    training._run(job["id"])
    assert training.get_job(job["id"])["status"] == "cancelled"
    assert training.get_job(job["id"])["checkpoint_path"] is None


def test_missing_upstream_failure_cannot_register_or_succeed(training, tmp_path):
    registered = []
    training.register_model = lambda *args: registered.append(args)
    dataset = ready_dataset(training, tmp_path)
    job = training.start(dataset["id"], {"steps": 2})
    result = wait_finished(training, job["id"])
    assert result["status"] == "failed"
    assert result["checkpoint_path"] is None and result["model"] is None
    assert "训练" in result["message"]
    assert not registered


def test_restart_marks_unfinished_jobs_interrupted_and_preserves_success(training):
    for status in ("queued", "running", "succeeded", "cancelled"):
        training._save_job(dict(id=status, dataset_id="d", status=status, pid=None, updated_at=""))
    reopened = TrainingService(training.settings, training.store)
    try:
        assert reopened.get_job("queued")["status"] == reopened.get_job("running")["status"] == "interrupted"
        assert reopened.get_job("succeeded")["status"] == "succeeded"
        assert reopened.get_job("cancelled")["status"] == "cancelled"
    finally:
        reopened.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group contract")
def test_cancel_kills_worker_and_child_process_group(training, tmp_path, monkeypatch):
    dataset = ready_dataset(training, tmp_path)
    original_popen = subprocess.Popen
    def sleeping_process(command, **kwargs):
        if not any("training_worker.py" in str(argument) for argument in command):
            return original_popen(command, **kwargs)
        script = "import subprocess,sys,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path('child.pid').write_text(str(child.pid)); print('ready',flush=True); time.sleep(60)"
        return original_popen([sys.executable, "-u", "-c", script], **kwargs)
    monkeypatch.setattr("studio.training.subprocess.Popen", sleeping_process)
    job = training.start(dataset["id"], {"steps": 2})
    deadline = time.monotonic() + 5
    while not training.processes and time.monotonic() < deadline:
        time.sleep(.02)
    process = training.processes[job["id"]]
    marker = Path(job["directory"]) / "child.pid"
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    child_pid = int(marker.read_text())
    assert training.cancel(job["id"])["status"] == "cancelled"
    process.wait(timeout=3)
    # The child must be gone (or already a dead zombie awaiting its reaper).
    child_state = subprocess.run(["ps", "-p", str(child_pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
    assert not child_state or child_state.startswith("Z")
    training.pool.shutdown(wait=True)
    assert training.get_job(job["id"])["status"] == "cancelled"
    assert process.returncode == -signal.SIGKILL


@pytest.mark.parametrize("settings", [
    {"steps": 0}, {"batch_size": True}, {"device": "bad"}, {"window_s": float("nan")},
    {"lr": float("inf")}, {"num_workers": 9}, {"model_kwargs": {"channels": [1]}},
])
def test_settings_reject_unsafe_or_unbounded_configuration(settings):
    with pytest.raises(ValueError):
        normalize_settings(settings)


@pytest.mark.skipif(os.environ.get("SONGCUT_TRAINING_SMOKE") != "1", reason="opt-in actual torch + original CLI smoke")
def test_real_original_cli_training_outputs_finite_checkpoint(tmp_path):
    import numpy as np
    import torch
    from studio.training_worker import main
    root = Path(__file__).resolve().parents[3]
    upstream = root / "upstream/AutoSlicer-v2"
    samples = []
    for index, split in enumerate(("train", "validation")):
        source = tmp_path / f"singing-{index}.wav"
        t = np.arange(16000 * 4) / 16000
        data = (.2 * np.sin(2 * math.pi * (220 + index * 80) * t) * 32767).astype("<i2")
        with wave.open(str(source), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(data.tobytes())
        import hashlib
        samples.append(dict(id=str(index), path=str(source), name=source.name, duration_ms=4000,
                            sha256=hashlib.sha256(source.read_bytes()).hexdigest(), split=split, reviewed=True,
                            regions=[dict(start_ms=0, end_ms=1600, label="target"), dict(start_ms=1600, end_ms=3000, label="other")]))
    snapshot = dict(upstream=str(upstream), dataset=dict(samples=samples), settings=normalize_settings({
        "device": "cpu", "steps": 2, "batch_size": 1, "window_s": 2.56, "val_every": 1, "warmup": 0}),
        _test_model_kwargs=dict(channels=[2, 2, 2, 2], rnn_hidden=4, rnn_layers=1, dropout=0))
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot))
    main(snapshot_path)
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["verified_finite"]
    checkpoint = torch.load(result["checkpoint_path"], map_location="cpu", weights_only=True)
    assert checkpoint["step"] in (1, 2)
    assert all(torch.isfinite(tensor).all() for tensor in checkpoint["model"].values())
    assert len(json.loads((tmp_path / "runs/model/history.json").read_text())) == 2


def test_observation_failure_terminates_real_worker_before_publishing_failure(training, tmp_path, monkeypatch):
    dataset = ready_dataset(training, tmp_path)
    original_popen = subprocess.Popen
    processes = []
    def worker(command, **kwargs):
        if any("training_worker.py" in str(argument) for argument in command):
            process = original_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            processes.append(process)
            return process
        return original_popen(command, **kwargs)
    monkeypatch.setattr("studio.training.subprocess.Popen", worker)
    def failed_observation(job):
        raise RuntimeError("observation failed")
    monkeypatch.setattr(training, "_observe_running", failed_observation)
    job = training.start(dataset["id"], {"steps": 2})
    result = wait_finished(training, job["id"])
    assert result["status"] == "failed" and "observation failed" in result["message"]
    assert processes[0].poll() is not None
    assert job["id"] not in training.processes


def test_cancellation_keeps_compute_busy_until_worker_reaped(training, tmp_path, monkeypatch):
    dataset = ready_dataset(training, tmp_path)
    original_popen = subprocess.Popen
    def worker(command, **kwargs):
        if any("training_worker.py" in str(argument) for argument in command):
            return original_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        return original_popen(command, **kwargs)
    monkeypatch.setattr("studio.training.subprocess.Popen", worker)
    job = training.start(dataset["id"], {"steps": 2})
    deadline = time.monotonic() + 5
    while not training.processes and time.monotonic() < deadline:
        time.sleep(.02)
    # Model a delayed/failed OS termination. The job must remain busy until retry.
    original_stop = training._stop_process
    monkeypatch.setattr(training, "_stop_process", lambda process: False)
    pending = training.cancel(job["id"])
    assert pending["status"] == "running"
    with pytest.raises(ConflictError):
        training.start(dataset["id"], {"steps": 2})
    monkeypatch.setattr(training, "_stop_process", original_stop)
    assert training.cancel(job["id"])["status"] == "cancelled"
    training.pool.shutdown(wait=True)
    assert training.get_job(job["id"])["status"] == "cancelled"


@pytest.mark.skipif(os.name == "nt", reason="POSIX restart process-group contract")
def test_restart_terminates_recorded_worker_and_children_before_unlocking(training, tmp_path):
    job_id = "recorded-restart-worker"
    directory = training.root / "jobs" / job_id
    directory.mkdir(parents=True)
    script = directory / "training_worker.py"
    script.write_text("import subprocess,sys,time\nfrom pathlib import Path\nchild=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\nPath('child.pid').write_text(str(child.pid))\ntime.sleep(60)\n")
    process = subprocess.Popen([sys.executable, str(script)], cwd=directory, start_new_session=True)
    reopened = None
    try:
        deadline = time.monotonic() + 5
        marker = directory / "child.pid"
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        child_pid = int(marker.read_text())
        training._save_job(dict(id=job_id, dataset_id="dataset", status="running", pid=process.pid))
        reopened = TrainingService(training.settings, training.store)
        assert reopened.get_job(job_id)["status"] == "interrupted"
        process.wait(timeout=3)
        child_state = subprocess.run(["ps", "-p", str(child_pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        assert not child_state or child_state.startswith("Z")
    finally:
        training._terminate_tree(process.pid)
        process.wait(timeout=3)
        if reopened:
            reopened.close()
