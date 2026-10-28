import asyncio
from pathlib import Path
import pytest
from starlette.requests import Request
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from studio.request_limits import RequestLimits
from studio.resources import ResourcePolicy, ResourceLimitError
from studio.workspace_lease import WorkspaceLease


def scope(path='/api/projects/upload', method='POST', query=b'', headers=None):
    return {'type': 'http', 'asgi': {'version': '3.0'}, 'method': method, 'path': path,
            'raw_path': path.encode(), 'query_string': query, 'headers': headers or [],
            'scheme': 'http', 'server': ('testserver', 80), 'client': ('127.0.0.1', 9999), 'http_version': '1.1'}


async def invoke(app, request_scope, events):
    sent, pending = [], iter(events)
    async def receive():
        return next(pending, {'type': 'http.disconnect'})
    async def send(event):
        sent.append(event)
    await app(request_scope, receive, send)
    return sent


@pytest.mark.parametrize('disk_failure', [False, True])
def test_chunked_multipart_rejected_before_handler_and_spool_closed(tmp_path, monkeypatch, disk_failure):
    import starlette.formparsers as parser
    created, consumed = [], []
    original = parser.SpooledTemporaryFile
    def track(*args, **kwargs):
        file = original(*args, **kwargs)
        created.append(file)
        return file
    monkeypatch.setattr(parser, 'SpooledTemporaryFile', track)
    class Policy:
        upload_bytes = 1
        def check_disk(self, directory):
            if disk_failure and created:
                raise ResourceLimitError('disk full', 507)
    async def endpoint(s, receive, send):
        try:
            async with Request(s, receive).form() as form:
                consumed.append(form)
            await JSONResponse({'ok': True})(s, receive, send)
        except MultiPartException:
            await JSONResponse({'detail': 'parser rejection'}, status_code=400)(s, receive, send)
    middleware = RequestLimits(endpoint, Policy(), tmp_path, server=True)
    prefix = b'--form\r\nContent-Disposition: form-data; name="file"; filename="recording.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
    events = [{'type': 'http.request', 'body': prefix + b'x' * 32, 'more_body': True},
              {'type': 'http.request', 'body': b'x' * (32 if disk_failure else 1024 * 1024 + 1), 'more_body': True},
              {'type': 'http.request', 'body': b'\r\n--form--\r\n', 'more_body': False}]
    sent = asyncio.run(invoke(middleware, scope(headers=[(b'content-type', b'multipart/form-data; boundary=form')]), events))
    assert sent[0]['status'] == (507 if disk_failure else 413)
    assert not consumed and created and all(f.closed for f in created)


def test_under_limit_chunked_upload_reaches_handler_and_checks_both_disks(tmp_path):
    checked = []
    class Policy:
        upload_bytes = 1024
        def check_disk(self, directory): checked.append(Path(directory))
    async def endpoint(s, receive, send):
        body = await Request(s, receive).body()
        await JSONResponse({'size': len(body)})(s, receive, send)
    middleware = RequestLimits(endpoint, Policy(), tmp_path, server=True)
    sent = asyncio.run(invoke(middleware, scope(), [{'type': 'http.request', 'body': b'1234', 'more_body': False}]))
    assert sent[0]['status'] == 200
    assert len(checked) == 2 and tmp_path in checked


def test_active_connections_release_after_response_and_exception(tmp_path, monkeypatch):
    monkeypatch.setenv('SONGCUT_MEDIA_CONNECTIONS', '1')
    monkeypatch.setenv('SONGCUT_MEDIA_MBPS', '0')
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        fail = False
        async def endpoint(s, receive, send):
            entered.set()
            await release.wait()
            if fail: raise RuntimeError('stream failed')
            await JSONResponse({'ok': True})(s, receive, send)
        middleware = RequestLimits(endpoint, ResourcePolicy(), tmp_path, server=True)
        first = asyncio.create_task(invoke(middleware, scope('/api/projects/x/media', 'GET'), []))
        await entered.wait()
        rejected = await invoke(middleware, scope('/api/projects/x/media', 'GET'), [])
        assert rejected[0]['status'] == 429
        release.set()
        await first
        assert middleware.connections == 0
        fail = True
        with pytest.raises(RuntimeError):
            await invoke(middleware, scope('/api/projects/x/media', 'GET'), [])
        assert middleware.connections == middleware.high_connections == 0
    asyncio.run(scenario())


def test_encoded_high_quality_cannot_bypass_hd_connection_limit(tmp_path, monkeypatch):
    monkeypatch.setenv('SONGCUT_HD_CONNECTIONS', '1')
    monkeypatch.setenv('SONGCUT_MEDIA_MBPS', '0')
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def endpoint(s, receive, send):
            entered.set()
            await release.wait()
            await JSONResponse({'ok': True})(s, receive, send)
        middleware = RequestLimits(endpoint, ResourcePolicy(), tmp_path, server=True)
        first = asyncio.create_task(invoke(middleware, scope('/api/projects/x/media', 'GET', b'quality=high'), []))
        await entered.wait()
        try:
            result = await asyncio.wait_for(invoke(middleware, scope('/api/projects/x/media', 'GET', b'quality=%68igh'), []), timeout=.25)
            assert result[0]['status'] == 429
        finally:
            release.set()
            await first
    asyncio.run(scenario())


def test_bandwidth_budget_is_shared_and_configuration_validated(tmp_path, monkeypatch):
    import studio.request_limits as module
    monkeypatch.setenv('SONGCUT_MEDIA_MBPS', '8')
    elapsed, sleeps = [100.0], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: elapsed[0])
    async def advance(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds
    monkeypatch.setattr(module.asyncio, 'sleep', advance)
    async def endpoint(s, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'x' * 1000000, 'more_body': False})
    middleware = RequestLimits(endpoint, ResourcePolicy(), tmp_path, server=True)
    async def scenario():
        await invoke(middleware, scope('/api/projects/x/media', 'GET'), [])
        await invoke(middleware, scope('/api/projects/y/media', 'GET'), [])
    asyncio.run(scenario())
    assert sleeps == [1.0]
    monkeypatch.setenv('SONGCUT_MEDIA_MBPS', '-1')
    with pytest.raises(ValueError): RequestLimits(endpoint, ResourcePolicy(), tmp_path, server=True)


def test_workspace_lease_exclusive_until_owner_releases(tmp_path):
    owner, other = WorkspaceLease(tmp_path), WorkspaceLease(tmp_path)
    owner.acquire()
    try:
        with pytest.raises(RuntimeError): other.acquire()
        assert other.file is None
        other.close()
        with pytest.raises(RuntimeError): other.acquire()
    finally: owner.close()
    other.acquire()
    other.close()
    other.close()


def test_second_api_instance_cannot_recover_first_instances_jobs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from studio.api import create_app
    from studio.config import Settings
    from studio.domain import Job
    monkeypatch.setenv('SONGCUT_SERVER', '0')
    monkeypatch.setenv('SONGCUT_DISK_RESERVE_MB', '0')
    settings = Settings(root=tmp_path, data=tmp_path / 'data', upstream=tmp_path / 'upstream', checkpoint=tmp_path / 'none')
    app = create_app(settings)
    with TestClient(app):
        record = Job(project_id='running-owner', kind='test', status='running')
        app.state.studio.store.save_job(record)
        with pytest.raises(RuntimeError, match='工作空间已有'):
            create_app(settings)
        assert app.state.studio.store.get_job(record.id).status == 'running'
    # The original app releases its lock on ordinary lifespan shutdown.
    second = create_app(settings)
    with TestClient(second):
        assert second.state.studio.store.get_job(record.id).status == 'interrupted'
