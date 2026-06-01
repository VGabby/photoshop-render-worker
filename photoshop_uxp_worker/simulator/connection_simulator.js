#!/usr/bin/env node

import os from "node:os";
import WebSocket from "ws";

const defaults = {
  server: "ws://127.0.0.1:8000/workers/ws",
  count: 1,
  prefix: "sim-worker",
  heartbeatIntervalSeconds: null,
  initialDelaySeconds: 1,
  maxDelaySeconds: 30,
  jitter: 0.3
};

class SimulatedWorker {
  constructor(workerId, options) {
    this.workerId = workerId;
    this.options = options;
    this.socket = null;
    this.sessionId = null;
    this.currentJobId = null;
    this.busy = false;
    this.stopped = false;
    this.reconnectAttempt = 0;
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
    this.heartbeatIntervalMs = (options.heartbeatIntervalSeconds || 20) * 1000;
  }

  connect({ resetBackoff = false } = {}) {
    if (this.stopped) {
      return;
    }
    if (resetBackoff) {
      this.reconnectAttempt = 0;
    }

    this.clearReconnect();
    this.stopHeartbeat();
    this.log(`connecting ${this.options.server}`);

    const socket = new WebSocket(this.options.server);
    this.socket = socket;

    socket.on("open", () => {
      this.reconnectAttempt = 0;
      this.send({
        type: "worker.hello",
        worker_id: this.workerId,
        platform: `simulator:${process.platform}`,
        max_concurrency: 1
      });
    });

    socket.on("message", (data) => {
      this.handleMessage(data.toString());
    });

    socket.on("error", (error) => {
      this.log(`websocket error: ${error.message || error}`);
    });

    socket.on("close", (code, reasonBuffer) => {
      this.socket = null;
      this.sessionId = null;
      this.busy = false;
      this.currentJobId = null;
      this.stopHeartbeat();
      const reason = reasonBuffer ? reasonBuffer.toString() : "";
      this.log(`disconnected code=${code}${reason ? ` reason=${reason}` : ""}`);
      if (!this.stopped) {
        this.scheduleReconnect();
      }
    });
  }

  handleMessage(rawMessage) {
    let message;
    try {
      message = JSON.parse(rawMessage);
    } catch (error) {
      this.log(`invalid_json ${error.message || error}`);
      return;
    }

    if (message.type === "worker.ready") {
      this.sessionId = message.session_id || null;
      if (!this.options.heartbeatIntervalSeconds) {
        this.heartbeatIntervalMs = Math.max(5000, Number(message.heartbeat_interval_seconds || 20) * 1000);
      }
      this.log(`connected session=${this.sessionId || "none"}`);
      this.startHeartbeat();
      return;
    }

    if (message.type === "worker.heartbeat_ack") {
      this.log("heartbeat_ack");
      return;
    }

    if (message.type === "render.request") {
      this.currentJobId = message.job_id;
      this.busy = true;
      this.log(`rejecting render_request job=${message.job_id}`);
      this.send({
        type: "job.failed",
        job_id: message.job_id,
        error: "Connection simulator cannot render Photoshop jobs"
      });
      this.currentJobId = null;
      this.busy = false;
      return;
    }

    this.log(`received ${message.type || "unknown"}`);
  }

  send(message) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      this.log(`send_skipped ${message.type}`);
      return false;
    }
    this.socket.send(JSON.stringify(message));
    return true;
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.sendHeartbeat();
    this.heartbeatTimer = setInterval(() => this.sendHeartbeat(), this.heartbeatIntervalMs);
  }

  sendHeartbeat() {
    this.send({
      type: "worker.heartbeat",
      worker_id: this.workerId,
      current_job_id: this.currentJobId,
      busy: this.busy
    });
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  scheduleReconnect() {
    this.clearReconnect();
    this.reconnectAttempt += 1;
    const baseDelayMs = this.options.initialDelaySeconds * 1000;
    const maxDelayMs = this.options.maxDelaySeconds * 1000;
    const exponentialDelay = baseDelayMs * 2 ** (this.reconnectAttempt - 1);
    const cappedDelay = Math.min(exponentialDelay, maxDelayMs);
    const jitter = Math.random() * cappedDelay * this.options.jitter;
    const delay = Math.round(cappedDelay + jitter);

    this.log(`reconnect in ${Math.round(delay / 1000)}s attempt=${this.reconnectAttempt}`);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  clearReconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  stop() {
    this.stopped = true;
    this.clearReconnect();
    this.stopHeartbeat();
    if (this.socket) {
      this.socket.close(1000, "Simulator stopped");
      this.socket = null;
    }
  }

  log(message) {
    console.log(`${new Date().toISOString()} ${this.workerId} ${message}`);
  }
}

function parseArgs(args) {
  const parsed = { ...defaults };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--help" || arg === "-h") {
      printHelpAndExit();
    }

    const next = args[index + 1];
    switch (arg) {
      case "--server":
        parsed.server = requireValue(arg, next);
        index += 1;
        break;
      case "--count":
        parsed.count = parsePositiveInt(arg, requireValue(arg, next));
        index += 1;
        break;
      case "--prefix":
        parsed.prefix = requireValue(arg, next);
        index += 1;
        break;
      case "--heartbeat-interval":
        parsed.heartbeatIntervalSeconds = parsePositiveNumber(arg, requireValue(arg, next));
        index += 1;
        break;
      case "--initial-delay":
        parsed.initialDelaySeconds = parsePositiveNumber(arg, requireValue(arg, next));
        index += 1;
        break;
      case "--max-delay":
        parsed.maxDelaySeconds = parsePositiveNumber(arg, requireValue(arg, next));
        index += 1;
        break;
      case "--jitter":
        parsed.jitter = parseNonNegativeNumber(arg, requireValue(arg, next));
        index += 1;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return parsed;
}

function requireValue(flag, value) {
  if (!value || value.startsWith("--")) {
    throw new Error(`${flag} requires a value`);
  }
  return value;
}

function parsePositiveInt(flag, value) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${flag} must be a positive integer`);
  }
  return parsed;
}

function parsePositiveNumber(flag, value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${flag} must be a positive number`);
  }
  return parsed;
}

function parseNonNegativeNumber(flag, value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${flag} must be a non-negative number`);
  }
  return parsed;
}

function sanitizeId(value) {
  return String(value)
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "worker";
}

function printHelpAndExit() {
  console.log(`Usage:
  node photoshop_uxp_worker/simulator/connection_simulator.js [options]

Options:
  --server <url>                WebSocket URL. Default: ${defaults.server}
  --count <n>                   Number of simulated workers. Default: ${defaults.count}
  --prefix <name>               Worker ID prefix. Default: ${defaults.prefix}
  --heartbeat-interval <sec>    Override server heartbeat interval.
  --initial-delay <sec>         Initial reconnect delay. Default: ${defaults.initialDelaySeconds}
  --max-delay <sec>             Maximum reconnect delay. Default: ${defaults.maxDelaySeconds}
  --jitter <ratio>              Reconnect jitter ratio. Default: ${defaults.jitter}
`);
  process.exit(0);
}

const config = parseArgs(process.argv.slice(2));
const hostname = sanitizeId(os.hostname() || "unknown-host");
const workers = [];

for (let index = 1; index <= config.count; index += 1) {
  const workerId = `${hostname}-${sanitizeId(config.prefix)}-${String(index).padStart(3, "0")}`;
  workers.push(new SimulatedWorker(workerId, config));
}

for (const worker of workers) {
  worker.connect({ resetBackoff: true });
}

process.on("SIGINT", () => {
  console.log("\nStopping simulated workers...");
  for (const worker of workers) {
    worker.stop();
  }
  process.exit(0);
});
