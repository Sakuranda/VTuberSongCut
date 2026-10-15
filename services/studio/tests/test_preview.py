"""Preview progress describes real encoding, not elapsed wall time."""
import json
from pathlib import Path
import shutil
import subprocess
from threading import Event
import time

from fastapi.testclient import TestClient
import pytest

from studio.api import create_app
from studio.adapters.media import FFmpegMedia
from studio.config import Settings
from studio.domain import Job
from studio.jobs import JobRunner
from studio.store import ConflictError, Store


def finished(store, project_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        jobs = store.jobs(project_id=project_id)
        if jobs and jobs[0].status not in {"queued", "running"}:
            return jobs[0]
        time.sleep(.01)
    raise AssertionError("preview did not finish")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGCUT_SERVER", "0")
    settings = Settings(root=tmp_path, data=tmp_path / "data", upstream=tmp_path / "upstream", checkpoint=tmp_path / "missing.pt")
    with TestClient(create_app(settings)) as client:
        client.headers["x-studio-token"] = client.get("/api/session").json()["token"]
        yield client


def add_sample(client, tmp_path):
    training = client.app.state.training
    dataset = training.create_dataset("test", "singer")
    source = tmp_path / "source.flv"
    source.write_bytes(b"original fixture media; not an encoded video")
    dataset = training.add_sample(dataset["id"], source, source.name, 2000)
    sample = dataset["samples"][0]
    base = f"/api/training/datasets/{dataset['id']}/samples/{sample['id']}"
    return dataset, sample, base


def test_progress_is_monotonic_failure_retains_progress_and_only_success_reaches_one(tmp_path):
    store = Store(tmp_path / "store.sqlite3")
    runner = JobRunner(store)
    entered, release = Event(), Event()
    def operation(ident):
        runner.update_progress(ident, .6, "encoding")
        runner.update_progress(ident, .2)
        assert store.get_job(ident).progress == .6
        runner.update_progress(ident, 1)
        entered.set()
        assert release.wait(5)
        raise RuntimeError("real encoder error")
    try:
        job = runner.submit("sample", "preview", operation)
        assert entered.wait(5)
        running = store.get_job(job.id)
        assert running.status == "running" and running.progress == .99
        with pytest.raises(ConflictError):
            runner.submit("sample", "preview", lambda _: [])
    finally:
        release.set()
        runner.close()
    failed = store.get_job(job.id)
    assert failed.status == "failed" and failed.progress == .99
    assert failed.message == "real encoder error"
    retry = JobRunner(store)
    try:
        completed = retry.submit("sample", "preview", lambda _: [])
    finally:
        retry.close()
    assert store.get_job(completed.id).progress == 1


def test_filtered_history_finds_older_sample_and_restart_preserves_real_progress(tmp_path):
    store = Store(tmp_path / "store.sqlite3")
    old = Job(project_id="training-media:sample", kind="training_preview", status="running", progress=.42)
    store.save_job(old)
    for number in range(100):
        store.save_job(Job(project_id=str(number), kind="export", status="succeeded"))
    assert store.jobs(project_id=old.project_id) == [old]
    runner = JobRunner(store)
    runner.close()
    restored = store.get_job(old.id)
    assert restored.status == "interrupted" and restored.progress == .42
    assert "重启" in restored.message
    assert Job.model_validate({"project_id": "legacy", "kind": "preview", "status": "succeeded"}).progress == 1


def test_training_preview_is_deduplicated_atomic_and_restorable(client, tmp_path, monkeypatch):
    dataset, sample, base = add_sample(client, tmp_path)
    studio = client.app.state.studio
    entered, release = Event(), Event()
    calls = []
    def preview(source, target, *, duration_ms, on_progress):
        calls.append(source)
        assert duration_ms == sample["duration_ms"]
        on_progress(.35)
        target.write_bytes(b"partial test double")
        entered.set()
        assert release.wait(5)
        target.write_bytes(b"completed test double")
    monkeypatch.setattr(studio.media, "preview", preview)
    assert client.get(f"/api/training/datasets/{dataset['id']}").json()["samples"][0]["preview_available"] is False
    try:
        response = client.post(base + "/preview")
        assert response.status_code == 202
        assert entered.wait(5)
        assert client.post(base + "/preview").status_code == 409
        history = client.get("/api/jobs", params={"project_id": "training-media:" + sample["id"]}).json()
        assert history[0]["progress"] == .35 and history[0]["status"] == "running"
        assert client.get(base + "/media").content == Path(sample["path"]).read_bytes()
    finally:
        release.set()
    job = finished(studio.store, "training-media:" + sample["id"])
    assert job.status == "succeeded" and job.progress == 1
    assert client.get(base + "/media").content == b"completed test double"
    assert client.get(f"/api/training/datasets/{dataset['id']}").json()["samples"][0]["preview_available"] is True
    assert client.post(base + "/preview").json()["id"] == job.id
    assert len(calls) == 1


def test_failed_preview_does_not_publish_partial_file_and_can_retry(client, tmp_path, monkeypatch):
    dataset, sample, base = add_sample(client, tmp_path)
    studio = client.app.state.studio
    def fail(source, target, *, duration_ms, on_progress):
        on_progress(.4)
        target.write_bytes(b"partial output")
        raise RuntimeError("生成兼容预览失败：invalid input")
    monkeypatch.setattr(studio.media, "preview", fail)
    assert client.post(base + "/preview").status_code == 202
    job = finished(studio.store, "training-media:" + sample["id"])
    assert job.status == "failed" and job.progress == .4
    assert "invalid input" in job.message
    assert not list((studio.data / "training/previews").glob("*.mp4"))
    assert client.get(f"/api/training/datasets/{dataset['id']}").json()["samples"][0]["preview_available"] is False
    assert client.get(base + "/media").content == Path(sample["path"]).read_bytes()
    monkeypatch.setattr(studio.media, "preview", lambda source, target, **options: target.write_bytes(b"retry completed"))
    retry = client.post(base + "/preview")
    assert retry.status_code == 202 and retry.json()["id"] != job.id
    completed = finished(studio.store, "training-media:" + sample["id"])
    assert completed.status == "succeeded" and completed.progress == 1
    assert client.get(base + "/media").content == b"retry completed"


@pytest.mark.parametrize("suffix", [".avi", ".ts", ".m4v", ".flac"])
def test_training_upload_accepts_formats_offered_by_file_picker(client, monkeypatch, suffix):
    monkeypatch.setattr(client.app.state.studio.media, "probe", lambda _: (2000, True))
    dataset = client.app.state.training.create_dataset("formats", "singer")
    response = client.post(f"/api/training/datasets/{dataset['id']}/samples", data={"revision": 0},
                           files={"file": ("sample" + suffix, b"fixture upload")})
    assert response.status_code == 201
    assert response.json()["samples"][0]["preview_available"] is False


def test_real_flv_preview_reports_ffmpeg_progress_and_publishes_only_complete_mp4(client, tmp_path, monkeypatch):
    studio = client.app.state.studio
    media = studio.media
    if not shutil.which(media.ffprobe) or not Path(media.ffmpeg).is_file():
        pytest.skip("real FFmpeg and ffprobe are not installed")
    source = tmp_path / "recording with spaces.flv"
    media.run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15", "-f", "lavfi", "-i",
               "sine=frequency=440:sample_rate=16000", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast",
               "-c:a", "aac", "-f", "flv", str(source)])
    dataset = client.app.state.training.create_dataset("real FLV", "singer")
    with source.open("rb") as stream:
        response = client.post(f"/api/training/datasets/{dataset['id']}/samples", data={"revision": 0},
                               files={"file": (source.name, stream, "video/x-flv")})
    assert response.status_code == 201
    sample = response.json()["samples"][0]
    project_id = "training-media:" + sample["id"]
    base = f"/api/training/datasets/{dataset['id']}/samples/{sample['id']}"
    output = studio.data / "training/previews" / (sample["id"] + ".mp4")
    observations = []
    original = media.preview
    original_stream = media._run_preview
    # Read this tiny fixture at its real frame rate so even fast machines emit
    # multiple genuine FFmpeg progress records before the atomic publication.
    monkeypatch.setattr(media, "_run_preview", lambda arguments, duration, report:
                        original_stream(["-re", *arguments], duration, report))
    def observed(source, target, *, duration_ms, on_progress):
        def report(progress):
            on_progress(progress)
            job = studio.store.jobs(project_id=project_id)[0]
            observations.append((progress, job.status, output.exists()))
        return original(source, target, duration_ms=duration_ms, on_progress=report)
    monkeypatch.setattr(media, "preview", observed)
    assert client.post(base + "/preview").status_code == 202
    job = finished(studio.store, project_id)
    assert job.status == "succeeded", job.message
    assert job.progress == 1
    assert len(observations) >= 2 and any(0 < value < .9 for value, _, _ in observations)
    assert observations[-1][0] > .9
    assert all(0 <= value < 1 and status == "running" and not published
               for value, status, published in observations)
    result = subprocess.run([media.ffprobe, "-v", "error", "-show_streams", "-of", "json", str(output)],
                            capture_output=True, text=True, check=True)
    streams = json.loads(result.stdout)["streams"]
    assert {stream["codec_name"] for stream in streams} == {"h264", "aac"}
    assert next(stream for stream in streams if stream["codec_type"] == "video")["pix_fmt"] == "yuv420p"
    print("Real FFmpeg progress:", [round(value, 4) for value, _, _ in observations], "then succeeded=1")


def test_real_invalid_upload_has_clear_error_and_no_saved_sample(client):
    if not shutil.which(client.app.state.studio.media.ffprobe):
        pytest.skip("ffprobe is not installed")
    dataset = client.app.state.training.create_dataset("invalid", "singer")
    response = client.post(f"/api/training/datasets/{dataset['id']}/samples", data={"revision": 0},
                           files={"file": ("broken.flac", b"not media")})
    assert response.status_code == 422
    assert "无法读取媒体" in response.json()["detail"]
    assert client.get(f"/api/training/datasets/{dataset['id']}").json()["samples"] == []
