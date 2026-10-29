"""Verified BREC Optional DTO mapping, without exposing upstream configuration."""
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import httpx
import pytest

from studio.recorder import RecorderClient, RecorderConfig, RecorderService, RecorderUnavailable
from studio.recorder_settings import RecorderSettingsService
from studio.recording_api import recording_router
from studio.store import Store, ConflictError


class Upstream:
    def __init__(self):
        # Names, enum numbers and units from the official v2.20.0 DTO/defaults.
        self.defaults = dict(recordingQuality='avc10000,hevc10000', cuttingMode=0, cuttingNumber=100,
            cuttingByTitle=False, recordDanmaku=False, recordDanmakuSuperChat=True,
            recordDanmakuGift=False, recordDanmakuGuard=True, saveStreamCover=False,
            timingStreamRetry=6000, timingCheckInterval=180, cookie='')
        self.current = {'optional' + key[0].upper() + key[1:]: {'hasValue': False, 'value': None}
                        for key in self.defaults}
        self.current.update(optionalCookie={'hasValue': True, 'value': 'SESSDATA=private-existing-cookie'},
                            optionalWebHookUrlsV2={'hasValue': True, 'value': 'http://private/hook?token=never-return'},
                            optionalUserScript={'hasValue': True, 'value': 'private script must stay'},
                            optionalFileNameRecordTemplate={'hasValue': True, 'value': 'private-path-template'})
        self.calls = []
        self.ignore_write = False

    def dispatch(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        if path == '/api/config/default' and method == 'GET': return deepcopy(self.defaults)
        if path == '/api/config/global':
            if method == 'POST' and not self.ignore_write: self.current.update(deepcopy(body))
            return deepcopy(self.current)
        raise AssertionError(f'unexpected upstream operation {method} {path}')

    def transport(self, request):
        expected = 'Basic ' + base64.b64encode(b'recorder:test-password').decode()
        assert request.headers['authorization'] == expected
        body = json.loads(request.content) if request.content else None
        return httpx.Response(200, json=self.dispatch(request.method, request.url.path, body))


@pytest.fixture
def setup(tmp_path):
    upstream = Upstream()
    config = RecorderConfig('http://recorder.test:2356', 'recorder', 'test-password', 's'*32, tmp_path)
    client = RecorderClient(config)
    client.client.close()
    client.client = httpx.Client(base_url=config.url, auth=(config.username, config.password), transport=httpx.MockTransport(upstream.transport))
    settings = RecorderSettingsService(client)
    yield settings, upstream, client
    client.close()


def edit(settings, **changes):
    current = settings.read()
    values = current['settings']
    values.update(changes)
    return {'revision': current['revision'], 'settings': values}


def test_default_resolution_and_response_never_contains_secrets_or_paths(setup):
    settings, upstream, _ = setup
    result = settings.read()
    assert result['available']
    assert result['settings']['split_mode'] == 'disabled'
    assert result['settings']['retry_seconds'] == 6
    assert result['settings']['check_interval_seconds'] == 180
    assert result['cookie_configured'] is True
    serialized = json.dumps(result)
    for secret in ('private-existing-cookie', 'never-return', 'private script', 'private-path', 'optionalCookie', 'optionalWebHook'):
        assert secret not in serialized
    assert len(result['revision']) == 64
    assert [path for _, path, _ in upstream.calls] == ['/api/config/global', '/api/config/default']


def test_partial_patch_preserves_upstream_settings_and_converts_units(setup):
    settings, upstream, _ = setup
    before = deepcopy(upstream.current)
    request = edit(settings, split_mode='time', split_value=30, recording_quality='10000,HEVC10000', retry_seconds=2.5, record_danmaku=True)
    result = settings.update(request)
    assert result['settings']['recording_quality'] == 'avc10000,hevc10000'
    assert result['settings']['split_mode'] == 'time'
    assert result['settings']['retry_seconds'] == 2.5
    patch = next(body for method, _, body in upstream.calls if method == 'POST')
    assert patch['optionalCuttingMode'] == {'hasValue': True, 'value': 1}
    assert patch['optionalCuttingNumber']['value'] == 30  # minutes, not seconds
    assert patch['optionalTimingStreamRetry']['value'] == 2500
    assert 'optionalCookie' not in patch
    for key in ('optionalCookie', 'optionalWebHookUrlsV2', 'optionalUserScript', 'optionalFileNameRecordTemplate'):
        assert upstream.current[key] == before[key]


def test_cookie_keep_replace_and_explicit_clear_are_write_only(setup):
    settings, upstream, _ = setup
    request = edit(settings)
    request['cookie'] = '   '
    settings.update(request)
    assert upstream.current['optionalCookie']['value'] == 'SESSDATA=private-existing-cookie'
    request = edit(settings)
    request['cookie'] = 'Cookie: SESSDATA=replacement-secret; other=value'
    result = settings.update(request)
    assert upstream.current['optionalCookie']['value'] == 'SESSDATA=replacement-secret; other=value'
    assert result['cookie_configured']
    assert 'replacement-secret' not in json.dumps(result)
    request = edit(settings); request['clear_cookie'] = True
    result = settings.update(request)
    assert upstream.current['optionalCookie']['value'] == ''
    assert not result['cookie_configured']


def test_changed_mapped_values_or_cookie_reject_stale_snapshot_before_write(setup):
    settings, upstream, _ = setup
    request = edit(settings)
    upstream.current['optionalCookie'] = {'hasValue': True, 'value': 'SESSDATA=changed-elsewhere'}
    with pytest.raises(ConflictError, match='重新读取'):
        settings.update(request)
    assert not any(method == 'POST' for method, _, _ in upstream.calls)


def test_unrelated_native_changes_are_preserved_without_false_conflict(setup):
    settings, upstream, _ = setup
    request = edit(settings, save_cover=True)
    upstream.current['optionalWebHookUrlsV2']['value'] = 'http://other/new-hook'
    settings.update(request)
    assert upstream.current['optionalWebHookUrlsV2']['value'] == 'http://other/new-hook'


@pytest.mark.parametrize('changes', [
    {'split_mode':'time','split_value':1441}, {'split_mode':'size','split_value':15},
    {'recording_quality':'https://private/secret'}, {'retry_seconds':0},
    {'retry_seconds':1.0001}, {'check_interval_seconds':1}, {'record_danmaku':'true'},
    {'optionalUserScript':'arbitrary-code'},
])
def test_invalid_values_do_not_send_any_configuration(setup, changes):
    settings, upstream, _ = setup
    request = edit(settings, **changes)
    with pytest.raises(ValueError, match='录制设置无效'):
        settings.update(request)
    assert not any(method == 'POST' for method, _, _ in upstream.calls)


@pytest.mark.parametrize('payload', [
    {'cookie':'SESSDATA=secret\r\nInjected: header'}, {'cookie': 'secret-without-equals'},
    {'cookie':'SESSDATA=private', 'clear_cookie':True}, {'cookie': {'private':'value'}},
])
def test_invalid_cookie_is_not_reflected_in_error(setup, payload):
    settings, upstream, _ = setup
    request = edit(settings); request.update(payload)
    with pytest.raises(ValueError) as raised:
        settings.update(request)
    assert 'SESSDATA' not in str(raised.value) and 'private' not in str(raised.value)
    assert not any(method == 'POST' for method, _, _ in upstream.calls)


def test_unsupported_configuration_fails_closed_instead_of_inventing_defaults(setup):
    settings, upstream, _ = setup
    upstream.current.pop('optionalRecordingQuality')
    result = settings.read()
    assert not result['available'] and 'settings' not in result and 'revision' not in result
    assert 'private' not in json.dumps(result)


def test_unsuccessful_readback_never_replays_a_write(setup):
    settings, upstream, _ = setup
    request = edit(settings, split_mode='size', split_value=1024)
    upstream.ignore_write = True
    with pytest.raises(ConflictError, match='不会自动重复提交'):
        settings.update(request)
    assert sum(method == 'POST' for method, _, _ in upstream.calls) == 1


def test_bounded_route_validation_masks_inputs_and_does_not_forward_extra_fields(setup, tmp_path):
    settings, upstream, client = setup
    service = RecorderService(client.config, Store(tmp_path/'state.sqlite3'), lambda *_: 'id')
    service.client.close(); service.client = client
    app = FastAPI()
    @app.exception_handler(ValueError)
    async def invalid(request, exc): return JSONResponse({'detail':str(exc)},status_code=422)
    @app.exception_handler(ConflictError)
    async def conflict(request, exc): return JSONResponse({'detail':str(exc)},status_code=409)
    app.include_router(recording_router(service))
    with TestClient(app) as connected:
        current = connected.get('/api/recorder/settings').json()
        body={'revision':current['revision'],'settings':current['settings'],'cookie':'SESSDATA=new-secret'}
        assert connected.put('/api/recorder/settings',json=body).status_code==200
        assert 'new-secret' not in connected.get('/api/recorder/settings').text
        body['settings']['record_danmaku']='bad-secret-value'
        response=connected.put('/api/recorder/settings',json=body)
        assert response.status_code==422 and 'bad-secret-value' not in response.text
        assert connected.put('/api/recorder/settings',content=b'x'*32769).status_code==413
    service.close()


def test_actual_http_global_settings_roundtrip(tmp_path):
    upstream = Upstream()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, data):
            payload=json.dumps(data).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def do_GET(self): self.reply(upstream.dispatch('GET',self.path))
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.reply(upstream.dispatch('POST',self.path,body))
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=httpd.serve_forever,daemon=True);thread.start()
    client=RecorderClient(RecorderConfig(f'http://127.0.0.1:{httpd.server_port}','recorder','test-password','s'*32,tmp_path))
    try:
        settings=RecorderSettingsService(client)
        result=settings.update(edit(settings,split_mode='size',split_value=2048))
        assert result['settings']['split_value']==2048 and result['settings']['split_mode']=='size'
        assert 'private-existing-cookie' not in json.dumps(result)
    finally:
        client.close();httpd.shutdown();httpd.server_close();thread.join()


def test_disabled_split_preserves_inactive_native_value_and_integer_seconds(setup):
    settings, upstream, _ = setup
    upstream.current['optionalCuttingNumber'] = {'hasValue': True, 'value': 0}
    result = settings.update(edit(settings, retry_seconds=6))
    assert result['settings']['split_value'] == 0
    assert result['settings']['retry_seconds'] == 6
    assert upstream.current['optionalCuttingNumber']['value'] == 0


@pytest.mark.parametrize('missing', ['default_cookie', 'override_cookie_value', 'default_quality', 'override_quality_value'])
def test_incomplete_upstream_snapshot_never_allows_a_write(setup, missing):
    settings, upstream, _ = setup
    request = edit(settings, save_cover=True)
    if missing == 'default_cookie':
        upstream.current['optionalCookie'] = {'hasValue': False, 'value': None}
        del upstream.defaults['cookie']
    elif missing == 'override_cookie_value':
        upstream.current['optionalCookie'] = {'hasValue': True}
    elif missing == 'default_quality':
        del upstream.defaults['recordingQuality']
    else:
        upstream.current['optionalRecordingQuality'] = {'hasValue': True}
    assert settings.read()['available'] is False
    with pytest.raises(RecorderUnavailable):
        settings.update(request)
    assert not any(method == 'POST' for method, _, _ in upstream.calls)


def test_unchanged_fields_keep_native_inheritance_and_noop_does_not_write(setup):
    settings, upstream, _ = setup
    before = deepcopy(upstream.current)
    settings.update(edit(settings, save_cover=True))
    patch = next(body for method, _, body in upstream.calls if method == 'POST')
    assert patch == {'optionalSaveStreamCover': {'hasValue': True, 'value': True}}
    assert upstream.current['optionalRecordingQuality'] == before['optionalRecordingQuality']
    upstream.calls.clear()
    settings.update(edit(settings))
    assert not any(method == 'POST' for method, _, _ in upstream.calls)


def test_application_settings_require_admin_session_and_csrf(tmp_path, monkeypatch):
    from studio.api import create_app
    from studio.authentication import totp
    from studio.config import Settings

    password = 'synthetic-recorder-permissions-password'
    for key in ('SONGCUT_USERS_FILE', 'SONGCUT_RECORDER_URL'):
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        'SONGCUT_SERVER': '1', 'SONGCUT_SERVER_USERNAME': 'admin',
        'SONGCUT_SERVER_PASSWORD': password, 'SONGCUT_AUTH_MODE': 'totp',
        'SONGCUT_HOST': '127.0.0.1', 'SONGCUT_PORT': '8765',
        'SONGCUT_PUBLIC_ORIGIN': 'http://127.0.0.1:8765',
        'SONGCUT_USERS_JSON': json.dumps({name: {'password': password, 'role': name} for name in ('editor', 'viewer')}),
        'SONGCUT_RECORDER_URL': 'http://recorder.test:2356',
        'SONGCUT_RECORDER_USERNAME': 'recorder', 'SONGCUT_RECORDER_PASSWORD': 'test-password',
        'SONGCUT_RECORDER_WEBHOOK_SECRET': 's' * 32, 'SONGCUT_RECORDER_ROOT': str(tmp_path),
        'SONGCUT_RECORDER_DISK_RESERVE_MB': '0',
    }.items():
        monkeypatch.setenv(key, value)
    config = Settings(tmp_path, tmp_path / 'data', tmp_path / 'upstream', tmp_path / 'model.pt')
    app = create_app(config)
    upstream = Upstream()
    recorder_client = app.state.recorder.client
    recorder_client.client.close()
    recorder_client.client = httpx.Client(base_url='http://recorder.test:2356', auth=('recorder', 'test-password'), transport=httpx.MockTransport(upstream.transport))
    app.state.auth.clock = lambda: 1700000000
    for name in ('admin', 'editor', 'viewer'):
        app.state.auth.enroll(name, tmp_path / f'{name}-synthetic-setup.json')
    with TestClient(app, base_url='http://127.0.0.1:8765') as connected:
        assert connected.get('/api/recorder/settings').status_code == 401
        assert connected.put('/api/recorder/settings', json={}).status_code == 401
        assert not upstream.calls
        for name in ('viewer', 'editor', 'admin'):
            connected.cookies.clear()
            factor = app.state.auth.factors()[name]
            response = connected.post('/api/auth/login', json={'username': name, 'password': password, 'code': totp(factor['secret'], app.state.auth.clock())})
            assert response.status_code == 200
            token = connected.get('/api/session').json()['token']
            response = connected.get('/api/recorder/settings')
            if name != 'admin':
                assert response.status_code == 403
                assert connected.put('/api/recorder/settings', json={}, headers={'x-studio-token': token}).status_code == 403
                assert not upstream.calls
            else:
                assert response.status_code == 200
                current = response.json()
                current['settings']['save_cover'] = True
                body = {'revision': current['revision'], 'settings': current['settings']}
                assert connected.put('/api/recorder/settings', json=body).status_code == 403
                assert not any(method == 'POST' for method, _, _ in upstream.calls)
                assert connected.put('/api/recorder/settings', json=body, headers={'x-studio-token': token}).status_code == 200
                assert connected.put('/api/recorder/settings', json=body, headers={'x-studio-token': token}).status_code == 409
                assert sum(method == 'POST' for method, _, _ in upstream.calls) == 1
