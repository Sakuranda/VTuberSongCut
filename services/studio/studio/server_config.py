"""Explicit server opt-in; desktop remains bound to loopback without a password."""
from dataclasses import dataclass
import base64
import binascii
import hashlib
import hmac
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ServerConfig:
    enabled: bool = False
    username: str = ""
    password: str = ""
    origin: str = ""
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def load(cls):
        enabled = os.environ.get("SONGCUT_SERVER", "0") == "1"
        host = os.environ.get("SONGCUT_HOST", "127.0.0.1")
        port = int(os.environ.get("SONGCUT_PORT", "8765"))
        if not 1 <= port <= 65535:
            raise ValueError("SONGCUT_PORT must be between 1 and 65535")
        if not enabled:
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Non-loopback binding requires SONGCUT_SERVER=1 and server credentials")
            return cls(host=host, port=port)
        username = os.environ.get("SONGCUT_SERVER_USERNAME", "")
        password = os.environ.get("SONGCUT_SERVER_PASSWORD", "")
        if not username or ":" in username or not password or len(password) < 16:
            raise ValueError("Server mode requires SONGCUT_SERVER_USERNAME and a password of at least 16 characters")
        origin = os.environ.get("SONGCUT_PUBLIC_ORIGIN", "http://localhost:8765")
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or "*" in origin):
            raise ValueError("SONGCUT_PUBLIC_ORIGIN must be one exact http(s) origin")
        # Reject invalid/out-of-range ports at startup, before accepting traffic.
        parsed.port
        return cls(True, username, password, origin.rstrip("/"), host, port)

    @property
    def allowed_hosts(self):
        if self.enabled:
            return list({urlsplit(self.origin).hostname, "127.0.0.1", "localhost"})
        return ["127.0.0.1", "localhost", "testserver"]

    @property
    def allowed_origins(self):
        if self.enabled:
            return {self.origin}
        return {"http://127.0.0.1:8765", "http://localhost:8765",
                "http://127.0.0.1:5173", "http://localhost:5173"}

    def authenticate(self, authorization: str):
        """Compare fixed-length digests; never include supplied credentials in errors."""
        try:
            scheme, encoded = authorization.split(" ", 1)
            if scheme.lower() != "basic" or len(encoded) > 8192:
                return False
            supplied = base64.b64decode(encoded, validate=True).decode("utf-8")
            username, password = supplied.split(":", 1)
        except (ValueError, UnicodeError, binascii.Error):
            return False
        user_matches = hmac.compare_digest(hashlib.sha256(username.encode()).digest(),
                                           hashlib.sha256(self.username.encode()).digest())
        password_matches = hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                                               hashlib.sha256(self.password.encode()).digest())
        return user_matches & password_matches
