# Worker Protocol

The FastAPI server is the orchestrator. Photoshop UXP plugins connect to it as workers over WebSocket.

## Worker Connect

```json
{
  "type": "worker.hello",
  "worker_id": "tuong-sicula",
  "platform": "darwin",
  "max_concurrency": 1
}
```

Server response:

```json
{
  "type": "worker.ready",
  "worker_id": "tuong-sicula"
}
```

## Render Request

```json
{
  "type": "render.request",
  "job_id": "uuid",
  "input_url": "http://127.0.0.1:8000/uploads/input.jpeg",
  "output_url": "https://storage.googleapis.com/plugin-bucket/processed/input_abcd1234_processed.jpg",
  "output_upload_url": "https://storage.googleapis.com/plugin-bucket/processed/input_abcd1234_processed.jpg",
  "quality": 12
}
```

## Status Updates

```json
{
  "type": "job.status",
  "job_id": "uuid",
  "status": "processing"
}
```

Supported statuses:

```text
queued
assigned
downloading
processing
uploading
completed
failed
```

## Completion

```json
{
  "type": "job.completed",
  "job_id": "uuid",
  "output_url": "https://storage.googleapis.com/plugin-bucket/processed/input_abcd1234_processed.jpg"
}
```

## Failure

```json
{
  "type": "job.failed",
  "job_id": "uuid",
  "error": "Upload failed with HTTP 403"
}
```

