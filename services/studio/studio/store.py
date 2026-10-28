"""SQLite snapshots with optimistic concurrency; no ML objects in persistence."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .domain import Project, Job


class ConflictError(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, revision INTEGER, data TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_cache_key ON jobs(json_extract(data, '$.cache_key'))")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_render_key ON jobs(json_extract(data, '$.task.render_key'))")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_project ON jobs(json_extract(data, '$.project_id'))")

    @contextmanager
    def connect(self):
        database = sqlite3.connect(self.path, timeout=30)
        try:
            with database:
                yield database
        finally:
            database.close()

    def create(self, project: Project):
        with self.connect() as db:
            db.execute("INSERT INTO projects VALUES (?, ?, ?)", (project.id, project.revision, project.model_dump_json()))

    def get(self, ident: str) -> Project:
        with self.connect() as db:
            row = db.execute("SELECT data FROM projects WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return Project.model_validate_json(row[0])

    def projects(self) -> list[Project]:
        with self.connect() as db:
            return [Project.model_validate_json(row[0]) for row in db.execute("SELECT data FROM projects ORDER BY rowid DESC")]

    def save(self, project: Project, expected_revision: int) -> Project:
        project = Project.model_validate(project.model_dump())
        project.revision = expected_revision + 1
        with self.connect() as db:
            changed = db.execute("UPDATE projects SET revision=?, data=? WHERE id=? AND revision=?",
                                 (project.revision, project.model_dump_json(), project.id, expected_revision)).rowcount
            if changed != 1:
                raise ConflictError("项目已更新，请重新载入后再保存")
        return project

    def update_media(self, ident, **fields):
        if not set(fields) <= {"preview_file", "preview_profile", "high_preview_file"}:
            raise ValueError("Unsupported media field")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM projects WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError(ident)
            project = Project.model_validate_json(row[0])
            for key, value in fields.items():
                setattr(project, key, value)
            db.execute("UPDATE projects SET data=? WHERE id=?", (project.model_dump_json(), ident))

    def save_job(self, job: Job):
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (job.id, job.model_dump_json()))

    def get_job(self, ident: str) -> Job:
        with self.connect() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return Job.model_validate_json(row[0])

    def jobs(self, limit: int | None = 100, project_id: str | None = None) -> list[Job]:
        with self.connect() as db:
            query, parameters = "SELECT data FROM jobs", []
            if project_id is not None:
                query += " WHERE json_extract(data, '$.project_id')=?"
                parameters.append(project_id)
            query += " ORDER BY rowid DESC"
            if limit is not None:
                query += " LIMIT ?"
                parameters.append(limit)
            rows = db.execute(query, parameters)
            return [Job.model_validate_json(row[0]) for row in rows]

    def cached_job(self, value, field="cache_key"):
        expressions = {"cache_key": "$.cache_key", "render_key": "$.task.render_key"}
        expression = expressions[field]
        with self.connect() as db:
            row = db.execute(f"SELECT data FROM jobs WHERE json_extract(data, '{expression}')=? AND json_extract(data, '$.status')='succeeded' ORDER BY rowid DESC LIMIT 1", (value,)).fetchone()
        return Job.model_validate_json(row[0]) if row else None

    def preview_jobs(self, signature):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM jobs WHERE json_extract(data, '$.kind')='preview' AND json_extract(data, '$.task.source')=? ORDER BY rowid DESC LIMIT 20", (signature,)).fetchall()
        return [Job.model_validate_json(row[0]) for row in rows]

    def recover(self):
        for job in self.jobs(limit=None):
            if job.status in ("queued", "running"):
                job.status, job.message = "interrupted", "服务已重启；请重新发起此任务"
                self.save_job(job)
