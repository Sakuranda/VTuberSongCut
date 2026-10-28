"""Private BililiveRecorder adapter and durable, bounded completed-file import."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import logging
import math
import os
import shutil
from pathlib import Path, PurePosixPath
from threading import Event, Lock, Thread
import time
from urllib.parse import urlsplit, urlencode
from uuid import UUID

import httpx
from .store import ConflictError

LOG = logging.getLogger(__name__)
class RecorderUnavailable(Exception): pass
class RecorderQueueFull(Exception): pass


@dataclass(frozen=True)
class RecorderConfig:
    url: str = ""
    username: str = ""
    password: str = ""
    secret: str = ""
    root: Path | None = None
    max_rooms: int = 1
    queue_limit: int = 32
    stability_seconds: float = 1.0
    reserve_bytes: int = 1024**3

    @property
    def enabled(self): return bool(self.url)

    @classmethod
    def load(cls):
        url = os.environ.get("SONGCUT_RECORDER_URL", "").rstrip("/")
        if not url: return cls()
        p = urlsplit(url)
        if (p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password
                or p.path not in {"", "/"} or p.query or p.fragment):
            raise ValueError("SONGCUT_RECORDER_URL 必须为固定 HTTP(S) 地址")
        p.port
        secret = os.environ.get("SONGCUT_RECORDER_WEBHOOK_SECRET", "")
        user = os.environ.get("SONGCUT_RECORDER_USERNAME", "")
        password = os.environ.get("SONGCUT_RECORDER_PASSWORD", "")
        root = os.environ.get("SONGCUT_RECORDER_ROOT", "")
        if not 32 <= len(secret) <= 256 or not user or not password or not root:
            raise ValueError("录播接入需要独立账号、密码、录制根目录和至少 32 字符的 webhook 密钥")
        if not Path(root).expanduser().is_absolute(): raise ValueError("录制根目录必须为绝对路径")
        rooms = int(os.environ.get("SONGCUT_RECORDER_MAX_ROOMS", "1"))
        limit = int(os.environ.get("SONGCUT_RECORDER_QUEUE_LIMIT", "32"))
        if not 1 <= rooms <= 50 or not 1 <= limit <= 1000: raise ValueError("录制房间数或队列上限无效")
        reserve = int(os.environ.get("SONGCUT_RECORDER_DISK_RESERVE_MB", "1024"))
        if not 0 <= reserve <= 1048576: raise ValueError("录制磁盘保留空间无效")
        return cls(url, user, password, secret, Path(root).expanduser().resolve(), rooms, limit, reserve_bytes=reserve * 1024**2)


class RecorderClient:
    def __init__(self, config):
        self.config, self.lock = config, Lock()
        self.client = httpx.Client(base_url=config.url or "http://127.0.0.1", timeout=8,
            auth=(config.username, config.password), follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))

    def request(self, method, path, body=None):
        if not self.config.enabled: raise RecorderUnavailable("尚未配置录播姬，请按录制部署说明启用")
        try:
            r = self.client.request(method, path, json=body)
            if not 200 <= r.status_code < 300:
                raise RecorderUnavailable(f"录播姬请求未完成（HTTP {r.status_code}），请检查连接与账号")
            return r.json() if r.content else None
        except (httpx.HTTPError, ValueError) as exc:
            raise RecorderUnavailable("无法连接录播姬或返回格式异常，请检查服务与账号") from exc

    @staticmethod
    def room(value):
        if not isinstance(value, dict): raise RecorderUnavailable("录播姬返回的房间格式异常")
        return {"room_id": value.get("roomId"), "name": value.get("name") or "", "title": value.get("title") or "",
                "auto_record": bool(value.get("autoRecord")), "recording": bool(value.get("recording")), "streaming": bool(value.get("streaming"))}

    def rooms(self):
        values = self.request("GET", "/api/room")
        if not isinstance(values, list): raise RecorderUnavailable("录播姬直播间列表格式异常")
        return [self.room(v) for v in values]

    def check_disk(self):
        try:
            if not self.config.root or shutil.disk_usage(self.config.root).free < self.config.reserve_bytes:
                raise RecorderUnavailable("录制磁盘空间不足，请释放空间后重新启用录制")
        except OSError as exc:
            raise RecorderUnavailable("无法读取录制磁盘，请检查挂载后重新启用录制") from exc

    def _capacity(self, room_id):
        self.check_disk()
        others = [r for r in self.rooms() if r["room_id"] != room_id and (r["recording"] or r["auto_record"])]
        if len(others) >= self.config.max_rooms:
            raise ConflictError(f"已达到 {self.config.max_rooms} 个自动录制房间上限，请先停止其他房间并关闭自动录制")

    def add(self, room_id, auto_record=True):
        with self.lock:
            if auto_record: self._capacity(room_id)
            return self.room(self.request("POST", "/api/room", {"roomId": room_id, "autoRecord": auto_record}))

    def configure(self, room_id, auto_record):
        with self.lock:
            if auto_record: self._capacity(room_id)
            self.request("POST", f"/api/room/{room_id}/config", {"autoRecord": auto_record})
            return self.room(self.request("GET", f"/api/room/{room_id}"))

    def action(self, room_id, action):
        if action not in {"start", "stop"}: raise ValueError("不支持的录制操作")
        with self.lock:
            if action == "start": self._capacity(room_id)
            return self.room(self.request("POST", f"/api/room/{room_id}/{action}"))

    def configure_webhook(self, origin):
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("回调地址必须是固定 HTTP(S) 根地址")
        path = "/api/recorder/webhook"
        target = origin.rstrip("/") + path + "?" + urlencode({"token": self.config.secret})
        current = self.request("GET", "/api/config/global")
        value = current.get("optionalWebHookUrlsV2") if isinstance(current, dict) else None
        if not isinstance(value, dict) or not isinstance(value.get("hasValue"), bool):
            raise RecorderUnavailable("录播姬配置格式不匹配，请通过管理界面设置 Webhook V2")
        raw = value.get("value") if value["hasValue"] else ""
        if raw is not None and not isinstance(raw, str): raise RecorderUnavailable("录播姬 Webhook 配置格式异常")
        hooks = [line.strip() for line in (raw or "").splitlines() if line.strip()]
        def ours(hook):
            existing = urlsplit(hook)
            return existing.scheme == parsed.scheme and existing.netloc == parsed.netloc and existing.path == path
        others = [hook for hook in hooks if not ours(hook)]
        desired = "\n".join([*others, target])
        if desired != (raw or ""):
            self.request("POST", "/api/config/global", {"optionalWebHookUrlsV2": {"hasValue": True, "value": desired}})
        verified = self.request("GET", "/api/config/global")
        saved = verified.get("optionalWebHookUrlsV2", {}) if isinstance(verified, dict) else {}
        if not saved.get("hasValue") or saved.get("value") != desired: raise RecorderUnavailable("录播姬没有保存 Webhook 设置，请检查版本")
        return {"configured": True, "retained_other_hooks": len(others)}

    def close(self): self.client.close()


class RecorderService:
    def __init__(self, config, store, import_recording):
        self.config, self.store, self.import_recording = config, store, import_recording
        self.client = RecorderClient(config)
        self.stop, self.wake, self.thread = Event(), Event(), None
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE, room_id INTEGER NOT NULL,
                session_id TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL, opened_at TEXT NOT NULL,
                closed_at TEXT NOT NULL, expected_size INTEGER NOT NULL, duration REAL NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                project_id TEXT, error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)''')
            db.execute("CREATE TABLE IF NOT EXISTS recorder_state (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute('''CREATE TABLE IF NOT EXISTS recorder_events (
                event_id TEXT PRIMARY KEY, digest TEXT NOT NULL, recording_id TEXT NOT NULL)''')

    def authorized(self, token):
        return bool(self.config.enabled and self.config.secret and isinstance(token, str)
                    and len(token) <= 256 and hmac.compare_digest(hashlib.sha256(self.config.secret.encode()).digest(), hashlib.sha256(token.encode()).digest()))

    def source_path(self, relative):
        if not self.config.root or not isinstance(relative, str) or not relative or len(relative) > 1024:
            raise ValueError("录制文件路径无效")
        normalized = relative.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or any(p in {"", ".", ".."} for p in normalized.split("/")) or ":" in normalized or "\x00" in normalized:
            raise ValueError("录制文件必须位于指定录制目录内")
        if pure.suffix.lower() not in {".flv", ".mp4", ".mkv", ".ts"}: raise ValueError("不支持的录制文件格式")
        path = self.config.root
        for part in pure.parts:
            path /= part
            if path.is_symlink() or path.resolve() != path: raise ValueError("录制文件不能经过符号链接或目录联接")
        return path

    def accept(self, event):
        if not self.config.enabled: raise RecorderUnavailable("尚未启用录播接入")
        if not isinstance(event, dict): raise ValueError("Webhook 必须是 JSON 对象")
        if event.get("EventType") != "FileClosed": return {"accepted": True, "ignored": True}
        try:
            event_id = str(UUID(event["EventId"]))
            d = event["EventData"]
            session_id = str(UUID(d["SessionId"]))
            room_id, size, duration = d["RoomId"], d["FileSize"], d["Duration"]
            if type(room_id) is not int or not 0 < room_id <= 2147483647 or type(size) is not int or not 0 < size <= 2**63-1: raise ValueError()
            if type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0: raise ValueError()
            path = self.source_path(d["RelativePath"])
            relative = path.relative_to(self.config.root).as_posix()
            opened, closed = d["FileOpenTime"], d["FileCloseTime"]
            if not isinstance(opened, str) or not isinstance(closed, str) or len(opened) > 64 or len(closed) > 64: raise ValueError()
            if datetime.fromisoformat(closed) < datetime.fromisoformat(opened): raise ValueError()
            name, title = str(d.get("Name", ""))[:200], str(d.get("Title", ""))[:300]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError("录播姬文件完成事件无效，请检查路径与事件字段") from exc
        digest = hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        ident = hashlib.sha256(f"{room_id}\0{session_id}\0{relative}\0{opened}\0{size}".encode()).hexdigest()[:32]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT digest,recording_id FROM recorder_events WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                if existing[0] != digest: raise ConflictError("相同事件 ID 的内容发生变化，已拒绝")
                return {"accepted": True, "recording_id": existing[1], "duplicate": True}
            old = db.execute("SELECT id FROM recordings WHERE relative_path=?", (relative,)).fetchone()
            if old and old[0] != ident: raise ConflictError("该录制路径已归档，请为新录制使用唯一文件名，避免覆盖素材")
            if not old:
                self._check_queue(db)
                db.execute('''INSERT INTO recordings
                    (id,relative_path,room_id,session_id,name,title,opened_at,closed_at,expected_size,duration,status,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (ident, relative, room_id, session_id, name, title, opened, closed, size, duration, "queued", time.time()))
            db.execute("INSERT INTO recorder_events VALUES (?,?,?)", (event_id, digest, ident))
        self.wake.set()
        return {"accepted": True, "recording_id": ident, "duplicate": bool(old)}

    def _check_queue(self, db):
        count = db.execute("SELECT COUNT(*) FROM recordings WHERE status IN ('queued','importing')").fetchone()[0]
        if count >= self.config.queue_limit: raise RecorderQueueFull("录播导入队列已满，请稍后重试；源文件仍保留")

    @staticmethod
    def view(row):
        return {key: row[key] for key in ("id", "room_id", "session_id", "name", "title", "opened_at", "closed_at",
                "expected_size", "duration", "status", "attempts", "project_id", "error")}

    def recordings(self, limit=100):
        import sqlite3
        with self.store.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM recordings ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self.view(row) for row in rows]

    def retry(self, ident):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,attempts FROM recordings WHERE id=?", (ident,)).fetchone()
            if not row: raise KeyError(ident)
            if row[0] != "failed": raise ConflictError("只有导入失败的录播可以重试")
            if row[1] >= 6: raise ConflictError("该文件已重试 6 次，请检查文件、磁盘和录播配置")
            self._check_queue(db)
            db.execute("UPDATE recordings SET status='queued', next_attempt=0, error='' WHERE id=?", (ident,))
        self.wake.set()
        return {"accepted": True, "recording_id": ident}

    def status(self):
        with self.store.connect() as db: counts = dict(db.execute("SELECT status,COUNT(*) FROM recordings GROUP BY status"))
        result = {"enabled": self.config.enabled, "connected": False, "max_rooms": self.config.max_rooms,
                  "queue_limit": self.config.queue_limit, "counts": counts, "message": "尚未启用录播姬接入"}
        if self.config.enabled:
            try:
                self.client.rooms()
                result.update(connected=True, message="录播姬已连接，文件完成后会自动导入")
            except RecorderUnavailable as exc: result["message"] = str(exc)
        with self.store.connect() as db:
            warning = db.execute("SELECT value FROM recorder_state WHERE key='disk_warning'").fetchone()
        result["disk_warning"] = warning[0] if warning else ""
        if self.config.enabled:
            try: self.client.check_disk()
            except RecorderUnavailable as exc: result["disk_warning"] = str(exc)
        return result

    def guard_disk(self):
        try: self.client.check_disk()
        except RecorderUnavailable:
            warning = "录制磁盘不足或不可读，已暂停自动录制；释放空间后请手动重新启用"
            try:
                for room in self.client.rooms():
                    ident = room["room_id"]
                    if self.stop.is_set(): break
                    for operation in ([lambda: self.client.configure(ident, False)] if room["auto_record"] else []) + ([lambda: self.client.action(ident, "stop")] if room["recording"] else []):
                        try: operation()
                        except RecorderUnavailable:
                            warning = "录制磁盘不足，部分房间未能停止，请检查录播姬与存储"
                            LOG.exception("Could not pause a recorder room")
            except RecorderUnavailable:
                warning = "录制磁盘不足且暂时无法联系录播姬，请立即检查录制服务和存储"
                LOG.exception("Could not pause recorder on disk pressure")
            with self.store.connect() as db:
                db.execute("INSERT INTO recorder_state VALUES ('disk_warning',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (warning,))
            return False
        return True

    def acknowledge_disk_warning(self):
        with self.store.connect() as db: db.execute("DELETE FROM recorder_state WHERE key='disk_warning'")

    def _claim(self):
        import sqlite3
        with self.store.connect() as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM recordings WHERE status='queued' AND next_attempt<=? ORDER BY created_at LIMIT 1", (time.time(),)).fetchone()
            if row:
                db.execute("UPDATE recordings SET status='importing',attempts=attempts+1 WHERE id=?", (row["id"],))
                return dict(row)

    def process_one(self):
        row = self._claim()
        if not row: return False
        try:
            source = self.source_path(row["relative_path"])
            before = source.stat()
            if not source.is_file() or before.st_size != row["expected_size"]: raise ValueError("录制文件尚未完整可读，或大小已变化")
            if self.stop.wait(self.config.stability_seconds): raise ValueError("服务正在关闭")
            source = self.source_path(row["relative_path"])
            after = source.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino): raise ValueError("录制文件仍在变化")
            project_id = self.import_recording(source, self.view(row), row["id"])
            with self.store.connect() as db:
                db.execute("UPDATE recordings SET status='ready',project_id=?,error='' WHERE id=?", (project_id, row["id"]))
        except Exception:
            LOG.exception("Recording metadata import failed: %s", row["id"])
            attempts = row["attempts"] + 1
            state = "queued" if attempts < 3 or self.stop.is_set() else "failed"
            with self.store.connect() as db:
                db.execute("UPDATE recordings SET status=?,next_attempt=?,error=? WHERE id=?", (state, time.time() + min(60, attempts * 5),
                            "录播导入未完成，请检查文件是否完整、挂载权限及磁盘空间后重试", row["id"]))
        return True

    def start(self):
        if not self.config.enabled or self.thread: return
        with self.store.connect() as db: db.execute("UPDATE recordings SET status='queued',next_attempt=0 WHERE status='importing'")
        def run():
            next_disk_check = 0
            while not self.stop.is_set():
                if time.monotonic() >= next_disk_check:
                    try: self.guard_disk()
                    except Exception: LOG.exception("Recorder disk guard failed")
                    next_disk_check = time.monotonic() + 30
                try:
                    if self.process_one(): continue
                except Exception: LOG.exception("Recording queue read failed; will retry")
                self.wake.wait(2)
                self.wake.clear()
        self.thread = Thread(target=run, name="songcut-recording-import", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set(); self.wake.set()
        if self.thread: self.thread.join(timeout=75)
        self.client.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Configure the private BililiveRecorder webhook")
    parser.add_argument("--configure-webhook", action="store_true", required=True)
    parser.parse_args()
    config = RecorderConfig.load()
    if not config.enabled: raise SystemExit("尚未启用录播姬接入")
    origin = os.environ.get("SONGCUT_RECORDER_CALLBACK_ORIGIN", "http://songcut:8765")
    client = RecorderClient(config)
    try:
        for attempt in range(10):
            try:
                result = client.configure_webhook(origin)
                print("录播姬 Webhook V2 已配置；保留其他回调数量：", result["retained_other_hooks"])
                return
            except RecorderUnavailable as exc:
                if attempt == 9: raise SystemExit(str(exc)) from exc
                time.sleep(2)
    finally: client.close()


if __name__ == "__main__": main()
