# Photoshop UXP Worker

UXP plugin scaffold for the Photoshop render worker.

It connects to the FastAPI WebSocket, receives one render job at a time, downloads the input JPEG, opens it in Photoshop, saves a rendered JPEG, uploads it to `output_upload_url`, and reports completion.

## Load in Photoshop

1. Open Adobe UXP Developer Tool.
2. Add this `photoshop_uxp_worker` folder as a plugin.
3. Load it into Photoshop.
4. Open the panel from Photoshop's Plugins menu.
5. Set WebSocket URL, for example:

```text
ws://127.0.0.1:8000/workers/ws
```

6. Click Connect.

## Connection Simulator

Use the simulator to test worker-server communication from a worker machine without opening Photoshop.

```bash
cd photoshop_uxp_worker/simulator
npm install
node connection_simulator.js \
  --server ws://127.0.0.1:8000/workers/ws \
  --count 5
```

Worker IDs include the local hostname to avoid collisions across machines:

```text
{hostname}-{prefix}-{index}
```

Example:

```text
mac-mini-office-sim-worker-001
```

For multi-machine tests, set a clear prefix:

```bash
node connection_simulator.js \
  --server wss://your-server/workers/ws \
  --count 10 \
  --prefix office-mac
```

The simulator only validates connection stability. If it receives a render job, it reports `job.failed` with `Connection simulator cannot render Photoshop jobs`.

## MVP Assumptions

- Photoshop is already open.
- Input files are `.jpg` or `.jpeg`.
- JPEGs contain embedded Camera Raw settings.
- Photoshop renders those settings when the plugin opens the image and calls `document.saveAs.jpg(...)`.
- `output_upload_url` accepts HTTP `PUT` with `image/jpeg`.
