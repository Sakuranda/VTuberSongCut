import json
import os
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from studio.api import create_app
from studio.config import Settings
from studio.domain import Job, Project


@pytest.fixture
def cache_app(tmp_path):
    settings = Settings(root=tmp_path, data=tmp_path / 'data', upstream=tmp_path / 'upstream', checkpoint=tmp_path / 'none.pt')
    app = create_app(settings)
    with TestClient(app) as client:
        yield app, client, settings.data


def put(root, name, data=b'1234'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def job(app, root, kind, status='succeeded', name='preview.mp4'):
    record = Job(project_id='project', kind=kind, status=status, artifacts=[name])
    app.state.studio.store.save_job(record)
    return record, put(root, f'jobs/{record.id}/{name}')


def test_preserves_referenced_previews_sources_exports_and_project_revision(cache_app):
    app, client, root = cache_app
    old, disposable = job(app, root, 'preview')
    current, protected = job(app, root, 'preview')
    _, export = job(app, root, 'export', name='clip.mp4')
    _, running = job(app, root, 'preview', 'running')
    _, source = job(app, root, 'preview')
    project = Project(name='draft', source_name='source.mp4', source_file=str(source), duration_ms=1000, preview_file=str(protected))
    app.state.studio.store.create(project)
    result = app.state.cache.inspect()
    assert result['files'] == 1 and result['bytes'] == 4
    assert client.post('/api/cache/clean').status_code == 403
    token = client.get('/api/session').json()['token']
    result = client.post('/api/cache/clean', headers={'x-studio-token': token}).json()
    assert result['removed_files'] == 1 and result['files'] == 0
    assert not disposable.exists()
    for path in (protected, export, running, source):
        assert path.exists()
    assert app.state.studio.store.get(project.id).revision == project.revision
    assert app.state.studio.store.get_job(old.id).artifacts == []
    assert client.get(f'/api/artifacts/{old.id}/preview.mp4').status_code == 404
    assert app.state.cache.clean()['removed_files'] == 0


def test_training_cache_terminal_only_preserves_checkpoints_logs_and_sources(cache_app):
    app, _, root = cache_app
    kept = []
    disposable = []
    for index, status in enumerate(['succeeded', 'failed', 'cancelled', 'interrupted', 'running', 'queued']):
        ident = f'{index:032x}'
        with app.state.studio.store.connect() as db:
            db.execute('INSERT INTO training_jobs VALUES (?,?)', (ident, json.dumps({'id': ident, 'status': status})))
        feature = put(root, f'training/jobs/{ident}/features/sample.mel.npy')
        (disposable if index < 4 else kept).append(feature)
        kept.append(put(root, f'training/jobs/{ident}/runs/model/best.pt'))
        kept.append(put(root, f'training/jobs/{ident}/training.log'))
    kept.append(put(root, 'models/best.pt'))
    kept.append(put(root, 'training/uploads/source.mp4'))
    result = app.state.cache.clean()
    assert result['removed_files'] == 4
    assert all(not p.exists() for p in disposable)
    assert all(p.exists() for p in kept)


def test_age_policy_and_detection_logs(cache_app):
    app, _, root = cache_app
    _, old = job(app, root, 'detect', name='source.probs.npy')
    _, fresh = job(app, root, 'detect', name='source.probs.npy')
    log = put(old.parent, 'detector.log')
    metadata = put(old.parent, 'source.segments.json')
    past = time.time() - 8 * 86400
    os.utime(old, (past, past))
    assert app.state.cache.clean(7)['removed_files'] == 1
    assert not old.exists()
    assert fresh.exists() and log.exists() and metadata.exists()
    future = time.time() + 86400
    os.utime(fresh, (future, future))
    assert app.state.cache.clean()['removed_files'] == 1
    assert not fresh.exists()


def test_training_preview_references_and_pending_job_ownership(cache_app):
    app, _, root = cache_app
    dataset = app.state.training.create_dataset('test', 'singer')
    sid = 'a' * 32
    dataset['samples'] = [{'id': sid, 'path': str(root / 'source.mp4')}]
    with app.state.studio.store.connect() as db:
        db.execute('UPDATE training_datasets SET data=? WHERE id=?', (json.dumps(dataset), dataset['id']))
    used = put(root, f'training/previews/{sid}.mp4')
    unused = put(root, f'training/previews/{"b" * 32}.mp4')
    finished = Job(project_id='training-media:' + sid, kind='training_preview', status='failed')
    active = Job(project_id='training-media:' + sid, kind='training_preview', status='running')
    for record in (finished, active):
        app.state.studio.store.save_job(record)
    orphan = put(root, f'training/previews/.{sid}.{finished.id}.pending-xyz.mp4')
    pending = put(root, f'training/previews/{sid}.{active.id}.pending.mp4')
    unknown = put(root, 'training/previews/do-not-guess.mp4')
    assert app.state.cache.clean()['removed_files'] == 2
    assert not unused.exists() and not orphan.exists()
    assert used.exists() and pending.exists() and unknown.exists()


def test_symlinks_and_referenced_source_inside_cache_are_never_deleted(cache_app):
    app, _, root = cache_app
    record, path = job(app, root, 'preview')
    outside = put(root.parent, 'private.mp4')
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip('symlink unavailable')
    assert app.state.cache.clean()['removed_files'] == 0
    assert outside.exists() and path.is_symlink()


def test_inspection_is_fresh_and_media_http_responses_are_not_cached(cache_app):
    app, client, root = cache_app
    _, path = job(app, root, 'preview')
    assert client.get('/api/cache').json()['files'] == 1
    # A preview selected after an earlier inspection must be protected at cleanup.
    project = Project(name='x', source_name='x', source_file=str(root / 'source.mp4'), duration_ms=1000, preview_file=str(path))
    app.state.studio.store.create(project)
    assert app.state.cache.clean()['removed_files'] == 0
    response = client.get(f'/api/projects/{project.id}/media')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/api/cache').headers['cache-control'] == 'no-store'


def test_cleanup_reports_locked_files_and_continues(cache_app, monkeypatch):
    app, _, root = cache_app
    _, locked = job(app, root, 'detect', name='locked.probs.npy')
    _, disposable = job(app, root, 'detect', name='free.probs.npy')
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == locked:
            raise PermissionError('file in use')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    result = app.state.cache.clean()
    assert result['failed_files'] == 1 and result['removed_files'] == 1
    assert locked.exists() and not disposable.exists()


def test_periodic_worker_sweeps_on_start_and_stops(cache_app):
    from threading import Event
    from studio.cache import RuntimeCache, RETENTION_DAYS
    app, _, root = cache_app
    cache = RuntimeCache(root, app.state.studio.store)
    called = Event()
    ages = []
    def clean(age):
        ages.append(age)
        called.set()
    cache.clean = clean
    cache.start()
    try:
        assert called.wait(2)
    finally:
        cache.close()
    assert ages == [RETENTION_DAYS]
    assert not cache.thread.is_alive()
