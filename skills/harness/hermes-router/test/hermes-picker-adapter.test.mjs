import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  HermesPickerAdapter,
  HERMES_PICKER_SLUG,
  attachSessionFromText,
  extractNewUserText,
  requestIdentity,
  threadIdFromHeaders,
} from "../src/hermes-picker-adapter.mjs";

const TASK = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const TASK_B = "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee";

function headers(extra = {}) {
  return { "thread-id": TASK, "x-request-id": extra.requestId || "turn-1", ...extra };
}

function payload(text, extra = {}) {
  return {
    model: HERMES_PICKER_SLUG,
    previous_response_id: extra.previous || undefined,
    stream: extra.stream,
    input: extra.input || [{ role: "user", content: [{ type: "input_text", text }] }],
  };
}

function mockResponse() {
  const chunks = [];
  return {
    headersSent: false,
    statusCode: 200,
    headers: {},
    setHeader(name, value) { this.headers[name] = value; },
    writeHead(status) { this.statusCode = status; this.headersSent = true; },
    write(chunk) { chunks.push(String(chunk)); },
    end(chunk) { if (chunk) chunks.push(String(chunk)); this.body = chunks.join(""); },
  };
}

async function adapterWith(fetchImpl, extras = {}) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "hermes-picker-"));
  return new HermesPickerAdapter({
    env: {
      HERMES_PICKER_TRANSPORT: "http",
      HERMES_PICKER_BASE_URL: "http://127.0.0.1:8766",
      HERMES_PICKER_API_KEY: "1234567890abcdef",
      HERMES_PICKER_ALLOW_CREATE: "true",
      HERMES_PICKER_MAP_PATH: path.join(dir, "map.json"),
      HERMES_PICKER_ARTIFACTS_ROOT: path.join(dir, "artifacts"),
      HERMES_PICKER_ALLOWED_ARTIFACT_ROOTS: "/tmp/codex-hermes-artifacts",
    },
    fetchImpl,
    copyFile: extras.copyFile === undefined ? null : extras.copyFile,
    mappingPath: path.join(dir, "map.json"),
  });
}

test("user extraction accepts role=user without type and rejects images", () => {
  assert.equal(extractNewUserText({ input: [{ role: "user", content: "plain next" }] }), "plain next");
  assert.throws(
    () => extractNewUserText({ input: [{ role: "user", content: [{ type: "input_image", image_url: "x" }] }] }),
    /image or file/,
  );
  assert.equal(attachSessionFromText("/hermes attach existing-desktop"), "existing-desktop");
  assert.equal(threadIdFromHeaders({ "thread-id": TASK }), TASK);
});

test("dedup uses turn identity, not repeated message text", async () => {
  const chats = [];
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      return new Response(JSON.stringify({ session: { id: "sess-1" } }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) {
      chats.push(JSON.parse(options.body).message);
      return new Response(JSON.stringify({ session_id: "sess-1", message: { content: `ack-${chats.length}` } }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  await adapter.handleResponses({ request: { headers: headers({ requestId: "t1" }) }, response: mockResponse(), payload: payload("next") });
  await adapter.handleResponses({ request: { headers: headers({ requestId: "t2" }) }, response: mockResponse(), payload: payload("next") });
  const replay = await adapter.handleResponses({ request: { headers: headers({ requestId: "t2" }) }, response: mockResponse(), payload: payload("next") });
  assert.deepEqual(chats, ["next", "next"]);
  assert.equal(replay.replayed, true);
  const fallback = requestIdentity({}, { previous_response_id: "resp_1", input: payload("yes").input });
  assert.equal(fallback.ambiguous, true);
  assert.notEqual(fallback.value, requestIdentity({}, { previous_response_id: "resp_2", input: payload("yes").input }).value);
});

test("missing task id fails closed", async () => {
  const adapter = await adapterWith(async () => new Response("{}"));
  const response = mockResponse();
  const result = await adapter.handleResponses({ request: { headers: {} }, response, payload: payload("hi") });
  assert.equal(result.status, 400);
});

test("first turn persists pending before create/chat and only sends user text", async () => {
  const calls = [];
  let adapter;
  const fetchImpl = async (url, options = {}) => {
    const parsed = new URL(url);
    calls.push({ path: parsed.pathname, method: options.method || "GET", body: options.body });
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      const stored = JSON.parse(await readFile(adapter.mappingPath, "utf8"));
      assert.equal(stored.tasks[TASK].pending.op, "create+chat");
      return new Response(JSON.stringify({ session: { id: "20260919_picker_canary" } }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) {
      return new Response(JSON.stringify({ session_id: "20260919_picker_canary", message: { content: "hermes-hello" } }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  };
  adapter = await adapterWith(fetchImpl);
  const response = mockResponse();
  const result = await adapter.handleResponses({ request: { headers: headers() }, response, payload: payload("hello Hermes") });
  assert.equal(result.status, 200);
  assert.equal(JSON.parse(calls[1].body).message, "hello Hermes");
  assert.match(response.body, /content_part.added/);
  assert.match(response.body, /output_text.done/);
  assert.doesNotMatch(response.body, /function_call/);
  const stored = JSON.parse(await readFile(adapter.mappingPath, "utf8"));
  assert.equal(stored.tasks[TASK].pending, null);
});

test("attach validates read-only and does not chat; failed attach keeps old mapping", async () => {
  const calls = [];
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    calls.push({ path: parsed.pathname, method: options.method || "GET" });
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      return new Response(JSON.stringify({ session: { id: "old-session" } }), { status: 200 });
    }
    if (parsed.pathname === "/api/sessions/old-session/chat") {
      return new Response(JSON.stringify({ session_id: "old-session", message: { content: "first" } }), { status: 200 });
    }
    if (parsed.pathname === "/api/sessions/good-session") {
      return new Response(JSON.stringify({ session: { id: "good-session", title: "Keep" } }), { status: 200 });
    }
    if (parsed.pathname === "/api/sessions/missing-session") {
      return new Response(JSON.stringify({ error: { message: "not found" } }), { status: 404 });
    }
    if (parsed.pathname.endsWith("/chat")) {
      throw new Error("chat should not run on attach");
    }
    return new Response("{}", { status: 404 });
  });
  await adapter.handleResponses({ request: { headers: headers({ requestId: "c1" }) }, response: mockResponse(), payload: payload("start") });
  const attached = await adapter.attachTask(TASK, "good-session");
  assert.equal(attached.attached, true);
  assert.equal(calls.filter((call) => call.path.endsWith("/chat")).length, 1);
  const failed = await adapter.handleResponses({
    request: { headers: headers({ requestId: "c3" }) },
    response: mockResponse(),
    payload: payload("/hermes attach missing-session"),
  });
  assert.equal(failed.status, 502);
  const stored = JSON.parse(await readFile(adapter.mappingPath, "utf8"));
  assert.equal(stored.tasks[TASK].hermesSessionId, "good-session");
});

test("uncertain chat leaves pending and retry does not resend", async () => {
  let chats = 0;
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      return new Response(JSON.stringify({ session: { id: "sess-u" } }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) {
      chats += 1;
      throw new DOMException("aborted", "AbortError");
    }
    return new Response("{}", { status: 404 });
  });
  const first = await adapter.handleResponses({ request: { headers: headers({ requestId: "u1" }) }, response: mockResponse(), payload: payload("do") });
  assert.equal(first.status, 502);
  const second = await adapter.handleResponses({ request: { headers: headers({ requestId: "u2" }) }, response: mockResponse(), payload: payload("later") });
  assert.equal(second.status, 409);
  assert.equal(chats, 1);
  const stored = JSON.parse(await readFile(adapter.mappingPath, "utf8"));
  assert.equal(stored.tasks[TASK].pending.uncertain, true);
});

test("task lock is acquired before store IO and session lock blocks a second mapped task", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      return new Response(JSON.stringify({ session: { id: "shared" } }), { status: 200 });
    }
    if (parsed.pathname === "/api/sessions/shared") {
      return new Response(JSON.stringify({ session: { id: "shared" } }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) {
      await gate;
      return new Response(JSON.stringify({ session_id: "shared", message: { content: "slow" } }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  const first = adapter.handleResponses({ request: { headers: headers({ requestId: "a" }) }, response: mockResponse(), payload: payload("one") });
  await new Promise((resolve) => setTimeout(resolve, 20));
  const concurrent = await adapter.handleResponses({ request: { headers: headers({ requestId: "b" }) }, response: mockResponse(), payload: payload("two") });
  assert.equal(concurrent.status, 409);
  await adapter.attachTask(TASK_B, "shared");
  const other = adapter.handleResponses({
    request: { headers: { "thread-id": TASK_B, "x-request-id": "c" } },
    response: mockResponse(),
    payload: payload("from-b"),
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  const blocked = await Promise.race([
    other,
    new Promise((resolve) => setTimeout(() => resolve({ status: "waiting" }), 20)),
  ]);
  assert.equal(blocked.status === 409 || blocked.status === "waiting", true);
  release();
  await first;
  await other;
});

test("explicit file copy uses markdown absolute links and does not scrape replies", async () => {
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      return new Response(JSON.stringify({ session: { id: "sess-f" } }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) {
      return new Response(JSON.stringify({
        session_id: "sess-f",
        message: { content: "wrote [secret](/tmp/secret.txt) and [ok](/tmp/codex-hermes-artifacts/ok.txt)" },
      }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  }, {
    copyFile: async (remotePath, localPath) => {
      const { mkdir, writeFile } = await import("node:fs/promises");
      await mkdir(path.dirname(localPath), { recursive: true });
      await writeFile(localPath, `copied:${remotePath}`);
      return localPath;
    },
  });
  const chat = mockResponse();
  await adapter.handleResponses({ request: { headers: headers({ requestId: "f1" }) }, response: chat, payload: payload("write file") });
  assert.match(chat.body, /ok\.txt/);
  assert.match(chat.body, /artifacts\//);
  const stored = JSON.parse(await readFile(adapter.mappingPath, "utf8"));
  assert.match(stored.tasks[TASK].lastContent, /\]\(\/tmp\/secret\.txt\)/);
  assert.doesNotMatch(stored.tasks[TASK].lastContent, /secret\.txt\)/s && /artifacts\/.*secret/);
  assert.match(stored.tasks[TASK].lastContent, /artifacts\//);
});

test("nonstream returns JSON and inspectMapping is available", async () => {
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      return new Response(JSON.stringify({ session: { id: "sess-n" } }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) {
      return new Response(JSON.stringify({ session_id: "sess-n", message: { content: "plain" } }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  const response = mockResponse();
  await adapter.handleResponses({
    request: { headers: headers({ requestId: "n1" }) },
    response,
    payload: payload("hi", { stream: false }),
  });
  assert.match(response.body, /"object":"response"/);
  assert.equal((await adapter.inspectMapping(TASK)).hermesSessionId, "sess-n");
});

test("configFromEnv defaults to SSH without requiring a local API key", async () => {
  const { configFromEnv } = await import("../src/hermes-picker-adapter.mjs");
  const config = configFromEnv({ HERMES_PICKER_MAP_PATH: "/tmp/map.json" });
  assert.equal(config.transport, "ssh");
  assert.equal(config.sshHost, "hermes-host");
  assert.equal(config.apiKey, undefined);
  assert.equal(config.skillAdvisorReleaseRoot, "");
  assert.equal(typeof config.skillAdvisorHost, "string");
});

test("UI attach uses idempotency-key and {data:[...]} session records without chatting", async () => {
  const calls = [];
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    calls.push({ path: parsed.pathname, method: options.method || "GET" });
    if (parsed.pathname === "/api/sessions/s1") {
      return new Response(JSON.stringify({ data: [{ id: "s1", title: "Canary" }] }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) throw new Error("chat should not run on attach");
    return new Response("{}", { status: 404 });
  });
  const response = mockResponse();
  const result = await adapter.handleResponses({
    request: { headers: { "thread-id": TASK, "idempotency-key": "ui-attach-1" } },
    response,
    payload: { model: HERMES_PICKER_SLUG, stream: false, input: [{ type: "message", role: "user", content: [{ type: "input_text", text: "/hermes attach s1" }] }] },
  });
  assert.equal(result.attached, true);
  assert.equal(result.sessionId, "s1");
  assert.equal(calls.some((call) => call.path.endsWith("/chat")), false);
  const replay = await adapter.handleResponses({
    request: { headers: { "thread-id": TASK, "idempotency-key": "ui-attach-1" } },
    response: mockResponse(),
    payload: { model: HERMES_PICKER_SLUG, stream: false, input: [{ type: "message", role: "user", content: [{ type: "input_text", text: "/hermes attach s1" }] }] },
  });
  assert.equal(replay.replayed, true);
});

test("turn metadata identity beats x-request-id", () => {
  const first = requestIdentity(
    { "x-request-id": "http-1", "x-codex-turn-metadata": JSON.stringify({ turnId: "turn-stable" }) },
    payload("yes"),
  );
  const retry = requestIdentity(
    { "x-request-id": "http-2", "x-codex-turn-metadata": JSON.stringify({ turnId: "turn-stable" }) },
    payload("yes"),
  );
  assert.equal(first.value, "turn-stable");
  assert.equal(retry.value, "turn-stable");
  assert.notEqual(first.value, "http-1");
});

test("shared Hermes session pending rejects a second task without marking it pending", async () => {
  const { writeFile } = await import("node:fs/promises");
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/api/sessions/shared") return new Response(JSON.stringify({ session: { id: "shared" } }), { status: 200 });
    if (parsed.pathname.endsWith("/chat")) throw new Error("second task must not chat");
    return new Response("{}", { status: 404 });
  });
  await writeFile(adapter.mappingPath, JSON.stringify({
    version: 1,
    tasks: {
      [TASK]: { hermesSessionId: "shared", pending: { op: "chat", sessionId: "shared", uncertain: true } },
      [TASK_B]: { hermesSessionId: "shared" },
    },
    sessions: { shared: { tasks: [TASK, TASK_B] } },
  }, null, 2));
  const result = await adapter.handleResponses({
    request: { headers: { "thread-id": TASK_B, "x-request-id": "other" } },
    response: mockResponse(),
    payload: payload("from-b"),
  });
  assert.equal(result.status, 409);
  const stored = JSON.parse(await readFile(adapter.mappingPath, "utf8"));
  assert.equal(stored.tasks[TASK_B].pending, undefined);
  assert.equal(stored.tasks[TASK].pending.uncertain, true);
});

test("stale lock from a dead PID is reclaimed; live PID is not", async () => {
  const { writeFile, mkdtemp } = await import("node:fs/promises");
  const { acquireFileLock, pidIsAlive } = await import("../src/hermes-picker-adapter.mjs");
  const dir = await mkdtemp(path.join(os.tmpdir(), "hermes-lock-"));
  const stale = path.join(dir, "stale.lock");
  await writeFile(stale, "9999999", { flag: "wx" });
  await acquireFileLock(stale);
  assert.equal(pidIsAlive(process.pid), true);
  const live = path.join(dir, "live.lock");
  await writeFile(live, String(process.pid), { flag: "wx" });
  await assert.rejects(() => acquireFileLock(live), /Timed out/);
});

test("real HTTP success and post-heartbeat error stay SSE", async () => {
  const http = await import("node:http");
  const adapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      return new Response(JSON.stringify({ session: { id: "http-s" } }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) {
      return new Response(JSON.stringify({ session_id: "http-s", message: { content: "ok-sse" } }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    await adapter.handleResponses({ request: req, response: res, payload: body });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const ok = await fetch(`http://127.0.0.1:${port}/`, {
    method: "POST",
    headers: { "content-type": "application/json", "thread-id": TASK, "x-request-id": "http-ok" },
    body: JSON.stringify(payload("hello")),
  });
  const okText = await ok.text();
  assert.equal(ok.status, 200);
  assert.match(ok.headers.get("content-type"), /text\/event-stream/);
  assert.match(okText, /ok-sse/);
  assert.doesNotMatch(okText, /ERR_HTTP_HEADERS_SENT/);

  const failAdapter = await adapterWith(async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/api/sessions" && options.method === "POST") {
      return new Response(JSON.stringify({ session: { id: "http-f" } }), { status: 200 });
    }
    if (parsed.pathname.endsWith("/chat")) throw new DOMException("aborted", "AbortError");
    return new Response("{}", { status: 404 });
  });
  const failServer = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    await failAdapter.handleResponses({ request: req, response: res, payload: body });
  });
  await new Promise((resolve) => failServer.listen(0, "127.0.0.1", resolve));
  const failPort = failServer.address().port;
  const bad = await fetch(`http://127.0.0.1:${failPort}/`, {
    method: "POST",
    headers: { "content-type": "application/json", "thread-id": TASK, "x-request-id": "http-bad" },
    body: JSON.stringify(payload("do")),
  });
  const badText = await bad.text();
  assert.equal(bad.status, 200);
  assert.match(bad.headers.get("content-type"), /text\/event-stream/);
  assert.match(badText, /event: error/);
  assert.doesNotMatch(badText, /ERR_HTTP_HEADERS_SENT/);
  server.close();
  failServer.close();
});
