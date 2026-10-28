#!/usr/bin/env python3
"""Build a self-contained onedir Python/ML backend for Tauri and portable archives."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FONT_URL = "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
FONT_LICENSE = "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/LICENSE"


def download(url, target):
    if not target.exists():
        with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as output:
            shutil.copyfileobj(response, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", help="Use an explicitly built FFmpeg; exclude the imageio wheel binary")
    parser.add_argument("--media-build-record", type=Path, help="Verify both tools against this controlled build manifest")
    parser.add_argument("--ffprobe", help="Path to ffprobe (its dependent libraries are collected)")
    parser.add_argument("--skip-freeze", action="store_true", help="Only refresh build inputs")
    parser.add_argument("--keep-build-work", action="store_true", help="Keep intermediate files for debugging after a successful freeze")
    args = parser.parse_args()
    import imageio_ffmpeg
    work = ROOT / "work/desktop-build"
    resources = work / "resources"
    if resources.exists():
        shutil.rmtree(resources)
    resources.mkdir(parents=True, exist_ok=True)
    for source, target in ((ROOT / "apps/web/dist", "apps/web/dist"), (ROOT / "docs", "docs"),
                           (ROOT / "upstream/AutoSlicer-v2/src", "upstream/AutoSlicer-v2/src")):
        if not source.is_dir():
            raise SystemExit(f"Missing {source}; build the web UI and fetch upstream first")
        shutil.copytree(source, resources / target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    workers = resources / "workers/studio"
    workers.mkdir(parents=True, exist_ok=True)
    for filename in ("__init__.py", "runtime.py", "model_probe.py", "training_worker.py", "inference_worker.py"):
        shutil.copy2(ROOT / "services/studio/studio" / filename, workers / filename)
    for filename in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        if (ROOT / filename).is_file():
            shutil.copy2(ROOT / filename, resources / filename)
    bin_dir, fonts, licenses = resources / "bin", resources / "fonts", resources / "THIRD_PARTY_LICENSES"
    for directory in (bin_dir, fonts, licenses):
        directory.mkdir(exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    ffprobe = args.ffprobe or shutil.which("ffprobe")
    if not ffprobe:
        raise SystemExit("ffprobe is required on the build host; install FFmpeg before building")
    ffmpeg = args.ffmpeg or imageio_ffmpeg.get_ffmpeg_exe()
    if args.ffmpeg and not args.ffprobe:
        raise SystemExit("Explicit release media requires both --ffmpeg and --ffprobe")
    media_record = None
    if args.media_build_record:
        from build_release_media import digest
        if not args.ffmpeg or not args.ffprobe:
            raise SystemExit("A controlled build record requires explicit media binaries")
        media_record = json.loads(args.media_build_record.read_text(encoding="utf-8"))
        for name, binary in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
            if digest(Path(binary)) != media_record["binaries"][name]["sha256"]:
                raise SystemExit(f"{name} does not match its controlled build record")
        shutil.copy2(args.media_build_record, licenses / "controlled-media-build.json")
    bundle_ffmpeg = Path(ffmpeg).resolve()
    if bundle_ffmpeg.name != "ffmpeg" + suffix:
        media_inputs = work / "media-inputs"
        media_inputs.mkdir(exist_ok=True)
        bundle_ffmpeg = media_inputs / ("ffmpeg" + suffix)
        shutil.copy2(ffmpeg, bundle_ffmpeg)
    download(FONT_URL, fonts / "NotoSansCJKsc-Regular.otf")
    download(FONT_LICENSE, licenses / "Noto-OFL.txt")
    python_license = next((p for p in (Path(sys.base_prefix) / "LICENSE.txt", Path(sysconfig.get_path("stdlib")) / "LICENSE.txt") if p.is_file()), None)
    if python_license:
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    else:
        download(f"https://raw.githubusercontent.com/python/cpython/v{platform.python_version()}/LICENSE", licenses / "Python-LICENSE.txt")
    contracts = resources / "packages/contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "packages/contracts/model-source.json", contracts / "model-source.json")
    upstream_license = ROOT / "upstream/AutoSlicer-v2/LICENSE"
    shutil.copy2(upstream_license, licenses / "AutoSlicer-MIT.txt")
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name", "unknown")
        directory = licenses / name
        directory.mkdir(exist_ok=True)
        (directory / "METADATA.txt").write_text(dist.read_text("METADATA") or json.dumps(dict(dist.metadata)), encoding="utf-8")
        for entry in dist.files or []:
            if any("license" in part.lower() or "copying" in part.lower() or "notice" in part.lower() for part in entry.parts):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    target = directory / str(entry).replace("..", "_")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    # Keep exact version/build information beside distributed executables.
    for name, binary in (("FFmpeg", ffmpeg), ("ffprobe", ffprobe)):
        report = subprocess.run([str(binary), "-version"], capture_output=True, text=True, check=True)
        (licenses / f"{name}-build.txt").write_text(report.stdout, encoding="utf-8")
        license_report = subprocess.run([str(binary), "-L"], capture_output=True, text=True, check=True)
        (licenses / f"{name}-license.txt").write_text(license_report.stdout + license_report.stderr, encoding="utf-8")
    (licenses / "MEDIA-SOURCES.txt").write_text(
        "Official release media is built using packaging/vcpkg.json and the pinned Microsoft vcpkg revision.\n"
        "The matching MediaSources-{platform}.tar.gz release asset contains actual patched source trees, original source archives, "
        "recipes, patches, logs, licenses and binary SHA-256 records. See docs/MEDIA-SOURCES.md.\n"
        + ("This is a controlled release-media build.\n" if media_record else
           "This is a private development build; media provenance has not been approved for public binary distribution.\n"),
        encoding="utf-8")
    (resources / "runtime-manifest.json").write_text(json.dumps({
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()},
        "font_source": FONT_URL, "model_weights_bundled": False,
        "controlled_media": media_record,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.skip_freeze:
        return
    destination = ROOT / "apps/desktop/src-tauri/binaries"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--name", "songcut-backend", "--distpath", str(destination),
               "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
               "--paths", str(ROOT / "services/studio"),
               "--add-data", f"{resources}{os.pathsep}resources",
               "--add-binary", f"{bundle_ffmpeg}{os.pathsep}resources/bin",
               "--add-binary", f"{Path(ffprobe).resolve()}{os.pathsep}resources/bin",
               "--hidden-import", "studio.inference_worker", "--hidden-import", "studio.model_probe", "--hidden-import", "studio.training_worker",
               "--hidden-import", "torch.utils.tensorboard", "--hidden-import", "torchaudio.transforms",
               "--hidden-import", "requests", "--hidden-import", "scipy.ndimage", "--collect-all", "torchaudio",
               "--collect-submodules", "tensorboard",
               "--copy-metadata", "torch", "--copy-metadata", "torchaudio",
               str(ROOT / "services/studio/studio/launcher.py")]
    if args.ffmpeg:
        command += ["--additional-hooks-dir", str(ROOT / "packaging/pyinstaller-hooks")]
    else:
        command += ["--collect-data", "imageio_ffmpeg"]
    subprocess.run(command, cwd=ROOT, check=True)
    if sys.platform == "linux":
        # The wheel's optional SoX extension requires an external libsox that is
        # absent from this runtime. The app uses the verified soundfile/media
        # paths; leaving this unused extension would break ELF dependency scans.
        for pattern in ("_torchaudio_sox*.so", "libtorchaudio_sox.so"):
            for binary in list((destination / "songcut-backend").rglob(pattern)):
                binary.unlink()
    if args.ffmpeg:
        extras = [p for p in (destination / "songcut-backend").rglob("*") if p.is_file() and "imageio_ffmpeg" in p.parts and p.name.startswith("ffmpeg-")]
        if extras:
            raise SystemExit(f"Uncontrolled imageio FFmpeg binaries leaked into the release: {extras}")
    print(f"Standalone backend ready: {destination / 'songcut-backend'}")
    if not args.keep_build_work:
        from clean import BUILD_WORK, remove_paths
        remove_paths(ROOT, BUILD_WORK)
        print("Removed disposable build intermediates; packaged backend and media compilation caches retained.")


if __name__ == "__main__":
    main()
