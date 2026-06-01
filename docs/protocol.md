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
  "worker_id": "tuong-sicula",
  "session_id": "uuid",
  "heartbeat_interval_seconds": 20
}
```

## Heartbeat

Workers send an application-level heartbeat because the UXP browser-style WebSocket API does not expose raw ping/pong frames.

```json
{
  "type": "worker.heartbeat",
  "worker_id": "tuong-sicula",
  "current_job_id": null,
  "busy": false
}
```

Server response:

```json
{
  "type": "worker.heartbeat_ack",
  "server_time": "2026-06-01T00:00:00+00:00"
}
```

If the server does not see any worker message for the stale timeout, it marks that worker stale/offline and fails any active job assigned to it.

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

Worker statuses exposed by `/workers`:

```text
idle
busy
offline
stale
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
