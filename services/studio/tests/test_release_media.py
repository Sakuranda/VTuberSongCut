"""Release source companions fail closed and exclude unrelated tool/object binaries."""
import importlib
import json
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("export_media_sources")


@pytest.fixture
def media_state(tmp_path, exporter, monkeypatch):
    state = tmp_path / "media"
    ports = exporter.REQUIRED_SOURCE_PORTS
    for port in ports:
        source = state / "vcpkg/buildtrees" / port / "src/patched.clean"
        source.mkdir(parents=True)
        (source / "preferred-source.c").write_text("/* patched source */\n")
        (source / "compiled.obj").write_bytes(b"compiled object")
        share = state / "installed/test-triplet/share" / port
        share.mkdir(parents=True)
        (share / "copyright").write_text("component license")
    status = state / "installed/vcpkg/status"
    status.parent.mkdir()
    status.write_text("\n\n".join(f"Package: {port}\nArchitecture: test-triplet\nStatus: install ok installed" for port in ports))
    downloads = state / "downloads"
    downloads.mkdir()
    (downloads / "ffmpeg-source.tar.gz").write_bytes(b"original source archive")
    (downloads / "unrelated-bootstrap-tool.zip").write_bytes(b"do not redistribute")
    (state / "build.log").write_text(f"Extracting source {downloads / 'ffmpeg-source.tar.gz'}\n")
    (state / "build-command.json").write_text("{}")
    backend = tmp_path / "backend"
    binaries = backend / "_internal/resources/bin"
    binaries.mkdir(parents=True)
    records = {}
    for name in ("ffmpeg", "ffprobe"):
        binary = binaries / name
        binary.write_bytes(b"frozen executable")
        records[name] = {"sha256": "original-build-hash", "version": "controlled version\n"}
    (state / "media-build.json").write_text(json.dumps({"vcpkg_commit": exporter.PIN, "triplet": "test-triplet", "binaries": records}))
    monkeypatch.setattr(exporter.subprocess, "check_output", lambda *args, **kwargs: "controlled version\n")
    return state, backend, tmp_path / "output"


def test_source_export_contains_sources_licenses_and_exact_binary_hashes(exporter, media_state):
    state, backend, output = media_state
    companion = exporter.export(state, "fixture", backend, output, check_checkout=False)
    with tarfile.open(companion) as archive:
        names = archive.getnames()
        assert any(name.endswith("preferred-source.c") for name in names)
        assert not any(name.endswith("compiled.obj") for name in names)
        assert not any("unrelated-bootstrap-tool" in name for name in names)
        assert any(name.endswith("original-source-archives/ffmpeg-source.tar.gz") for name in names)
        assert any(name.endswith("share/ffmpeg/copyright") for name in names)
        manifest = json.load(archive.extractfile("MediaSources-fixture/SOURCE-AND-BINARY-MANIFEST.json"))
        assert manifest["binaries"]["ffmpeg"]["frozen_sha256"] == exporter.digest(backend / "_internal/resources/bin/ffmpeg")


def test_source_export_rejects_missing_source_or_recorded_archive(exporter, media_state):
    import shutil
    state, backend, output = media_state
    shutil.rmtree(state / "vcpkg/buildtrees/x264/src")
    with pytest.raises(ValueError, match="Missing actual patched"):
        exporter.export(state, "fixture", backend, output, check_checkout=False)
    (state / "vcpkg/buildtrees/x264/src").mkdir()
    (state / "downloads/ffmpeg-source.tar.gz").unlink()
    with pytest.raises(ValueError, match="Recorded source download is missing"):
        exporter.export(state, "fixture", backend, output, check_checkout=False)


def test_source_export_rejects_mismatched_frozen_configuration(exporter, media_state, monkeypatch):
    state, backend, output = media_state
    monkeypatch.setattr(exporter.subprocess, "check_output", lambda *args, **kwargs: "unrelated binary version\n")
    with pytest.raises(ValueError, match="differs from the controlled build"):
        exporter.export(state, "fixture", backend, output, check_checkout=False)
