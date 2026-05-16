# Photoshop Render Server

FastAPI orchestrator for Photoshop workers connected over WebSocket.

The server stores only job metadata. Images live in a public bucket or any URL-addressable storage. Workers receive an `input_url`, render through Photoshop, then upload to `output_upload_url`.

## Run

```bash
cd photoshop_render_server
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Environment

```env
OUTPUT_PUBLIC_BASE_URL=https://bucket.example.com/processed
OUTPUT_UPLOAD_BASE_URL=https://bucket.example.com/processed
DEFAULT_JPEG_QUALITY=12
JOB_TIMEOUT_SECONDS=600
```

For MVP, `OUTPUT_PUBLIC_BASE_URL` and `OUTPUT_UPLOAD_BASE_URL` can be the same public-write bucket URL. Later, keep `output_url` public-readable and make `output_upload_url` a signed upload URL.

## API

Create a render job:

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H 'content-type: application/json' \
  -d '{"input_url":"https://bucket.example.com/input/photo-001.jpg"}'
```

Create a render job from a local JPEG upload:

```bash
curl -X POST http://127.0.0.1:8000/jobs/upload \
  -F 'file=@/Users/bachhoang/Downloads/test_acr.jpeg'
```

Uploaded inputs are saved under `photoshop_render_server/data/uploads/` and served from `/uploads`. This is intended for same-machine development testing; production jobs should continue using public input URLs.

Check status:

```bash
curl http://127.0.0.1:8000/jobs/{job_id}
```

Worker WebSocket:

```text
ws://127.0.0.1:8000/workers/ws
```
