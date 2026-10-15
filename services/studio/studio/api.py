"""Loopback-only HTTP controller. Both browser and desktop use the same API."""
from contextlib import asynccontextmanager
import hmac
from pathlib import Path
import secrets
import shutil
from uuid import uuid4
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import Settings
from .domain import Record, Project, Clip, Job
from .store import Store, ConflictError
from .jobs import JobRunner
from .service import Studio
from .adapters.autoslicer import AutoSlicerDetector
from .adapters.media import FFmpegMedia


class SaveProject(Record):
    revision: int
    clips: list[Clip]


class ImportLyrics(Record):
    text: str
    offset_ms: int = 0


def create_app(settings: Settings | None = None):
    config = settings or Settings.load()
    store = Store(config.data / "studio.sqlite3")
    runner = JobRunner(store)
    media = FFmpegMedia()
    detector = AutoSlicerDetector(config.upstream, config.checkpoint, config.device, config.model_python, media.ffmpeg)
    studio = Studio(config.data, store, runner, detector, media)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        runner.close()

    app = FastAPI(title="VTuber SongCut", version="0.1.0", lifespan=lifespan)
    app.state.studio = studio
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def protect_local_service(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:8765", "http://localhost:8765", "http://127.0.0.1:5173", "http://localhost:5173"}:
            return JSONResponse({"detail": "此服务仅接受本机工作台请求"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not hmac.compare_digest(request.headers.get("x-studio-token", ""), token):
                return JSONResponse({"detail": "会话已失效，请刷新工作台"}, status_code=403)
        return await call_next(request)

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
        return {**media.capabilities(), "detector_ready": config.checkpoint.is_file() and (config.upstream / "src/infer.py").is_file(),
                "recognition": False, "alignment": False}

    @app.get("/api/projects")
    def projects() -> list[Project]:
        return store.projects()

    @app.post("/api/projects/upload", status_code=201)
    def upload(file: UploadFile = File(...)) -> Project:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".mp4", ".flv", ".mkv", ".mov", ".webm", ".wav", ".mp3", ".m4a"}:
            raise HTTPException(415, "请选择 MP4、FLV、MKV、MOV、WebM 或常见音频文件")
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
            raise HTTPException(422, f"无法导入文件，请检查媒体文件与 FFmpeg 安装：{type(exc).__name__}") from exc
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
        return store.save(project, body.revision)

    @app.post("/api/projects/{ident}/detect", status_code=202)
    def detect(ident: str) -> Job:
        return studio.detect(ident)

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
    def jobs() -> list[Job]:
        return store.jobs()

    @app.get("/api/artifacts/{job_id}/{filename}")
    def artifact(job_id: str, filename: str):
        job = store.get_job(job_id)
        if not job or job.status != "succeeded" or filename not in job.artifacts or Path(filename).name != filename:
            raise HTTPException(404, "文件不存在或任务尚未完成")
        return FileResponse(config.data / "jobs" / job_id / filename, filename=filename)

    web = config.root / "apps/web/dist"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8765, access_log=False)


if __name__ == "__main__":
    main()
