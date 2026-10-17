"""Real launcher readiness is published only after its own loopback bind succeeds."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import pytest


ROOT = Path(__file__).resolve().parents[3]


def launcher(tmp_path, port, log):
    environment = {**os.environ, "SONGCUT_SERVER": "0", "SONGCUT_HOST": "127.0.0.1",
                   "SONGCUT_PORT": str(port), "SONGCUT_ROOT": str(ROOT), "SONGCUT_DATA": str(tmp_path / "data"),
                   "SONGCUT_READY_FILE": str(tmp_path / "ready.json"),
                   "SONGCUT_SHUTDOWN_FILE": str(tmp_path / "shutdown")}
    return subprocess.Popen([sys.executable, "-m", "studio.launcher"], cwd=ROOT, env=environment,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def test_owned_launcher_publishes_exact_pid_port_and_cleans_up_on_private_shutdown(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (tmp_path / "backend.log").open("w+b") as log:
        process = launcher(tmp_path, port, log)
        ready = tmp_path / "ready.json"
        try:
            deadline = time.monotonic() + 15
            marker = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail(log.read().decode("utf-8", errors="replace"))
                try:
                    marker = json.loads(ready.read_text())
                except (OSError, ValueError):
                    pass
                if marker is not None:
                    break
                time.sleep(.05)
            assert marker == {"pid": process.pid, "port": port}
            origin = f"http://127.0.0.1:{port}"
            with urllib.request.urlopen(origin + "/api/health", timeout=3) as response:
                assert json.load(response)["application"] == "VTuberSongCut"
            request = urllib.request.Request(origin + "/api/session", headers={"Origin": origin})
            with urllib.request.urlopen(request, timeout=3) as response:
                assert json.load(response)["token"]
        finally:
            (tmp_path / "shutdown").write_bytes(b"shutdown")
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        assert process.returncode == 0
        assert not ready.exists()
        with socket.socket() as check:
            assert check.connect_ex(("127.0.0.1", port)) != 0


def test_launcher_cannot_claim_readiness_on_a_port_owned_by_another_service(tmp_path):
    with socket.socket() as other, (tmp_path / "failed.log").open("w+b") as log:
        other.bind(("127.0.0.1", 0))
        other.listen()
        address = other.getsockname()
        process = launcher(tmp_path, address[1], log)
        try:
            assert process.wait(timeout=15) != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        assert not (tmp_path / "ready.json").exists()
        # The unrelated listener still accepts connections after the failed child.
        with socket.create_connection(address, timeout=2):
            pass
