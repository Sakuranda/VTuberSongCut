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
    environment = {**os.environ, "VCPKG_BINARY_SOURCES": "clear", "VCPKG_DISABLE_METRICS": "1",
                   "VCPKG_DOWNLOADS": str(state / "downloads")}
    # Restore installed packages only together with their source trees. A binary
    # cache alone is deliberately disabled because it cannot supply those trees.
    bootstrap = [str(vcpkg / "bootstrap-vcpkg.bat"), "-disableMetrics"] if os.name == "nt" else ["sh", str(vcpkg / "bootstrap-vcpkg.sh"), "-disableMetrics"]
    subprocess.run(bootstrap, cwd=vcpkg, env=environment, check=True)
    executable = vcpkg / ("vcpkg.exe" if os.name == "nt" else "vcpkg")
    command = [str(executable), "install", f"--triplet={args.triplet}",
               f"--x-manifest-root={ROOT / 'packaging'}", f"--x-install-root={state / 'installed'}",
               f"--overlay-triplets={ROOT / 'packaging/triplets'}", "--no-print-usage"]
    with (state / "build-command.json").open("w", encoding="utf-8") as output:
        json.dump({"argv": command, "vcpkg_commit": PIN, "triplet": args.triplet,
                   "platform": platform.platform(), "python": sys.version,
                   "environment": {key: environment.get(key) for key in ("VCPKG_BINARY_SOURCES", "VCPKG_DOWNLOADS", "CC", "CXX", "MACOSX_DEPLOYMENT_TARGET", "ImageVersion")}}, output, indent=2)
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
    suffix = ".exe" if os.name == "nt" else ""
    tools = state / "installed" / args.triplet / "tools/ffmpeg"
    binaries = {name: tools / (name + suffix) for name in ("ffmpeg", "ffprobe")}
    reports = {}
    for name, binary in binaries.items():
        if not binary.is_file():
            raise SystemExit(f"Controlled media tool missing: {binary}")
        result = subprocess.check_output([str(binary), "-version"], text=True, encoding="utf-8", errors="replace")
        if "--enable-nonfree" in result:
            raise SystemExit("Non-redistributable FFmpeg configuration rejected")
        reports[name] = {"path": str(binary.relative_to(ROOT)), "sha256": digest(binary), "version": result}
    features = subprocess.check_output([str(binaries["ffmpeg"]), "-hide_banner", "-filters"], text=True, stderr=subprocess.STDOUT)
    encoders = subprocess.check_output([str(binaries["ffmpeg"]), "-hide_banner", "-encoders"], text=True, stderr=subprocess.STDOUT)
    if " ass " not in features or not all(codec in encoders for codec in ("libx264", "libx265", "libmp3lame")):
        raise SystemExit("Controlled media build lacks required subtitle/video/audio capabilities")
    (state / "media-build.json").write_text(json.dumps({"vcpkg_commit": PIN, "triplet": args.triplet, "binaries": reports}, indent=2), encoding="utf-8")
    if github_env := os.environ.get("GITHUB_ENV"):
        with open(github_env, "a", encoding="utf-8") as output:
            output.write(f"SONGCUT_BUILD_FFMPEG={binaries['ffmpeg']}\nSONGCUT_BUILD_FFPROBE={binaries['ffprobe']}\n")
    print("Controlled media ready; preserve buildtrees sources until export_media_sources.py completes")


if __name__ == "__main__":
    main()
