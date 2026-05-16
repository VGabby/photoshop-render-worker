const state = {
  socket: null,
  busy: false,
  workerId: "",
  currentJob: null,
  photoshopApp: null,
  photoshopCore: null,
  localFileSystem: null,
  storageFormats: null
};

init();

function init() {
  const savedWorkerId = localStorage.getItem("workerId") || defaultWorkerId();
  const savedServerUrl = localStorage.getItem("serverUrl") || "ws://127.0.0.1:8000/workers/ws";

  getEl("workerId").value = savedWorkerId;
  getEl("serverUrl").value = savedServerUrl;
  state.workerId = savedWorkerId;

  getEl("connectButton").addEventListener("click", connect);
  getEl("disconnectButton").addEventListener("click", disconnect);
  log("Worker panel ready.");

  try {
    const photoshop = require("photoshop");
    const uxp = require("uxp");
    state.photoshopApp = photoshop.app;
    state.photoshopCore = photoshop.core;
    state.localFileSystem = uxp.storage.localFileSystem;
    state.storageFormats = uxp.storage.formats;
    log("Photoshop and UXP APIs loaded.");
  } catch (error) {
    log(`API load failed: ${error.message || error}`);
  }
}

function getEl(id) {
  return document.getElementById(id);
}

function defaultWorkerId() {
  return `photoshop-worker-${Math.random().toString(16).slice(2, 10)}`;
}

function setConnected(isConnected) {
  const badge = getEl("statusBadge");
  badge.textContent = isConnected ? "Connected" : "Disconnected";
  badge.classList.toggle("connected", isConnected);
  getEl("connectButton").disabled = isConnected;
  getEl("disconnectButton").disabled = !isConnected;
}

function setCurrentJob(job) {
  state.currentJob = job;
  getEl("jobView").textContent = job ? JSON.stringify(job, null, 2) : "None";
}

function log(message) {
  const line = `[${new Date().toISOString()}] ${message}`;
  const logView = getEl("logView");
  logView.textContent = `${line}\n${logView.textContent}`.slice(0, 12000);
}

function send(message) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    throw new Error("Worker WebSocket is not connected");
  }
  state.socket.send(JSON.stringify(message));
}

function connect() {
  const workerId = getEl("workerId").value.trim() || defaultWorkerId();
  const serverUrl = getEl("serverUrl").value.trim();

  localStorage.setItem("workerId", workerId);
  localStorage.setItem("serverUrl", serverUrl);
  state.workerId = workerId;

  const socket = new WebSocket(serverUrl);
  state.socket = socket;

  socket.onopen = () => {
    setConnected(true);
    send({
      type: "worker.hello",
      worker_id: workerId,
      platform: navigator.platform || "unknown",
      max_concurrency: 1
    });
    log(`Connected to ${serverUrl}`);
  };

  socket.onmessage = async (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "worker.ready") {
      log(`Registered as ${message.worker_id}`);
      return;
    }

    if (message.type === "render.request") {
      await handleRenderRequest(message);
      return;
    }

    log(`Received ${message.type}`);
  };

  socket.onerror = () => {
    log("WebSocket error.");
  };

  socket.onclose = () => {
    state.socket = null;
    state.busy = false;
    setConnected(false);
    setCurrentJob(null);
    log("Disconnected.");
  };
}

function disconnect() {
  if (state.socket) {
    state.socket.close();
  }
}

async function handleRenderRequest(job) {
  if (state.busy) {
    send({
      type: "job.failed",
      job_id: job.job_id,
      error: "Worker received a job while busy"
    });
    return;
  }

  state.busy = true;
  setCurrentJob(job);

  try {
    await renderJob(job);
    send({
      type: "job.completed",
      job_id: job.job_id,
      output_url: job.output_url
    });
    log(`Completed ${job.job_id}`);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    send({
      type: "job.failed",
      job_id: job.job_id,
      error: message
    });
    log(`Failed ${job.job_id}: ${message}`);
  } finally {
    state.busy = false;
    setCurrentJob(null);
  }
}

async function renderJob(job) {
  if (!state.photoshopApp || !state.photoshopCore || !state.localFileSystem) {
    throw new Error("Photoshop/UXP APIs are unavailable. Reload the plugin from UXP Developer Tool.");
  }

  const tempRoot = await state.localFileSystem.getTemporaryFolder();
  const jobFolder = await tempRoot.createFolder(`photoshop-render-${job.job_id}`, {
    overwrite: true
  });
  const inputFile = await jobFolder.createFile("input.jpg", { overwrite: true });
  const outputFile = await jobFolder.createFile("output.jpg", { overwrite: true });

  try {
    sendStatus(job.job_id, "downloading");
    await downloadToFile(job.input_url, inputFile);

    sendStatus(job.job_id, "processing");
    await renderInPhotoshop(inputFile, outputFile, job.quality || 12);

    sendStatus(job.job_id, "uploading");
    await uploadFile(job.output_upload_url, outputFile);
  } finally {
    await deleteEntry(jobFolder);
  }
}

function sendStatus(jobId, status) {
  send({
    type: "job.status",
    job_id: jobId,
    status
  });
}

async function downloadToFile(url, file) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Download failed with HTTP ${response.status}`);
  }
  const bytes = await response.arrayBuffer();
  await file.write(bytes, { format: state.storageFormats.binary });
}

async function uploadFile(url, file) {
  const bytes = await file.read({ format: state.storageFormats.binary });
  const response = await fetch(url, {
    method: "PUT",
    headers: {
      "content-type": "image/jpeg"
    },
    body: bytes
  });

  if (!response.ok) {
    throw new Error(`Upload failed with HTTP ${response.status}`);
  }
}

async function renderInPhotoshop(inputFile, outputFile, quality) {
  let document = null;

  await state.photoshopCore.executeAsModal(
    async () => {
      document = await state.photoshopApp.open(inputFile);
      await document.saveAs.jpg(outputFile, { quality }, true);
    },
    { commandName: "Render embedded Camera Raw JPEG" }
  );

  if (document) {
    await state.photoshopCore.executeAsModal(
      async () => {
        if (typeof document.closeWithoutSaving === "function") {
          await document.closeWithoutSaving();
        } else {
          await document.close();
        }
      },
      { commandName: "Close rendered document" }
    );
  }
}

async function deleteEntry(entry) {
  try {
    if (entry && typeof entry.delete === "function") {
      await entry.delete();
    }
  } catch (error) {
    log(`Could not delete temp files: ${error.message || error}`);
  }
}
