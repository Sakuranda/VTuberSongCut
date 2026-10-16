"""Shared desktop/server HTTP controller, with explicit authenticated server mode."""
from contextlib import asynccontextmanager
import hmac
from pathlib import Path
import secrets
import shutil
import tempfile
from threading import Lock
from typing import Literal
from uuid import uuid4
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import Settings
from .server_config import ServerConfig
from .domain import Record, Project, Clip, Job
from .store import Store, ConflictError
from .jobs import JobRunner
from .service import Studio
from .adapters.autoslicer import AutoSlicerDetector
from .adapters.media import FFmpegMedia
from .model_library import ModelLibrary, ModelInfo, MAX_MODEL_BYTES, checksum
from .training import TrainingService, normalize_settings


MEDIA_SUFFIXES = {".mp4", ".flv", ".mkv", ".mov", ".webm", ".avi", ".ts", ".m4v",
                  ".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus"}


class SaveProject(Record):
    revision: int
    clips: list[Clip]
    detector_model_id: str | None = None
    detector_device: Literal["auto", "cpu", "mps", "cuda"] = "auto"


class ImportLyrics(Record):
    text: str
    offset_ms: int = 0


class CreateDataset(Record):
    name: str
    target_name: str


class UpdateSample(Record):
    revision: int
    split: Literal["train", "validation"]
    reviewed: bool
    regions: list[dict]


class DatasetRevision(Record):
    revision: int


class StartTraining(Record):
    revision: int
    settings: dict = {}


def create_app(settings: Settings | None = None):
    server = ServerConfig.load()
    config = settings or Settings.load()
    store = Store(config.data / "studio.sqlite3")
    runner = JobRunner(store)
    media = FFmpegMedia()
    library = ModelLibrary(config, store)
    studio = Studio(config.data, store, runner, None, media)
    training = TrainingService(config, store, register_model=lambda path, metadata:
        library.register_file(path, f"{metadata['target_name']} · 训练模型", "trained", metadata).model_dump())
    compute_lock = Lock()
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        training.close()
        runner.close()

    app = FastAPI(title="VTuber SongCut", version="0.1.0", lifespan=lifespan)
    app.state.studio = studio
    app.state.models = library
    app.state.training = training
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=server.allowed_hosts)

    @app.middleware("http")
    async def protect_service(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in server.allowed_origins:
            return JSONResponse({"detail": "请求来源不在工作台允许列表中"}, status_code=403)
        # Authenticate the UI as well, so browser-native media/range/download requests
        # reuse the browser's HTTP Basic credentials. Only liveness is public.
        if server.enabled and request.url.path != "/api/health":
            if not server.authenticate(request.headers.get("authorization", "")):
                return JSONResponse({"detail": "请使用服务器账户登录"}, status_code=401,
                                    headers={"WWW-Authenticate": 'Basic realm="SongCut", charset="UTF-8"',
                                             "Cache-Control": "no-store"})
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not hmac.compare_digest(request.headers.get("x-studio-token", ""), token):
                return JSONResponse({"detail": "会话已失效，请刷新工作台"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if server.enabled and request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse({"detail": "项目或片段不存在"}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"application": "VTuberSongCut", "version": "0.1.0"}

    @app.get("/api/session")
    def session():
        return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})

    @app.get("/api/capabilities")
    def capabilities():
        return {**media.capabilities(), "detector_ready": (config.upstream / "src/infer.py").is_file(),
                "recognition": False, "alignment": False}

    @app.get("/api/projects")
    def projects() -> list[Project]:
        return store.projects()

    @app.post("/api/projects/upload", status_code=201)
    def upload(file: UploadFile = File(...)) -> Project:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in MEDIA_SUFFIXES:
            raise HTTPException(415, "请选择 MP4、FLV、MKV、MOV、WebM、AVI、TS、M4V 或常见音频文件")
        folder = config.data / "media" / uuid4().hex
        folder.mkdir(parents=True)
        source = folder / ("source" + suffix)
        try:
            with source.open("wb") as output:
                shutil.copyfileobj(file.file, output, length=1024*1024)
            duration, has_video = media.probe(source)
            project = Project(name=Path(file.filename).stem, source_name=Path(file.filename).name,
                              source_file=str(source), duration_ms=duration, has_video=has_video, source_fps=media.source_fps(source) if has_video else None)
            store.create(project)
        except Exception as exc:
            shutil.rmtree(folder)
            if isinstance(exc, ValueError):
                raise
            raise HTTPException(422, "导入未完成，请检查磁盘空间和文件权限后重试") from exc
        finally:
            file.file.close()
        return project

    @app.get("/api/projects/{ident}")
    def project(ident: str) -> Project:
        return store.get(ident)

    @app.put("/api/projects/{ident}")
    def save(ident: str, body: SaveProject) -> Project:
        project = store.get(ident)
        project.clips = body.clips
        if "detector_model_id" in body.model_fields_set:
            if body.detector_model_id:
                library.resolve(body.detector_model_id)
            project.detector_model_id = body.detector_model_id
        if "detector_device" in body.model_fields_set:
            project.detector_device = body.detector_device
        return store.save(project, body.revision)

    @app.post("/api/projects/{ident}/detect", status_code=202)
    def detect(ident: str) -> Job:
        project = store.get(ident)
        if not project.detector_model_id:
            raise ValueError("请先选择主播声线模型并保存，再开始检测")
        with compute_lock:
            if any(j["status"] in {"queued", "running"} for j in training.jobs()):
                raise ConflictError("模型正在训练，请等待完成或取消训练后再检测")
            info, checkpoint = library.resolve(project.detector_model_id)
            device = library.resolve_device(project.detector_device)
            detector = AutoSlicerDetector(config.upstream, checkpoint, device, config.model_python, media.ffmpeg)
            return studio.detect(ident, detector, {"model_id": info.id, "model_name": info.name,
                                                   "model_sha256": info.sha256, "device": device})

    @app.post("/api/projects/{ident}/preview", status_code=202)
    def preview(ident: str) -> Job:
        return studio.preview(ident)

    @app.post("/api/projects/{ident}/clips/{clip_id}/export", status_code=202)
    def export(ident: str, clip_id: str) -> Job:
        return studio.export(ident, clip_id)

    @app.post("/api/projects/{ident}/clips/{clip_id}/lyrics")
    def lyrics(ident: str, clip_id: str, body: ImportLyrics) -> Project:
        return studio.lyrics(ident, clip_id, body.text, body.offset_ms)

    @app.get("/api/projects/{ident}/media")
    def source_media(ident: str):
        project = store.get(ident)
        return FileResponse(project.preview_file or project.source_file)

    @app.get("/api/jobs")
    def jobs(project_id: str | None = None) -> list[Job]:
        return store.jobs(project_id=project_id)

    @app.get("/api/artifacts/{job_id}/{filename}")
    def artifact(job_id: str, filename: str):
        job = store.get_job(job_id)
        if not job or job.status != "succeeded" or filename not in job.artifacts or Path(filename).name != filename:
            raise HTTPException(404, "文件不存在或任务尚未完成")
        return FileResponse(config.data / "jobs" / job_id / filename, filename=filename)

    @app.get("/api/models")
    def models():
        items = library.list()
        runtime = library.runtime()
        return {"models": items, "devices": runtime["devices"], "engine_ready": runtime["engine_ready"],
                "engine_error": runtime["engine_error"] or library.seed_error}

    @app.post("/api/models/upload", status_code=201)
    def import_model(file: UploadFile = File(...), name: str = Form("")) -> ModelInfo:
        filename = Path((file.filename or "model.pt").replace("\\", "/")).name
        if Path(filename).suffix.lower() not in {".pt", ".pth"}:
            raise HTTPException(415, "请选择 AutoSlicer 训练得到的 .pt 或 .pth 模型")
        try:
            with tempfile.TemporaryDirectory(dir=config.data) as directory:
                path = Path(directory) / filename
                size = 0
                with path.open("wb") as output:
                    while chunk := file.file.read(1024*1024):
                        size += len(chunk)
                        if size > MAX_MODEL_BYTES:
                            raise HTTPException(413, "模型不能超过 256 MB")
                        output.write(chunk)
                return library.register_file(path, name or Path(filename).stem)
        finally:
            file.file.close()

    @app.get("/api/models/{model_id}/download")
    def download_model(model_id: str):
        info, path = library.resolve(model_id)
        return FileResponse(path, filename="voice-model.pt", media_type="application/octet-stream")

    def dataset_view(dataset):
        for sample in dataset["samples"]:
            sample["preview_available"] = (config.data / "training/previews" / (sample["id"] + ".mp4")).is_file()
        return dataset

    @app.get("/api/training/datasets")
    def datasets():
        return [dataset_view(dataset) for dataset in training.datasets()]

    @app.post("/api/training/datasets", status_code=201)
    def create_dataset(body: CreateDataset):
        return dataset_view(training.create_dataset(body.name, body.target_name))

    @app.get("/api/training/datasets/{dataset_id}")
    def dataset(dataset_id: str):
        return dataset_view(training.get_dataset(dataset_id))

    @app.post("/api/training/datasets/{dataset_id}/samples", status_code=201)
    def add_sample(dataset_id: str, revision: int = Form(...), file: UploadFile = File(...)):
        training.get_dataset(dataset_id)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in MEDIA_SUFFIXES:
            raise HTTPException(415, "请选择 MP4、FLV、MKV、MOV、WebM、AVI、TS、M4V 或常见音频素材")
        folder = config.data / "training/uploads" / uuid4().hex
        folder.mkdir(parents=True)
        path = folder / ("source" + suffix)
        try:
            with path.open("wb") as output:
                shutil.copyfileobj(file.file, output, length=1024*1024)
            duration, _ = media.probe(path)
            return dataset_view(training.add_sample(dataset_id, path, Path(file.filename).name, duration,
                                                   checksum(path), expected_revision=revision))
        except Exception:
            shutil.rmtree(folder)
            raise
        finally:
            file.file.close()

    @app.put("/api/training/datasets/{dataset_id}/samples/{sample_id}")
    def update_sample(dataset_id: str, sample_id: str, body: UpdateSample):
        return dataset_view(training.update_sample(dataset_id, sample_id, body.model_dump(exclude={"revision"}), body.revision))

    @app.delete("/api/training/datasets/{dataset_id}/samples/{sample_id}")
    def remove_sample(dataset_id: str, sample_id: str, body: DatasetRevision):
        return dataset_view(training.remove_sample(dataset_id, sample_id, body.revision))

    @app.get("/api/training/datasets/{dataset_id}/samples/{sample_id}/media")
    def training_media(dataset_id: str, sample_id: str):
        sample = next((s for s in training.get_dataset(dataset_id)["samples"] if s["id"] == sample_id), None)
        if not sample:
            raise KeyError(sample_id)
        preview = config.data / "training/previews" / (sample_id + ".mp4")
        return FileResponse(preview if preview.is_file() else sample["path"])

    @app.post("/api/training/datasets/{dataset_id}/samples/{sample_id}/preview", status_code=202)
    def training_preview(dataset_id: str, sample_id: str):
        sample = next((s for s in training.get_dataset(dataset_id)["samples"] if s["id"] == sample_id), None)
        if not sample:
            raise KeyError(sample_id)
        project_id = "training-media:" + sample_id
        preview = config.data / "training/previews" / (sample_id + ".mp4")
        if preview.is_file():
            previous = next((job for job in store.jobs(project_id=project_id)
                             if job.kind == "training_preview" and job.status == "succeeded"), None)
            if previous:
                return previous
            completed = Job(project_id=project_id, kind="training_preview", status="succeeded", message="兼容预览已存在")
            store.save_job(completed)
            return completed
        def operation(job_id):
            directory = config.data / "training/previews"
            directory.mkdir(parents=True, exist_ok=True)
            temporary = directory / (sample_id + "." + job_id + ".pending.mp4")
            try:
                runner.update_progress(job_id, 0, "正在生成兼容预览")
                media.preview(Path(sample["path"]), temporary, duration_ms=sample["duration_ms"],
                              on_progress=lambda value: runner.update_progress(job_id, value),
                              on_fallback=lambda: runner.reset_progress(job_id, "正在转换兼容预览"))
                temporary.replace(preview)
            finally:
                temporary.unlink(missing_ok=True)
            return []
        return runner.submit(project_id, "training_preview", operation)

    @app.post("/api/training/datasets/{dataset_id}/train", status_code=202)
    def start_training(dataset_id: str, body: StartTraining):
        with compute_lock:
            if any(j.kind == "detect" and j.status in {"queued", "running"} for j in store.jobs(limit=None)):
                raise ConflictError("正在检测录播，请等待检测完成后再训练")
            settings = normalize_settings(body.settings)
            library.resolve_device(settings["device"])
            return training.start(dataset_id, settings, body.revision)

    @app.get("/api/training/jobs")
    def training_jobs():
        return training.jobs()

    @app.get("/api/training/jobs/{job_id}")
    def training_job(job_id: str):
        return training.get_job(job_id)

    @app.post("/api/training/jobs/{job_id}/cancel")
    def cancel_training(job_id: str):
        return training.cancel(job_id)

    @app.get("/help/faq.md")
    def faq_markdown():
        return FileResponse(config.root / "docs/FAQ.md", media_type="text/plain; charset=utf-8")

    def render_help_document(filename: str):
        import markdown
        source = (config.root / "docs" / filename).read_text(encoding="utf-8")
        source = source.replace("(MODELS.md", "(/help/models").replace("(FAQ.md", "(/help/faq")
        source = source.replace("(../packages/", "(https://github.com/Sakuranda/VTuberSongCut/blob/main/packages/")
        source = source.replace("(../patches/", "(https://github.com/Sakuranda/VTuberSongCut/blob/main/patches/")
        content = markdown.markdown(source,
                                    extensions=["fenced_code", "tables", "toc"])
        return HTMLResponse("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                            "<title>拾歌 · 使用帮助</title><style>body{max-width:850px;margin:40px auto;padding:0 24px;font:16px/1.8 system-ui;color:#30354e;background:#f7f8fc}"
                            "a{color:#5159a8}pre{overflow:auto;padding:18px;background:#eaecf7}h2{margin-top:36px}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #d7dbeb}</style>"
                            "<a href='/'>← 返回拾歌</a>" + content + "</html>",
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @app.get("/help/faq", response_class=HTMLResponse)
    def faq():
        return render_help_document("FAQ.md")

    @app.get("/help/models", response_class=HTMLResponse)
    def model_help():
        return render_help_document("MODELS.md")

    web = config.root / "apps/web/dist"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


def main():
    import uvicorn
    server = ServerConfig.load()
    uvicorn.run(create_app(), host=server.host, port=server.port, access_log=False,
                proxy_headers=server.enabled, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
