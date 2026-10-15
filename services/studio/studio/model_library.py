"""Content-addressed model registry, independent of the HTTP controller."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Lock
from uuid import uuid4
from pydantic import Field
from .domain import Record
from .config import Settings
from .store import Store

MAX_MODEL_BYTES = 256 * 1024 * 1024
OFFICIAL_AZUSA_SHA = "ea42cb11ae7b833cd45af749353577c90bcba74d9ed0ff3dbb98ba3e5247b1c8"


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ModelInfo(Record):
    id: str
    name: str
    filename: str
    sha256: str
    size_bytes: int
    architecture: str
    classes: int
    origin: str
    metadata: dict = Field(default_factory=dict)
    available: bool = True
    unavailable_reason: str | None = None


class ModelLibrary:
    def __init__(self, settings: Settings, store: Store):
        self.config, self.store = settings, store
        self.folder = settings.data / "models/managed"
        self.folder.mkdir(parents=True, exist_ok=True)
        self._runtime = None
        self._runtime_lock = Lock()
        self._seed_lock = Lock()
        self._seed_attempted = False
        self.seed_error = None
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, path TEXT NOT NULL, info TEXT NOT NULL)")

    def probe(self, checkpoint: Path | None = None) -> dict:
        command = [self.config.model_python or sys.executable, str(Path(__file__).with_name("model_probe.py")),
                   "--repository", str(self.config.upstream)]
        if checkpoint:
            command += ["--checkpoint", str(checkpoint)]
        environment = {**os.environ, "PYTHONUTF8": "1"}
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    env=environment, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("无法启动模型环境，请检查模型 Python 和 AutoSlicer 安装") from exc
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise ValueError("模型环境未就绪：请安装 PyTorch 与 AutoSlicer 依赖") from None
        if result.returncode:
            raise ValueError("模型校验失败：" + data.get("error", "无法读取兼容检查点"))
        return data

    def runtime(self) -> dict:
        with self._runtime_lock:
            if self._runtime is None:
                try:
                    self._runtime = {**self.probe(), "engine_ready": True, "engine_error": None}
                except ValueError as exc:
                    self._runtime = {"devices": [], "engine_ready": False, "engine_error": str(exc)}
            return self._runtime

    def seed(self):
        with self._seed_lock:
            if self._seed_attempted:
                return
            self._seed_attempted = True
            if not self.config.checkpoint.is_file():
                return
            try:
                sha = checksum(self.config.checkpoint)
                with self.store.connect() as db:
                    found = db.execute("SELECT id FROM models WHERE id=?", (sha,)).fetchone()
                if found:
                    return
                name = "阿梓 · 官方声线模型" if sha == OFFICIAL_AZUSA_SHA else "预配置声线模型"
                self.register_file(self.config.checkpoint, name, "preconfigured")
            except ValueError as exc:
                self.seed_error = str(exc)

    def list(self) -> list[ModelInfo]:
        self.seed()
        with self.store.connect() as db:
            rows = db.execute("SELECT path, info FROM models ORDER BY rowid DESC").fetchall()
        return [self._info(path, raw) for path, raw in rows]

    def _info(self, path: str, raw: str) -> ModelInfo:
        info = ModelInfo.model_validate_json(raw)
        if not Path(path).is_file():
            info.available, info.unavailable_reason = False, "模型文件已移走，请重新导入"
        elif Path(path).stat().st_size != info.size_bytes:
            info.available, info.unavailable_reason = False, "模型文件已变化，请重新导入校验"
        return info

    def resolve(self, ident: str) -> tuple[ModelInfo, Path]:
        self.seed()
        with self.store.connect() as db:
            row = db.execute("SELECT path, info FROM models WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("所选模型不存在，请重新选择或导入")
        info = self._info(*row)
        if not info.available:
            raise ValueError(info.unavailable_reason)
        path = Path(row[0])
        if checksum(path) != info.sha256:
            raise ValueError("模型内容与校验记录不一致，请重新导入")
        return info, path

    def resolve_device(self, requested: str) -> str:
        runtime = self.runtime()
        if not runtime["engine_ready"]:
            raise ValueError(runtime["engine_error"])
        if requested not in {d["id"] for d in runtime["devices"] if d["available"]}:
            raise ValueError("此电脑无法使用所选运行设备")
        return runtime["automatic_device"] if requested == "auto" else "cuda:0" if requested == "cuda" else requested

    def register_file(self, path: Path, name: str, origin: str = "imported", metadata: dict | None = None) -> ModelInfo:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_MODEL_BYTES:
            raise ValueError("模型文件为空或超过 256 MB")
        # Validate a private copy so replacing an external source cannot race validation.
        staging = self.folder / f"{uuid4().hex}.pending"
        try:
            shutil.copyfile(path, staging)
            details = self.probe(staging)
            sha = checksum(staging)
            destination = self.folder / f"{sha}.pt"
            with self.store.connect() as db:
                existing = db.execute("SELECT path, info FROM models WHERE id=?", (sha,)).fetchone()
            if existing and Path(existing[0]).is_file() and checksum(Path(existing[0])) == sha:
                return ModelInfo.model_validate_json(existing[1])
            staging.replace(destination)
            info = ModelInfo(id=sha, name=name.strip()[:100] or path.stem, filename=path.name,
                             sha256=sha, size_bytes=destination.stat().st_size,
                             architecture=details["architecture"], classes=details["classes"], origin=origin,
                             metadata=metadata or {})
            with self.store.connect() as db:
                db.execute("INSERT INTO models VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path, info=excluded.info",
                           (sha, str(destination), info.model_dump_json()))
            return info
        finally:
            staging.unlink(missing_ok=True)
