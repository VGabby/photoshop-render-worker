import asyncio
import re
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
    connected_at: datetime
    last_seen_at: datetime


class WorkerConnection:
    def __init__(self, websocket: WebSocket, record: WorkerRecord) -> None:
        self.websocket = websocket
        self.record = record


app = FastAPI(title="Photoshop Render Server", version="0.1.0")

jobs: dict[str, JobRecord] = {}
workers: dict[str, WorkerConnection] = {}
state_lock = asyncio.Lock()
upload_dir = Path(__file__).resolve().parent.parent / "data" / "uploads"
upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")


def now() -> datetime:
    return datetime.now(timezone.utc)


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

    await dispatch_jobs()
    return job


async def dispatch_jobs() -> None:
    async with state_lock:
        idle_workers = [
            connection
            for connection in workers.values()
            if connection.record.status == WorkerStatus.idle
        ]
        queued_jobs = [job for job in jobs.values() if job.status == JobStatus.queued]

        assignments: list[tuple[WorkerConnection, JobRecord]] = []
        for worker, job in zip(idle_workers, queued_jobs, strict=False):
            job.status = JobStatus.assigned
            job.assigned_worker_id = worker.record.worker_id
            job.updated_at = now()
            worker.record.status = WorkerStatus.busy
            worker.record.current_job_id = job.job_id
            worker.record.last_seen_at = now()
            assignments.append((worker, job))

    for worker, job in assignments:
        await worker.websocket.send_json(
            {
                "type": "render.request",
                "job_id": job.job_id,
                "input_url": job.input_url,
                "output_url": job.output_url,
                "output_upload_url": job.output_upload_url,
                "quality": job.quality,
            }
        )


async def release_worker(worker_id: str) -> None:
    async with state_lock:
        worker = workers.get(worker_id)
        if worker is None:
            return
        worker.record.status = WorkerStatus.idle
        worker.record.current_job_id = None
        worker.record.last_seen_at = now()


async def fail_active_job_on_disconnect(worker_id: str) -> None:
    async with state_lock:
        worker = workers.pop(worker_id, None)
        if worker is None or worker.record.current_job_id is None:
            return

        job = jobs.get(worker.record.current_job_id)
        if job and job.status not in {JobStatus.completed, JobStatus.failed}:
            job.status = JobStatus.failed
            job.error = f"Worker {worker_id} disconnected while processing"
            job.updated_at = now()


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
    return [connection.record for connection in workers.values()]


@app.websocket("/workers/ws")
async def worker_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    worker_id: str | None = None

    try:
        hello = await websocket.receive_json()
        if hello.get("type") != "worker.hello":
            await websocket.close(code=1008, reason="Expected worker.hello")
            return

        worker_id = str(hello.get("worker_id") or uuid4())
        timestamp = now()
        record = WorkerRecord(
            worker_id=worker_id,
            status=WorkerStatus.idle,
            platform=hello.get("platform"),
            max_concurrency=int(hello.get("max_concurrency") or 1),
            connected_at=timestamp,
            last_seen_at=timestamp,
        )

        async with state_lock:
            workers[worker_id] = WorkerConnection(websocket, record)

        await websocket.send_json({"type": "worker.ready", "worker_id": worker_id})
        await dispatch_jobs()

        while True:
            message: dict[str, Any] = await websocket.receive_json()
            message_type = message.get("type")
            job_id = message.get("job_id")

            async with state_lock:
                worker = workers.get(worker_id)
                if worker:
                    worker.record.last_seen_at = now()

                job = jobs.get(job_id) if job_id else None
                if message_type == "job.status" and job:
                    status = message.get("status")
                    if status in JobStatus._value2member_map_:
                        job.status = JobStatus(status)
                        job.updated_at = now()
                elif message_type == "job.completed" and job:
                    job.status = JobStatus.completed
                    job.error = None
                    job.updated_at = now()
                elif message_type == "job.failed" and job:
                    job.status = JobStatus.failed
                    job.error = str(message.get("error") or "Worker failed without details")
                    job.updated_at = now()

            if message_type in {"job.completed", "job.failed"}:
                await release_worker(worker_id)
                await dispatch_jobs()

    except WebSocketDisconnect:
        if worker_id:
            await fail_active_job_on_disconnect(worker_id)
    except Exception as exc:
        if worker_id:
            await fail_active_job_on_disconnect(worker_id)
        try:
            await websocket.close(code=1011, reason=str(exc))
        except RuntimeError:
            pass
