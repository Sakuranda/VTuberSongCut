"""Server boundaries: credentials cover native media as well as JSON and writes."""
import base64

from fastapi.testclient import TestClient
import pytest

from studio.api import create_app
from studio.config import Settings
from studio.domain import Project
from studio.server_config import ServerConfig

PASSWORD = "test-only-password-32-characters"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGCUT_SERVER", "1")
    monkeypatch.setenv("SONGCUT_SERVER_USERNAME", "admin")
    monkeypatch.setenv("SONGCUT_SERVER_PASSWORD", PASSWORD)
    monkeypatch.setenv("SONGCUT_PUBLIC_ORIGIN", "https://songs.example.com")
    settings = Settings(root=tmp_path, data=tmp_path / "data", upstream=tmp_path / "upstream", checkpoint=tmp_path / "missing.pt")
    web = tmp_path / "apps/web/dist"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>SongCut test</title>")
    with TestClient(create_app(settings), base_url="https://songs.example.com") as client:
        yield client


def auth():
    return {"Authorization": "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()}


@pytest.mark.parametrize("path", ["/", "/api/session", "/api/projects", "/api/projects/x", "/api/jobs",
    "/api/projects/x/media", "/api/artifacts/x/movie.mp4", "/api/models", "/api/models/x/download",
    "/api/training/datasets", "/api/training/datasets/x/samples/y/media", "/api/training/jobs",
    "/help/faq", "/docs", "/openapi.json"])
def test_server_requires_authentication_for_every_workspace_surface(server, path):
    response = server.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("header", ["", "Bearer wrong", "Basic !!!!", "Basic /w==",
    "Basic " + base64.b64encode(b"admin:wrong").decode(),
    "Basic " + base64.b64encode(f"other:{PASSWORD}".encode()).decode()])
def test_invalid_credentials_are_rejected(server, header):
    assert server.get("/api/session", headers={"Authorization": header}).status_code == 401


def test_health_is_public_but_session_and_writes_require_auth_and_csrf(server):
    assert server.get("/api/health").json()["application"] == "VTuberSongCut"
    headers = auth()
    assert server.get("/", headers=headers).status_code == 200
    session = server.get("/api/session", headers=headers)
    assert session.status_code == 200 and session.headers["cache-control"] == "no-store"
    body = {"name": "Shared dataset", "target_name": "Singer"}
    assert server.post("/api/training/datasets", json=body).status_code == 401
    assert server.post("/api/training/datasets", headers=headers, json=body).status_code == 403
    headers["x-studio-token"] = session.json()["token"]
    headers["Origin"] = "https://songs.example.com"
    assert server.post("/api/training/datasets", headers=headers, json=body).status_code == 201
    assert len(server.get("/api/training/datasets", headers=headers).json()) == 1
    headers["Origin"] = "https://attacker.example"
    assert server.get("/api/session", headers=headers).status_code == 403
    assert server.get("/api/projects", headers={**auth(), "Host": "attacker.example"}).status_code == 400
    assert "access-control-allow-origin" not in session.headers


def test_native_media_range_and_download_are_authenticated_without_csrf_header(server, tmp_path):
    media = tmp_path / "source.wav"
    media.write_bytes(b"0123456789abcdef")
    project = Project(name="Fixture", source_name=media.name, source_file=str(media), duration_ms=1000)
    server.app.state.studio.store.create(project)
    path = f"/api/projects/{project.id}/media"
    assert server.get(path).status_code == 401
    response = server.get(path, headers={**auth(), "Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("updates", [
    {"SONGCUT_SERVER_PASSWORD": ""}, {"SONGCUT_SERVER_PASSWORD": "short"},
    {"SONGCUT_SERVER_USERNAME": ""}, {"SONGCUT_SERVER_USERNAME": "bad:name"},
    {"SONGCUT_PUBLIC_ORIGIN": "*"}, {"SONGCUT_PUBLIC_ORIGIN": "https://*.example.com"},
    {"SONGCUT_PUBLIC_ORIGIN": "https://songs.example.com/path"},
    {"SONGCUT_PUBLIC_ORIGIN": "https://user:pass@songs.example.com"},
    {"SONGCUT_PUBLIC_ORIGIN": "https://songs.example.com:99999"},
])
def test_server_refuses_incomplete_or_unsafe_configuration(monkeypatch, updates):
    monkeypatch.setenv("SONGCUT_SERVER", "1")
    monkeypatch.setenv("SONGCUT_SERVER_USERNAME", "admin")
    monkeypatch.setenv("SONGCUT_SERVER_PASSWORD", PASSWORD)
    for key, value in updates.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        ServerConfig.load()


def test_desktop_cannot_accidentally_bind_publicly(monkeypatch):
    monkeypatch.delenv("SONGCUT_SERVER", raising=False)
    monkeypatch.setenv("SONGCUT_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="Non-loopback"):
        ServerConfig.load()


def test_custom_desktop_port_loads_its_own_page_without_trusting_other_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGCUT_SERVER", "0")
    monkeypatch.setenv("SONGCUT_PORT", "8766")
    settings = Settings(root=tmp_path, data=tmp_path / "data", upstream=tmp_path / "upstream", checkpoint=tmp_path / "missing.pt")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8766") as client:
        assert client.get("/api/session", headers={"Origin": "http://127.0.0.1:8766"}).status_code == 200
        assert client.get("/api/session", headers={"Origin": "http://127.0.0.1:8767"}).status_code == 403
        assert client.get("/api/session", headers={"Origin": "https://attacker.example"}).status_code == 403
