#!/usr/bin/env python3
"""Mount the actual DMG read-only and exercise its installed backend/runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    installers = list(args.bundle.resolve().rglob("*.dmg"))
    if len(installers) != 1:
        raise SystemExit(f"Expected exactly one DMG, found {len(installers)}")
    installer = installers[0]
    with tempfile.TemporaryDirectory(prefix="songcut-dmg-") as temporary:
        mount = Path(temporary) / "mounted app"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", str(installer), "-readonly", "-nobrowse", "-mountpoint", str(mount)], check=True)
        try:
            applications = list(mount.glob("*.app"))
            if len(applications) != 1:
                raise RuntimeError("DMG does not contain exactly one application")
            app = applications[0]
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
            backend = app / "Contents/Resources/backend/songcut-backend"
            if not backend.is_file():
                raise RuntimeError("Installed DMG backend resource missing")
            subprocess.run([sys.executable, str(Path(__file__).with_name("smoke_desktop_backend.py")), str(backend)], check=True)
            files = {"installer": installer, "backend": backend,
                     "ffmpeg": backend.parent / "_internal/resources/bin/ffmpeg",
                     "ffprobe": backend.parent / "_internal/resources/bin/ffprobe"}
            report = {"platform": "macOS-AppleSilicon", "commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
                      "checks": ["read-only DMG mount", "strict application signature", "installed resource layout", "real media encode/CJK subtitles/MP3", "actual training and inference", "web/help and graceful shutdown"],
                      "files": {name: {"name": path.name, "size_bytes": path.stat().st_size, "sha256": digest(path)} for name, path in files.items()}}
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True)
    print("macOS DMG installed runtime passed")


if __name__ == "__main__":
    main()
