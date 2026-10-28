"""Paths and subprocess entry points shared by source and standalone builds."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    if override := os.environ.get("SONGCUT_ROOT"):
        return Path(override).expanduser().resolve()
    if frozen():
        return Path(sys._MEIPASS) / "resources"
    return Path(__file__).resolve().parents[3]


def data_root(root: Path) -> Path:
    if override := os.environ.get("SONGCUT_DATA"):
        return Path(override).expanduser().resolve()
    if not frozen():
        return root / "var"
    if sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return base / "VTuberSongCut"


def configure_runtime() -> None:
    """Use shipped media tools and font without touching a user's global PATH."""
    if not frozen():
        return
    root = resource_root()
    suffix = ".exe" if os.name == "nt" else ""
    for key, filename in (("SONGCUT_FFMPEG", "ffmpeg"), ("SONGCUT_FFPROBE", "ffprobe")):
        os.environ.setdefault(key, str(root / "bin" / (filename + suffix)))
    os.environ.setdefault("AUTOSLICER_FFMPEG", os.environ["SONGCUT_FFMPEG"])
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", os.environ["SONGCUT_FFMPEG"])
    os.environ.setdefault("SONGCUT_FONT_FILE", str(root / "fonts/NotoSansCJKsc-Regular.otf"))
    os.environ.setdefault("SONGCUT_FONT_FAMILY", "Noto Sans CJK SC")


def worker_command(kind: str, args: list[str], *, repository: Path | None = None,
                   python: str | None = None) -> list[str]:
    if kind not in {"probe", "prepare", "train", "infer", "infer-stream"}:
        raise ValueError(f"Unknown worker: {kind}")
    if frozen() and not python:
        command = [sys.executable, "--worker", kind]
        if repository is not None:
            command += ["--repository", str(repository)]
        return command + args
    if kind in {"train", "infer"}:
        if repository is None:
            raise ValueError("An upstream repository is required")
        script = repository / "src" / f"{kind}.py"
    else:
        script_root = resource_root() / "workers/studio" if frozen() else Path(__file__).parent
        script = script_root / {"probe": "model_probe.py", "prepare": "training_worker.py", "infer-stream": "inference_worker.py"}[kind]
        if kind in {"probe", "infer-stream"} and repository is not None:
            args = ["--repository", str(repository), *args]
    return [python or sys.executable, "-u", str(script), *args]
