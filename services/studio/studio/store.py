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

    def jobs(self, limit: int | None = 100) -> list[Job]:
        with self.connect() as db:
            query = "SELECT data FROM jobs ORDER BY rowid DESC"
            rows = db.execute(query if limit is None else query + " LIMIT ?", () if limit is None else (limit,))
            return [Job.model_validate_json(row[0]) for row in rows]

    def recover(self):
        for job in self.jobs(limit=None):
            if job.status in ("queued", "running"):
                job.status, job.message = "interrupted", "服务已重启；请重新发起此任务"
                self.save_job(job)
