const reconnectConfig = {
  initialDelayMs: 1000,
  multiplier: 2,
  maxDelayMs: 30000,
  jitterRatio: 0.3
};

const state = {
  socket: null,
  busy: false,
  workerId: "",
  serverUrl: "",
  sessionId: null,
  currentJob: null,
  photoshopApp: null,
  photoshopCore: null,
  localFileSystem: null,
  storageFormats: null,
  autoReconnect: false,
  manualDisconnect: false,
  reconnectAttempt: 0,
  reconnectTimer: null,
  heartbeatTimer: null,
  heartbeatIntervalMs: 20000
};

init();

function init() {
  const savedWorkerId = localStorage.getItem("workerId") || defaultWorkerId();
  const savedServerUrl = localStorage.getItem("serverUrl") || "ws://127.0.0.1:8000/workers/ws";

  getEl("workerId").value = savedWorkerId;
  getEl("serverUrl").value = savedServerUrl;
  state.workerId = savedWorkerId;
  state.serverUrl = savedServerUrl;

  getEl("connectButton").addEventListener("click", () => connect({ manual: true }));
  getEl("disconnectButton").addEventListener("click", disconnect);
  setConnectionState("Disconnected");
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

function setConnectionState(label) {
  const badge = getEl("statusBadge");
  badge.textContent = label;
  badge.classList.toggle("connected", label === "Connected" || label === "Busy");
  getEl("connectButton").disabled = label === "Connecting" || label === "Connected" || label === "Busy";
  getEl("disconnectButton").disabled = label === "Disconnected";
}

function setCurrentJob(job) {
  state.currentJob = job;
  getEl("jobView").textContent = job ? JSON.stringify(job, null, 2) : "None";
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    setConnectionState(job ? "Busy" : "Connected");
  }
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

function trySend(message) {
  try {
    send(message);
    return true;
  } catch (error) {
    log(`Send skipped: ${error.message || error}`);
    return false;
  }
}

function connect({ manual = false } = {}) {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    return;
  }

  if (manual) {
    state.manualDisconnect = false;
    state.autoReconnect = true;
    state.reconnectAttempt = 0;
  }

  cancelReconnect();
  stopHeartbeat();

  const workerId = getEl("workerId").value.trim() || defaultWorkerId();
  const serverUrl = getEl("serverUrl").value.trim();

  localStorage.setItem("workerId", workerId);
  localStorage.setItem("serverUrl", serverUrl);
  state.workerId = workerId;
  state.serverUrl = serverUrl;

  setConnectionState(manual ? "Connecting" : "Reconnecting");
  log(`Connecting to ${serverUrl}`);

  const socket = new WebSocket(serverUrl);
  state.socket = socket;

  socket.onopen = () => {
    state.reconnectAttempt = 0;
    setConnectionState("Connected");
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
      state.sessionId = message.session_id || null;
      state.heartbeatIntervalMs = Math.max(5000, Number(message.heartbeat_interval_seconds || 20) * 1000);
      startHeartbeat();
      log(`Registered as ${message.worker_id}`);
      return;
    }

    if (message.type === "worker.heartbeat_ack") {
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

  socket.onclose = (event) => {
    const wasManual = state.manualDisconnect;
    state.socket = null;
    state.sessionId = null;
    stopHeartbeat();
    if (!state.busy) {
      setCurrentJob(null);
    }

    log(`Disconnected${event.reason ? `: ${event.reason}` : "."}`);
    if (wasManual || !state.autoReconnect) {
      state.busy = false;
      setConnectionState("Disconnected");
      return;
    }

    setConnectionState("Reconnecting");
    scheduleReconnect();
  };
}

function disconnect() {
  state.manualDisconnect = true;
  state.autoReconnect = false;
  cancelReconnect();
  stopHeartbeat();
  if (state.socket) {
    state.socket.close(1000, "Manual disconnect");
  }
  state.socket = null;
  state.sessionId = null;
  state.busy = false;
  setCurrentJob(null);
  setConnectionState("Disconnected");
  log("Manual disconnect.");
}

function scheduleReconnect() {
  cancelReconnect();
  state.reconnectAttempt += 1;
  const exponentialDelay = reconnectConfig.initialDelayMs * reconnectConfig.multiplier ** (state.reconnectAttempt - 1);
  const cappedDelay = Math.min(exponentialDelay, reconnectConfig.maxDelayMs);
  const jitter = Math.random() * cappedDelay * reconnectConfig.jitterRatio;
  const delay = Math.round(cappedDelay + jitter);

  log(`Reconnect attempt ${state.reconnectAttempt} in ${Math.round(delay / 1000)}s.`);
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    connect({ manual: false });
  }, delay);
}

function cancelReconnect() {
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

function startHeartbeat() {
  stopHeartbeat();
  sendHeartbeat();
  state.heartbeatTimer = setInterval(sendHeartbeat, state.heartbeatIntervalMs);
}

function stopHeartbeat() {
  if (state.heartbeatTimer) {
    clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = null;
  }
}

function sendHeartbeat() {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    return;
  }

  try {
    send({
      type: "worker.heartbeat",
      worker_id: state.workerId,
      current_job_id: state.currentJob ? state.currentJob.job_id : null,
      busy: state.busy
    });
  } catch (error) {
    log(`Heartbeat failed: ${error.message || error}`);
  }
}

async function handleRenderRequest(job) {
  if (state.busy) {
    trySend({
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
    trySend({
      type: "job.completed",
      job_id: job.job_id,
      output_url: job.output_url
    });
    log(`Completed ${job.job_id}`);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    trySend({
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
  trySend({
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
