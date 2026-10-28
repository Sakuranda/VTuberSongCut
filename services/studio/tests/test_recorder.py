import base64
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from studio.recorder import RecorderClient, RecorderConfig, RecorderQueueFull, RecorderService, RecorderUnavailable
from studio.recording_api import recording_router
from studio.store import ConflictError, Store


def event(relative='room/live.flv', **changes):
    data = dict(SessionId=str(uuid4()), RoomId=123, FileSize=4, Duration=10.0,
                RelativePath=relative, FileOpenTime='2026-10-28T12:00:00+08:00',
                FileCloseTime='2026-10-28T12:00:10+08:00', Name='Singer', Title='Live')
    data.update(changes)
    return dict(EventId=str(uuid4()), EventType='FileClosed', EventTimestamp=data['FileCloseTime'], EventData=data)


@pytest.fixture
def service(tmp_path):
    root = tmp_path / 'recordings'
    (root / 'room').mkdir(parents=True)
    (root / 'room/live.flv').write_bytes(b'live')
    imported = []
    config = RecorderConfig('http://127.0.0.1:9', 'recorder', 'private-password', 'x' * 32, root, stability_seconds=0)
    def register(path, metadata, project_id):
        imported.append((path, metadata, project_id))
        return project_id
    service = RecorderService(config, Store(tmp_path / 'studio.sqlite3'), register)
    service.imported = imported
    yield service
    service.close()


def test_defaults_disabled_and_config_rejects_untrusted_targets(monkeypatch):
    monkeypatch.delenv('SONGCUT_RECORDER_URL', raising=False)
    assert not RecorderConfig.load().enabled
    monkeypatch.setenv('SONGCUT_RECORDER_URL', 'http://user:secret@localhost:2356')
    with pytest.raises(ValueError): RecorderConfig.load()
    monkeypatch.setenv('SONGCUT_RECORDER_URL', 'http://localhost:2356')
    with pytest.raises(ValueError): RecorderConfig.load()


def test_durable_duplicate_events_and_out_of_order_session_events(service):
    e = event()
    assert service.accept({'EventType': 'SessionEnded'})['ignored']
    accepted = service.accept(e)
    assert service.accept(e)['duplicate']
    duplicate = {**e, 'EventId': str(uuid4())}
    assert service.accept(duplicate)['duplicate']
    assert service.process_one()
    assert len(service.imported) == 1
    assert service.recordings()[0]['project_id'] == accepted['recording_id']
    assert service.recordings()[0]['status'] == 'ready'
    assert 'relative_path' not in service.recordings()[0]
    assert not service.process_one()
    changed = {**e, 'EventData': {**e['EventData'], 'Title': 'changed'}}
    with pytest.raises(ConflictError): service.accept(changed)
    assert (service.config.root / 'room/live.flv').read_bytes() == b'live'


@pytest.mark.parametrize('path', ['../outside.flv', '/etc/secret.mp4', 'C:\\private.mp4', 'room/../private.mp4',
                                  'room//live.flv', 'room/config.json', 'room\x00/live.flv'])
def test_path_boundaries(service, path):
    with pytest.raises(ValueError): service.accept(event(path))
    assert not service.recordings()


def test_symlink_parent_and_link_replacement_are_rejected(service, tmp_path):
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'outside/live.flv').write_bytes(b'else')
    try: (service.config.root / 'link').symlink_to(tmp_path / 'outside', target_is_directory=True)
    except OSError: pytest.skip('symlink unavailable')
    with pytest.raises(ValueError): service.accept(event('link/live.flv'))
    service.accept(event())
    source = service.config.root / 'room/live.flv'
    source.unlink()
    source.symlink_to(tmp_path / 'outside/live.flv')
    service.process_one()
    assert not service.imported
    assert (tmp_path / 'outside/live.flv').read_bytes() == b'else'


def test_incomplete_size_does_not_import_and_retries_are_bounded(service):
    accepted = service.accept(event(FileSize=100))
    for _ in range(3):
        with service.store.connect() as db: db.execute('UPDATE recordings SET next_attempt=0')
        service.process_one()
    row = service.recordings()[0]
    assert row['status'] == 'failed' and row['attempts'] == 3 and not service.imported
    for _ in range(3):
        service.retry(accepted['recording_id'])
        service.process_one()
    with pytest.raises(ConflictError): service.retry(accepted['recording_id'])
    assert service.recordings()[0]['attempts'] == 6


def test_queue_limit_and_path_reuse(service):
    service.config = replace(service.config, queue_limit=1)
    first = event()
    service.accept(first)
    assert service.accept(first)['duplicate']
    with pytest.raises(RecorderQueueFull): service.accept(event('room/second.flv'))
    service.process_one()
    with pytest.raises(ConflictError): service.accept(event())


def test_restart_replays_deterministic_project_id(service):
    e = event()
    ident = service.accept(e)['recording_id']
    row = service._claim()  # Crash after claim, before callback result persisted.
    assert row['id'] == ident
    service.start()
    deadline = time.time() + 3
    while time.time() < deadline and service.recordings()[0]['status'] != 'ready': time.sleep(.02)
    assert service.recordings()[0]['status'] == 'ready'
    assert service.imported[0][2] == ident


def test_webhook_auth_size_and_error_statuses(service):
    app = FastAPI()
    app.include_router(recording_router(service))
    @app.exception_handler(ValueError)
    async def invalid(request, exc): return JSONResponse({'detail': str(exc)}, status_code=422)
    @app.exception_handler(ConflictError)
    async def conflict(request, exc): return JSONResponse({'detail': str(exc)}, status_code=409)
    with TestClient(app) as client:
        path = '/api/recorder/webhook'
        assert client.post(path, json=event()).status_code == 403
        assert client.post(path + '?token=中文', json=event()).status_code == 403
        path += '?token=' + service.config.secret
        assert client.post(path, headers={'Origin': 'http://attacker'}, json=event()).status_code == 403
        assert client.post(path, content='x' * 65537).status_code == 413
        assert client.post(path, content='{bad').status_code == 422
        response = client.post(path, json=event())
        assert response.status_code == 202
        assert not service.imported  # Request only persisted; no probe/encoding here.
        assert client.get('/api/recordings').json()[0]['status'] == 'queued'
        assert 'password' not in client.get('/api/recorder/status').text


@pytest.fixture
def fake_recorder():
    state = {'rooms': [], 'calls': [], 'auth': [], 'global': {'optionalWebHookUrlsV2': {'hasValue': False, 'value': None}}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self):
            body = json.loads(self.rfile.read(int(self.headers.get('content-length', 0))) or 'null')
            state['calls'].append((self.command, self.path, body))
            state['auth'].append(self.headers.get('Authorization'))
            if self.path == '/api/config/global':
                if self.command == 'POST': state['global'].update(body)
                payload = json.dumps(state['global']).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
                return
            room_id = int(self.path.split('/')[3]) if len(self.path.split('/')) > 3 else None
            room = next((r for r in state['rooms'] if r['roomId'] == room_id), None)
            if self.command == 'POST' and self.path == '/api/room':
                room = {'roomId': body['roomId'], 'autoRecord': body['autoRecord'], 'name': 'Singer'}
                state['rooms'].append(room)
            elif self.command == 'POST' and self.path.endswith('/config'):
                room['autoRecord'] = body['autoRecord']
            elif self.command == 'POST': room['recording'] = self.path.endswith('/start')
            response = state['rooms'] if self.command == 'GET' and self.path == '/api/room' else room
            payload = json.dumps(response).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
        do_GET = respond
        do_POST = respond
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_port}', state
    server.shutdown(); server.server_close(); thread.join()


def test_official_rest_contract_over_real_http_and_room_limit(service, fake_recorder):
    url, state = fake_recorder
    client = RecorderClient(replace(service.config, url=url))
    try:
        assert client.rooms() == []
        assert client.add(123)['auto_record']
        assert client.action(123, 'start')['recording']
        with pytest.raises(ConflictError): client.add(456)
        assert not client.action(123, 'stop')['recording']
        assert not client.configure(123, False)['auto_record']
        assert client.add(456)['room_id'] == 456
        expected = 'Basic ' + base64.b64encode(b'recorder:private-password').decode()
        assert all(auth == expected for auth in state['auth'])
        assert ('POST', '/api/room/123/config', {'autoRecord': False}) in state['calls']
    finally: client.close()


def test_configure_webhook_preserves_other_callbacks_and_is_idempotent(service, fake_recorder):
    url, state = fake_recorder
    state['global']['optionalWebHookUrlsV2'] = {'hasValue': True, 'value': 'https://other.example/events\nhttp://songcut:8765/api/recorder/webhook?token=old'}
    client = RecorderClient(replace(service.config, url=url))
    try:
        assert client.configure_webhook('http://songcut:8765') == {'configured': True, 'retained_other_hooks': 1}
        assert client.configure_webhook('http://songcut:8765')['configured']
        value = state['global']['optionalWebHookUrlsV2']
        assert value == {'hasValue': True, 'value': 'https://other.example/events\nhttp://songcut:8765/api/recorder/webhook?token=' + service.config.secret}
        assert sum(method == 'POST' for method, _, _ in state['calls']) == 1
        with pytest.raises(ValueError): client.configure_webhook('http://user:secret@songcut:8765')
    finally: client.close()


def test_recorder_disk_guard_blocks_starts_pauses_and_never_auto_resumes(service, monkeypatch):
    from types import SimpleNamespace
    free = [0]
    monkeypatch.setattr('studio.recorder.shutil.disk_usage', lambda path: SimpleNamespace(free=free[0]))
    with pytest.raises(RecorderUnavailable): service.client.add(123)
    with pytest.raises(RecorderUnavailable): service.client.configure(123, True)
    with pytest.raises(RecorderUnavailable): service.client.action(123, 'start')
    rooms = [{'room_id': 123, 'auto_record': True, 'recording': True}]
    calls = []
    monkeypatch.setattr(service.client, 'rooms', lambda: rooms)
    def configure(ident, enabled):
        calls.append(('auto', ident, enabled)); rooms[0]['auto_record'] = enabled
    def action(ident, name):
        calls.append((name, ident)); rooms[0]['recording'] = False
    monkeypatch.setattr(service.client, 'configure', configure)
    monkeypatch.setattr(service.client, 'action', action)
    assert not service.guard_disk()
    assert calls == [('auto', 123, False), ('stop', 123)]
    assert service.status()['disk_warning']
    assert (service.config.root / 'room/live.flv').read_bytes() == b'live'
    free[0] = 10 * 1024**3
    assert service.guard_disk()
    assert len(calls) == 2 and service.status()['disk_warning']  # No silent resume.
    service.acknowledge_disk_warning()
    assert not service.status()['disk_warning']
