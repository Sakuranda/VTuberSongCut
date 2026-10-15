#!/usr/bin/env python3
"""Build controlled release FFmpeg/ffprobe with the checked-in vcpkg manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import time
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PIN = "20ceb8adbf0504d4b0d5e4058a32f9d4fc896391"


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--triplet", required=True)
    parser.add_argument("--reuse-verified-cache", action="store_true", help="Use only an exact-key CI cache already verified with its complete sources")
    args = parser.parse_args()
    state = ROOT / "work/media"
    vcpkg = state / "vcpkg"
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=vcpkg, text=True).strip() != PIN:
        raise SystemExit("Release media requires the exact pinned vcpkg checkout")
    manifest = json.loads((ROOT / "packaging/vcpkg.json").read_text())
    if manifest["builtin-baseline"] != PIN:
        raise SystemExit("vcpkg manifest and build pin disagree")
    if not (ROOT / "packaging/triplets" / f"{args.triplet}.cmake").is_file():
        raise SystemExit("Unknown release triplet")
    (state / "downloads").mkdir(parents=True, exist_ok=True)
    cached_record = None
    history = state / "build-history"
    history.mkdir(exist_ok=True)
    saved_record = state / "media-build.json"
    if not saved_record.is_file():
        saved_record = history / "cached-media-build.json"
    if args.reuse_verified_cache and saved_record.is_file():
        cached_record = json.loads(saved_record.read_text(encoding="utf-8"))
        if cached_record["vcpkg_commit"] != PIN or cached_record["triplet"] != args.triplet:
            raise SystemExit("Cached media record does not match the requested pin/triplet")
    for name in ("media-build.json", "build-command.json"):
        if (state / name).is_file():
            shutil.copy2(state / name, history / f"{Path(name).stem}-{time.time_ns()}.json")
    environment = {**os.environ, "VCPKG_BINARY_SOURCES": "clear", "VCPKG_DISABLE_METRICS": "1",
                   "VCPKG_DOWNLOADS": str(state / "downloads")}
    # Restore installed packages only together with their source trees. A binary
    # cache alone is deliberately disabled because it cannot supply those trees.
    bootstrap = [str(vcpkg / "bootstrap-vcpkg.bat"), "-disableMetrics"] if os.name == "nt" else ["sh", str(vcpkg / "bootstrap-vcpkg.sh"), "-disableMetrics"]
    if not args.reuse_verified_cache:
        subprocess.run(bootstrap, cwd=vcpkg, env=environment, check=True)
    elif not (state / "build.log").is_file() or not (state / "installed/vcpkg/status").is_file():
        raise SystemExit("Verified cache is missing its original build log or installed package status")
    executable = vcpkg / ("vcpkg.exe" if os.name == "nt" else "vcpkg")
    command = [str(executable), "install", f"--triplet={args.triplet}",
               f"--x-manifest-root={ROOT / 'packaging'}", f"--x-install-root={state / 'installed'}",
               f"--overlay-triplets={ROOT / 'packaging/triplets'}", "--no-print-usage"]
    overlay = ROOT / "packaging/ports" / args.triplet
    if overlay.is_dir():
        command.append(f"--overlay-ports={overlay}")
    with (state / "build-command.json").open("w", encoding="utf-8") as output:
        json.dump({"argv": command, "executed": not args.reuse_verified_cache,
                   "reused_verified_cache": args.reuse_verified_cache, "vcpkg_commit": PIN, "triplet": args.triplet,
                   "platform": platform.platform(), "python": sys.version,
                   "environment": {key: environment.get(key) for key in ("VCPKG_BINARY_SOURCES", "VCPKG_DOWNLOADS", "CC", "CXX", "MACOSX_DEPLOYMENT_TARGET", "ImageVersion")}}, output, indent=2)
    if not args.reuse_verified_cache:
        history = state / "build-history"
        history.mkdir(exist_ok=True)
        if (state / "build.log").is_file():
            shutil.copy2(state / "build.log", history / f"build-{time.time_ns()}.log")
        with (state / "build.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
            if process.wait():
                raise SystemExit(process.returncode)
    else:
        print("Reusing verified exact-key media/source cache; native compiler ABI revalidation is unnecessary for existing tools", flush=True)
    suffix = ".exe" if os.name == "nt" else ""
    tools = state / "installed" / args.triplet / "tools/ffmpeg"
    binaries = {name: tools / (name + suffix) for name in ("ffmpeg", "ffprobe")}
    reports = {}
    for name, binary in binaries.items():
        if not binary.is_file():
            raise SystemExit(f"Controlled media tool missing: {binary}")
        if cached_record and digest(binary) != cached_record["binaries"][name]["sha256"]:
            raise SystemExit(f"Cached {name} does not match its saved controlled-build digest")
        result = subprocess.check_output([str(binary), "-version"], text=True, encoding="utf-8", errors="replace")
        if args.reuse_verified_cache:
            expected = json.loads((vcpkg / "ports/ffmpeg/vcpkg.json").read_text(encoding="utf-8"))["version"]
            if expected not in result.splitlines()[0]:
                raise SystemExit(f"Cached {name} version does not match the pinned port")
        if "--enable-nonfree" in result:
            raise SystemExit("Non-redistributable FFmpeg configuration rejected")
        reports[name] = {"path": str(binary.relative_to(ROOT)), "sha256": digest(binary), "version": result}
    features = subprocess.check_output([str(binaries["ffmpeg"]), "-hide_banner", "-filters"], text=True, stderr=subprocess.STDOUT)
    encoders = subprocess.check_output([str(binaries["ffmpeg"]), "-hide_banner", "-encoders"], text=True, stderr=subprocess.STDOUT)
    if " ass " not in features or not all(codec in encoders for codec in ("libx264", "libx265", "libmp3lame")):
        raise SystemExit("Controlled media build lacks required subtitle/video/audio capabilities")
    (state / "media-build.json").write_text(json.dumps({"vcpkg_commit": PIN, "triplet": args.triplet, "binaries": reports}, indent=2), encoding="utf-8")
    if args.reuse_verified_cache:
        from export_media_sources import inspect_source_inputs
        inspect_source_inputs(state)
    if github_env := os.environ.get("GITHUB_ENV"):
        with open(github_env, "a", encoding="utf-8") as output:
            output.write(f"SONGCUT_BUILD_FFMPEG={binaries['ffmpeg']}\nSONGCUT_BUILD_FFPROBE={binaries['ffprobe']}\n")
    print("Controlled media ready; preserve buildtrees sources until export_media_sources.py completes")


if __name__ == "__main__":
    main()
