"""Explicit server opt-in; desktop remains bound to loopback without a password."""
from dataclasses import dataclass
import os
import ipaddress
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ServerConfig:
    enabled: bool = False
    username: str = ""
    password: str = ""
    origin: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    auth_mode: str = "totp"

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
        if not username or len(username) > 200 or ":" in username or not password or not 16 <= len(password) <= 1024:
            raise ValueError("Server mode requires SONGCUT_SERVER_USERNAME and a password of at least 16 characters")
        origin = os.environ.get("SONGCUT_PUBLIC_ORIGIN", "http://localhost:8765")
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or "*" in origin):
            raise ValueError("SONGCUT_PUBLIC_ORIGIN must be one exact http(s) origin")
        # Reject invalid/out-of-range ports at startup, before accepting traffic.
        parsed.port
        mode = os.environ.get("SONGCUT_AUTH_MODE", "totp")
        if mode not in {"totp", "basic"}:
            raise ValueError("SONGCUT_AUTH_MODE must be totp or basic")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Public server origin requires HTTPS")
        value = cls(True, username, password, origin.rstrip("/"), host, port, mode)
        value.trusted_proxy_ips
        return value

    @property
    def trusted_proxy_ips(self):
        values = os.environ.get("SONGCUT_TRUSTED_PROXY_IPS", "127.0.0.1").split(",")
        if not 1 <= len(values) <= 8:
            raise ValueError("Configure only explicit trusted proxy IP addresses")
        return ",".join(str(ipaddress.ip_address(value.strip())) for value in values)

    @property
    def allowed_hosts(self):
        if self.enabled:
            return list({urlsplit(self.origin).hostname, "127.0.0.1", "localhost"})
        return ["127.0.0.1", "localhost", "testserver"]

    @property
    def allowed_origins(self):
        if self.enabled:
            return {self.origin}
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}",
                "http://127.0.0.1:5173", "http://localhost:5173"}

    def authenticate(self, authorization: str):
        """Compatibility helper; TOTP mode never accepts Basic credentials."""
        if not self.enabled or self.auth_mode != "basic":
            return False
        from .access import Access
        return Access(self).identify(authorization) is not None
