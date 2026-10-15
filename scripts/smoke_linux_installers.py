#!/usr/bin/env python3
"""Run the complete standalone backend check from both extracted Linux installers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verify_media(backend: Path, directory: Path) -> dict[str, str]:
    env = {**os.environ, "PATH": "/usr/bin:/bin"}
    for name in ("PYTHONPATH", "SONGCUT_MODEL_PYTHON", "SONGCUT_ROOT"):
        env.pop(name, None)
    info = json.loads(subprocess.check_output([str(backend), "--runtime-info"],
                      cwd=directory, env=env, text=True))
    resources = Path(info["resources"])
    ffmpeg, ffprobe = resources / "bin/ffmpeg", resources / "bin/ffprobe"
    fonts = directory / "fonts"
    fonts.mkdir()
    shutil.copy2(resources / "fonts/NotoSansCJKsc-Regular.otf", fonts)
    (directory / "subtitles.ass").write_text("""[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC,24,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,拾歌字幕验收
""", encoding="utf-8")
    subprocess.run([str(ffmpeg), "-hide_banner", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=16000", "-t", "1", "-vf",
                    "ass=subtitles.ass:fontsdir=fonts", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "sample.mp4"], cwd=directory, env=env,
                   capture_output=True, check=True, timeout=90)
    probe = json.loads(subprocess.check_output([str(ffprobe), "-v", "error", "-show_streams",
                       "-show_format", "-of", "json", "sample.mp4"], cwd=directory, env=env, text=True))
    assert {stream["codec_name"] for stream in probe["streams"]} == {"h264", "aac"}
    assert abs(float(probe["format"]["duration"]) - 1) < .1
    subprocess.run([str(ffmpeg), "-hide_banner", "-y", "-i", "sample.mp4", "-vn",
                    "-c:a", "libmp3lame", "sample.mp3"], cwd=directory, env=env,
                   capture_output=True, check=True, timeout=90)
    assert (directory / "sample.mp3").stat().st_size > 1000
    return {"backend": digest(backend), "ffmpeg": digest(ffmpeg), "ffprobe": digest(ffprobe)}


def verify(installer: Path) -> dict:
    installer = installer.resolve()
    with tempfile.TemporaryDirectory(prefix="songcut-linux-installer-") as directory:
        extracted = Path(directory)
        if installer.suffix == ".deb":
            subprocess.run(["dpkg-deb", "--extract", str(installer), str(extracted)], check=True)
        elif installer.suffix == ".AppImage":
            subprocess.run([str(installer), "--appimage-extract"], cwd=extracted,
                           stdout=subprocess.DEVNULL, check=True, timeout=180)
        else:
            raise ValueError(f"Unsupported installer: {installer}")
        candidates = [path for path in extracted.rglob("songcut-backend")
                      if path.is_file() and path.parent.name == "backend"]
        if len(candidates) != 1:
            raise ValueError(f"Expected one bundled runtime in {installer.name}: {candidates}")
        backend = candidates[0]
        # The shared smoke checker itself uses a fresh temporary working directory,
        # removes Python overrides, and invokes only this packaged executable.
        subprocess.run([sys.executable, str(ROOT / "scripts/smoke_desktop_backend.py"),
                        str(backend)], check=True, timeout=1200)
        with tempfile.TemporaryDirectory(prefix="songcut-linux-media-") as media_directory:
            hashes = verify_media(backend, Path(media_directory))
        return {"installer": installer.name, "installer_sha256": digest(installer),
                "backend": str(backend.relative_to(extracted)), "runtime_sha256": hashes,
                "status": "passed", "checks": ["training", "checkpoint", "inference", "web", "shutdown",
                "packaged FFmpeg H264/AAC encode", "CJK ASS subtitles", "ffprobe duration/codecs", "MP3 encode"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    installers = sorted(args.bundle.rglob("*.deb")) + sorted(args.bundle.rglob("*.AppImage"))
    if {path.suffix for path in installers} != {".deb", ".AppImage"}:
        raise ValueError("Both deb and AppImage installers are required")
    report = [verify(installer) for installer in installers]
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stdout or "")
        print(error.stderr or "")
        raise
