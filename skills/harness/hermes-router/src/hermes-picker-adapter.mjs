import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { HermesClient, parseAllowedSessions, validateSessionId } from "./hermes-client.mjs";
import { copyRemoteArtifact, createSshHermesClient } from "./hermes-picker-ssh.mjs";

export const HERMES_PICKER_SLUG = "hermes-vps/agent";
export const HERMES_PICKER_MODEL = "hermes-vps-agent";
export const STORE_LOCK_NOTE = "Single-process adapter plus file lock serializes mapping writes. A second process waits on the lock rather than overwriting snapshots.";

const TASK_ID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const DEFAULT_TIMEOUT_MS = 240000;
const FILE_COPY_MAX_BYTES = 2 * 1024 * 1024;
const UNSUPPORTED_PARTS = new Set(["input_image", "input_file", "image_url", "file"]);

export function isHermesPickerModel(model) {
  return model === HERMES_PICKER_SLUG || model === HERMES_PICKER_MODEL;
}

export function mappingStorePath(env = process.env) {
  return env.HERMES_PICKER_MAP_PATH
    || path.join(env.CODEX_HOME || path.join(os.homedir(), ".codex"), "hermes-picker", "task-sessions.json");
}

export function configFromEnv(env = process.env) {
  const transport = env.HERMES_PICKER_TRANSPORT || "ssh";
  const allowCreate = String(env.HERMES_PICKER_ALLOW_CREATE || env.HERMES_ALLOW_CREATE_SESSIONS || "true") === "true";
  const artifactsRoot = env.HERMES_PICKER_ARTIFACTS_ROOT
    || path.join(env.CODEX_HOME || path.join(os.homedir(), ".codex"), "hermes-picker", "artifacts");
  const allowedArtifactRoots = String(env.HERMES_PICKER_ALLOWED_ARTIFACT_ROOTS || "/tmp/codex-hermes-artifacts")
    .split(",").map((item) => item.trim()).filter(Boolean);
  const shared = {
    transport,
    allowCreate,
    timeoutMs: Number(env.HERMES_PICKER_TIMEOUT_MS || DEFAULT_TIMEOUT_MS),
    mappingPath: mappingStorePath(env),
    artifactsRoot,
    allowedSessions: parseAllowedSessions(env.HERMES_PICKER_ALLOWED_SESSION_IDS || env.HERMES_ALLOWED_SESSION_IDS || ""),
    sshHost: env.HERMES_PICKER_SSH_HOST || "hermes-host",
    remoteBaseUrl: env.HERMES_PICKER_REMOTE_BASE_URL || "http://127.0.0.1:8766",
    remoteEnvPath: env.HERMES_PICKER_REMOTE_ENV_PATH || "~/.hermes/.env",
    allowedArtifactRoots,
  };
  if (transport === "http") {
    const baseUrl = env.HERMES_PICKER_BASE_URL || env.HERMES_BASE_URL || "http://127.0.0.1:8766";
    const parsed = new URL(baseUrl);
    if (parsed.hostname !== "127.0.0.1" && parsed.hostname !== "localhost") {
      throw new Error("HTTP Hermes picker mode must use a loopback base URL.");
    }
    const apiKey = env.HERMES_PICKER_API_KEY || env.HERMES_API_KEY || "";
    if (!apiKey || apiKey.length < 16) throw new Error("HTTP Hermes picker mode requires a loopback API key.");
    return { ...shared, baseUrl: parsed.toString().replace(/\/$/, ""), apiKey };
  }
  return shared;
}

export function threadIdFromHeaders(headers = {}) {
  for (const name of ["thread-id", "session-id", "session_id"]) {
    const value = headerText(headers, name);
    const id = typeof value === "string" ? value.match(TASK_ID_PATTERN)?.[0] : undefined;
    if (id) return id;
  }
  const metadata = headerText(headers, "x-codex-turn-metadata");
  if (!metadata) return undefined;
  try {
    return metadataThreadId(JSON.parse(metadata));
  } catch {
    return undefined;
  }
}

function headerText(headers, name) {
  const value = headers?.[name] ?? headers?.[name.toLowerCase()];
  if (Array.isArray(value)) return value[0];
  return typeof value === "string" ? value : undefined;
}

function metadataThreadId(value) {
  if (!value || typeof value !== "object") return undefined;
  for (const [key, item] of Object.entries(value)) {
    if (["threadId", "thread_id", "conversationId", "conversation_id", "sessionId", "session_id"].includes(key)) {
      const id = typeof item === "string" ? item.match(TASK_ID_PATTERN)?.[0] : undefined;
      if (id) return id;
    }
  }
  for (const item of Object.values(value)) {
    const nested = metadataThreadId(item);
    if (nested) return nested;
  }
  return undefined;
}

export function requestIdentity(headers = {}, payload = {}) {
  const metadata = headerText(headers, "x-codex-turn-metadata");
  let parsed = {};
  try { parsed = metadata ? JSON.parse(metadata) : {}; } catch { parsed = {}; }
  const explicit = firstString(
    nestedString(parsed, ["turnId", "turn_id"]),
    headerText(headers, "x-codex-turn-id"),
    headerText(headers, "idempotency-key"),
    nestedString(parsed, ["idempotencyKey", "idempotency_key"]),
    payload.id,
    payload.request_id,
    nestedString(parsed, ["requestId", "request_id", "responseId", "response_id"]),
    headerText(headers, "x-request-id"),
  );
  if (explicit) return { kind: "turn", value: explicit, ambiguous: false };
  const previous = typeof payload.previous_response_id === "string" ? payload.previous_response_id : "";
  const digest = createHash("sha256")
    .update(JSON.stringify({ previous, input: payload.input ?? null }))
    .digest("hex");
  return {
    kind: "input-fingerprint",
    value: digest,
    ambiguous: true,
    note: "No turn/request id was present. Dedup uses previous_response_id plus full input, not message text. Distinct turns that replay identical input without a new previous_response_id remain ambiguous.",
  };
}

function firstString(...values) {
  return values.find((value) => typeof value === "string" && value.trim()) || null;
}

function nestedString(value, keys) {
  if (!value || typeof value !== "object") return null;
  for (const key of keys) {
    if (typeof value[key] === "string" && value[key].trim()) return value[key];
  }
  for (const item of Object.values(value)) {
    const nested = nestedString(item, keys);
    if (nested) return nested;
  }
  return null;
}

export function extractNewUserText(payload) {
  const input = payload?.input;
  if (typeof input === "string" && input.trim()) return input.trim();
  if (!Array.isArray(input)) throw new Error("Hermes picker requires Responses input.");
  for (let index = input.length - 1; index >= 0; index -= 1) {
    const item = input[index];
    if (!item || typeof item !== "object") continue;
    const isUser = item.role === "user" && (item.type === "message" || item.type == null);
    if (!isUser && item.type !== "input_text") continue;
    if (item.type === "input_text") {
      if (typeof item.text === "string" && item.text.trim()) return item.text.trim();
      continue;
    }
    return messageText(item);
  }
  throw new Error("Hermes picker found no new user message to dispatch.");
}

function messageText(item) {
  if (typeof item.content === "string") return item.content.trim();
  if (!Array.isArray(item.content)) throw new Error("Unsupported user message content.");
  const texts = [];
  for (const part of item.content) {
    if (typeof part === "string") {
      if (part.trim()) texts.push(part.trim());
      continue;
    }
    if (!part || typeof part !== "object") continue;
    if (UNSUPPORTED_PARTS.has(part.type) || part.image_url || part.file_id) {
      throw new Error("Hermes picker does not accept image or file input parts.");
    }
    if (part.type === "input_text" || part.type === "text" || part.type === "output_text") {
      if (typeof part.text === "string" && part.text.trim()) texts.push(part.text.trim());
      continue;
    }
    if (part.type) throw new Error(`Unsupported user content part: ${part.type}`);
  }
  const text = texts.join("\n").trim();
  if (!text) throw new Error("Hermes picker found no new user message to dispatch.");
  return text;
}

export function attachSessionFromText(text) {
  const match = String(text || "").match(/^\s*\/hermes(?:-picker)?\s+(?:attach|use)\s+(\S+)/i);
  if (!match) return null;
  return validateSessionId(match[1]);
}

export function requestedFilesFromText(text) {
  const match = String(text || "").match(/^\s*\/hermes(?:-picker)?\s+files?\s+(.+)$/i);
  if (!match) return [];
  return match[1].split(/\s+/).map((item) => item.trim()).filter(Boolean);
}

function emptyStore() {
  return { version: 1, tasks: {}, sessions: {} };
}

export function pidIsAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code !== "ESRCH";
  }
}

export async function acquireFileLock(lockPath) {
  const started = Date.now();
  while (true) {
    try {
      await writeFile(lockPath, String(process.pid), { flag: "wx", mode: 0o600 });
      return;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      let owner = "";
      try { owner = (await readFile(lockPath, "utf8")).trim(); } catch {}
      const pid = Number(owner);
      if (Number.isInteger(pid) && pid > 0 && !pidIsAlive(pid)) {
        await unlink(lockPath).catch(() => {});
        continue;
      }
      if (Date.now() - started > 5000) throw new Error("Timed out waiting for Hermes picker mapping lock.");
      await new Promise((resolve) => setTimeout(resolve, 15));
    }
  }
}

async function withStoreLock(filePath, mutate) {
  await mkdir(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const lockPath = `${filePath}.lock`;
  await acquireFileLock(lockPath);
  try {
    let store;
    try {
      store = JSON.parse(await readFile(filePath, "utf8"));
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      store = emptyStore();
    }
    if (!store.tasks) store.tasks = {};
    if (!store.sessions) store.sessions = {};
    const result = await mutate(store);
    const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
    await writeFile(temporary, `${JSON.stringify(store, null, 2)}\n`, { mode: 0o600 });
    await rename(temporary, filePath);
    return { store, result };
  } finally {
    await unlink(lockPath).catch(() => {});
  }
}

export class HermesPickerAdapter {
  constructor(options = {}) {
    this.env = options.env || process.env;
    this.config = options.config || configFromEnv(this.env);
    this.client = options.client || this._defaultClient(options);
    this.mappingPath = options.mappingPath || this.config.mappingPath;
    this.copyFile = options.copyFile === undefined
      ? (this.config.transport === "http" ? null : (remotePath, localPath, maxBytes) => copyRemoteArtifact(remotePath, localPath, maxBytes, this.config))
      : options.copyFile;
    this.activeTasks = new Map();
    this.activeSessions = new Map();
  }

  _defaultClient(options) {
    if (this.config.transport === "http") {
      return new HermesClient({
        baseUrl: this.config.baseUrl,
        apiKey: this.config.apiKey,
        allowedSessions: this.config.allowedSessions,
        allowCreate: this.config.allowCreate,
        timeoutMs: this.config.timeoutMs,
        fetchImpl: options.fetchImpl,
      });
    }
    return createSshHermesClient(this.config);
  }

  inspectMapping(taskId) {
    return this._readTask(taskId);
  }

  async _readTask(taskId) {
    try {
      const store = JSON.parse(await readFile(this.mappingPath, "utf8"));
      return store.tasks?.[taskId] || null;
    } catch (error) {
      if (error?.code === "ENOENT") return null;
      throw error;
    }
  }

  async attachTask(taskId, sessionId) {
    return this.handleResponses({
      request: { headers: { "thread-id": taskId, "x-request-id": `attach-${randomUUID()}` } },
      response: discardedResponse(),
      payload: {
        model: HERMES_PICKER_SLUG,
        input: [{ role: "user", content: `/hermes attach ${sessionId}` }],
      },
    });
  }

  async handleResponses({ request, response, payload, signal }) {
    const taskId = threadIdFromHeaders(request?.headers || {});
    if (!taskId) {
      writeJson(response, 400, missingTaskError());
      return { status: 400, handled: true };
    }
    let message;
    try {
      message = extractNewUserText(payload);
    } catch (error) {
      writeJson(response, 400, { error: { type: "hermes_picker_unsupported_input", message: String(error.message || error) } });
      return { status: 400, handled: true };
    }
    const identity = requestIdentity(request?.headers || {}, payload);
    const attachId = attachSessionFromText(message);
    const requestedFiles = requestedFilesFromText(message);
    const stream = payload?.stream !== false;
    const taskLock = this._acquire(this.activeTasks, taskId, "task");
    if (!taskLock.ok) return this._locked(response, taskLock);
    let sessionLock = { ok: true, key: null, map: this.activeSessions };
    const abortUncertain = () => {};
    signal?.addEventListener?.("abort", abortUncertain, { once: true });
    try {
      const current = await this._readTask(taskId);
      if (current?.pending) {
        writeJson(response, 409, pendingError(current.pending));
        return { status: 409, handled: true, pending: current.pending };
      }
      if (current?.lastIdentity === identity.value && current.lastContent) {
        writeAssistant(response, { text: current.lastContent, sessionId: current.hermesSessionId, replayed: true, stream });
        return { status: 200, handled: true, replayed: true, sessionId: current.hermesSessionId, identity };
      }

      if (attachId) return await this._attach({ taskId, attachId, identity, current, response, stream, signal });
      if (requestedFiles.length) return await this._copyOnly({ taskId, current, requestedFiles, identity, response, stream });

      const prepared = await withStoreLock(this.mappingPath, async (store) => {
        const mapping = store.tasks[taskId] || {};
        if (mapping.pending) return { blocked: mapping.pending };
        if (mapping.lastIdentity === identity.value && mapping.lastContent) {
          return { replay: mapping };
        }
        const existingSession = mapping.hermesSessionId;
        if (existingSession) {
          const shared = pendingForSession(store, existingSession, taskId);
          if (shared) return { blocked: shared.pending, sharedTaskId: shared.taskId };
          store.sessions[existingSession] = store.sessions[existingSession] || { tasks: [] };
          if (!store.sessions[existingSession].tasks.includes(taskId)) store.sessions[existingSession].tasks.push(taskId);
          mapping.pending = { op: "chat", identity: identity.value, identityKind: identity.kind, ambiguous: identity.ambiguous, sessionId: existingSession, startedAt: new Date().toISOString() };
          store.tasks[taskId] = mapping;
          return { sessionId: existingSession, create: false };
        }
        if (!this.config.allowCreate) throw new Error("No Hermes session is mapped to this Codex task. Send `/hermes attach <session-id>` first.");
        mapping.pending = { op: "create+chat", identity: identity.value, identityKind: identity.kind, ambiguous: identity.ambiguous, startedAt: new Date().toISOString() };
        store.tasks[taskId] = mapping;
        return { sessionId: null, create: true };
      });
      if (prepared.result.blocked) {
        writeJson(response, 409, pendingError(prepared.result.blocked));
        return { status: 409, handled: true };
      }
      if (prepared.result.replay) {
        writeAssistant(response, { text: prepared.result.replay.lastContent, sessionId: prepared.result.replay.hermesSessionId, replayed: true, stream });
        return { status: 200, handled: true, replayed: true, sessionId: prepared.result.replay.hermesSessionId };
      }

      let sessionId = prepared.result.sessionId;
      sessionLock = this._acquire(this.activeSessions, sessionId || `creating:${taskId}`, "session");
      if (!sessionLock.ok) {
        await this._setPending(taskId, prepared.result.create ? { op: "create+chat", identity: identity.value, blocked: "session-lock" } : { op: "chat", identity: identity.value, sessionId, blocked: "session-lock" });
        writeJson(response, 409, sessionLock.error);
        return { status: 409, handled: true };
      }
      const heartbeat = stream ? startHeartbeat(response) : null;
      let resultCommitted = false;
      try {
        if (prepared.result.create) {
          const created = await this.client.request("/api/sessions", {
            method: "POST",
            body: { title: `Codex ${taskId.slice(0, 8)}`.slice(0, 120), source: "api_server" },
            signal,
          });
          sessionId = created?.session?.id;
          if (!sessionId) throw uncertain("Session creation outcome uncertain: Hermes did not return a new session ID.");
          validateSessionId(sessionId);
          this._release(sessionLock);
          sessionLock = this._acquire(this.activeSessions, sessionId, "session");
          if (!sessionLock.ok) {
            await this._setPending(taskId, { op: "chat", identity: identity.value, sessionId, note: "created; session lock contended" });
            writeJson(response, 409, sessionLock.error);
            return { status: 409, handled: true, sessionId };
          }
          await this._commitMapping(taskId, (mapping, store) => {
            mapping.hermesSessionId = sessionId;
            mapping.pending = { op: "chat", identity: identity.value, sessionId, startedAt: new Date().toISOString() };
            store.sessions[sessionId] = store.sessions[sessionId] || { tasks: [] };
            if (!store.sessions[sessionId].tasks.includes(taskId)) store.sessions[sessionId].tasks.push(taskId);
          });
        }
        this.client.allowedSessions.add(sessionId);
        const result = await this.client.continueSession(sessionId, message, { signal });
        const effectiveSessionId = result.sessionId || sessionId;
        this.client.allowedSessions.add(effectiveSessionId);
        const text = String(result.content || "");
        await this._commitMapping(taskId, (mapping, store) => {
          mapping.hermesSessionId = effectiveSessionId;
          mapping.lastIdentity = identity.value;
          mapping.lastIdentityKind = identity.kind;
          mapping.lastIdentityAmbiguous = identity.ambiguous;
          mapping.lastContent = text;
          mapping.pending = null;
          mapping.updatedAt = new Date().toISOString();
          store.sessions[effectiveSessionId] = store.sessions[effectiveSessionId] || { tasks: [] };
          if (!store.sessions[effectiveSessionId].tasks.includes(taskId)) store.sessions[effectiveSessionId].tasks.push(taskId);
        });
        resultCommitted = true;
        const decorated = await this._decorateArtifacts({ taskId, sessionId: effectiveSessionId, text });
        if (decorated !== text) {
          await this._commitMapping(taskId, (mapping) => {
            if (mapping.lastIdentity !== identity.value) return;
            mapping.lastContent = decorated;
            mapping.updatedAt = new Date().toISOString();
          });
        }
        stopHeartbeat(heartbeat);
        writeAssistant(response, { text: decorated, sessionId: effectiveSessionId, stream });
        return { status: 200, handled: true, sessionId: effectiveSessionId, identity };
      } catch (error) {
        stopHeartbeat(heartbeat);
        if (resultCommitted) {
          return { status: 499, handled: true, completed: true, sessionId, error };
        }
        await this._setPending(taskId, {
          op: prepared.result.create ? "create+chat" : "chat",
          identity: identity.value,
          sessionId,
          uncertain: true,
          error: String(error.message || error),
        });
        const code = /uncertain|Do not retry|cancelled|timed out/i.test(String(error.message || error)) ? 502 : 500;
        writeJson(response, code, {
          error: {
            type: "hermes_picker_uncertain_write",
            message: String(error.message || error),
            retryable: false,
          },
        });
        return { status: code, handled: true, error };
      }
    } finally {
      signal?.removeEventListener?.("abort", abortUncertain);
      this._release(taskLock);
      this._release(sessionLock);
    }
  }

  async _attach({ taskId, attachId, identity, current, response, stream, signal }) {
    try {
      validateSessionId(attachId);
      this.client.allowedSessions.add(attachId);
      const remote = await this.client.request(`/api/sessions/${encodeURIComponent(attachId)}`, { signal });
      const session = sessionRecord(remote, attachId);
      if (!session?.id || session.id !== attachId) throw new Error("Hermes returned an unexpected session for attach.");
      const committed = await withStoreLock(this.mappingPath, async (store) => {
        const mapping = store.tasks[taskId] || {};
        if (mapping.pending) return { blocked: mapping.pending, mapping };
        mapping.previousHermesSessionId = mapping.hermesSessionId || mapping.previousHermesSessionId || null;
        mapping.hermesSessionId = attachId;
        mapping.attached = true;
        mapping.lastIdentity = identity.value;
        mapping.lastContent = `Attached existing Hermes session \`${attachId}\` without sending a chat turn.`;
        mapping.updatedAt = new Date().toISOString();
        store.tasks[taskId] = mapping;
        store.sessions[attachId] = store.sessions[attachId] || { tasks: [] };
        if (!store.sessions[attachId].tasks.includes(taskId)) store.sessions[attachId].tasks.push(taskId);
        return { mapping };
      });
      if (committed.result.blocked) {
        writeJson(response, 409, pendingError(committed.result.blocked));
        return { status: 409, handled: true, pending: committed.result.blocked };
      }
      writeAssistant(response, { text: `Attached existing Hermes session \`${attachId}\` without sending a chat turn.`, sessionId: attachId, stream });
      return { status: 200, handled: true, sessionId: attachId, attached: true };
    } catch (error) {
      await withStoreLock(this.mappingPath, async (store) => {
        const mapping = store.tasks[taskId];
        if (!mapping || mapping.pending) return;
        mapping.lastAttachError = String(error.message || error);
        mapping.updatedAt = new Date().toISOString();
      });
      writeJson(response, 502, {
        error: { type: "hermes_picker_attach_failed", message: String(error.message || error), preservedSessionId: current?.hermesSessionId || null },
      });
      return { status: 502, handled: true, error };
    }
  }

  async _decorateArtifacts({ taskId, sessionId, text }) {
    const links = extractAllowedArtifactLinks(text, this.config.allowedArtifactRoots).slice(0, 8);
    if (!links.length || !this.copyFile) return text;
    const replacements = new Map();
    const failures = [];
    for (const remotePath of links) {
      try {
        const localPath = path.join(this.config.artifactsRoot, taskId, encodeURIComponent(sessionId), `${randomUUID()}-${path.basename(remotePath)}`);
        await this.copyFile(remotePath, localPath, FILE_COPY_MAX_BYTES, this.config);
        replacements.set(remotePath, localPath);
      } catch (error) {
        failures.push(`${remotePath}: ${error.message || error}`);
      }
    }
    let decorated = text;
    for (const [remotePath, localPath] of replacements) {
      decorated = decorated.replaceAll(`](${remotePath})`, `](<${localPath}>)`);
    }
    if (failures.length) {
      decorated += `\n\nFile download failed (Hermes reply was already saved; not resent):\n${failures.map((item) => `- ${item}`).join("\n")}`;
    }
    return decorated;
  }

  async _copyOnly({ taskId, current, requestedFiles, identity, response, stream }) {
    if (!current?.hermesSessionId) {
      writeJson(response, 400, { error: { type: "hermes_picker_no_session", message: "Attach or chat before requesting files." } });
      return { status: 400, handled: true };
    }
    if (!this.copyFile) {
      writeJson(response, 400, { error: { type: "hermes_picker_copy_unavailable", message: "No injected artifact copier is configured." } });
      return { status: 400, handled: true };
    }
    const files = [];
    const failures = [];
    for (const remotePath of requestedFiles) {
      try {
        assertAllowedArtifact(remotePath, this.config.allowedArtifactRoots);
        const localPath = path.join(this.config.artifactsRoot, taskId, encodeURIComponent(current.hermesSessionId), `${randomUUID()}-${path.basename(remotePath)}`);
        await this.copyFile(remotePath, localPath, FILE_COPY_MAX_BYTES, this.config);
        files.push(localPath);
      } catch (error) {
        failures.push(`${remotePath}: ${error.message || error}`);
      }
    }
    const links = files.map((file) => `- [${path.basename(file)}](${file})`).join("\n");
    const text = [
      files.length ? `Copied ${files.length} file(s):` : "No files copied.",
      links,
      failures.length ? `Failed:\n${failures.map((item) => `- ${item}`).join("\n")}` : "",
    ].filter(Boolean).join("\n\n");
    await this._commitMapping(taskId, (mapping) => {
      mapping.lastIdentity = identity.value;
      mapping.lastContent = text;
      mapping.pending = null;
      mapping.updatedAt = new Date().toISOString();
    });
    writeAssistant(response, { text, sessionId: current.hermesSessionId, stream });
    return { status: 200, handled: true, files, failures };
  }

  async _commitMapping(taskId, mutate) {
    await withStoreLock(this.mappingPath, async (store) => {
      store.tasks[taskId] = store.tasks[taskId] || {};
      mutate(store.tasks[taskId], store);
    });
  }

  async _setPending(taskId, pending) {
    await this._commitMapping(taskId, (mapping) => {
      mapping.pending = { ...pending, updatedAt: new Date().toISOString() };
    });
  }

  _acquire(map, key, kind) {
    if (!key) return { ok: true, key: null, map };
    if (map.has(key)) {
      return {
        ok: false,
        key,
        map,
        error: {
          error: {
            type: kind === "session" ? "hermes_picker_session_in_flight" : "hermes_picker_turn_in_flight",
            message: kind === "session"
              ? "That Hermes session already has an active Codex turn."
              : "That Codex task already has an active Hermes turn.",
          },
        },
      };
    }
    map.set(key, { startedAt: Date.now() });
    return { ok: true, key, map };
  }

  _release(lock) {
    if (lock?.ok && lock.key) lock.map.delete(lock.key);
  }

  _locked(response, lock) {
    writeJson(response, 409, lock.error);
    return { status: 409, handled: true };
  }
}



export function extractAllowedArtifactLinks(text, roots = []) {
  const matches = [...String(text || "").matchAll(/\[[^\]]*\]\((\/[^)\s]+)\)/g)].map((match) => match[1]);
  const unique = [];
  for (const remotePath of matches) {
    try {
      assertAllowedArtifact(remotePath, roots);
      if (!unique.includes(remotePath)) unique.push(remotePath);
    } catch {}
  }
  return unique;
}

function pendingForSession(store, sessionId, exceptTaskId) {
  if (!sessionId) return null;
  for (const [otherId, other] of Object.entries(store.tasks || {})) {
    if (otherId === exceptTaskId || !other?.pending) continue;
    const otherSession = other.pending.sessionId || other.hermesSessionId;
    if (otherSession === sessionId) return { taskId: otherId, pending: other.pending };
  }
  return null;
}

function sessionRecord(remote, attachId) {
  if (!remote || typeof remote !== "object") return null;
  if (remote.session?.id) return remote.session;
  if (remote.id) return remote;
  if (Array.isArray(remote.data)) return remote.data.find((item) => item?.id === attachId) || remote.data[0] || null;
  if (remote.data?.id) return remote.data;
  if (Array.isArray(remote.sessions)) return remote.sessions.find((item) => item?.id === attachId) || remote.sessions[0] || null;
  return null;
}

function missingTaskError() {
  return {
    error: {
      type: "hermes_picker_missing_task_id",
      message: "Hermes (VPS) requires a Codex task UUID in thread-id / x-codex-turn-metadata. Refusing to share a default session.",
    },
  };
}

function pendingError(pending) {
  return {
    error: {
      type: "hermes_picker_pending_operation",
      message: "A previous Hermes operation is unresolved. Inspect the remote session before sending another turn; the adapter will not resend it.",
      pending,
      retryable: false,
    },
  };
}

function uncertain(message) {
  return new Error(`Hermes write outcome uncertain: ${message}. Do not retry automatically; inspect the session first.`);
}

function assertAllowedArtifact(remotePath, roots) {
  if (!remotePath.startsWith("/") || remotePath.includes("..")) throw new Error("Invalid artifact path");
  const allowed = (roots || []).some((root) => remotePath === root || remotePath.startsWith(`${root.replace(/\/$/, "")}/`));
  if (!allowed) throw new Error("Artifact path is outside the allowed roots");
}

function discardedResponse() {
  const chunks = [];
  return {
    headersSent: false,
    statusCode: 200,
    setHeader() {},
    writeHead(status) { this.statusCode = status; this.headersSent = true; },
    write(chunk) { chunks.push(String(chunk)); },
    end(chunk) { if (chunk) chunks.push(String(chunk)); this.body = chunks.join(""); },
  };
}

function headersAlreadySent(response) {
  return Boolean(response?.headersSent);
}

function isEventStream(response) {
  const type = response?.getHeader?.("content-type") || response?.headers?.["Content-Type"] || "";
  return String(type).includes("text/event-stream");
}

function writeJson(response, status, payload) {
  if (headersAlreadySent(response) || isEventStream(response)) {
    try {
      response.write?.(`event: error\ndata: ${JSON.stringify({ type: "error", status, ...payload })}\n\n`);
      response.end?.("data: [DONE]\n\n");
    } catch {}
    return;
  }
  if (typeof response.writeHead === "function") {
    response.writeHead(status, { "Content-Type": "application/json" });
  } else {
    response.statusCode = status;
  }
  if (typeof response.end === "function") response.end(JSON.stringify(payload));
}

function startHeartbeat(response) {
  if (!headersAlreadySent(response) && typeof response.setHeader === "function") {
    try {
      response.setHeader("Content-Type", "text/event-stream; charset=utf-8");
      response.setHeader("Cache-Control", "no-cache");
    } catch {}
  }
  if (!headersAlreadySent(response) && typeof response.writeHead === "function") {
    try { response.writeHead(200, { "Cache-Control": "no-cache", Connection: "keep-alive" }); } catch {}
  }
  if (typeof response.write === "function") response.write(": keepalive\n\n");
  return setInterval(() => {
    try { response.write?.(": keepalive\n\n"); } catch {}
  }, 10000);
}

function stopHeartbeat(timer) {
  if (timer) clearInterval(timer);
}

export function writeAssistant(response, { text, sessionId, replayed = false, stream = true }) {
  const responseId = `resp_${randomUUID().replaceAll("-", "")}`;
  const messageId = `msg_${randomUUID().replaceAll("-", "")}`;
  const partId = `txt_${randomUUID().replaceAll("-", "")}`;
  const created = Math.floor(Date.now() / 1000);
  const outputItem = {
    type: "message",
    id: messageId,
    role: "assistant",
    status: "completed",
    content: [{ type: "output_text", text, id: partId }],
  };
  const completed = {
    id: responseId,
    object: "response",
    created_at: created,
    status: "completed",
    model: HERMES_PICKER_SLUG,
    output: [outputItem],
    usage: null,
    metadata: { hermes_session_id: sessionId, hermes_picker_replayed: replayed },
  };
  if (stream === false) {
    writeJson(response, 200, completed);
    return;
  }
  if (!headersAlreadySent(response) && typeof response.setHeader === "function") {
    try {
      response.setHeader("Content-Type", "text/event-stream; charset=utf-8");
      if (sessionId) response.setHeader("X-Hermes-Session-Id", sessionId);
    } catch {}
  }
  if (!headersAlreadySent(response) && typeof response.writeHead === "function") {
    try { response.writeHead(200, { "Cache-Control": "no-cache", Connection: "keep-alive" }); } catch {}
  }
  const events = [
    ["response.created", { response: { ...completed, status: "in_progress", output: [] } }],
    ["response.output_item.added", { output_index: 0, item: { ...outputItem, status: "in_progress", content: [] } }],
    ["response.content_part.added", { output_index: 0, content_index: 0, item_id: messageId, part: { type: "output_text", text: "", id: partId } }],
    ["response.output_text.delta", { output_index: 0, content_index: 0, item_id: messageId, delta: text }],
    ["response.output_text.done", { output_index: 0, content_index: 0, item_id: messageId, text }],
    ["response.content_part.done", { output_index: 0, content_index: 0, item_id: messageId, part: { type: "output_text", text, id: partId } }],
    ["response.output_item.done", { output_index: 0, item: outputItem }],
    ["response.completed", { response: completed }],
  ];
  events.forEach(([type, data], sequence) => {
    response.write?.(`event: ${type}\ndata: ${JSON.stringify({ type, sequence_number: sequence, ...data })}\n\n`);
  });
  response.end?.("data: [DONE]\n\n");
}

export function hermesPickerRouteRecord() {
  return {
    slug: HERMES_PICKER_SLUG,
    gatewayModel: HERMES_PICKER_MODEL,
    compHash: "hermes-vps-agent-picker-v1",
    upstreamModel: "hermes-agent",
    provider: "hermes-vps",
    listed: true,
    displayName: "Hermes (VPS)",
    description: "Existing remote Hermes agent via session chat. Not an inference model.",
    priority: 40,
    contextWindow: 128000,
    autoCompact: 110000,
    inputModalities: ["text"],
  };
}
