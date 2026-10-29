import base64
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from studio.api import create_app
from studio.config import Settings
from studio.domain import Project
from studio.authentication import totp

PASSWORD='synthetic-admin-password-only'


def prepare(tmp_path,monkeypatch):
    monkeypatch.setenv('SONGCUT_SERVER','1')
    monkeypatch.setenv('SONGCUT_AUTH_MODE','totp')
    monkeypatch.setenv('SONGCUT_PUBLIC_ORIGIN','https://songs.example.com')
    monkeypatch.setenv('SONGCUT_SERVER_USERNAME','admin')
    monkeypatch.setenv('SONGCUT_SERVER_PASSWORD',PASSWORD)
    web=tmp_path/'apps/web/dist';(web/'assets').mkdir(parents=True)
    (web/'index.html').write_text('<html>Login shell</html>')
    (web/'assets/app.js').write_text('// public application assets')
    app=create_app(Settings(tmp_path,tmp_path/'data',tmp_path/'upstream',tmp_path/'none.pt'))
    app.state.auth.clock=lambda:1700000000
    app.state.auth.enroll('admin',tmp_path/'synthetic-enrollment.json')
    source=tmp_path/'test.wav';source.write_bytes(b'0123456789abcdef')
    project=Project(name='private fixture',owner='admin',source_name=source.name,source_file=str(source),has_video=False,duration_ms=1000)
    app.state.studio.store.create(project)
    return app,project


def test_gate_covers_direct_api_media_and_recorder_settings_without_basic_bypass(tmp_path,monkeypatch):
    app,project=prepare(tmp_path,monkeypatch)
    with TestClient(app,base_url='https://songs.example.com') as client:
        assert client.get('/').status_code==200
        assert client.get('/assets/app.js').status_code==200
        assert not client.get('/api/auth/status').json()['authenticated']
        basic={'Authorization':'Basic '+base64.b64encode(f'admin:{PASSWORD}'.encode()).decode()}
        for path in ['/api/projects','/api/session',f'/api/projects/{project.id}/media','/api/recorder/settings','/openapi.json']:
            response=client.get(path,headers=basic)
            assert response.status_code==401
            assert 'www-authenticate' not in response.headers
        secret=app.state.auth.factors()['admin']['secret']
        bad=client.post('/api/auth/login',json={'username':'admin','password':PASSWORD,'code':'000000'})
        assert bad.status_code==401
        login=client.post('/api/auth/login',json={'username':'admin','password':PASSWORD,'code':totp(secret,app.state.auth.clock())})
        assert login.status_code==200
        assert 'Secure' in login.headers['set-cookie'] and 'HttpOnly' in login.headers['set-cookie']
        assert client.get('/api/projects').json()[0]['id']==project.id
        media=client.get(f'/api/projects/{project.id}/media',headers={'Range':'bytes=2-5'})
        assert media.status_code==206 and media.content==b'2345'
        assert media.headers['vary']=='Cookie'
        assert client.post(f'/api/projects/{project.id}/clone').status_code==403
        token=client.get('/api/session').json()['token']
        assert client.post(f'/api/projects/{project.id}/clone',headers={'x-studio-token':token}).status_code==201
        assert client.post('/api/auth/logout',headers={'x-studio-token':token}).status_code==200
        assert client.get('/api/projects').status_code==401
        assert client.post('/api/auth/login',json={'username':'admin','password':PASSWORD,'code':totp(secret,app.state.auth.clock())}).status_code==401


@pytest.mark.parametrize('trusted',[False,True])
def test_tls_forwarding_only_from_pinned_proxy(tmp_path,monkeypatch,trusted):
    app,_=prepare(tmp_path,monkeypatch)
    proxy=ProxyHeadersMiddleware(app,trusted_hosts=['172.29.77.2'])
    peer='172.29.77.2' if trusted else '192.0.2.3'
    with TestClient(proxy,base_url='http://songs.example.com',client=(peer,1234)) as client:
        response=client.get('/api/auth/status',headers={'x-forwarded-proto':'https'})
        assert response.status_code==(200 if trusted else 403)


def test_unconfigured_factor_never_falls_back_to_password_only(tmp_path,monkeypatch):
    app,_=prepare(tmp_path,monkeypatch)
    app.state.auth.disable_factor('admin')
    with TestClient(app,base_url='https://songs.example.com') as client:
        assert client.get('/api/auth/status').json()['setup_required']
        response=client.post('/api/auth/login',json={'username':'admin','password':PASSWORD,'code':'123456'})
        assert response.status_code==401
