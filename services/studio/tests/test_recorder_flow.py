"""Real media path from authenticated recorder event to shared low preview/draft."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from studio.api import create_app
from studio.adapters.media import FFmpegMedia
from studio.config import Settings


def test_closed_recording_import_preview_and_editor_clone(tmp_path, monkeypatch):
    media = FFmpegMedia()
    if not shutil.which(media.ffprobe) or not Path(media.ffmpeg).is_file():
        pytest.skip('real FFmpeg/ffprobe unavailable')
    recorder_root = tmp_path / 'recordings'
    recorder_root.mkdir()
    source = recorder_root / 'live.flv'
    media.run(['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24', '-f', 'lavfi', '-i',
               'sine=frequency=440:sample_rate=48000', '-t', '1.5', '-c:v', 'libx264', '-preset', 'ultrafast',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)])
    original = hashlib.sha256(source.read_bytes()).digest()
    password = 'integration-only-password-12345'
    users = tmp_path / 'users.json'
    users.write_text(json.dumps({'editor': {'password': password, 'role': 'editor'},
                                 'viewer': {'password': password, 'role': 'viewer'}}))
    for key, value in {'SONGCUT_SERVER': '1', 'SONGCUT_SERVER_USERNAME': 'admin',
                       'SONGCUT_SERVER_PASSWORD': password, 'SONGCUT_PUBLIC_ORIGIN': 'https://songs.example.com',
                       'SONGCUT_USERS_FILE': str(users), 'SONGCUT_RECORDER_URL': 'http://127.0.0.1:9',
                       'SONGCUT_RECORDER_USERNAME': 'recorder', 'SONGCUT_RECORDER_PASSWORD': password,
                       'SONGCUT_RECORDER_WEBHOOK_SECRET': 'x' * 32, 'SONGCUT_RECORDER_ROOT': str(recorder_root),
                       'SONGCUT_DISK_RESERVE_MB': '0', 'SONGCUT_CPU_THREADS': '1'}.items():
        monkeypatch.setenv(key, value)
    def headers(name):
        return {'Authorization': 'Basic ' + base64.b64encode(f'{name}:{password}'.encode()).decode()}
    payload = {'EventType': 'FileClosed', 'EventId': str(uuid4()), 'EventData': {
        'SessionId': str(uuid4()), 'RoomId': 123, 'Name': 'Test singer', 'Title': 'Real media test',
        'RelativePath': source.name, 'FileSize': source.stat().st_size, 'Duration': 1.5,
        'FileOpenTime': '2026-10-28T12:00:00+08:00', 'FileCloseTime': '2026-10-28T12:00:02+08:00'}}
    settings = Settings(tmp_path, tmp_path / 'data', tmp_path / 'upstream', tmp_path / 'none.pt')
    app = create_app(settings)
    with TestClient(app, base_url='https://songs.example.com') as client:
        route = '/api/recorder/webhook?token=' + 'x' * 32
        assert client.post('/api/recorder/webhook', json=payload).status_code == 403
        result = client.post(route, json=payload)
        assert result.status_code == 202
        ident = result.json()['recording_id']
        assert client.post(route, json=payload).json()['duplicate']
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            project = client.get(f'/api/projects/{ident}', headers=headers('editor'))
            if project.status_code == 200 and project.json().get('preview_profile') == 'low320-v1': break
            time.sleep(.05)
        assert project.status_code == 200, project.text
        value = project.json()
        assert value['preview_profile'] == 'low320-v1'
        assert value['shared'] and not value['can_edit'] and value['can_clone']
        assert value['revision'] == 0
        info = media._probe_data(Path(value['preview_file']))
        assert next(s for s in info['streams'] if s['codec_type'] == 'video')['height'] == 320
        default_media = client.get(f'/api/projects/{ident}/media', headers=headers('viewer'))
        assert default_media.status_code == 200
        assert default_media.content == Path(value['preview_file']).read_bytes()
        assert client.get('/api/recorder/status', headers=headers('editor')).status_code == 403
        editor = headers('editor')
        editor['x-studio-token'] = client.get('/api/session', headers=editor).json()['token']
        clone = client.post(f'/api/projects/{ident}/clone', headers=editor)
        assert clone.status_code == 201, clone.text
        draft = clone.json()
        assert draft['owner'] == 'editor' and draft['can_edit'] and not draft['shared']
        assert draft['source_file'] == str(source) and draft['source_project_id'] == ident
        assert draft['preview_file'] == value['preview_file']
        assert client.post(route, json=payload).json()['duplicate']
        projects = app.state.studio.store.projects()
        assert len(projects) == 2 and sum(p.shared for p in projects) == 1
        preview_jobs = [j for j in app.state.studio.store.jobs() if j.kind == 'preview']
        assert len(preview_jobs) == 1
    assert hashlib.sha256(source.read_bytes()).digest() == original
    assert list(recorder_root.iterdir()) == [source]
