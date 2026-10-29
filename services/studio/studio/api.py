"""Shared desktop/server HTTP controller, with explicit authenticated server mode."""
from contextlib import asynccontextmanager
import hmac
from pathlib import Path
import hashlib
import logging
import os
import re
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
from starlette.concurrency import run_in_threadpool
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
from .cache import RuntimeCache
from .access import Access
from .authentication import AuthService
from .auth_api import auth_router
from .request_limits import RequestLimits
from .workspace_lease import WorkspaceLease
from .media_maintenance import PreviewMaintenance
from .resources import ResourcePolicy, ResourceLimitError
from .recorder import RecorderConfig, RecorderService
from .recording_api import recording_router


MEDIA_SUFFIXES = {".mp4", ".flv", ".mkv", ".mov", ".webm", ".avi", ".ts", ".m4v",
                  ".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus"}


class PreviewRequest(Record):
    profile: Literal["low", "high"] = "low"


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
    policy = ResourcePolicy.load(server.enabled)
    access = Access(server)
    recorder_config = RecorderConfig.load()
    lease = WorkspaceLease(config.data)
    lease.acquire()
    cleanup = []
    try:
        return _create_app(server, config, policy, access, recorder_config, lease, cleanup)
    except BaseException:
        for close in reversed(cleanup):
            try:
                close()
            except Exception:
                logging.exception("Failed to close partially initialized service")
        lease.close()
        raise


def _create_app(server, config, policy, access, recorder_config, lease, cleanup):
    store = Store(config.data / "studio.sqlite3")
    auth = AuthService(server, access, store)
    os.environ.setdefault("SONGCUT_CPU_THREADS", str(policy.threads))
    runner = JobRunner(store, policy)
    cleanup.append(lambda: runner.close(preserve_pending=True))
    media = FFmpegMedia()
    library = ModelLibrary(config, store)
    studio = Studio(config.data, store, runner, None, media, policy)
    training = TrainingService(config, store, register_model=lambda path, metadata:
        library.register_file(path, f"{metadata['target_name']} · 训练模型", "trained", metadata).model_dump())
    cleanup.append(training.close)
    compute_lock = Lock()
    cache = RuntimeCache(config.data, store)
    cleanup.append(cache.close)
    token = secrets.token_urlsafe(32)

    def restore_detector(info):
        model, path = library.resolve(info["model_id"])
        if model.sha256 != info["model_sha256"]:
            raise ValueError("模型已变化，请重新发起检测")
        device = "cpu" if policy.low else library.resolve_device(info["device"])
        return AutoSlicerDetector(config.upstream, path, device, config.model_python, media.ffmpeg)
    studio.detector_factory = restore_detector

    def make_project(path, name, **extra):
        duration, has_video = media.probe(path)
        metadata = media.metadata(path)
        return Project(name=name, source_name=path.name, source_file=str(path), duration_ms=duration,
                       has_video=has_video, source_fps=metadata["fps"], source_width=metadata["width"],
                       source_height=metadata["height"], source_browser_compatible=metadata["browser_compatible"], **extra)

    def prepare_preview(project):
        if not project.has_video:
            return
        try:
            studio.preview(project.id, owner=None)
        except (ConflictError, ResourceLimitError):
            pass  # Durable source remains available; UI can retry when queue drains.

    def import_recording(path, metadata, project_id):
        try:
            project = store.get(project_id)
        except KeyError:
            project = make_project(path, metadata.get("title") or path.stem, id=project_id, shared=True,
                                   owner=server.username or "local", recording=metadata)
            store.create(project)
        prepare_preview(project)
        return project.id

    recorder = RecorderService(recorder_config, store, import_recording)
    cleanup.append(recorder.close)
    previews = PreviewMaintenance(studio, training)
    cleanup.append(previews.close)

    def visible_project(request, ident, write=False):
        project = store.get(ident)
        user = request.state.identity
        if not access.view(user, project):
            raise KeyError(ident)
        if write and not access.edit(user, project):
            raise HTTPException(403, "请先创建自己的剪辑副本")
        return project

    def job_view(job):
        return job.model_dump(exclude={"task"})

    def project_view(request, project):
        value = studio.with_previews(project).model_dump()
        value["can_edit"] = access.edit(request.state.identity, project)
        value["can_clone"] = request.state.identity.role in {"admin", "editor"}
        return value

    def no_training_in_progress():
        if any(j["status"] in {"queued", "running"} for j in training.jobs()):
            raise ConflictError("模型正在训练，请等待训练结束后再提交媒体任务")

    @asynccontextmanager
    async def lifespan(app):
        try:
            runner.restore()
            recorder.start()
            cache.start()
            previews.start()
            yield
        finally:
            try:
                for close in reversed(cleanup):
                    try:
                        close()
                    except Exception:
                        logging.exception("Service cleanup failed")
            finally:
                lease.close()

    app = FastAPI(title="VTuber SongCut", version="0.1.0", lifespan=lifespan)
    app.state.studio = studio
    app.state.models = library
    app.state.training = training
    app.state.cache = cache
    app.state.recorder = recorder
    app.state.policy = policy
    app.state.lease = lease
    app.state.auth = auth
    hosts = server.allowed_hosts
    if recorder_config.enabled:
        hosts.append(os.environ.get("SONGCUT_RECORDER_WEBHOOK_HOST", "songcut"))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(RequestLimits, policy=policy, data=config.data, server=server.enabled)

    @app.middleware("http")
    async def protect_service(request: Request, call_next):
        path = request.url.path
        # Recorder V2 supplies a shared secret URL; its exact callback validates
        # independently before reading the body. No browser or other route bypass.
        if path == "/api/recorder/webhook" and request.method == "POST":
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin and origin not in server.allowed_origins:
            return JSONResponse({"detail": "请求来源不在工作台允许列表中"}, status_code=403)
        user = await run_in_threadpool(auth.identify, request)
        auth_entry = path in {"/api/auth/status", "/api/auth/login", "/api/auth/logout"}
        login_shell = server.enabled and server.auth_mode == "totp" and (path in {"/", "/index.html", "/favicon.ico"} or path.startswith("/assets/"))
        if user is None and path != "/api/health" and not auth_entry and not login_shell:
            headers = {"Cache-Control": "no-store"}
            if server.auth_mode == "basic":
                headers["WWW-Authenticate"] = 'Basic realm="SongCut", charset="UTF-8"'
            return JSONResponse({"detail": "请登录工作台"}, status_code=401, headers=headers)
        request.state.identity = user
        mutating = path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}
        if mutating and not auth_entry:
            expected_token = (await run_in_threadpool(auth.csrf, request)) if server.enabled and server.auth_mode == "totp" else hmac.new(token.encode(), user.name.encode(), hashlib.sha256).hexdigest()
            if not expected_token:
                return JSONResponse({"detail": "请重新登录工作台"}, status_code=401, headers={"Cache-Control": "no-store"})
            supplied_token = request.headers.get("x-studio-token", "")
            if not supplied_token.isascii() or not hmac.compare_digest(supplied_token, expected_token):
                return JSONResponse({"detail": "会话已失效，请刷新工作台"}, status_code=403)
        admin_path = (path.startswith("/api/training/") or path.startswith("/api/recorder/")
                      or path.startswith("/api/cache") or path == "/api/models/upload"
                      or (path.startswith("/api/models/") and path.endswith("/download"))
                      or (path.startswith("/api/recordings/") and mutating))
        viewer_media_action = bool(re.fullmatch(r"/api/projects/[^/]+/preview|/api/jobs/[^/]+/cancel", path))
        if user and not auth_entry and ((admin_path and not user.admin) or (mutating and user.role == "viewer" and not viewer_media_action)):
            return JSONResponse({"detail": "当前账户没有此操作权限"}, status_code=403)
        if mutating and (path.endswith("/upload") or path.endswith("/samples")):
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "无效的上传长度"}, status_code=400)
            if length > policy.upload_bytes + 1024 * 1024:
                return JSONResponse({"detail": "录播超过服务器上传限制"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            if path.endswith("/media") or path.startswith("/api/artifacts/"):
                response.headers["Cache-Control"] = "private, max-age=60, must-revalidate" if response.status_code < 400 else "no-store"
                response.headers["Vary"] = "Cookie" if server.auth_mode == "totp" and server.enabled else "Authorization"
            else:
                response.headers["Cache-Control"] = "no-store"
        elif request.url.path in {"/", "/index.html"}:
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse({"detail": "项目或片段不存在"}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ResourceLimitError)
    async def resource_limit(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status, headers={"Retry-After": "30"} if error.status == 429 else None)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"application": "VTuberSongCut", "version": "0.1.0"}

    @app.get("/api/session")
    def session(request: Request):
        value = auth.csrf(request) if server.enabled and server.auth_mode == "totp" else hmac.new(token.encode(), request.state.identity.name.encode(), hashlib.sha256).hexdigest()
        if not value:
            raise HTTPException(401, "请重新登录工作台")
        return JSONResponse({"token": value}, headers={"Cache-Control": "no-store"})

    @app.get("/api/capabilities")
    def capabilities(request: Request):
        return {**media.capabilities(), "detector_ready": (config.upstream / "src/infer.py").is_file(),
                "recognition": False, "alignment": False, "user": request.state.identity.name,
                "role": request.state.identity.role, "low_resource": policy.low,
                "training_enabled": not policy.low and request.state.identity.admin,
                "export_max_seconds": policy.export_seconds, "queue_limit": policy.max_pending}

    @app.get("/api/cache")
    def cache_status():
        return cache.inspect()

    @app.post("/api/cache/clean")
    def clean_cache():
        return cache.clean()

    @app.get("/api/projects")
    def projects(request: Request):
        return [project_view(request, p) for p in store.projects() if access.view(request.state.identity, p)]

    @app.post("/api/projects/upload", status_code=201)
    def upload(request: Request, file: UploadFile = File(...)):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in MEDIA_SUFFIXES:
            raise HTTPException(415, "请选择 MP4、FLV、MKV、MOV、WebM、AVI、TS、M4V 或常见音频文件")
        folder = config.data / "media" / uuid4().hex
        folder.mkdir(parents=True)
        source = folder / ("source" + suffix)
        try:
            policy.copy_upload(file.file, source)
            project = make_project(source, Path(file.filename).stem, owner=request.state.identity.name)
            project.source_name = Path(file.filename).name
            store.create(project)
        except Exception as exc:
            shutil.rmtree(folder)
            if isinstance(exc, ValueError):
                raise
            raise HTTPException(422, "导入未完成，请检查磁盘空间和文件权限后重试") from exc
        finally:
            file.file.close()
        prepare_preview(project)
        return project_view(request, project)

    @app.get("/api/projects/{ident}")
    def project(ident: str, request: Request):
        return project_view(request, visible_project(request, ident))

    @app.post("/api/projects/{ident}/clone", status_code=201)
    def clone_project(ident: str, request: Request):
        source = studio.with_previews(visible_project(request, ident))
        clone = source.model_copy(deep=True, update={"id": uuid4().hex, "revision": 0, "owner": request.state.identity.name,
                            "shared": False, "source_project_id": source.source_project_id or source.id,
                            "recording": None, "name": source.name + " · 我的剪辑"})
        store.create(clone)
        prepare_preview(clone)
        return project_view(request, clone)

    @app.put("/api/projects/{ident}")
    def save(ident: str, body: SaveProject, request: Request):
        project = visible_project(request, ident, write=True)
        project.clips = body.clips
        if "detector_model_id" in body.model_fields_set:
            if body.detector_model_id:
                library.resolve(body.detector_model_id)
            project.detector_model_id = body.detector_model_id
        if "detector_device" in body.model_fields_set:
            project.detector_device = body.detector_device
        return project_view(request, store.save(project, body.revision))

    @app.post("/api/projects/{ident}/detect", status_code=202)
    def detect(ident: str, request: Request):
        project = visible_project(request, ident, write=True)
        if not project.detector_model_id:
            raise ValueError("请先选择主播声线模型并保存，再开始检测")
        with compute_lock:
            if any(j["status"] in {"queued", "running"} for j in training.jobs()):
                raise ConflictError("模型正在训练，请等待完成或取消训练后再检测")
            info, checkpoint = library.resolve(project.detector_model_id)
            device = "cpu" if policy.low else library.resolve_device(project.detector_device)
            detector = AutoSlicerDetector(config.upstream, checkpoint, device, config.model_python, media.ffmpeg)
            return job_view(studio.detect(ident, detector, {"model_id": info.id, "model_name": info.name,
                                                   "model_sha256": info.sha256, "device": device}, owner=request.state.identity.name))

    @app.post("/api/projects/{ident}/preview", status_code=202)
    def preview(ident: str, request: Request, body: PreviewRequest = PreviewRequest()):
        visible_project(request, ident)
        no_training_in_progress()
        return job_view(studio.preview(ident, body.profile, owner=request.state.identity.name).model_copy(update={"project_id": ident}))

    @app.post("/api/projects/{ident}/clips/{clip_id}/export", status_code=202)
    def export(ident: str, clip_id: str, request: Request):
        visible_project(request, ident, write=True)
        no_training_in_progress()
        return job_view(studio.export(ident, clip_id, owner=request.state.identity.name))

    @app.post("/api/projects/{ident}/clips/{clip_id}/lyrics")
    def lyrics(ident: str, clip_id: str, body: ImportLyrics, request: Request):
        visible_project(request, ident, write=True)
        return project_view(request, studio.lyrics(ident, clip_id, body.text, body.offset_ms))

    @app.get("/api/projects/{ident}/media")
    def source_media(ident: str, request: Request, quality: Literal["low", "high", "source"] | None = None):
        project = studio.with_previews(visible_project(request, ident))
        if quality == "source" and not request.state.identity.admin:
            raise HTTPException(403, "原始文件仅管理员可直接读取")
        if quality is None and server.enabled:
            quality = "low"
        if quality is None:  # Desktop legacy clients only.
            path = project.preview_file or project.source_file
        elif quality == "source" or not project.has_video:
            path = project.source_file
        elif quality == "low":
            path = project.preview_file if project.preview_profile == "low320-v1" else None
        else:
            path = project.high_preview_file
        if not path or not Path(path).is_file():
            raise HTTPException(409, "预览尚未就绪，请等待生成或重新尝试")
        return FileResponse(path)

    @app.get("/api/jobs")
    def jobs(request: Request, project_id: str | None = None):
        target = visible_project(request, project_id) if project_id and not project_id.startswith("training-media:") else None
        if project_id and project_id.startswith("training-media:") and not request.state.identity.admin:
            raise HTTPException(403, "需要管理员权限")
        result = []
        history = store.jobs(project_id=project_id)
        if target:
            try:
                from .service import source_signature
                shared = store.preview_jobs(source_signature(target))
                seen = {j.id for j in history}
                history.extend(j for j in shared if j.id not in seen)
            except (OSError, ValueError):
                pass
        for job in history:
            if project_id and job.project_id != project_id:
                # Shared previews are one task even for distinct editing drafts.
                if not target or job.kind != "preview" or not job.task:
                    continue
                try:
                    from .service import source_signature
                    if job.task.get("source") != source_signature(target):
                        continue
                except OSError:
                    continue
                result.append(job.model_copy(update={"project_id": project_id}))
                continue
            if request.state.identity.admin or job.owner == request.state.identity.name:
                result.append(job)
            else:
                try:
                    if access.view(request.state.identity, store.get(job.project_id)):
                        result.append(job)
                except KeyError:
                    pass
        return [job_view(j) for j in result]

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request):
        job = store.get_job(job_id)
        if not request.state.identity.admin and job.owner != request.state.identity.name:
            raise HTTPException(403, "只能取消自己的任务")
        return job_view(runner.cancel(job_id))

    @app.get("/api/artifacts/{job_id}/{filename}")
    def artifact(job_id: str, filename: str, request: Request):
        job = store.get_job(job_id)
        if not request.state.identity.admin:
            visible_project(request, job.project_id)
        if not job or job.status != "succeeded" or filename not in job.artifacts or Path(filename).name != filename:
            raise HTTPException(404, "文件不存在或任务尚未完成")
        path = config.data / "jobs" / job_id / filename
        if not path.is_file():
            raise HTTPException(404, "文件已清理或不存在")
        return FileResponse(path, filename=filename)

    @app.get("/api/models")
    def models():
        items = library.list()
        runtime = library.runtime()
        if policy.low:
            runtime = dict(runtime)
            runtime['devices'] = [{**d, 'available': d['available'] and d['id'] in {'auto', 'cpu'},
                                   'label': '自动（CPU）' if d['id'] == 'auto' else d['label']} for d in runtime['devices']]
        return {"models": items, "devices": runtime["devices"], "engine_ready": runtime["engine_ready"],
                "engine_error": runtime["engine_error"] or library.seed_error}

    @app.post("/api/models/upload", status_code=201)
    def import_model(file: UploadFile = File(...), name: str = Form("")) -> ModelInfo:
        if policy.low and runner.active:
            raise ResourceLimitError('服务器正在处理媒体任务，请稍后导入模型')
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
            policy.copy_upload(file.file, path)
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
        if policy.low:
            raise HTTPException(403, "低配服务器已关闭训练，请在本地电脑训练后导入模型")
        with compute_lock:
            if any(j.status in {"queued", "running"} for j in store.jobs(limit=None)):
                raise ConflictError("正在处理媒体任务，请完成后再训练")
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

    @app.get("/help/recording", response_class=HTMLResponse)
    def recording_help():
        return render_help_document("RECORDING.md")

    @app.get("/help/models", response_class=HTMLResponse)
    def model_help():
        return render_help_document("MODELS.md")

    app.include_router(auth_router(auth))
    app.include_router(recording_router(recorder))

    web = config.root / "apps/web/dist"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


def main():
    import uvicorn
    server = ServerConfig.load()
    uvicorn.run(create_app(), host=server.host, port=server.port, access_log=False,
                proxy_headers=server.enabled, forwarded_allow_ips=server.trusted_proxy_ips)


if __name__ == "__main__":
    main()
