"""Resource and ownership boundaries for the low-resource shared server."""
import base64
from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from studio.api import create_app
from studio.config import Settings
from studio.domain import Clip, Job, Project
from studio.resources import ResourcePolicy, ResourceLimitError

PASSWORD = 'server-test-only-password-1234'


def credentials(name):
    return {'Authorization': 'Basic ' + base64.b64encode(f'{name}:{PASSWORD}'.encode()).decode()}


def authorized(client, name):
    headers = credentials(name)
    response = client.get('/api/session', headers=headers)
    assert response.status_code == 200
    return {**headers, 'X-Studio-Token': response.json()['token']}


@pytest.fixture
def server(tmp_path, monkeypatch):
    users = tmp_path / 'users.json'
    users.write_text(json.dumps({name: {'password': PASSWORD, 'role': role} for name, role in
                                [('alice', 'editor'), ('bob', 'editor'), ('visitor', 'viewer')]}))
    for key, value in {'SONGCUT_SERVER': '1', 'SONGCUT_SERVER_USERNAME': 'admin',
                       'SONGCUT_SERVER_PASSWORD': PASSWORD, 'SONGCUT_PUBLIC_ORIGIN': 'http://testserver',
                       'SONGCUT_USERS_FILE': str(users), 'SONGCUT_LOW_RESOURCE': '1',
                       'SONGCUT_DISK_RESERVE_MB': '0'}.items():
        monkeypatch.setenv(key, value)
    config = Settings(root=tmp_path, data=tmp_path / 'data', upstream=tmp_path / 'upstream', checkpoint=tmp_path / 'none')
    app = create_app(config)
    with TestClient(app) as client:
        source = config.data / 'original.mp4'
        source.write_bytes(b'original-source-not-proxy')
        shared = Project(name='shared', source_name=source.name, source_file=str(source), duration_ms=60000,
                         source_height=720, source_fps=30, source_browser_compatible=True, shared=True, owner='admin')
        alice = shared.model_copy(update={'id': 'alice-project', 'name': 'alice-private', 'owner': 'alice', 'shared': False})
        bob = shared.model_copy(update={'id': 'bob-project', 'name': 'bob-private', 'owner': 'bob', 'shared': False})
        for project in (shared, alice, bob):
            app.state.studio.store.create(project)
        proxy = app.state.studio.preview_target(shared)
        proxy.parent.mkdir(parents=True, exist_ok=True)
        proxy.write_bytes(b'low320-preview')
        yield app, client, shared, alice, bob


def test_private_projects_and_edits_are_isolated(server):
    app, client, shared, alice, bob = server
    headers = authorized(client, 'alice')
    assert {p['id'] for p in client.get('/api/projects', headers=headers).json()} == {shared.id, alice.id}
    assert client.get(f'/api/projects/{bob.id}', headers=headers).status_code == 404
    assert client.get(f'/api/projects/{bob.id}/media?quality=low', headers=headers).status_code == 404
    assert client.put(f'/api/projects/{shared.id}', headers=headers, json={'revision': 0, 'clips': []}).status_code == 403
    assert client.put(f'/api/projects/{bob.id}', headers=headers, json={'revision': 0, 'clips': []}).status_code == 404
    assert client.put(f'/api/projects/{alice.id}', headers=headers, json={'revision': 0, 'clips': []}).status_code == 200
    assert app.state.studio.store.get(bob.id).revision == 0


def test_shared_recording_clone_is_owned_and_does_not_duplicate_source(server):
    app, client, shared, _, _ = server
    headers = authorized(client, 'alice')
    clone = client.post(f'/api/projects/{shared.id}/clone', headers=headers)
    assert clone.status_code == 201
    clone = clone.json()
    assert clone['owner'] == 'alice' and not clone['shared'] and clone['can_edit']
    assert clone['source_file'] == shared.source_file and clone['source_project_id'] == shared.id
    assert client.get(f'/api/projects/{clone["id"]}', headers=credentials('bob')).status_code == 404
    assert app.state.studio.store.get(shared.id).revision == 0
    assert client.post(f'/api/projects/{shared.id}/clone', headers=authorized(client, 'visitor')).status_code == 403


def test_server_defaults_to_low_preview_and_explicit_source_is_admin_only(server):
    _, client, shared, _, _ = server
    headers = credentials('visitor')
    for suffix in ('', '?quality=low'):
        response = client.get(f'/api/projects/{shared.id}/media{suffix}', headers=headers)
        assert response.status_code == 200
        assert response.content == b'low320-preview'
        assert response.headers['vary'] == 'Authorization'
        assert response.headers['cache-control'].startswith('private,')
    assert client.get(f'/api/projects/{shared.id}/media?quality=source', headers=headers).status_code == 403
    response = client.get(f'/api/projects/{shared.id}/media?quality=source', headers=credentials('admin'))
    assert response.content == b'original-source-not-proxy'


def test_viewers_can_prepare_shared_previews_but_not_edit_and_tokens_are_bound_to_identity(server):
    _, client, shared, alice, _ = server
    viewer = authorized(client, 'visitor')
    assert client.post(f'/api/projects/{shared.id}/preview', headers=viewer).status_code == 202
    assert client.post(f'/api/projects/{shared.id}/detect', headers=viewer).status_code == 403
    alice_headers = authorized(client, 'alice')
    mixed = {**credentials('bob'), 'X-Studio-Token': alice_headers['X-Studio-Token']}
    assert client.post(f'/api/projects/{alice.id}/clone', headers=mixed).status_code == 403
    for path in ('/api/training/datasets', '/api/cache', '/api/recorder/status'):
        assert client.get(path, headers=alice_headers).status_code == 403


def test_low_resource_server_disables_training_and_blocks_expensive_exports(server):
    app, client, _, alice, _ = server
    admin = authorized(client, 'admin')
    capabilities = client.get('/api/capabilities', headers=admin).json()
    assert capabilities['low_resource'] is True and capabilities['training_enabled'] is False
    dataset = app.state.training.create_dataset('test', 'singer')
    response = client.post(f'/api/training/datasets/{dataset["id"]}/train', headers=admin,
                           json={'revision': dataset['revision'], 'settings': {}})
    assert response.status_code == 403
    alice.clips = [Clip(start_ms=0, end_ms=1000)]
    alice.source_fps = 60
    app.state.studio.store.save(alice, 0)
    response = client.post(f'/api/projects/{alice.id}/clips/{alice.clips[0].id}/export', headers=authorized(client, 'alice'))
    assert response.status_code == 422


def test_job_payloads_downloads_and_cancellation_do_not_cross_owners(server):
    app, client, _, alice, bob = server
    store = app.state.studio.store
    job = Job(project_id=bob.id, owner='bob', kind='export', status='succeeded', artifacts=['clip.mp4'],
              task={'secret-project-snapshot': 'not-for-alice'})
    store.save_job(job)
    artifact = store.path.parent / 'jobs' / job.id / 'clip.mp4'
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b'bob-export')
    alice_headers = authorized(client, 'alice')
    assert job.id not in {j['id'] for j in client.get('/api/jobs', headers=alice_headers).json()}
    assert client.get(f'/api/artifacts/{job.id}/clip.mp4', headers=alice_headers).status_code == 404
    assert client.post(f'/api/jobs/{job.id}/cancel', headers=alice_headers).status_code == 403
    own = client.get('/api/jobs', headers=credentials('bob')).json()
    assert len(own) == 1 and 'task' not in own[0]
    assert client.get(f'/api/artifacts/{job.id}/clip.mp4', headers=credentials('bob')).content == b'bob-export'


def test_disk_byte_and_effective_fps_limits(tmp_path, monkeypatch):
    policy = ResourcePolicy(low=True, upload_bytes=4, reserve_bytes=10)
    monkeypatch.setattr('studio.resources.shutil.disk_usage', lambda p: SimpleNamespace(free=100))
    with pytest.raises(ResourceLimitError) as error:
        policy.copy_upload(io.BytesIO(b'12345'), tmp_path / 'over-limit.mp4')
    assert error.value.status == 413
    monkeypatch.setattr('studio.resources.shutil.disk_usage', lambda p: SimpleNamespace(free=9))
    with pytest.raises(ResourceLimitError) as error:
        policy.check_disk(tmp_path)
    assert error.value.status == 507
    project = Project(name='x', source_name='x', source_file='x', duration_ms=10000, source_fps=60, source_height=1080)
    clip = Clip(start_ms=0, end_ms=1000)
    with pytest.raises(ResourceLimitError):
        policy.validate_export(project, clip)
    clip.export_settings.fps = 30
    policy.validate_export(project, clip)


def test_shared_preview_reuse_keeps_requesters_project_context(server):
    from threading import Event
    app, client, _, alice, bob = server
    runner = app.state.studio.runner
    entered, release = Event(), Event()
    def hold(_):
        entered.set()
        assert release.wait(4)
    try:
        runner.submit('hold', 'test', hold, owner='admin')
        assert entered.wait(2)
        bob_response = client.post(f'/api/projects/{bob.id}/preview', headers=authorized(client, 'bob'))
        alice_response = client.post(f'/api/projects/{alice.id}/preview', headers=authorized(client, 'alice'))
        assert bob_response.status_code == alice_response.status_code == 202
        first, reused = bob_response.json(), alice_response.json()
        assert first['id'] == reused['id']
        assert reused['project_id'] == alice.id
        assert 'task' not in reused
        visible = client.get(f'/api/jobs?project_id={alice.id}', headers=credentials('alice')).json()
        assert len(visible) == 1 and visible[0]['id'] == reused['id']
        assert visible[0]['project_id'] == alice.id and 'task' not in visible[0]
    finally:
        release.set()


def test_container_accounts_can_use_environment_without_world_readable_mount(monkeypatch):
    from studio.access import Access
    from studio.server_config import ServerConfig
    import base64
    monkeypatch.delenv('SONGCUT_USERS_FILE', raising=False)
    monkeypatch.setenv('SONGCUT_USERS_JSON', json.dumps({'reader': {'role':'viewer','password':'private-reader-password'}}))
    access = Access(ServerConfig(enabled=True, username='admin', password='private-admin-password'))
    header = 'Basic ' + base64.b64encode(b'reader:private-reader-password').decode()
    identity = access.identify(header)
    assert identity.name == 'reader' and identity.role == 'viewer'
    monkeypatch.setenv('SONGCUT_USERS_FILE', '/must-not-be-read')
    with pytest.raises(ValueError, match='only one'):
        Access(ServerConfig(enabled=True, username='admin', password='private-admin-password'))
