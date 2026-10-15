from pathlib import Path
import sys

from studio import runtime
from studio.config import Settings


def test_source_paths_and_external_interpreter(monkeypatch, tmp_path):
    monkeypatch.delenv("SONGCUT_DATA", raising=False)
    assert Settings.load().data == runtime.resource_root() / "var"
    command = runtime.worker_command("infer", ["--help"], repository=tmp_path, python="model-python")
    assert command == ["model-python", "-u", str(tmp_path / "src/infer.py"), "--help"]


def test_frozen_worker_uses_dispatcher_and_writable_data(monkeypatch, tmp_path):
    monkeypatch.setattr("studio.config.configure_runtime", lambda: None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "immutable"), raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user"))
    monkeypatch.delenv("SONGCUT_DATA", raising=False)
    settings = Settings.load()
    assert settings.root == tmp_path / "immutable/resources"
    assert settings.data == tmp_path / "user/VTuberSongCut"
    assert settings.upstream == settings.root / "upstream/AutoSlicer-v2"
    assert runtime.worker_command("prepare", ["snapshot.json"]) == [sys.executable, "--worker", "prepare", "snapshot.json"]
    assert runtime.worker_command("train", ["--help"], repository=tmp_path) == [sys.executable, "--worker", "train", "--repository", str(tmp_path), "--help"]


def test_data_override_and_invalid_worker(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setenv("SONGCUT_DATA", str(tmp_path))
    assert Settings.load().data == tmp_path
    with pytest.raises(ValueError):
        runtime.worker_command("untrusted-script", [])


def test_resource_root_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SONGCUT_ROOT", str(tmp_path))
    assert runtime.resource_root() == tmp_path
    assert Settings.load().upstream == tmp_path / "upstream/AutoSlicer-v2"


def test_gui_launch_without_standard_handles_still_configures_logging(monkeypatch):
    import logging
    from studio.launcher import configure_stdio
    from uvicorn.logging import DefaultFormatter
    streams = []
    try:
        with monkeypatch.context() as context:
            context.setattr(sys, "stdout", None)
            context.setattr(sys, "stderr", None)
            configure_stdio()
            streams = [sys.stdout, sys.stderr]
            assert all(stream is not None for stream in streams)
            # This previously raised AttributeError on stdout=None.
            formatter = DefaultFormatter()
            assert formatter.format(logging.makeLogRecord({"msg": "ready", "levelno": logging.INFO, "levelname": "INFO"})) == "ready"
    finally:
        for stream in streams:
            stream.close()
