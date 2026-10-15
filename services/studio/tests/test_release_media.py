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
        original = state / "installed/test-triplet/tools/ffmpeg" / name
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b"controlled build executable")
        records[name] = {"sha256": exporter.digest(original), "version": "controlled version\n"}
    (state / "media-build.json").write_text(json.dumps({"vcpkg_commit": exporter.PIN, "triplet": "test-triplet", "binaries": records}))
    monkeypatch.setattr(exporter.subprocess, "check_output", lambda *args, **kwargs: "controlled version\n")
    return state, backend, tmp_path / "output"


def test_source_export_contains_sources_licenses_and_exact_binary_hashes(exporter, media_state):
    state, backend, output = media_state
    companion = exporter.export(state, "fixture", backend, output, check_checkout=False)
    with tarfile.open(companion) as archive:
        names = archive.getnames()
        assert any(name.endswith("preferred-source.c") for name in names)
        assert "MediaSources-fixture/recipes/project/scripts/build_release_media.py" in names
        assert "MediaSources-fixture/recipes/project/packaging/vcpkg.json" in names
        assert not any(name.endswith("compiled.obj") for name in names)
        assert not any("unrelated-bootstrap-tool" in name for name in names)
        assert any(name.endswith("original-source-archives/ffmpeg-source.tar.gz") for name in names)
        assert any(name.endswith("/ffmpeg/copyright") for name in names)
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


def test_original_sources_survive_independent_download_cache_eviction(exporter, media_state):
    state, backend, output = media_state
    exporter.export(state, "fixture", backend, output, check_checkout=False)
    (state / "downloads/ffmpeg-source.tar.gz").unlink()
    assert (state / "source-archives/ffmpeg-source.tar.gz").is_file()
    companion = exporter.export(state, "fixture", backend, output, check_checkout=False)
    with tarfile.open(companion) as archive:
        assert any(name.endswith("original-source-archives/ffmpeg-source.tar.gz") for name in archive.getnames())


def test_fresh_bootstrap_creates_download_directory_before_running_tools(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    builder = importlib.import_module("build_release_media")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder.platform, "platform", lambda: "fixture platform")
    monkeypatch.setattr(builder.sys, "argv", ["build_release_media.py", "--triplet", "test-triplet"])
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "github-env"))
    state = tmp_path / "work/media"
    (state / "vcpkg").mkdir(parents=True)
    packaging = tmp_path / "packaging"
    (packaging / "triplets").mkdir(parents=True)
    (packaging / "triplets/test-triplet.cmake").write_text("release triplet")
    (packaging / "vcpkg.json").write_text(json.dumps({"builtin-baseline": builder.PIN}))

    def bootstrap(command, **kwargs):
        assert Path(kwargs["env"]["VCPKG_DOWNLOADS"]).is_dir(), "bootstrap must receive an existing downloads directory"

    class Install:
        stdout = ["source build completed\n"]

        def __init__(self, command, **kwargs):
            assert Path(kwargs["env"]["VCPKG_DOWNLOADS"]).is_dir()
            tools = state / "installed/test-triplet/tools/ffmpeg"
            tools.mkdir(parents=True)
            for name in ("ffmpeg", "ffprobe"):
                (tools / (name + (".exe" if builder.os.name == "nt" else ""))).write_bytes(b"native test binary")

        def wait(self):
            return 0

    def inspect(command, **kwargs):
        if command[0] == "git":
            return builder.PIN + "\n"
        if "-filters" in command:
            return " T.. ass V->V\n"
        if "-encoders" in command:
            return "libx264 libx265 libmp3lame\n"
        return "controlled version --enable-gpl\n"

    monkeypatch.setattr(builder.subprocess, "run", bootstrap)
    monkeypatch.setattr(builder.subprocess, "Popen", Install)
    monkeypatch.setattr(builder.subprocess, "check_output", inspect)
    builder.main()
    assert (state / "media-build.json").is_file()
    assert "SONGCUT_BUILD_FFMPEG=" in (tmp_path / "github-env").read_text()


def test_early_cache_preserves_nested_logs_sources_and_matching_binaries(exporter, media_state):
    state, backend, output = media_state
    build = state / "vcpkg/buildtrees/ffmpeg/triplet-rel/ffbuild"
    build.mkdir(parents=True)
    (build / "config.log").write_text("actual native compiler/configure record")
    (build / "compiled.o").write_bytes(b"compiled object")
    exporter.prepare_cache(state, check_checkout=False)
    assert not build.exists()
    assert (state / "port-logs/ffmpeg/triplet-rel/ffbuild/config.log").read_text() == "actual native compiler/configure record"
    (state / "downloads/ffmpeg-source.tar.gz").unlink()
    companion = exporter.export(state, "fixture", backend, output, check_checkout=False)
    with tarfile.open(companion) as archive:
        member = "MediaSources-fixture/build-records/ports/ffmpeg/triplet-rel/ffbuild/config.log"
        assert archive.extractfile(member).read() == b"actual native compiler/configure record"
        assert not any(name.endswith("compiled.o") for name in archive.getnames())


def test_early_cache_rejects_a_binary_changed_after_source_build(exporter, media_state):
    state, _, _ = media_state
    (state / "installed/test-triplet/tools/ffmpeg/ffmpeg").write_bytes(b"replaced executable")
    with pytest.raises(ValueError, match="does not match its controlled build record"):
        exporter.prepare_cache(state, check_checkout=False)


def test_known_system_shims_are_recipe_only_on_linux_but_windows_keeps_real_sources(exporter, media_state):
    state, _, _ = media_state
    status = state / "installed/vcpkg/status"
    with status.open("a") as output:
        for port in ("pthread", "pthreads", "libiconv"):
            output.write(f"\n\nPackage: {port}\nArchitecture: platform\nStatus: install ok installed")
    record_path = state / "media-build.json"
    record = json.loads(record_path.read_text())
    record["triplet"] = "x64-linux-songcut"
    record_path.write_text(json.dumps(record))
    _, _, _, recipe_only, _ = exporter.inspect_source_inputs(state)
    assert {"pthread", "pthreads", "libiconv"} <= recipe_only
    record["triplet"] = "x64-windows-songcut"
    record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Installed packages lack source coverage"):
        exporter.inspect_source_inputs(state)
