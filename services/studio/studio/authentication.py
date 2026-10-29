"""Server-side TOTP sessions. Factors are provisioned only through an operator CLI."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import struct
from threading import Lock
import time
from urllib.parse import quote, urlencode, urlsplit

from .access import Access, Identity, hash_password
from .config import Settings
from .server_config import ServerConfig
from .store import Store

COOKIE_HTTPS = '__Host-songcut-session'
COOKIE_LOCAL = 'songcut-dev-session'
FAILURE = '账号、密码或验证码不正确'


class AuthenticationError(Exception):
    def __init__(self, message=FAILURE, status=401, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def decode_secret(secret):
    if not isinstance(secret, str) or not re.fullmatch(r'[A-Z2-7]{32,128}', secret):
        raise ValueError('Invalid authenticator secret')
    decoded = base64.b32decode(secret + '=' * ((-len(secret)) % 8))
    if len(decoded) < 20:
        raise ValueError('Authenticator secret is too short')
    return decoded


def totp(secret, timestamp, digits=6):
    """RFC 6238 SHA-1; digits parameter exists only for standard test vectors."""
    key = decode_secret(secret)
    step = int(timestamp) // 30
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff
    return str(value % (10 ** digits)).zfill(digits)


def matched_step(secret, code, timestamp):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
        return None
    current = int(timestamp) // 30
    matches = [step for step in (current - 1, current, current + 1) if step >= 0 and hmac.compare_digest(totp(secret, step * 30), code)]
    return max(matches) if matches else None


def write_private(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode() + b'\n'
    if exclusive:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(payload); output.flush(); os.fsync(output.fileno())
        return
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(payload); output.flush(); os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Session:
    identity: Identity
    token: str
    csrf: str
    expires: int


class AuthService:
    def __init__(self, server: ServerConfig, access: Access, store: Store, clock=time.time):
        self.server, self.access, self.store, self.clock = server, access, store, clock
        self.lock = Lock()
        self.directory = store.path.parent / 'auth'
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name != 'nt': self.directory.chmod(0o700)
        self.factor_file = self.directory / 'authenticators.json'
        key_file = self.directory / 'session-key'
        try:
            descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(secrets.token_bytes(32)); output.flush(); os.fsync(output.fileno())
        self.key = key_file.read_bytes()
        if len(self.key) != 32: raise ValueError('Authentication key is invalid; operator repair required')
        if os.name != 'nt': key_file.chmod(0o600)
        self.absolute_seconds = self._bounded_env('SONGCUT_SESSION_HOURS', 8, 1, 72) * 3600
        self.idle_seconds = min(self.absolute_seconds, self._bounded_env('SONGCUT_SESSION_IDLE_MINUTES', 60, 5, 1440) * 60)
        self.secure = urlsplit(server.origin).scheme == 'https'
        self.cookie_name = COOKIE_HTTPS if self.secure else COOKIE_LOCAL
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS auth_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, fingerprint TEXT NOT NULL, created INTEGER NOT NULL, seen INTEGER NOT NULL, expires INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS auth_replay (username TEXT NOT NULL, generation TEXT NOT NULL, last_step INTEGER NOT NULL, PRIMARY KEY(username,generation))')
            db.execute('CREATE TABLE IF NOT EXISTS auth_limits (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, started INTEGER NOT NULL)')

    @staticmethod
    def _bounded_env(name, default, low, high):
        value = int(os.environ.get(name, str(default)))
        if not low <= value <= high: raise ValueError(f'{name} is outside the supported range')
        return value

    def factors(self):
        try: value = json.loads(self.factor_file.read_text(encoding='utf-8'))
        except FileNotFoundError: return {}
        if not isinstance(value, dict): raise ValueError('Authenticator configuration is invalid')
        for name, factor in value.items():
            if not isinstance(name, str) or not isinstance(factor, dict) or not isinstance(factor.get('generation'), str) or not re.fullmatch(r'[0-9a-f]{32}', factor['generation']):
                raise ValueError('Authenticator configuration is invalid')
            decode_secret(factor.get('secret'))
        if os.name != 'nt': self.factor_file.chmod(0o600)
        return value

    def _fingerprint(self, name, factor):
        account = self.access.accounts.get(name)
        if not account or not factor: return None
        decode_secret(factor.get('secret'))
        data = json.dumps([name, account, factor], sort_keys=True, ensure_ascii=False).encode()
        return hmac.new(self.key, b'identity:' + data, hashlib.sha256).hexdigest()

    def _csrf(self, token):
        return hmac.new(self.key, b'csrf:' + token.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, request):
        if not self.server.enabled or self.server.auth_mode != 'totp': return None
        if self.secure and request.url.scheme != 'https': return None
        token = request.cookies.get(self.cookie_name, '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token): return None
        now = int(self.clock())
        digest = self._token_hash(token)
        with self.store.connect() as db:
            row = db.execute('SELECT username,fingerprint,seen,expires FROM auth_sessions WHERE token_hash=?', (digest,)).fetchone()
            if not row: return None
            name, fingerprint, seen, expires = row
            try: current = self._fingerprint(name, self.factors().get(name))
            except (ValueError, OSError): current = None
            if not current or not hmac.compare_digest(fingerprint, current) or expires <= now or seen + self.idle_seconds <= now:
                db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (digest,)); return None
            if now - seen >= 30:
                db.execute('UPDATE auth_sessions SET seen=? WHERE token_hash=?', (now, digest))
        identity = Identity(name, self.access.accounts[name]['role'])
        return Session(identity, token, self._csrf(token), expires)

    def identify(self, request):
        if not self.server.enabled: return Identity('local', 'admin')
        if self.secure and request.url.scheme != 'https': return None
        if self.server.auth_mode == 'basic': return self.access.identify(request.headers.get('authorization', ''))
        session = self.session(request)
        return session.identity if session else None

    def csrf(self, request):
        session = self.session(request)
        return session.csrf if session else None

    def status(self, request):
        identity = self.identify(request)
        result = {'server': self.server.enabled, 'mode': self.server.auth_mode if self.server.enabled else 'local', 'authenticated': bool(identity), 'setup_required': False}
        if identity: result['user'] = {'name': identity.name, 'role': identity.role}
        if self.server.enabled and self.server.auth_mode == 'totp':
            try: result['setup_required'] = not any(name in self.access.accounts for name in self.factors())
            except (ValueError, OSError): result['setup_required'] = True
        return result

    def require_same_origin(self, request):
        if self.secure and request.url.scheme != 'https':
            raise AuthenticationError('请通过 HTTPS 地址登录', 403)
        origin = request.headers.get('origin')
        if (origin and origin not in self.server.allowed_origins) or request.headers.get('sec-fetch-site') == 'cross-site':
            raise AuthenticationError('登录请求来源无效', 403)

    def _buckets(self, username, request):
        ip = request.client.host if request.client else 'unknown'
        def digest(value): return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()
        return [('account:' + digest(username), 5), ('ip:' + digest(ip), 20)]

    def _reserve_attempt(self, username, request, now):
        buckets = self._buckets(username, request)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM auth_limits WHERE started <= ?', (now - 900,))
            db.execute('DELETE FROM auth_sessions WHERE expires <= ? OR seen <= ?', (now, now - self.idle_seconds))
            for bucket, limit in buckets:
                row = db.execute('SELECT count,started FROM auth_limits WHERE bucket=?', (bucket,)).fetchone()
                if row and row[0] >= limit:
                    raise AuthenticationError('尝试次数过多，请稍后再试', 429, max(1, row[1] + 900 - now))
            if db.execute('SELECT COUNT(*) FROM auth_limits').fetchone()[0] >= 4096:
                raise AuthenticationError('登录请求较多，请稍后再试', 429, 60)
            for bucket, _ in buckets:
                db.execute('INSERT INTO auth_limits VALUES (?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1', (bucket, 1, now))
        return buckets

    def login(self, username, password, code, request):
        if not self.server.enabled or self.server.auth_mode != 'totp':
            raise AuthenticationError('当前入口不使用动态验证码登录', 409)
        self.require_same_origin(request)
        if not all(isinstance(v, str) for v in (username, password, code)) or len(username) > 200 or len(password) > 1024 or len(code) > 16:
            raise AuthenticationError()
        try:
            username.encode('utf-8'); password.encode('utf-8')
        except UnicodeEncodeError:
            raise AuthenticationError()
        now = int(self.clock())
        with self.lock:
            buckets = self._reserve_attempt(username, request, now)
            identity = self.access.verify_credentials(username, password)
            try: factor = self.factors().get(username)
            except (ValueError, OSError): raise AuthenticationError('认证配置暂不可用，请联系管理员', 503)
            try:
                step = matched_step(factor['secret'], code, now) if factor else None
                fingerprint = self._fingerprint(username, factor) if identity else None
            except (ValueError, KeyError): step = fingerprint = None
            if not identity or step is None or not fingerprint:
                raise AuthenticationError()
            token = secrets.token_urlsafe(32)
            generation = hashlib.sha256(json.dumps(factor, sort_keys=True).encode()).hexdigest()
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                changed = db.execute('INSERT INTO auth_replay VALUES (?,?,?) ON CONFLICT(username,generation) DO UPDATE SET last_step=excluded.last_step WHERE last_step < excluded.last_step', (username, generation, step)).rowcount
                if changed != 1: raise AuthenticationError()
                # Bound each account's live sessions, retaining the newest seven.
                db.execute('DELETE FROM auth_sessions WHERE username=? AND token_hash NOT IN (SELECT token_hash FROM auth_sessions WHERE username=? ORDER BY created DESC LIMIT 7)', (username, username))
                db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,?,?)', (self._token_hash(token), username, fingerprint, now, now, now + self.absolute_seconds))
                db.execute('DELETE FROM auth_limits WHERE bucket=?', (buckets[0][0],))
                db.execute('UPDATE auth_limits SET count=MAX(0,count-1) WHERE bucket=?', (buckets[1][0],))
            return token, identity

    def logout(self, request):
        self.require_same_origin(request)
        session = self.session(request)
        supplied = request.headers.get('x-studio-token', '')
        if not session or not supplied.isascii() or not hmac.compare_digest(supplied, session.csrf):
            raise AuthenticationError('会话已失效，请重新登录', 403)
        with self.store.connect() as db:
            db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (self._token_hash(session.token),))

    def set_cookie(self, response, token):
        response.set_cookie(self.cookie_name, token, max_age=self.absolute_seconds, secure=self.secure, httponly=True, samesite='lax', path='/')

    def clear_cookie(self, response):
        response.delete_cookie(self.cookie_name, path='/', secure=self.secure, httponly=True, samesite='lax')

    @contextmanager
    def factor_lock(self):
        handle = (self.directory / 'factors.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            handle.close()

    def enroll(self, username, output, replace=False, *, if_missing=False):
        if username not in self.access.accounts: raise ValueError('Unknown configured account')
        if replace and if_missing:
            raise ValueError('Use only one of replace or if_missing')
        output = Path(output)
        with self.factor_lock():
            factors = self.factors()
            if username in factors:
                if if_missing: return None
                if not replace: raise ValueError('Authenticator exists; use explicit --replace to reset it')
            if output.resolve().is_relative_to(self.directory.resolve()):
                raise ValueError('Enrollment output must be outside the private authentication directory')
            secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
            issuer = '拾歌'
            uri = 'otpauth://totp/' + quote(f'{issuer}:{username}', safe='') + '?' + urlencode({'secret': secret, 'issuer': issuer, 'algorithm': 'SHA1', 'digits': 6, 'period': 30})
            bundle = {'username': username, 'issuer': issuer, 'secret': secret, 'otpauth_uri': uri, 'instructions': 'Add this secret to your authenticator, then log in with its six-digit code. Keep this file private.'}
            write_private(output, bundle, exclusive=True)
            try:
                factors[username] = {'secret': secret, 'generation': secrets.token_hex(16)}
                write_private(self.factor_file, factors)
            except BaseException:
                output.unlink(missing_ok=True); raise
            self.revoke(username)
        return output

    def disable_factor(self, username):
        with self.factor_lock():
            factors = self.factors()
            factors.pop(username, None)
            write_private(self.factor_file, factors)
            self.revoke(username)

    def revoke(self, username):
        with self.store.connect() as db:
            db.execute('DELETE FROM auth_sessions WHERE username=?', (username,))


def main():
    parser = argparse.ArgumentParser(description='Private operator setup for SongCut authentication')
    parser.add_argument('--data', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    enroll = commands.add_parser('enroll')
    identity = enroll.add_mutually_exclusive_group(required=True)
    identity.add_argument('--username'); identity.add_argument('--default-admin', action='store_true')
    enroll.add_argument('--output', required=True, type=Path)
    enrollment_mode = enroll.add_mutually_exclusive_group()
    enrollment_mode.add_argument('--replace', action='store_true')
    enrollment_mode.add_argument('--if-missing', action='store_true', help='Keep an existing valid authenticator without writing setup output')
    revoke = commands.add_parser('revoke'); revoke.add_argument('--username', required=True); revoke.add_argument('--factor', action='store_true', help='Also remove the authenticator; login stays disabled until offline enrollment')
    hashed = commands.add_parser('hash-password'); hashed.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'hash-password':
        import getpass
        password = getpass.getpass('New password: ')
        if password != getpass.getpass('Repeat password: '): raise SystemExit('Passwords differ')
        write_private(args.output, {'password_hash': hash_password(password)}, exclusive=True)
        print('Private password hash written to:', args.output)
        return
    config = ServerConfig.load(); access = Access(config)
    data = args.data or Settings.load().data
    service = AuthService(config, access, Store(data / 'studio.sqlite3'))
    if args.command == 'enroll':
        username = config.username if args.default_admin else args.username
        result = service.enroll(username, args.output, args.replace, if_missing=args.if_missing)
        if result is None: print('Authenticator already enrolled; kept unchanged.')
        else: print('Private authenticator setup written to:', result)
    else:
        if args.factor: service.disable_factor(args.username)
        else: service.revoke(args.username)
        print('Authenticator and sessions revoked for:' if args.factor else 'Sessions revoked for:', args.username)


if __name__ == '__main__': main()
