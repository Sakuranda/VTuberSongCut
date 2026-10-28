"""Optional named server accounts. Passwords stay in a server-mounted file."""
from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import os
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
                if not isinstance(value, dict) or not name or ':' in name or value.get('role') not in {'admin', 'editor', 'viewer'}:
                    raise ValueError('Invalid server account configuration')
                password = value.get('password')
                if not isinstance(password, str) or len(password) < 16:
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
            account = self.accounts.get(name)
            expected = account['password'] if account else ''
            valid = hmac.compare_digest(hashlib.sha256(password.encode()).digest(), hashlib.sha256(expected.encode()).digest())
            return Identity(name, account['role']) if valid and account else None
        except (ValueError, UnicodeError):
            return None

    def view(self, user, project):
        return user.admin or project.shared or project.owner == user.name

    def edit(self, user, project):
        return user.admin or (user.role == 'editor' and project.owner == user.name and not project.shared)
