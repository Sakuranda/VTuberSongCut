"""Synthetic credentials only: factor lifecycle, durable replay protection and cookie boundaries."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import stat
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request

from studio.access import Access, hash_password, valid_password_hash
from studio.authentication import AuthService, AuthenticationError, COOKIE_HTTPS, COOKIE_LOCAL, FAILURE, matched_step, totp
from studio.auth_api import auth_router
from studio.server_config import ServerConfig
from studio.store import Store

PASSWORD = 'synthetic-password-for-tests-only'
SECRET = base64.b32encode(b'12345678901234567890').decode()


def request(token=None, *, scheme='https', headers=None, ip='192.0.2.1'):
    values = {'host': 'songs.example.com', **(headers or {})}
    if token: values['cookie'] = f'{COOKIE_HTTPS if scheme == "https" else COOKIE_LOCAL}={token}'
    return Request({'type': 'http', 'scheme': scheme, 'server': ('songs.example.com', 443), 'path': '/', 'query_string': b'', 'client': (ip, 123), 'headers': [(k.lower().encode(), v.encode()) for k, v in values.items()]})


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv('SONGCUT_USERS_FILE', raising=False)
    monkeypatch.delenv('SONGCUT_USERS_JSON', raising=False)
    server = ServerConfig(True, 'admin', PASSWORD, 'https://songs.example.com')
    instance = AuthService(server, Access(server), Store(tmp_path / 'studio.sqlite3'), clock=lambda: 1700000000)
    instance.enroll('admin', tmp_path / 'setup.json')
    return instance


def code(service, delta=0):
    return totp(service.factors()['admin']['secret'], service.clock() + delta)


def login(service):
    return service.login('admin', PASSWORD, code(service), request())[0]


@pytest.mark.parametrize('timestamp,expected', [(59, '94287082'), (1111111109, '07081804'), (1111111111, '14050471'), (1234567890, '89005924'), (2000000000, '69279037'), (20000000000, '65353130')])
def test_rfc6238_sha1_vectors(timestamp, expected):
    assert totp(SECRET, timestamp, digits=8) == expected


def test_six_digits_and_one_step_window_only():
    now = 1700000000
    for delta in [-30, 0, 30]:
        assert matched_step(SECRET, totp(SECRET, now + delta), now) is not None
    for delta in [-60, 60]:
        assert matched_step(SECRET, totp(SECRET, now + delta), now) is None
    for bad in [123456, '', '12345', '1234567', '１２３４５６', '123 45', None]:
        assert matched_step(SECRET, bad, now) is None


def test_login_opaque_session_hash_only_and_per_session_csrf(service):
    token = login(service)
    assert len(token) == 43
    identity = service.identify(request(token))
    assert (identity.name, identity.role) == ('admin', 'admin')
    assert service.csrf(request(token)) and service.csrf(request(token)) != token
    with service.store.connect() as db:
        row = db.execute('SELECT * FROM auth_sessions').fetchone()
    assert token not in row and PASSWORD not in row
    assert service.status(request(token)) == {'server': True, 'mode': 'totp', 'authenticated': True, 'setup_required': False, 'user': {'name': 'admin', 'role': 'admin'}}
    basic = base64.b64encode(f'admin:{PASSWORD}'.encode()).decode()
    assert service.identify(request(headers={'authorization': 'Basic ' + basic})) is None
    assert not service.server.authenticate('Basic ' + basic)


def test_replay_survives_restart_and_competing_workers(service):
    second = AuthService(service.server, service.access, service.store, clock=service.clock)
    def attempt(instance):
        try: return instance.login('admin', PASSWORD, code(instance), request())[0]
        except AuthenticationError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [service, second]))
    assert sum(result is not None for result in results) == 1
    restarted = AuthService(service.server, service.access, service.store, clock=service.clock)
    with pytest.raises(AuthenticationError): restarted.login('admin', PASSWORD, code(service), request())
    assert restarted.identify(request(next(x for x in results if x))) is not None


def test_expiry_idle_absolute_and_session_unique_csrf(service):
    token = login(service)
    service.clock = lambda: 1700000030
    token2 = login(service)
    assert service.csrf(request(token)) != service.csrf(request(token2))
    service.clock = lambda: 1700000030 + service.idle_seconds
    assert service.identify(request(token2)) is None
    service.clock = lambda: 1700000000
    # Advance the session periodically to ensure the absolute limit still wins.
    with service.store.connect() as db:
        db.execute('UPDATE auth_sessions SET seen=?,expires=?', (1700000000 + service.absolute_seconds, 1700000000 + service.absolute_seconds))
    service.clock = lambda: 1700000000 + service.absolute_seconds
    assert service.identify(request(token)) is None


def test_changed_password_and_factor_reset_invalidate_sessions(service, tmp_path):
    token = login(service)
    service.access.accounts['admin']['password'] = 'another-synthetic-password-for-test'
    assert service.identify(request(token)) is None
    service.access.accounts['admin']['password'] = PASSWORD
    service.clock = lambda: 1700000030
    token = login(service)
    old = code(service)
    old_factor = service.factors()['admin']
    service.enroll('admin', tmp_path / 'reset.json', replace=True)
    assert service.identify(request(token)) is None
    assert service.factors()['admin'] != old_factor
    service.disable_factor('admin')
    assert service.status(request())['setup_required']
    with pytest.raises(AuthenticationError): service.login('admin', PASSWORD, old, request())


def test_logout_requires_correct_csrf_and_origin(service):
    token = login(service)
    for headers in [{}, {'x-studio-token': 'wrong'}, {'x-studio-token': service.csrf(request(token)), 'origin': 'https://attacker.example'}]:
        with pytest.raises(AuthenticationError): service.logout(request(token, headers=headers))
    csrf = service.csrf(request(token))
    service.logout(request(token, headers={'x-studio-token': csrf, 'origin': service.server.origin}))
    assert service.identify(request(token)) is None


def test_rate_limit_errors_and_persistence(service):
    for name, password, factor in [('unknown', PASSWORD, code(service)), ('admin', 'wrong', code(service)), ('admin', PASSWORD, 'nope')]:
        with pytest.raises(AuthenticationError, match=FAILURE): service.login(name, password, factor, request())
    for _ in range(3):
        with pytest.raises(AuthenticationError) as error: service.login('admin', PASSWORD, 'bad', request())
        assert error.value.status == 401
    restarted = AuthService(service.server, service.access, service.store, clock=service.clock)
    with pytest.raises(AuthenticationError) as error: restarted.login('admin', PASSWORD, code(service), request())
    assert error.value.status == 429 and error.value.retry_after == 900
    service.clock = lambda: 1700000900
    assert login(service)


@pytest.mark.parametrize('bad', [[], {'admin': None}, {'admin': {'secret': 'bad', 'generation': '0'*32}}, {'admin': {'secret': SECRET}}])
def test_malformed_factor_file_fails_closed(service, bad):
    token = login(service)
    service.factor_file.write_text(json.dumps(bad))
    assert service.identify(request(token)) is None
    assert service.status(request())['setup_required']
    with pytest.raises(AuthenticationError) as error: service.login('admin', PASSWORD, '123456', request())
    assert error.value.status == 503


def test_private_enrollment_never_overwrites_and_revoke(service, tmp_path):
    if os.name != 'nt':
        assert stat.S_IMODE(service.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(service.factor_file.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / 'setup.json').stat().st_mode) == 0o600
    before = service.factor_file.read_bytes()
    with pytest.raises(ValueError): service.enroll('admin', tmp_path / 'unused.json')
    with pytest.raises(FileExistsError): service.enroll('admin', tmp_path / 'setup.json', replace=True)
    assert service.factor_file.read_bytes() == before
    token = login(service)
    service.revoke('admin')
    assert service.identify(request(token)) is None


def test_scrypt_hash_replaces_plaintext_without_default_password_bypass(service, monkeypatch):
    password_hash = hash_password('hashed-synthetic-password-value')
    assert valid_password_hash(password_hash)
    monkeypatch.setenv('SONGCUT_USERS_JSON', json.dumps({'admin': {'password_hash': password_hash, 'role': 'admin'}}))
    access = Access(service.server)
    basic_server = __import__('dataclasses').replace(service.server, auth_mode='basic')
    default_header = 'Basic ' + base64.b64encode(f'admin:{PASSWORD}'.encode()).decode()
    assert not basic_server.authenticate(default_header)
    assert access.verify_credentials('admin', PASSWORD) is None
    assert access.verify_credentials('admin', 'hashed-synthetic-password-value').admin
    assert access.verify_credentials('missing', PASSWORD) is None


def test_router_https_json_bounds_cookie_flags_and_logout(service):
    app = FastAPI(); app.include_router(auth_router(service))
    with TestClient(app, base_url=service.server.origin) as client:
        payload = {'username': 'admin', 'password': PASSWORD, 'code': code(service)}
        assert client.post('/api/auth/login', content='x', headers={'content-type': 'text/plain'}).status_code == 415
        assert client.post('/api/auth/login', content='x'*8193, headers={'content-type': 'application/json'}).status_code == 413
        assert client.post('/api/auth/login', content='{', headers={'content-type': 'application/json'}).status_code == 422
        assert client.post('/api/auth/login', json={}, headers={'origin': 'https://attacker.example'}).status_code == 403
        assert client.post('/api/auth/login', json=payload, headers={'sec-fetch-site': 'cross-site'}).status_code == 403
        response = client.post('/api/auth/login', json=payload)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        cookie = response.headers['set-cookie']
        assert COOKIE_HTTPS in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie and 'Path=/' in cookie and 'Domain=' not in cookie
        assert client.get('/api/auth/status').json()['authenticated']
        assert client.post('/api/auth/logout').status_code == 403
        token = client.cookies.get(COOKIE_HTTPS)
        assert client.post('/api/auth/logout', headers={'x-studio-token': service.csrf(request(token))}).status_code == 200
        assert not client.get('/api/auth/status').json()['authenticated']
    with TestClient(app, base_url='http://songs.example.com') as client:
        assert client.get('/api/auth/status').status_code == 403
        assert client.get('/api/auth/status', headers={'x-forwarded-proto': 'https'}).status_code == 403
        assert client.post('/api/auth/login', json=payload).status_code == 403
        assert service.identify(request('a'*43, scheme='http')) is None


def test_config_default_totp_and_reject_public_http(monkeypatch):
    monkeypatch.setenv('SONGCUT_SERVER', '1'); monkeypatch.setenv('SONGCUT_SERVER_USERNAME', 'admin'); monkeypatch.setenv('SONGCUT_SERVER_PASSWORD', PASSWORD)
    monkeypatch.delenv('SONGCUT_AUTH_MODE', raising=False)
    monkeypatch.setenv('SONGCUT_PUBLIC_ORIGIN', 'https://songs.example.com')
    assert ServerConfig.load().auth_mode == 'totp'
    monkeypatch.setenv('SONGCUT_PUBLIC_ORIGIN', 'http://songs.example.com')
    with pytest.raises(ValueError): ServerConfig.load()


def test_cli_offline_enrollment_reset_and_disable_with_no_stdout_secret(service, tmp_path):
    env = {**os.environ, 'SONGCUT_SERVER': '1', 'SONGCUT_SERVER_USERNAME': 'admin',
           'SONGCUT_SERVER_PASSWORD': PASSWORD, 'SONGCUT_PUBLIC_ORIGIN': service.server.origin,
           'PYTHONPATH': str(__import__('pathlib').Path(__file__).resolve().parents[1])}
    output = tmp_path / 'cli-setup.json'
    command = [sys.executable, '-m', 'studio.authentication', '--data', str(tmp_path)]
    result = subprocess.run(command + ['enroll', '--default-admin', '--replace', '--output', str(output)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    bundle = json.loads(output.read_text())
    assert bundle['secret'] not in result.stdout + result.stderr
    assert bundle['otpauth_uri'] not in result.stdout + result.stderr
    assert bundle['otpauth_uri'].startswith('otpauth://totp/')
    assert 'digits=6' in bundle['otpauth_uri'] and 'period=30' in bundle['otpauth_uri']
    assert service.factors()['admin']['secret'] == bundle['secret']
    result = subprocess.run(command + ['revoke', '--username', 'admin', '--factor'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert service.factors() == {}


def test_enrollment_output_cannot_corrupt_auth_files(service):
    with pytest.raises(ValueError): service.enroll('admin', service.directory / 'bad.json', replace=True)


def test_unicode_credentials_return_generic_failure(service):
    for name, password in [('\ud800', PASSWORD), ('admin', '\ud800')]:
        with pytest.raises(AuthenticationError, match=FAILURE):
            service.login(name, password, code(service), request())


def test_idempotent_enrollment_keeps_factor_output_and_sessions(service, tmp_path):
    token = login(service)
    factor_before = service.factor_file.read_bytes()
    original_output = tmp_path / 'setup.json'
    output_before = original_output.read_bytes()
    assert service.enroll('admin', original_output, if_missing=True) is None
    assert original_output.read_bytes() == output_before
    absent_output = tmp_path / 'not-created.json'
    assert service.enroll('admin', absent_output, if_missing=True) is None
    assert not absent_output.exists()
    assert service.factor_file.read_bytes() == factor_before
    assert service.identify(request(token)) is not None
    with pytest.raises(ValueError): service.enroll('admin', absent_output, replace=True, if_missing=True)


def test_idempotent_missing_factor_never_overwrites_occupied_output(service, tmp_path):
    service.disable_factor('admin')
    occupied = tmp_path / 'occupied.json'
    occupied.write_text('preserve existing data')
    with pytest.raises(FileExistsError): service.enroll('admin', occupied, if_missing=True)
    assert occupied.read_text() == 'preserve existing data'
    assert service.factors() == {}
    output = tmp_path / 'fresh.json'
    assert service.enroll('admin', output, if_missing=True) == output
    assert json.loads(output.read_text())['secret'] == service.factors()['admin']['secret']


def test_idempotent_cli_existing_factor_does_not_require_output_file(service, tmp_path):
    env = {**os.environ, 'SONGCUT_SERVER': '1', 'SONGCUT_SERVER_USERNAME': 'admin',
           'SONGCUT_SERVER_PASSWORD': PASSWORD, 'SONGCUT_PUBLIC_ORIGIN': service.server.origin,
           'PYTHONPATH': str(__import__('pathlib').Path(__file__).resolve().parents[1])}
    output = tmp_path / 'absent-cli-output.json'
    before = service.factor_file.read_bytes()
    command = [sys.executable, '-m', 'studio.authentication', '--data', str(tmp_path), 'enroll', '--default-admin', '--if-missing', '--output', str(output)]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'kept unchanged' in result.stdout
    assert not output.exists() and service.factor_file.read_bytes() == before
    assert service.factors()['admin']['secret'] not in result.stdout + result.stderr
    service.disable_factor('admin'); output.write_text('occupied')
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert output.read_text() == 'occupied' and service.factors() == {}
