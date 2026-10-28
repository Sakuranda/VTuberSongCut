"""Independent contract checks; no model weights, FFmpeg, or network required."""
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from studio.api import create_app
from studio.config import Settings
from studio.domain import Clip, Job, LyricCue, LyricWord, Project, SubtitleStyle
from studio.store import Store, ConflictError
from studio.jobs import JobRunner
from studio.service import Studio
from studio.adapters.lyrics import parse_lrc, render_ass


def project(**updates):
    return Project(**{**dict(name="测试歌回", source_name="vod.mp4", source_file="/unused/vod.mp4", duration_ms=60_000), **updates})


@pytest.mark.parametrize("start,end", [(-1, 100), (100, 100), (200, 100)])
def test_clip_rejects_invalid_ranges(start, end):
    with pytest.raises(ValidationError):
        Clip(start_ms=start, end_ms=end)


def test_project_rejects_duplicate_ids_and_out_of_source_bounds():
    clip = Clip(start_ms=0, end_ms=1000)
    with pytest.raises(ValidationError):
        project(clips=[clip, clip])
    with pytest.raises(ValidationError):
        project(clips=[Clip(start_ms=59_000, end_ms=60_001)])


@pytest.mark.parametrize("words", [
    [LyricWord(start_ms=900, end_ms=1200, text="前")],
    [LyricWord(start_ms=1500, end_ms=2100, text="后")],
    [LyricWord(start_ms=1500, end_ms=1700, text="一"), LyricWord(start_ms=1600, end_ms=1900, text="二")],
])
def test_words_must_be_ordered_and_inside_cue(words):
    with pytest.raises(ValidationError):
        LyricCue(start_ms=1000, end_ms=2000, text="歌词", words=words)


def test_sqlite_rejects_stale_edits_and_preserves_committed_snapshot(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    original = project()
    store.create(original)
    left, right = store.get(original.id), store.get(original.id)
    left.clips = [Clip(start_ms=100, end_ms=1000, title="已保存")]
    saved = store.save(left, left.revision)
    assert saved.revision == 1
    right.clips = [Clip(start_ms=2000, end_ms=3000, title="过期编辑")]
    with pytest.raises(ConflictError):
        store.save(right, right.revision)
    reopened = Store(store.path).get(original.id)
    assert reopened.revision == 1
    assert reopened.clips[0].title == "已保存"
    # Assignment bypasses Pydantic validation: persistence must validate again.
    reopened.clips = [Clip(start_ms=59_000, end_ms=61_000)]
    with pytest.raises(ValidationError):
        store.save(reopened, reopened.revision)
    assert store.get(original.id).revision == 1


def test_runner_recovery_marks_pending_jobs_without_changing_terminal_jobs(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    for status in ["queued", "running", "succeeded", "failed", "interrupted"]:
        store.save_job(Job(id=status, project_id="p", kind="detect", status=status, artifacts=["existing.json"]))
    runner = JobRunner(store)
    runner.close()
    jobs = {job.id: job for job in store.jobs()}
    assert jobs["queued"].status == jobs["running"].status == "interrupted"
    for status in ["succeeded", "failed", "interrupted"]:
        assert jobs[status].status == status
        assert jobs[status].artifacts == ["existing.json"]


def test_failed_operation_is_persisted_and_project_lock_is_released(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    runner = JobRunner(store)
    entered, release = Event(), Event()
    def fail(_):
        entered.set()
        assert release.wait(5)
        raise RuntimeError("detector failed honestly")
    try:
        job = runner.submit("p", "detect", fail)
        assert entered.wait(5)
        with pytest.raises(ConflictError):
            runner.submit("p", "detect", lambda _: [])
    finally:
        release.set()
        runner.close()
    persisted = next(j for j in store.jobs() if j.id == job.id)
    assert persisted.status == "failed"
    assert "detector failed honestly" in persisted.message
    assert persisted.artifacts == []
    assert "p" not in runner.active


def test_detection_preserves_manual_edits_made_while_running(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    original = project(source_file=str(source))
    store.create(original)
    entered, release = Event(), Event()
    class Detector:
        def detect(self, source, output, duration_ms):
            entered.set()
            assert release.wait(5)
            return [Clip(start_ms=1000, end_ms=5000, detection="autoslicer"),
                    Clip(start_ms=10_000, end_ms=15_000, detection="autoslicer")]
    runner = JobRunner(store)
    studio = Studio(tmp_path, store, runner, Detector(), None)
    try:
        job = studio.detect(original.id)
        assert entered.wait(5)
        edited = store.get(original.id)
        manual = Clip(start_ms=1500, end_ms=4500, title="人工精修")
        edited.clips = [manual]
        store.save(edited, edited.revision)
    finally:
        release.set()
        runner.close()
    current = store.get(original.id)
    assert [(c.start_ms, c.end_ms, c.title) for c in current.clips] == [
        (1500, 4500, "人工精修"), (10_000, 15_000, "未命名歌曲")]
    assert next(j for j in store.jobs() if j.id == job.id).status == "succeeded"


def test_lrc_offset_fraction_and_repeated_stamps_use_source_milliseconds():
    cues = parse_lrc("[offset:-250]\n[00:01.5][00:03.050]同句\n[00:05]后句", 10_000, 60_000)
    assert [(c.start_ms, c.end_ms, c.text) for c in cues] == [
        (11_250, 12_800, "同句"), (12_800, 14_750, "同句"), (14_750, 60_000, "后句")]
    assert all(not cue.words for cue in cues)


@pytest.mark.parametrize("text", ["没有时间", "[00:60]非法秒"])
def test_lrc_rejects_missing_or_invalid_timestamps(text):
    with pytest.raises(ValueError):
        parse_lrc(text, 0, 60_000)


def test_ass_clips_to_relative_time_and_neutralizes_user_override_syntax():
    clip = Clip(start_ms=10_000, end_ms=14_000, lyrics=[
        LyricCue(start_ms=9000, end_ms=12_000, text=r"{\pos(1,1)}歌词"),
        LyricCue(start_ms=12_000, end_ms=15_000, text="后句\n换行")])
    ass = render_ass(clip)
    assert "Dialogue: 0,0:00:00.00,0:00:02.00" in ass
    assert "Dialogue: 0,0:00:02.00,0:00:04.00" in ass
    assert r"{\pos(1,1)}" not in ass
    assert "｛＼pos(1,1)｝歌词" in ass
    assert "后句 换行" in ass


def test_ktv_only_uses_word_highlighting_with_real_word_times():
    clip = Clip(start_ms=10_000, end_ms=15_000, subtitle=SubtitleStyle(layout="ktv"), lyrics=[
        LyricCue(start_ms=10_000, end_ms=12_000, text="仅行级"),
        LyricCue(start_ms=12_000, end_ms=15_000, text="逐字", words=[
            LyricWord(start_ms=12_500, end_ms=13_000, text="逐"),
            LyricWord(start_ms=13_500, end_ms=14_000, text="字")])])
    lines = [line for line in render_ass(clip).splitlines() if line.startswith("Dialogue:")]
    assert all("\\k" not in line for line in lines if "仅行级" in line)
    highlighted = [line for line in lines if "\\kf" in line]
    assert len(highlighted) == 1
    assert r"{\kf50}逐" in highlighted[0] and r"{\kf50}字" in highlighted[0]
    assert highlighted[0].count(r"{\k50}\h") == 2


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Dummy bytes never reach FFprobe; this tests HTTP/persistence, not media decoding.
    from studio.adapters.media import FFmpegMedia
    monkeypatch.setattr(FFmpegMedia, "probe", lambda self, source: (60_000, True))
    monkeypatch.setattr(FFmpegMedia, "metadata", lambda *args: dict(width=640, height=360, fps=25, browser_compatible=True))
    monkeypatch.setattr(FFmpegMedia, "preview", lambda self, source, output, **kwargs: (output.parent.mkdir(parents=True, exist_ok=True), output.write_bytes(b"preview")))
    settings = Settings(root=tmp_path, data=tmp_path / "data", upstream=tmp_path / "upstream", checkpoint=tmp_path / "missing.pt")
    app = create_app(settings)
    with TestClient(app) as connected:
        yield connected


def token_headers(client):
    response = client.get("/api/session")
    assert response.headers["cache-control"] == "no-store"
    return {"x-studio-token": response.json()["token"]}


def upload(client, headers):
    response = client.post("/api/projects/upload", headers=headers, files={"file": ("测试.mp4", b"dummy", "video/mp4")})
    assert response.status_code == 201
    return response.json()


def test_api_rejects_missing_wrong_token_and_untrusted_origin_or_host(client):
    assert client.post("/api/projects/missing/detect").status_code == 403
    assert client.post("/api/projects/missing/detect", headers={"x-studio-token": "wrong"}).status_code == 403
    assert client.get("/api/session", headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/session", headers={"Host": "attacker.example"}).status_code == 400
    headers = {**token_headers(client), "Origin": "http://localhost:5173"}
    assert client.post("/api/projects/missing/detect", headers=headers).status_code == 404


def test_api_project_roundtrip_validation_and_conflicts(client):
    headers = token_headers(client)
    original = upload(client, headers)
    assert original["id"] in [item["id"] for item in client.get("/api/projects").json()]
    url = f'/api/projects/{original["id"]}'
    clip = Clip(start_ms=10_000, end_ms=20_000, original_artist="原唱", performer="阿梓").model_dump()
    saved = client.put(url, headers=headers, json={"revision": 0, "clips": [clip]})
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert client.put(url, headers=headers, json={"revision": 0, "clips": []}).status_code == 409
    bad = {**clip, "end_ms": 60_001}
    assert client.put(url, headers=headers, json={"revision": 1, "clips": [bad]}).status_code == 422
    current = client.get(url).json()
    assert current["revision"] == 1 and current["clips"][0]["performer"] == "阿梓"
    lyrics = client.post(f'{url}/clips/{clip["id"]}/lyrics', headers=headers, json={"text": "[00:01.50]歌词", "offset_ms": 250})
    assert lyrics.status_code == 200
    assert lyrics.json()["clips"][0]["lyrics"][0]["start_ms"] == 11_750
    assert client.get("/api/projects/missing").status_code == 404
    assert client.post(f"{url}/clips/missing/export", headers=headers).status_code == 404
    assert client.get("/api/artifacts/missing/clip.mp4").status_code == 404


def test_api_failed_upload_cleans_partial_media(client, monkeypatch):
    headers = token_headers(client)
    assert client.post("/api/projects/upload", headers=headers, files={"file": ("bad.exe", b"x")}).status_code == 415
    def broken_probe(_):
        raise ValueError("invalid media")
    monkeypatch.setattr(client.app.state.studio.media, "probe", broken_probe)
    assert client.post("/api/projects/upload", headers=headers, files={"file": ("bad.mp4", b"x")}).status_code == 422
    assert client.get("/api/projects").json() == []
    assert list((client.app.state.studio.data / "media").iterdir()) == []


def test_recovery_includes_pending_jobs_older_than_history_page(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    store.save_job(Job(id="old-running", project_id="p", kind="detect", status="running"))
    for number in range(100):
        store.save_job(Job(id=f"completed-{number}", project_id="p", kind="detect", status="succeeded"))
    store.recover()
    with store.connect() as db:
        payload = db.execute("SELECT data FROM jobs WHERE id=?", ("old-running",)).fetchone()[0]
    assert Job.model_validate_json(payload).status == "interrupted"


def test_get_job_reads_older_than_history_page_and_reports_missing(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    old = Job(id="old-artifact", project_id="p", kind="export", status="succeeded", artifacts=["clip.mp4"])
    store.save_job(old)
    for number in range(100):
        store.save_job(Job(id=f"newer-{number}", project_id="p", kind="detect", status="succeeded"))
    assert old.id not in [job.id for job in store.jobs()]
    assert store.get_job(old.id) == old
    with pytest.raises(KeyError):
        store.get_job("missing")


@pytest.mark.parametrize("updates", [
    {"video": False, "audio": False},
    {"video_codec": "vp9"}, {"resolution": "480p"}, {"fps": 29.97},
    {"rate_control": "cbr"}, {"crf": -1}, {"crf": 41},
    {"video_bitrate_kbps": 249}, {"video_bitrate_kbps": 100001},
    {"audio_bitrate_kbps": 120},
])
def test_export_settings_reject_unsupported_or_out_of_bounds_values(updates):
    from studio.domain import ExportSettings
    with pytest.raises(ValidationError):
        ExportSettings(**updates)


@pytest.mark.parametrize("updates", [
    {"video": False}, {"audio": False},
    {"video_codec": "hevc", "resolution": "2160p", "fps": 60},
    {"rate_control": "bitrate", "video_bitrate_kbps": 250, "audio_bitrate_kbps": 64},
    {"rate_control": "quality", "crf": 40, "video_bitrate_kbps": 100000, "audio_bitrate_kbps": 320},
])
def test_export_settings_accept_supported_extremes(updates):
    from studio.domain import ExportSettings
    settings = ExportSettings(**updates)
    for name, value in updates.items():
        assert getattr(settings, name) == value


def test_export_settings_defaults_and_api_persistence(client):
    from studio.domain import ExportSettings
    defaults = ExportSettings()
    assert defaults.video and defaults.audio
    assert defaults.video_codec == "h264" and defaults.resolution == "source"
    assert defaults.fps is None and defaults.rate_control == "quality"
    assert defaults.crf == 20 and defaults.video_bitrate_kbps == 8000 and defaults.audio_bitrate_kbps == 192
    assert project().source_fps is None
    assert Clip(start_ms=0, end_ms=1000).export_settings == defaults
    headers = token_headers(client)
    original = upload(client, headers)
    clip = Clip(start_ms=1000, end_ms=5000, export_settings=ExportSettings(
        video=False, audio=True, video_codec="hevc", resolution="1080p", fps=25,
        rate_control="bitrate", crf=16, video_bitrate_kbps=12000, audio_bitrate_kbps=256))
    url = f'/api/projects/{original["id"]}'
    saved = client.put(url, headers=headers, json={"revision": 0, "clips": [clip.model_dump()]})
    assert saved.status_code == 200
    assert client.get(url).json()["clips"][0]["export_settings"] == clip.export_settings.model_dump()


def test_export_job_uses_immutable_project_snapshot_while_user_edits(tmp_path):
    from studio.domain import ExportSettings
    store = Store(tmp_path / "test.sqlite3")
    original_clip = Clip(start_ms=1000, end_ms=4000, title="导出时标题", export_settings=ExportSettings(
        video=False, audio=True, audio_bitrate_kbps=128))
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    original = project(source_file=str(source), source_fps=29.97, clips=[original_clip])
    store.create(original)
    entered, release = Event(), Event()
    observed = {}
    class FakeMedia:
        def render(self, snapshot, clip, output):
            entered.set()
            assert release.wait(5)
            observed.update(title=clip.title, start=clip.start_ms, bitrate=clip.export_settings.audio_bitrate_kbps,
                            source_fps=snapshot.source_fps, revision=snapshot.revision)
            output.mkdir(parents=True)
            marker = output / "fake-render-marker.txt"
            marker.write_text("test double; not a real media export", encoding="utf-8")
            return [marker]
    runner = JobRunner(store)
    studio = Studio(tmp_path, store, runner, None, FakeMedia())
    try:
        job = studio.export(original.id, original_clip.id)
        assert entered.wait(5)
        edited = store.get(original.id)
        edited.clips[0].title = "后续编辑标题"
        edited.clips[0].start_ms = 2000
        edited.clips[0].export_settings.audio_bitrate_kbps = 320
        store.save(edited, edited.revision)
    finally:
        release.set()
        runner.close()
    assert observed == {"title": "导出时标题", "start": 1000, "bitrate": 128, "source_fps": 29.97, "revision": 0}
    assert store.get(original.id).revision == 1
    persisted = store.get_job(job.id)
    assert persisted.status == "succeeded" and persisted.input_revision == 0
    snapshot = Project.model_validate_json((tmp_path / "jobs" / job.id / "project.snapshot.json").read_text(encoding="utf-8"))
    assert snapshot.clips[0] == original_clip
    assert snapshot.source_fps == 29.97 and snapshot.revision == 0


def test_bilingual_lrc_preserves_both_texts_at_same_timestamp():
    cues = parse_lrc("[00:01]原文\n[00:01]translation\n[00:02]后句", 10_000, 20_000)
    assert len(cues) == 2
    assert cues[0].start_ms == 11_000 and cues[0].end_ms == 12_000
    assert cues[0].text == "原文\ntranslation"


def test_ktv_preserves_already_sung_word_when_clip_starts_mid_line():
    clip = Clip(start_ms=11_000, end_ms=13_000, subtitle=SubtitleStyle(layout="ktv"), lyrics=[
        LyricCue(start_ms=10_000, end_ms=14_000, text="前中后", words=[
            LyricWord(start_ms=10_000, end_ms=10_500, text="前"),
            LyricWord(start_ms=10_500, end_ms=11_500, text="中"),
            LyricWord(start_ms=13_500, end_ms=14_000, text="后")])])
    ass = render_ass(clip)
    assert "Dialogue: 0,0:00:00.00,0:00:02.00" in ass
    assert r"{\kf0}前" in ass
    assert r"{\kf50}中" in ass
    assert "后" in ass


def test_detection_does_not_apply_old_model_after_selection_changes(tmp_path):
    source = tmp_path/'source.mp4'; source.write_bytes(b'fixture')
    store = Store(tmp_path/'store.sqlite3')
    original = project(source_file=str(source), detector_model_id='model-a')
    store.create(original)
    entered, release = Event(), Event()
    class Detector:
        def detect(self, *args):
            entered.set()
            assert release.wait(5)
            return [Clip(start_ms=1000,end_ms=2000)]
    runner = JobRunner(store)
    studio = Studio(tmp_path,store,runner,Detector(),None)
    try:
        job = studio.detect(original.id, model_info={'model_id':'model-a'})
        assert entered.wait(5)
        current = store.get(original.id); current.detector_model_id='model-b'
        store.save(current,current.revision)
    finally:
        release.set(); runner.close()
    assert store.get_job(job.id).status=='failed'
    assert not store.get(original.id).clips
