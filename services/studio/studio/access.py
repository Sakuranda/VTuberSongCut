"""Optional named server accounts. Passwords stay in a server-mounted file."""
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


@dataclass(frozen=True)
class Identity:
    name: str
    role: str

    @property
    def admin(self):
        return self.role == 'admin'


class Access:
    def __init__(self, server):
        self.server = server
        self.accounts = {server.username: {'password': server.password, 'role': 'admin'}} if server.enabled else {}
        filename = os.environ.get('SONGCUT_USERS_FILE')
        inline = os.environ.get('SONGCUT_USERS_JSON')
        if filename and inline:
            raise ValueError('Use only one account configuration source')
        if filename or inline:
            values = json.loads(inline if inline else Path(filename).read_text(encoding='utf-8'))
            if not isinstance(values, dict):
                raise ValueError('SONGCUT_USERS_FILE must contain an account object')
            for name, value in values.items():
                if not isinstance(value, dict) or not name or len(name) > 200 or ':' in name or value.get('role') not in {'admin', 'editor', 'viewer'}:
                    raise ValueError('Invalid server account configuration')
                password = value.get('password')
                password_hash = value.get('password_hash')
                if password_hash:
                    if password is not None or not valid_password_hash(password_hash):
                        raise ValueError('Invalid hashed server password configuration')
                elif not isinstance(password, str) or not 16 <= len(password) <= 1024:
                    raise ValueError('Server account passwords need at least 16 characters')
                self.accounts[name] = value
        if server.enabled and not any(a["role"] == "admin" for a in self.accounts.values()):
            raise ValueError("Server accounts must retain an administrator")

    def identify(self, authorization):
        if not self.server.enabled:
            return Identity('local', 'admin')
        try:
            scheme, encoded = authorization.split(' ', 1)
            if scheme.lower() != 'basic' or len(encoded) > 8192:
                return None
            name, password = base64.b64decode(encoded, validate=True).decode().split(':', 1)
            return self.verify_credentials(name, password)
        except (ValueError, UnicodeError):
            return None

    def verify_credentials(self, name, password):
        if not isinstance(name, str) or not isinstance(password, str) or len(name) > 200 or len(password) > 1024:
            return None
        try:
            name.encode('utf-8'); password.encode('utf-8')
        except UnicodeEncodeError:
            return None
        account = self.accounts.get(name)
        if account and account.get('password_hash'):
            valid = verify_password_hash(password, account['password_hash'])
        else:
            # Match the work of hashed accounts so unknown names do not form a timing oracle.
            hashlib.scrypt(password.encode(), salt=b'account-lookup!!', n=16384, r=8, p=1, dklen=32, maxmem=64*1024*1024)
            expected = account.get('password', '') if account else ''
            valid = hmac.compare_digest(hashlib.sha256(password.encode()).digest(), hashlib.sha256(expected.encode()).digest())
        return Identity(name, account['role']) if valid and account else None

    def view(self, user, project):
        return user.admin or project.shared or project.owner == user.name

    def edit(self, user, project):
        return user.admin or (user.role == 'editor' and project.owner == user.name and not project.shared)


def hash_password(password):
    if not isinstance(password, str) or len(password) < 16 or len(password) > 1024:
        raise ValueError('Password needs 16–1024 characters')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32, maxmem=64*1024*1024)
    return 'scrypt$' + salt.hex() + '$' + digest.hex()


def valid_password_hash(value):
    try:
        kind, salt, digest = value.split('$')
        return kind == 'scrypt' and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32
    except (AttributeError, ValueError):
        return False


def verify_password_hash(password, value):
    if not valid_password_hash(value):
        return False
    _, salt, expected = value.split('$')
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32, maxmem=64*1024*1024)
    return hmac.compare_digest(actual, bytes.fromhex(expected))
