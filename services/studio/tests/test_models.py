from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from studio.api import create_app
from studio.config import Settings
from studio.store import Store
from studio.model_library import ModelLibrary, checksum


@pytest.fixture
def setup(tmp_path, monkeypatch):
    config = Settings(root=tmp_path, data=tmp_path / "data", upstream=tmp_path / "upstream", checkpoint=tmp_path / "absent.pt")
    (config.upstream / "src").mkdir(parents=True)
    (config.upstream / "src/infer.py").write_text("", encoding="utf-8")
    def probe(self, checkpoint=None):
        if checkpoint and checkpoint.read_bytes() == b"invalid":
            raise ValueError("模型校验失败")
        return {"architecture": "crnn", "classes": 1, "devices": [{"id": "auto", "available": True, "label": "自动"},
                 {"id": "cpu", "available": True, "label": "CPU"}], "automatic_device": "cpu"}
    monkeypatch.setattr(ModelLibrary, "probe", probe)
    return config


def test_model_registry_copies_deduplicates_and_survives_restart(setup, tmp_path):
    store = Store(setup.data / "db")
    library = ModelLibrary(setup, store)
    source = tmp_path / "my-singer.pt"
    source.write_bytes(b"test validated checkpoint")
    model = library.register_file(source, "我的主播")
    duplicate = library.register_file(source, "重复导入")
    assert duplicate.id == model.id
    source.unlink()
    reopened = ModelLibrary(setup, Store(store.path))
    info, owned = reopened.resolve(model.id)
    assert info.name == "我的主播" and owned.is_file()
    assert len(reopened.list()) == 1


def test_changed_model_is_rejected_even_if_size_is_unchanged(setup, tmp_path):
    library = ModelLibrary(setup, Store(setup.data / "db"))
    source = tmp_path / "model.pt"
    source.write_bytes(b"original")
    info = library.register_file(source, "模型")
    _, path = library.resolve(info.id)
    path.write_bytes(b"modified")
    with pytest.raises(ValueError, match="校验记录"):
        library.resolve(info.id)


def test_invalid_model_never_registers_and_staging_is_cleaned(setup, tmp_path):
    library = ModelLibrary(setup, Store(setup.data / "db"))
    source = tmp_path / "model.pt"
    source.write_bytes(b"invalid")
    with pytest.raises(ValueError):
        library.register_file(source, "无效模型")
    assert library.list() == []
    assert list(library.folder.iterdir()) == []


def test_selection_is_explicit_and_project_scoped(setup, monkeypatch):
    from studio.adapters.media import FFmpegMedia
    from studio.adapters.autoslicer import AutoSlicerDetector
    monkeypatch.setattr(FFmpegMedia, "probe", lambda *args: (60_000, True))
    monkeypatch.setattr(FFmpegMedia, "source_fps", lambda *args: 25)
    monkeypatch.setattr(FFmpegMedia, "metadata", lambda *args: dict(width=640, height=360, fps=25, browser_compatible=True))
    monkeypatch.setattr(FFmpegMedia, "preview", lambda self, source, output, **kwargs: (output.parent.mkdir(parents=True, exist_ok=True), output.write_bytes(b"preview")))
    calls = []
    monkeypatch.setattr(AutoSlicerDetector, "detect", lambda self, *args: calls.append((self.checkpoint, self.device)) or [])
    app = create_app(setup)
    with TestClient(app) as client:
        auth = {"X-Studio-Token": client.get("/api/session").json()["token"]}
        model = client.post("/api/models/upload", headers=auth, files={"file": ("我的主播.pt", b"model one")}, data={"name": "主播甲"}).json()
        second = client.post("/api/models/upload", headers=auth, files={"file": ("other.pth", b"model two")}, data={"name": "主播乙"}).json()
        project = client.post("/api/projects/upload", headers=auth, files={"file": ("vod.mp4", b"fake")}).json()
        url = '/api/projects/' + project['id']
        assert client.post(url + '/detect', headers=auth).status_code == 422
        body = {"revision": 0, "clips": [], "detector_model_id": second['id'], "detector_device": "cpu"}
        assert client.put(url, headers=auth, json=body).status_code == 200
        assert client.get(url).json()['detector_model_id'] == second['id']
        response = client.post(url + '/detect', headers=auth)
        assert response.status_code == 202
        job = response.json()
        assert job['model_id'] == second['id'] and job['model_name'] == '主播乙'
        assert job['model_sha256'] == second['sha256'] and job['device'] == 'cpu'
        assert client.get('/api/models/' + model['id'] + '/download').content == b'model one'
    assert len(calls) == 1 and calls[0][0].name == second['sha256'] + '.pt'


def test_training_http_rejects_duplicates_stale_annotation_and_unreviewed_start(setup, monkeypatch):
    from studio.adapters.media import FFmpegMedia
    monkeypatch.setattr(FFmpegMedia, "probe", lambda *args: (60_000, True))
    app = create_app(setup)
    with TestClient(app) as client:
        auth = {"X-Studio-Token": client.get('/api/session').json()['token']}
        dataset = client.post('/api/training/datasets', headers=auth, json={"name": "第一版", "target_name": "小歌"}).json()
        url = '/api/training/datasets/' + dataset['id']
        upload = client.post(url + '/samples', headers=auth, data={"revision": 0}, files={"file": ("sample.mp4", b"unique")})
        assert upload.status_code == 201
        updated = upload.json()
        duplicate = client.post(url + '/samples', headers=auth, data={"revision": updated['revision']}, files={"file": ("renamed.mp4", b"unique")})
        assert duplicate.status_code == 422
        sample = updated['samples'][0]
        data = {"revision": 0, "reviewed": True, "split": "train", "regions": [{"start_ms": 0, "end_ms": 5000, "label": "target"}]}
        assert client.put(url + '/samples/' + sample['id'], headers=auth, json=data).status_code == 409
        assert client.post(url + '/train', headers=auth, json={"revision": updated['revision'], "settings": {}}).status_code == 422
        assert app.state.training.jobs() == []
