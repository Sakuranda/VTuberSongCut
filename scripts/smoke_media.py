"""Real media smoke test; generated test patterns, never a singing-quality benchmark."""
from pathlib import Path
import json
import time
from fastapi.testclient import TestClient
from studio.api import create_app
from studio.config import Settings
from studio.adapters.media import FFmpegMedia
from studio.domain import Clip, ExportSettings, SubtitleStyle

root = Path(__file__).resolve().parents[1]
work = root / "var/smoke/integration"
work.mkdir(parents=True, exist_ok=True)
media = FFmpegMedia()
source = work / "synthetic.mp4"
media.run(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
           "-t", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)])
media.run(["-i", str(source), "-c", "copy", str(work / "synthetic.flv")])
settings = Settings(root=root, data=work / "data", upstream=root / "upstream/AutoSlicer-v2", checkpoint=root / "var/models/best.pt")
report = {"material": "synthetic test pattern and sine wave, not actual singing", "checks": []}
with TestClient(create_app(settings)) as client:
    headers = {"X-Studio-Token": client.get("/api/session").json()["token"]}
    def post(path, **kwargs):
        response = client.post(path, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()
    def wait(job):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
            if state["status"] in ("succeeded", "failed", "interrupted"):
                assert state["status"] == "succeeded", state
                return state
            time.sleep(0.1)
        raise AssertionError("media job timed out")
    for suffix in ("mp4", "flv"):
        with (work / f"synthetic.{suffix}").open("rb") as file:
            project = post("/api/projects/upload", files={"file": (f"合成验收.{suffix}", file)})
        assert project["has_video"] and abs(project["duration_ms"] - 8000) < 150
        assert project["source_fps"] == 25
        report["checks"].append(f"{suffix} imported and probed at 25 fps")
    ident = project["id"]
    wait(post(f"/api/projects/{ident}/preview"))
    assert client.get(f"/api/projects/{ident}/media", headers={"Range": "bytes=0-99"}).status_code == 206
    wait(post(f"/api/projects/{ident}/detect"))
    project = client.get(f"/api/projects/{ident}").json()
    assert project["clips"] == [], "synthetic sine wave should not be a target singing event"
    clip = Clip(start_ms=1000, end_ms=6000, title="合成字幕验收", subtitle=SubtitleStyle(enabled=True, layout="double", font_size=72),
                export_settings=ExportSettings(resolution="720p", fps=30, rate_control="bitrate", video_bitrate_kbps=1500))
    response = client.put(f"/api/projects/{ident}", headers=headers, json={"revision": project["revision"], "clips": [clip.model_dump()]})
    response.raise_for_status()
    post(f"/api/projects/{ident}/clips/{clip.id}/lyrics", json={"text": "[00:00]拾歌 字幕验收\n[00:02.50]这里是合成测试素材", "offset_ms": 0})
    job = wait(post(f"/api/projects/{ident}/clips/{clip.id}/export"))
    for filename in job["artifacts"]:
        assert client.get(f"/api/artifacts/{job['id']}/{filename}").status_code == 200
    output = settings.data / "jobs" / job["id"]
    duration, _ = media.probe(output / "clip.mp4")
    assert abs(duration - 5000) < 50
    assert media.source_fps(output / "clip.mp4") == 30
    media.run(["-ss", "1", "-i", str(output / "clip.mp4"), "-frames:v", "1", str(work / "subtitle-preview.png")])
    report["checks"] += ["FLV preview proxy and HTTP range streaming", "real checkpoint detection on synthetic audio", "edit + LRC ingest + queued render",
                         "720p / 30fps H264 + MP3 export and artifact downloads", "5-second clip duration verified"]
    report["output"] = str(output)
(work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
