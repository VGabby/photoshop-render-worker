import asyncio
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

from .config import settings


class JobStatus(StrEnum):
    queued = "queued"
    assigned = "assigned"
    downloading = "downloading"
    processing = "processing"
    uploading = "uploading"
    completed = "completed"
    failed = "failed"


class WorkerStatus(StrEnum):
    idle = "idle"
    busy = "busy"
    offline = "offline"
    stale = "stale"


class CreateJobRequest(BaseModel):
    input_url: HttpUrl
    quality: int | None = Field(default=None, ge=1, le=12)


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    input_url: str
    output_url: str
    output_upload_url: str
    quality: int
    assigned_worker_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkerRecord(BaseModel):
    worker_id: str
    status: WorkerStatus
    platform: str | None = None
    max_concurrency: int = 1
    current_job_id: str | None = None
    session_id: str | None = None
    connected_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    reconnect_count: int = 0


class WorkerConnection:
    def __init__(self, websocket: WebSocket, session_id: str) -> None:
        self.websocket = websocket
        self.session_id = session_id


data_dir = Path(__file__).resolve().parent.parent / "data"
upload_dir = data_dir / "uploads"
db_path = data_dir / "render_server.sqlite3"
data_dir.mkdir(parents=True, exist_ok=True)
upload_dir.mkdir(parents=True, exist_ok=True)
jobs: dict[str, JobRecord] = {}
worker_records: dict[str, WorkerRecord] = {}
worker_connections: dict[str, WorkerConnection] = {}
state_lock = asyncio.Lock()


def now() -> datetime:
    return datetime.now(timezone.utc)


def dt_to_db(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def dt_from_db(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                input_url TEXT NOT NULL,
                output_url TEXT NOT NULL,
                output_upload_url TEXT NOT NULL,
                quality INTEGER NOT NULL,
                assigned_worker_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS workers (
                worker_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                platform TEXT,
                max_concurrency INTEGER NOT NULL,
                current_job_id TEXT,
                session_id TEXT,
                connected_at TEXT,
                last_seen_at TEXT,
                last_heartbeat_at TEXT,
                reconnect_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def save_job(job: JobRecord) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO jobs (
                job_id, status, input_url, output_url, output_upload_url, quality,
                assigned_worker_id, error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                input_url = excluded.input_url,
                output_url = excluded.output_url,
                output_upload_url = excluded.output_upload_url,
                quality = excluded.quality,
                assigned_worker_id = excluded.assigned_worker_id,
                error = excluded.error,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                job.job_id,
                job.status,
                job.input_url,
                job.output_url,
                job.output_upload_url,
                job.quality,
                job.assigned_worker_id,
                job.error,
                dt_to_db(job.created_at),
                dt_to_db(job.updated_at),
            ),
        )


def save_worker(worker: WorkerRecord) -> None:
    with get_db() as db:
        db.execute(
            """
            INSERT INTO workers (
                worker_id, status, platform, max_concurrency, current_job_id,
                session_id, connected_at, last_seen_at, last_heartbeat_at, reconnect_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                status = excluded.status,
                platform = excluded.platform,
                max_concurrency = excluded.max_concurrency,
                current_job_id = excluded.current_job_id,
                session_id = excluded.session_id,
                connected_at = excluded.connected_at,
                last_seen_at = excluded.last_seen_at,
                last_heartbeat_at = excluded.last_heartbeat_at,
                reconnect_count = excluded.reconnect_count
            """,
            (
                worker.worker_id,
                worker.status,
                worker.platform,
                worker.max_concurrency,
                worker.current_job_id,
                worker.session_id,
                dt_to_db(worker.connected_at),
                dt_to_db(worker.last_seen_at),
                dt_to_db(worker.last_heartbeat_at),
                worker.reconnect_count,
            ),
        )


def load_state() -> None:
    init_db()
    restart_time = now()
    jobs_to_save: list[JobRecord] = []
    workers_to_save: list[WorkerRecord] = []
    active_on_restart = {
        JobStatus.assigned,
        JobStatus.downloading,
        JobStatus.processing,
        JobStatus.uploading,
    }

    with get_db() as db:
        for row in db.execute("SELECT * FROM jobs ORDER BY created_at"):
            status = JobStatus(row["status"])
            error = row["error"]
            updated_at = dt_from_db(row["updated_at"]) or restart_time
            if status in active_on_restart:
                status = JobStatus.failed
                error = "Server restarted during active job"
                updated_at = restart_time

            job = JobRecord(
                job_id=row["job_id"],
                status=status,
                input_url=row["input_url"],
                output_url=row["output_url"],
                output_upload_url=row["output_upload_url"],
                quality=row["quality"],
                assigned_worker_id=row["assigned_worker_id"],
                error=error,
                created_at=dt_from_db(row["created_at"]) or restart_time,
                updated_at=updated_at,
            )
            jobs[job.job_id] = job
            if status == JobStatus.failed and JobStatus(row["status"]) in active_on_restart:
                jobs_to_save.append(job)

        for row in db.execute("SELECT * FROM workers ORDER BY worker_id"):
            worker = WorkerRecord(
                worker_id=row["worker_id"],
                status=WorkerStatus.offline,
                platform=row["platform"],
                max_concurrency=row["max_concurrency"],
                current_job_id=None,
                session_id=None,
                connected_at=dt_from_db(row["connected_at"]),
                last_seen_at=dt_from_db(row["last_seen_at"]),
                last_heartbeat_at=dt_from_db(row["last_heartbeat_at"]),
                reconnect_count=row["reconnect_count"],
            )
            worker_records[worker.worker_id] = worker
            workers_to_save.append(worker)

    for job in jobs_to_save:
        save_job(job)
    for worker in workers_to_save:
        save_worker(worker)


async def liveness_loop() -> None:
    while True:
        await asyncio.sleep(settings.liveness_scan_seconds)
        stale_connections: list[tuple[str, WorkerConnection]] = []
        cutoff_seconds = settings.worker_stale_after_seconds
        timestamp = now()

        async with state_lock:
            for worker_id, connection in list(worker_connections.items()):
                worker = worker_records.get(worker_id)
                if not worker or worker.status == WorkerStatus.offline:
                    continue

                last_seen = worker.last_seen_at or worker.connected_at or timestamp
                if (timestamp - last_seen).total_seconds() < cutoff_seconds:
                    continue

                if worker.current_job_id:
                    job = jobs.get(worker.current_job_id)
                    if job and job.status not in {JobStatus.completed, JobStatus.failed}:
                        job.status = JobStatus.failed
                        job.error = f"Worker {worker_id} heartbeat timed out"
                        job.updated_at = timestamp
                        save_job(job)

                worker.status = WorkerStatus.stale
                worker.current_job_id = None
                worker.session_id = None
                save_worker(worker)
                stale_connections.append((worker_id, connection))
                worker_connections.pop(worker_id, None)

        for _, connection in stale_connections:
            try:
                await connection.websocket.close(code=1011, reason="Worker heartbeat timed out")
            except RuntimeError:
                pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_state()
    task = asyncio.create_task(liveness_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Photoshop Render Server", version="0.2.0", lifespan=lifespan)
app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")


def clean_original_name(input_url: str) -> str:
    parsed = urlparse(input_url)
    name = PurePosixPath(parsed.path).name or "image.jpg"
    stem = PurePosixPath(name).stem or "image"
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return clean or "image"


def join_url(base_url: str, filename: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(filename)}"


def build_output_urls(input_url: str, job_id: str) -> tuple[str, str]:
    original_name = clean_original_name(input_url)
    short_job_id = job_id.split("-", 1)[0]
    filename = f"{original_name}_{short_job_id}_processed.jpg"
    return (
        join_url(settings.output_public_base_url, filename),
        join_url(settings.output_upload_base_url, filename),
    )


async def enqueue_job(input_url: str, quality: int | None) -> JobRecord:
    job_id = str(uuid4())
    output_url, output_upload_url = build_output_urls(input_url, job_id)
    timestamp = now()
    job = JobRecord(
        job_id=job_id,
        status=JobStatus.queued,
        input_url=input_url,
        output_url=output_url,
        output_upload_url=output_upload_url,
        quality=quality or settings.default_jpeg_quality,
        created_at=timestamp,
        updated_at=timestamp,
    )

    async with state_lock:
        jobs[job_id] = job
        save_job(job)

    await dispatch_jobs()
    return job


async def dispatch_jobs() -> None:
    assignments: list[tuple[WorkerConnection, JobRecord]] = []
    async with state_lock:
        idle_worker_ids = [
            worker_id
            for worker_id, worker in worker_records.items()
            if worker.status == WorkerStatus.idle and worker_id in worker_connections
        ]
        queued_jobs = [job for job in jobs.values() if job.status == JobStatus.queued]
        timestamp = now()

        for worker_id, job in zip(idle_worker_ids, queued_jobs, strict=False):
            worker = worker_records[worker_id]
            connection = worker_connections[worker_id]
            job.status = JobStatus.assigned
            job.assigned_worker_id = worker.worker_id
            job.updated_at = timestamp
            worker.status = WorkerStatus.busy
            worker.current_job_id = job.job_id
            worker.last_seen_at = timestamp
            save_job(job)
            save_worker(worker)
            assignments.append((connection, job))

    for connection, job in assignments:
        try:
            await connection.websocket.send_json(
                {
                    "type": "render.request",
                    "job_id": job.job_id,
                    "input_url": job.input_url,
                    "output_url": job.output_url,
                    "output_upload_url": job.output_upload_url,
                    "quality": job.quality,
                }
            )
        except RuntimeError:
            await fail_active_job_on_disconnect(job.assigned_worker_id or "", connection.session_id)


async def release_worker(worker_id: str, session_id: str | None = None) -> None:
    async with state_lock:
        worker = worker_records.get(worker_id)
        if worker is None:
            return
        if session_id and worker.session_id != session_id:
            return
        worker.status = WorkerStatus.idle
        worker.current_job_id = None
        worker.last_seen_at = now()
        save_worker(worker)


async def fail_active_job_on_disconnect(worker_id: str, session_id: str | None = None) -> None:
    async with state_lock:
        worker = worker_records.get(worker_id)
        connection = worker_connections.get(worker_id)
        if worker is None:
            return
        if session_id and worker.session_id != session_id:
            return

        worker_connections.pop(worker_id, None)
        timestamp = now()
        if worker.current_job_id:
            job = jobs.get(worker.current_job_id)
            if job and job.status not in {JobStatus.completed, JobStatus.failed}:
                job.status = JobStatus.failed
                job.error = f"Worker {worker_id} disconnected while processing"
                job.updated_at = timestamp
                save_job(job)

        worker.status = WorkerStatus.offline
        worker.current_job_id = None
        worker.session_id = None
        worker.last_seen_at = timestamp
        save_worker(worker)

    if connection:
        try:
            await connection.websocket.close(code=1011, reason="Worker disconnected")
        except RuntimeError:
            pass


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs", response_model=JobRecord)
async def create_job(request: CreateJobRequest) -> JobRecord:
    input_url = str(request.input_url)
    if not input_url.lower().split("?", 1)[0].endswith((".jpg", ".jpeg")):
        raise HTTPException(status_code=400, detail="Only .jpg and .jpeg inputs are supported")

    return await enqueue_job(input_url, request.quality)


@app.post("/jobs/upload", response_model=JobRecord)
async def create_job_from_upload(
    request: Request,
    file: UploadFile = File(...),
    quality: int | None = None,
) -> JobRecord:
    filename = file.filename or "image.jpg"
    if not filename.lower().endswith((".jpg", ".jpeg")):
        raise HTTPException(status_code=400, detail="Only .jpg and .jpeg uploads are supported")

    upload_id = str(uuid4())
    safe_name = clean_original_name(filename)
    suffix = ".jpeg" if filename.lower().endswith(".jpeg") else ".jpg"
    stored_name = f"{safe_name}_{upload_id}{suffix}"
    stored_path = upload_dir / stored_name

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    stored_path.write_bytes(contents)

    input_url = str(request.url_for("uploads", path=stored_name))
    return await enqueue_job(input_url, quality)


@app.get("/jobs", response_model=list[JobRecord])
async def list_jobs() -> list[JobRecord]:
    return sorted(jobs.values(), key=lambda job: job.created_at, reverse=True)


@app.get("/jobs/{job_id}", response_model=JobRecord)
async def get_job(job_id: str) -> JobRecord:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/workers", response_model=list[WorkerRecord])
async def list_workers() -> list[WorkerRecord]:
    return sorted(worker_records.values(), key=lambda worker: worker.worker_id)


async def register_worker(websocket: WebSocket, hello: dict[str, Any]) -> tuple[str, str]:
    worker_id = str(hello.get("worker_id") or uuid4())
    session_id = str(uuid4())
    timestamp = now()
    old_connection: WorkerConnection | None = None

    async with state_lock:
        existing = worker_records.get(worker_id)
        if existing and existing.current_job_id:
            job = jobs.get(existing.current_job_id)
            if job and job.status not in {JobStatus.completed, JobStatus.failed}:
                job.status = JobStatus.failed
                job.error = f"Worker {worker_id} reconnected during active job"
                job.updated_at = timestamp
                save_job(job)

        old_connection = worker_connections.pop(worker_id, None)
        worker = WorkerRecord(
            worker_id=worker_id,
            status=WorkerStatus.idle,
            platform=hello.get("platform"),
            max_concurrency=int(hello.get("max_concurrency") or 1),
            current_job_id=None,
            session_id=session_id,
            connected_at=timestamp,
            last_seen_at=timestamp,
            last_heartbeat_at=timestamp,
            reconnect_count=(existing.reconnect_count + 1) if existing else 0,
        )
        worker_records[worker_id] = worker
        worker_connections[worker_id] = WorkerConnection(websocket, session_id)
        save_worker(worker)

    if old_connection:
        try:
            await old_connection.websocket.close(code=1012, reason="Worker replaced by reconnect")
        except RuntimeError:
            pass

    await websocket.send_json(
        {
            "type": "worker.ready",
            "worker_id": worker_id,
            "session_id": session_id,
            "heartbeat_interval_seconds": settings.worker_heartbeat_interval_seconds,
        }
    )
    await dispatch_jobs()
    return worker_id, session_id


@app.websocket("/workers/ws")
async def worker_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    worker_id: str | None = None
    session_id: str | None = None

    try:
        hello = await websocket.receive_json()
        if hello.get("type") != "worker.hello":
            await websocket.close(code=1008, reason="Expected worker.hello")
            return

        worker_id, session_id = await register_worker(websocket, hello)

        while True:
            message: dict[str, Any] = await websocket.receive_json()
            message_type = message.get("type")
            job_id = message.get("job_id")
            should_dispatch = False

            async with state_lock:
                worker = worker_records.get(worker_id)
                if not worker or worker.session_id != session_id:
                    continue

                timestamp = now()
                worker.last_seen_at = timestamp
                if message_type == "worker.heartbeat":
                    worker.last_heartbeat_at = timestamp
                    save_worker(worker)
                else:
                    save_worker(worker)

                job = jobs.get(job_id) if job_id else None
                job_is_terminal = bool(job and job.status in {JobStatus.completed, JobStatus.failed})
                if message_type == "job.status" and job and not job_is_terminal:
                    status = message.get("status")
                    if status in JobStatus._value2member_map_:
                        job.status = JobStatus(status)
                        job.updated_at = timestamp
                        save_job(job)
                elif message_type == "job.completed" and job and not job_is_terminal:
                    job.status = JobStatus.completed
                    job.error = None
                    job.updated_at = timestamp
                    save_job(job)
                    worker.status = WorkerStatus.idle
                    worker.current_job_id = None
                    save_worker(worker)
                    should_dispatch = True
                elif message_type == "job.failed" and job and not job_is_terminal:
                    job.status = JobStatus.failed
                    job.error = str(message.get("error") or "Worker failed without details")
                    job.updated_at = timestamp
                    save_job(job)
                    worker.status = WorkerStatus.idle
                    worker.current_job_id = None
                    save_worker(worker)
                    should_dispatch = True

            if message_type == "worker.heartbeat":
                await websocket.send_json({"type": "worker.heartbeat_ack", "server_time": dt_to_db(now())})

            if should_dispatch:
                await dispatch_jobs()

    except WebSocketDisconnect:
        if worker_id and session_id:
            await fail_active_job_on_disconnect(worker_id, session_id)
    except Exception as exc:
        if worker_id and session_id:
            await fail_active_job_on_disconnect(worker_id, session_id)
        try:
            await websocket.close(code=1011, reason=str(exc))
        except RuntimeError:
            pass
