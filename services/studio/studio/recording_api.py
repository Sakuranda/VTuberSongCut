"""Recorder routes: normal controls use the application's admin middleware.

Only POST /api/recorder/webhook is exempted there; it has its own shared-secret
check below. BililiveRecorder v2 sends URL-only webhooks, not HMAC signatures.
"""
import json
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from .recorder import RecorderUnavailable, RecorderQueueFull


class RoomInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    room_id: int = Field(gt=0, le=2147483647)
    auto_record: bool = True


class RoomSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    auto_record: bool


def recording_router(service):
    router = APIRouter()

    def call(function, *args):
        try: return function(*args)
        except RecorderUnavailable as exc: raise HTTPException(503, str(exc)) from exc
        except RecorderQueueFull as exc: raise HTTPException(429, str(exc), headers={"Retry-After": "30"}) from exc

    @router.get("/api/recorder/status")
    def status(): return service.status()

    @router.get("/api/recorder/rooms")
    def rooms(): return call(service.client.rooms)

    @router.post("/api/recorder/rooms")
    def add(body: RoomInput):
        value = call(service.client.add, body.room_id, body.auto_record)
        if body.auto_record: service.acknowledge_disk_warning()
        return value

    @router.patch("/api/recorder/rooms/{room_id}")
    def configure(room_id: int, body: RoomSettings):
        if not 0 < room_id <= 2147483647: raise HTTPException(422, "房间号无效")
        value = call(service.client.configure, room_id, body.auto_record)
        if body.auto_record: service.acknowledge_disk_warning()
        return value

    @router.post("/api/recorder/rooms/{room_id}/{action}")
    def action(room_id: int, action: str):
        if not 0 < room_id <= 2147483647 or action not in {"start", "stop"}: raise HTTPException(422, "录制操作无效")
        value = call(service.client.action, room_id, action)
        if action == "start": service.acknowledge_disk_warning()
        return value

    @router.get("/api/recordings")
    def recordings(): return service.recordings()

    @router.post("/api/recordings/{ident}/retry", status_code=202)
    def retry(ident: str): return call(service.retry, ident)

    @router.post("/api/recorder/webhook", status_code=202)
    async def webhook(request: Request):
        if request.headers.get("origin") or not service.authorized(request.query_params.get("token")):
            raise HTTPException(403, "录播事件认证失败")
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > 65536 or int(length) < 0: raise ValueError()
            except ValueError as exc: raise HTTPException(413, "录播事件过大") from exc
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536: raise HTTPException(413, "录播事件过大")
        try: event = json.loads(body)
        except (ValueError, UnicodeError) as exc: raise HTTPException(422, "录播事件不是有效 JSON") from exc
        return call(service.accept, event)

    return router
