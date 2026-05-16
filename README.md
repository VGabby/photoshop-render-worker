# Photoshop Camera Raw Render Worker

This repository is a small monorepo for a Photoshop-backed render worker.

## Folders

- `photoshop_render_server/` - FastAPI orchestrator with in-memory jobs and WebSocket worker dispatch.
- `photoshop_uxp_worker/` - Photoshop UXP plugin scaffold that connects as a worker and renders JPEGs.
- `docs/` - protocol notes and git workflow.

## MVP Flow

```text
Client -> FastAPI POST /jobs { input_url }
FastAPI -> Photoshop plugin over WebSocket
Plugin downloads JPEG
Plugin opens JPEG in Photoshop
Plugin saves rendered JPEG with saveAs.jpg(... quality: 12 ...)
Plugin uploads to output_upload_url
FastAPI marks job completed
```

## Output Naming

The server generates:

```text
{original_name}_{short_job_id}_processed.jpg
```

Example:

```text
photo-001_550e8400_processed.jpg
```

## Start Server

```bash
cd photoshop_render_server
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then load `photoshop_uxp_worker/` with Adobe UXP Developer Tool and connect to:

```text
ws://127.0.0.1:8000/workers/ws
```

For same-machine testing with a local JPEG:

```bash
curl -X POST http://127.0.0.1:8000/jobs/upload \
  -F 'file=@/Users/bachhoang/Downloads/test_acr.jpeg'
```

## Git

This repo is set up so the Photoshop worker monorepo can be committed without runtime files:

```bash
git add .
git status
```

See `docs/commit-guide.md` before the first commit.
