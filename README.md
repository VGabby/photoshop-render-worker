# Photoshop Render Worker

Monorepo for using Photoshop as a render worker behind a FastAPI orchestration server.

## Design

- **FastAPI is the control plane.** It owns job creation, worker state, dispatch, and status APIs.
- **Photoshop UXP is the worker.** It connects outbound over WebSocket, downloads JPEG input, lets Photoshop render embedded Camera Raw settings, then uploads the JPEG output.
- **Storage is URL-based.** The server stores paths/URLs and job metadata, not image bytes, except for local development uploads.
- **Workers are long-lived and reconnecting.** Each worker keeps a stable `worker_id`, sends heartbeats, and reconnects with backoff after network/server failures.
- **SQLite is the local durable state.** Jobs and worker records survive server restarts; runtime data stays under `photoshop_render_server/data/`.
- **Simulator is worker-side.** `photoshop_uxp_worker/simulator/` can create many fake workers without Photoshop to test WebSocket stability from worker machines.

## Layout

```text
photoshop_render_server/   FastAPI server, SQLite state, REST + WebSocket APIs
photoshop_uxp_worker/      Photoshop UXP plugin and worker-side simulator
docs/                      Protocol and commit notes
```

## Run Server

```bash
cd photoshop_render_server
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Worker WebSocket URL:

```text
ws://127.0.0.1:8000/workers/ws
```

## Test Without Photoshop

```bash
cd photoshop_uxp_worker/simulator
npm install
node connection_simulator.js --server ws://127.0.0.1:8000/workers/ws --count 5
```

## Useful APIs

```text
POST /jobs          Create job from input_url
POST /jobs/upload   Dev-only local JPEG upload
GET  /jobs/{id}     Check job status
GET  /workers       Check worker state
WS   /workers/ws    Worker connection endpoint
```

See `docs/protocol.md` for message shapes.
