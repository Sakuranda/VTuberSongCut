#!/usr/bin/env python3
"""Pair frozen media binaries with actual patched sources and exact build recipes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import posixpath
import shutil
import subprocess
import tarfile

from build_release_media import ROOT, PIN, digest

ARCHIVE_SUFFIXES = (".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tgz", ".zip", ".7z", ".tar")
REQUIRED_SOURCE_PORTS = {"ffmpeg", "x264", "x265", "libass", "mp3lame"}
NATIVE_SUFFIXES = {".o", ".obj", ".a", ".lib", ".so", ".dylib", ".dll", ".exe", ".pdb", ".pyc"}


def installed_ports(status: Path) -> set[str]:
    return set(re.findall(r"^Package: (.+)$", status.read_text(encoding="utf-8"), re.MULTILINE))


def source_filter(info: tarfile.TarInfo):
    path = Path(info.name)
    if any(part in {".git", "__pycache__"} for part in path.parts) or path.suffix.lower() in NATIVE_SUFFIXES:
        return None
    if info.issym() or info.islnk():
        target = Path(info.linkname)
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(info.name), info.linkname))
        if target.is_absolute() or resolved.split("/")[0] != info.name.split("/")[0]:
            raise ValueError(f"Unsafe source symlink: {info.name} -> {info.linkname}")
    return info


def collect_source_archives(downloads: Path, logs: list[Path]) -> list[Path]:
    """Use vcpkg's extraction messages, excluding unrelated downloaded tool binaries."""
    names = set()
    for log in logs:
        content = re.sub(r"\x1b\[[0-9;]*m", "", log.read_text(encoding="utf-8", errors="replace"))
        for value in re.findall(r"Extracting source ([^\r\n]+)", content):
            value = value.strip().strip('"')
            # Logs from a restored runner may use the other OS's separators.
            name = value.replace("\\", "/").rsplit("/", 1)[-1]
            if name.lower().endswith(ARCHIVE_SUFFIXES):
                names.add(name)
    result = []
    for name in sorted(names):
        archive = downloads / name
        if not archive.is_file():
            archive = downloads.parent / "source-archives" / name
        if not archive.is_file():
            raise ValueError(f"Recorded source download is missing: {name}")
        result.append(archive)
    return result


def inspect_source_inputs(state: Path):
    vcpkg = state / "vcpkg"
    record = json.loads((state / "media-build.json").read_text(encoding="utf-8"))
    if record["vcpkg_commit"] != PIN:
        raise ValueError("Media build pin mismatch")
    status = state / "installed/vcpkg/status"
    ports = installed_ports(status)
    trees = {port.name: port / "src" for port in (vcpkg / "buildtrees").iterdir() if (port / "src").is_dir()}
    missing = REQUIRED_SOURCE_PORTS - trees.keys()
    if missing:
        raise ValueError(f"Missing actual patched library source trees: {sorted(missing)}")
    # These helper ports consist of the versioned recipe itself. Every other
    # installed dependency must have its actual extracted/patched source tree.
    recipe_only = {port for port in ports if port.startswith("vcpkg-") or port in {"ffmpeg-bin2c", "pthread"}}
    # The pinned non-Windows ports are empty shims over the platform iconv/pthreads.
    # Windows MSVC builds those libraries and must retain their actual source.
    if record["triplet"].startswith(("x64-linux", "arm64-osx")):
        recipe_only.update((ports - trees.keys()) & {"libiconv", "pthreads", "dirent"})
    missing = ports - trees.keys() - recipe_only
    if missing:
        raise ValueError(f"Installed packages lack source coverage: {sorted(missing)}")
    logs = [state / "build.log", *sorted((state / "build-history").glob("*.log"))]
    logs = [log for log in logs if log.is_file()]
    archives = collect_source_archives(state / "downloads", logs)
    if not archives:
        raise ValueError("No original source archive extraction records were preserved")
    # Keep original sources with the installed/source cache as well as in the
    # downloads cache: either GitHub cache entry may expire independently.
    source_cache = state / "source-archives"
    source_cache.mkdir(exist_ok=True)
    for archive in archives:
        cached = source_cache / archive.name
        if archive.resolve() != cached.resolve():
            shutil.copy2(archive, cached)
    return record, ports, trees, recipe_only, archives


def capture_port_logs(state: Path):
    """Keep nested configure logs before object directories are pruned."""
    target = state / "port-logs"
    for port in (state / "vcpkg/buildtrees").iterdir():
        if not port.is_dir():
            continue
        source = port / "src"
        for log in port.rglob("*.log"):
            if source in log.parents or not log.is_file():
                continue
            saved = target / port.name / log.relative_to(port)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(log, saved)


def prepare_cache(state: Path, *, check_checkout=True):
    """Validate and retain a complete source/binary pair before later app steps."""
    if check_checkout and subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=state / "vcpkg", text=True).strip() != PIN:
        raise ValueError("vcpkg checkout no longer matches the build pin")
    record, _, _, _, _ = inspect_source_inputs(state)
    suffix = ".exe" if record["triplet"].startswith("x64-windows") else ""
    for name, built in record["binaries"].items():
        binary = state / "installed" / record["triplet"] / "tools/ffmpeg" / (name + suffix)
        if not binary.is_file() or digest(binary) != built["sha256"]:
            raise ValueError(f"Installed {name} does not match its controlled build record")
    capture_port_logs(state)
    prune_build_outputs(state)
    # Keep the cache path list stable: GitHub includes that list in its hidden
    # cache version, so adding top-level records would invalidate existing builds.
    history = state / "build-history"
    history.mkdir(exist_ok=True)
    for name in ("media-build.json", "build-command.json"):
        if (state / name).is_file():
            shutil.copy2(state / name, history / f"cached-{name}")
    print("Matched controlled media, patched/original sources and logs are ready for early caching")


def export(state: Path, label: str, backend: Path, output: Path, *, check_checkout=True) -> Path:
    vcpkg = state / "vcpkg"
    if check_checkout and subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=vcpkg, text=True).strip() != PIN:
        raise ValueError("vcpkg checkout no longer matches the build pin")
    record, ports, trees, recipe_only, archives = inspect_source_inputs(state)
    capture_port_logs(state)
    output.mkdir(parents=True, exist_ok=True)
    staging = state / "source-export" / f"MediaSources-{label}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    recipes = staging / "recipes"
    recipes.mkdir()
    if check_checkout:
        subprocess.run(["git", "archive", "--format=tar.gz", f"--output={recipes / 'vcpkg-source.tar.gz'}", PIN], cwd=vcpkg, check=True)
    project = recipes / "project"
    shutil.copytree(ROOT / "packaging", project / "packaging")
    (project / "scripts").mkdir()
    (project / "docs").mkdir()
    shutil.copy2(ROOT / "scripts/build_release_media.py", project / "scripts/build_release_media.py")
    shutil.copy2(ROOT / "scripts/export_media_sources.py", project / "scripts/export_media_sources.py")
    shutil.copy2(ROOT / "docs/MEDIA-SOURCES.md", project / "docs/MEDIA-SOURCES.md")
    shutil.copy2(ROOT / "docs/MEDIA-SOURCES.md", staging / "HOW-TO-BUILD.md")
    metadata = staging / "build-records"
    metadata.mkdir()
    for name in ("build-command.json", "media-build.json", "build.log"):
        shutil.copy2(state / name, metadata / name)
    if (state / "build-history").is_dir():
        shutil.copytree(state / "build-history", metadata / "build-history")
    if (state / "port-logs").is_dir():
        shutil.copytree(state / "port-logs", metadata / "ports")
    shutil.copytree(state / "installed/vcpkg", metadata / "installed-vcpkg", ignore=shutil.ignore_patterns("updates"))
    for installed_triplet in sorted((state / "installed").iterdir()):
        if (installed_triplet / "share").is_dir():
            shutil.copytree(installed_triplet / "share", metadata / "installed-share" / installed_triplet.name)
    inventory = {
        "vcpkg_commit": PIN, "triplet": record["triplet"], "platform": label,
        "created_utc": datetime.now(timezone.utc).isoformat(), "source_ports": sorted(trees),
        "installed_ports": sorted(ports), "recipe_only_ports": sorted(recipe_only),
        "source_archives": {archive.name: digest(archive) for archive in archives}, "binaries": {},
    }
    for name, built in record["binaries"].items():
        suffix = ".exe" if record["triplet"].startswith("x64-windows") else ""
        shipped = backend / "_internal/resources/bin" / (name + suffix)
        if not shipped.is_file():
            raise ValueError(f"Frozen media binary missing: {shipped}")
        version = subprocess.check_output([str(shipped), "-version"], text=True, encoding="utf-8", errors="replace")
        if version != built["version"]:
            raise ValueError(f"Frozen {name} differs from the controlled build version/configuration")
        inventory["binaries"][name] = {"controlled_build_sha256": built["sha256"], "frozen_sha256": digest(shipped), "version": version}
    (staging / "SOURCE-AND-BINARY-MANIFEST.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    target = output / f"MediaSources-{label}.tar.gz"
    with tarfile.open(target, "w:gz", compresslevel=6) as archive:
        archive.add(staging, arcname=staging.name, filter=source_filter)
        for port, tree in sorted(trees.items()):
            archive.add(tree, arcname=f"{staging.name}/patched-sources/{port}/src", filter=source_filter)
        for source in archives:
            archive.add(source, arcname=f"{staging.name}/original-source-archives/{source.name}")
    print(f"Source companion ready: {target}")
    return target


def prune_build_outputs(state: Path):
    """Cache matched installed binaries plus sources/logs, never compiled objects."""
    for port in (state / "vcpkg/buildtrees").iterdir():
        if not port.is_dir():
            continue
        for entry in port.iterdir():
            if entry.name == "src" or (entry.is_file() and entry.suffix.lower() in {".log", ".json", ".txt", ".cmake"}):
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    shutil.rmtree(state / "vcpkg/packages", ignore_errors=True)
    shutil.rmtree(state / "source-export", ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--backend", type=Path, default=ROOT / "apps/desktop/src-tauri/binaries/songcut-backend")
    parser.add_argument("--output", type=Path, default=ROOT / "release-assets")
    parser.add_argument("--prune-build-outputs", action="store_true")
    parser.add_argument("--prepare-cache", action="store_true", help="Validate/cache sources and installed media immediately after compilation")
    args = parser.parse_args()
    state = ROOT / "work/media"
    if args.prepare_cache:
        prepare_cache(state)
        return
    export(state, args.label, args.backend.resolve(), args.output.resolve())
    if args.prune_build_outputs:
        prune_build_outputs(state)


if __name__ == "__main__":
    main()
