import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { HermesClient } from "./hermes-client.mjs";

const helper = readFileSync(new URL("./hermes-picker-remote.py", import.meta.url), "utf8");
const quote = (value) => "'" + value.replaceAll("'", "'\\''") + "'";

export async function sshOperation(payload, config = {}, { signal } = {}) {
  const host = config.sshHost || "hermes-host";
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_.@-]*$/.test(host)) throw new Error("Invalid configured SSH alias");
  signal?.throwIfAborted();
  const child = spawn("ssh", ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", host,
    `python3 -c ${quote(helper)}`], { stdio: ["pipe", "pipe", "pipe"] });
  const chunks = [];
  let size = 0;
  const maxBytes = payload.action === "file" ? 30 * 1024 * 1024 : 17 * 1024 * 1024;
  return await new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      if (error) { child.kill("SIGKILL"); reject(error); } else resolve(result);
    };
    const abort = () => finish(new Error("SSH request cancelled; remote operation may still be running"));
    const timer = setTimeout(() => finish(new Error("SSH request timed out; remote operation may still be running")),
      (config.timeoutMs || 240000) + 15000);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) { abort(); return; }
    child.stdout.on("data", (chunk) => {
      size += chunk.length;
      if (size > maxBytes) finish(new Error("Remote response exceeds limit"));
      else chunks.push(chunk);
    });
    child.stderr.on("data", () => {});
    child.on("error", () => finish(new Error("Could not start SSH transport")));
    child.stdin.on("error", () => finish(new Error("SSH input channel closed")));
    child.on("close", (code) => {
      if (code !== 0) return finish(new Error(`SSH transport exited ${code}`));
      try { finish(null, JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
      catch { finish(new Error("Remote Hermes transport returned invalid JSON")); }
    });
    child.stdin.end(JSON.stringify(payload));
  });
}

export function createSshHermesClient(config = {}) {
  const client = new HermesClient({ baseUrl: "http://127.0.0.1:8766", apiKey: "unused-remote-credential", allowedSessions: config.allowedSessions || [],
    allowCreate: config.allowCreate ?? true, timeoutMs: config.timeoutMs || 240000 });
  client.request = async (route, { method = "GET", body, signal } = {}) => {
    try {
      const result = await sshOperation({ action: "request", path: route, method,
        ...(body === undefined ? {} : { body }), baseUrl: config.remoteBaseUrl || "http://127.0.0.1:8766",
        envPath: config.remoteEnvPath || "~/.hermes/.env", timeoutSeconds: (config.timeoutMs || 240000) / 1000 }, config, { signal });
      if (result.status < 200 || result.status >= 300) throw new Error(result.error || `Hermes API returned HTTP ${result.status}`);
      return result.data;
    } catch (error) {
      if (method !== "GET") throw new Error(`Hermes write outcome uncertain: ${error.message}. Do not retry automatically; inspect the session first.`, { cause: error });
      throw error;
    }
  };
  return client;
}

export async function copyRemoteArtifact(remotePath, localPath, maxBytes, config = {}) {
  const roots = config.allowedArtifactRoots || ["/tmp/codex-hermes-artifacts"];
  const result = await sshOperation({ action: "file", path: remotePath, roots, maxBytes }, config);
  if (result.status !== 200) throw new Error(result.error || "Artifact transfer failed");
  const bytes = Buffer.from(result.data.base64, "base64");
  if (bytes.length !== result.data.bytes || bytes.length > maxBytes) throw new Error("Artifact transfer length mismatch");
  await mkdir(path.dirname(localPath), { recursive: true, mode: 0o700 });
  await writeFile(localPath, bytes, { mode: 0o600, flag: "wx" });
  return localPath;
}
