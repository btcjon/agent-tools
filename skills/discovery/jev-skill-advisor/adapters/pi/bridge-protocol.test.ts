import assert from "node:assert/strict";
import test from "node:test";

import {
  BRIDGE_TOOL_NAMES,
  BridgeSession,
  PINNED_RELEASE_ID,
  PINNED_TOOLS,
  StdioBridge,
  bridgeLogLine,
  defaultBridgePaths,
  pushLines,
  renderForwarded,
  schemaBytes,
  validateToolList,
  type BridgeDeps,
  type BridgeProcess,
} from "./bridge-protocol.ts";

class FakeProcess implements BridgeProcess {
  private toolList: unknown;
  private hang: boolean;
  private toolMode: "error-once" | "hang-once" | null;
  private stdout: (chunk: string) => void = () => {};
  private exit: (code: number | null) => void = () => {};
  readonly calls: { method: string; params: unknown }[] = [];
  killed = false;

  constructor(toolList: unknown = { tools: PINNED_TOOLS }, hang = false,
              toolMode: "error-once" | "hang-once" | null = null) {
    this.toolList = toolList;
    this.hang = hang;
    this.toolMode = toolMode;
  }

  write(line: string): void {
    const message = JSON.parse(line);
    if (message.method === "notifications/initialized") return;
    this.calls.push({ method: message.method, params: message.params });
    if (this.hang) return;
    if (message.method === "tools/call" && this.toolMode) {
      const mode = this.toolMode;
      this.toolMode = null;
      if (mode === "hang-once") return;
      queueMicrotask(() => this.stdout(`${JSON.stringify({ jsonrpc: "2.0", id: message.id, error: { code: -32602, message: "bad arguments" } })}\n`));
      return;
    }
    const result = message.method === "initialize"
      ? { protocolVersion: "2024-11-05", capabilities: {}, serverInfo: { name: "test", version: "1" } }
      : message.method === "tools/list"
        ? this.toolList
        : { structuredContent: { status: "denied", reason: "unauthorized_capability" } };
    queueMicrotask(() => this.stdout(`${JSON.stringify({ jsonrpc: "2.0", id: message.id, result })}\n`));
  }

  onStdout(handler: (chunk: string) => void): void { this.stdout = handler; }
  onExit(handler: (code: number | null) => void): void { this.exit = handler; }
  kill(): void { this.killed = true; this.exit(null); }
}

function deps(proc: FakeProcess, overrides: Partial<BridgeDeps> = {}): BridgeDeps {
  return {
    pointer: () => PINNED_RELEASE_ID,
    argv: () => ["/test/bridge", "--expected-release", PINNED_RELEASE_ID],
    start: () => proc,
    log: () => {},
    timeoutMs: 30,
    callTimeoutMs: 30,
    ...overrides,
  };
}

test("the pinned bridge exposes exactly five known schemas", async () => {
  const proc = new FakeProcess();
  const bridge = new StdioBridge(deps(proc));
  const opened = await bridge.open();
  assert.equal(opened.ok, true);
  if (opened.ok) {
    assert.deepEqual(opened.tools.map((tool) => tool.name), [...BRIDGE_TOOL_NAMES]);
    assert.equal(opened.schemaBytes, schemaBytes(opened.tools));
  }
  assert.deepEqual(proc.calls.map((call) => call.method), ["initialize", "tools/list"]);
  bridge.close();
  assert.equal(proc.killed, true);
});

test("an installed extension still launches the pinned package root", () => {
  const paths = defaultBridgePaths();
  assert.match(paths.launcher, /\/Projects\/agent-tools\/skills\/discovery\/jev-skill-advisor\/adapters\/registration\/run_bridge\.py$/);
  assert.equal(paths.launcher.startsWith("/Users/jonbennett/.pi/"), false);
});

test("schema drift and extra tools fail before Pi registration", async () => {
  const changed = structuredClone(PINNED_TOOLS);
  changed[4].inputSchema.properties.page_id = { title: "Page Id", type: "number" };
  assert.deepEqual(validateToolList({ tools: changed }), { ok: false, reason: "schema_drift" });
  assert.deepEqual(validateToolList({ tools: [...PINNED_TOOLS, { name: "extra" }] }), { ok: false, reason: "tool_cap" });
  const proc = new FakeProcess({ tools: changed });
  const opened = await new StdioBridge(deps(proc)).open();
  assert.deepEqual(opened, { ok: false, reason: "schema_drift" });
  assert.equal(proc.killed, true);
});

test("release mismatch and startup timeout fail closed", async () => {
  const skipped = new FakeProcess();
  const mismatch = await new StdioBridge(deps(skipped, { pointer: () => "0".repeat(64) })).open();
  assert.deepEqual(mismatch, { ok: false, reason: "release_mismatch" });
  assert.equal(skipped.calls.length, 0);
  const hung = new FakeProcess(undefined, true);
  const timeout = await new StdioBridge(deps(hung, { timeoutMs: 5 })).open();
  assert.deepEqual(timeout, { ok: false, reason: "timeout" });
  assert.equal(hung.killed, true);
});

test("tool calls are forwarded and receipt denials stay denials", async () => {
  const proc = new FakeProcess();
  const bridge = new StdioBridge(deps(proc));
  assert.equal((await bridge.open()).ok, true);
  const args = { protocol_version: 1, session_id: "session", receipt_id: "missing", page_id: "fixture" };
  const result = await bridge.call("notion-fetch", args);
  assert.equal(result.ok, true);
  assert.deepEqual(proc.calls.at(-1), { method: "tools/call", params: { name: "notion-fetch", arguments: args } });
  assert.deepEqual(JSON.parse(renderForwarded(result).content[0].text), { status: "denied", reason: "unauthorized_capability" });
  bridge.close();
});

test("one tool error or timeout does not poison the whole session", async () => {
  for (const mode of ["error-once", "hang-once"] as const) {
    const proc = new FakeProcess({ tools: PINNED_TOOLS }, false, mode);
    const bridge = new StdioBridge(deps(proc, { callTimeoutMs: 5 }));
    assert.equal((await bridge.open()).ok, true);
    const first = await bridge.call("notion-fetch", {});
    assert.deepEqual(first, { ok: false, reason: mode === "error-once" ? "protocol_error" : "timeout" });
    assert.equal(bridge.isReady(), true);
    assert.equal((await bridge.call("notion-fetch", {})).ok, true);
    bridge.close();
  }
});

test("a transient bridge startup timeout can be retried on a later turn", async () => {
  const hung = new FakeProcess(undefined, true);
  const ready = new FakeProcess();
  let starts = 0;
  const d = deps(hung, { timeoutMs: 5, start: () => ++starts === 1 ? hung : ready });
  let active: string[] = [];
  const host = {
    registerTool() {},
    getActiveTools() { return active; },
    setActiveTools(names: string[]) { active = names; },
  };
  const session = new BridgeSession(d);
  assert.deepEqual(await session.sync(host, true), { active: false, schemaBytes: 0, reason: "timeout" });
  assert.equal((await session.sync(host, true)).active, true);
  assert.equal(starts, 2);
  session.close();
});

test("authorized page text is preserved even when it documents a token variable name", () => {
  const result = { status: "called", result: { markdown: "Set NOTION_API_TOKEN in your environment." } };
  const forwarded = renderForwarded({ ok: true, result: { structuredContent: result } });
  assert.deepEqual(JSON.parse(forwarded.content[0].text), result);
  assert.equal(forwarded.details.forwarded, true);
});

test("irrelevant turns add zero schemas and relevant turns remove them when done", async () => {
  const proc = new FakeProcess();
  const registered: unknown[] = [];
  let active = ["read"];
  const host = {
    registerTool(tool: unknown) { registered.push(tool); },
    getActiveTools() { return active; },
    setActiveTools(names: string[]) { active = names; },
  };
  const session = new BridgeSession(deps(proc));
  assert.deepEqual(await session.sync(host, false), { active: false, schemaBytes: 0 });
  assert.equal(proc.calls.length, 0);
  const enabled = await session.sync(host, true);
  assert.equal(enabled.active, true);
  assert.equal(registered.length, 5);
  assert.deepEqual(active, ["read", ...BRIDGE_TOOL_NAMES]);
  assert.deepEqual(await session.sync(host, false), { active: false, schemaBytes: 0 });
  assert.deepEqual(active, ["read"]);
  session.close();
});

test("framing and telemetry discard oversized or sensitive fields", () => {
  assert.deepEqual(pushLines("", "x".repeat(1_000_001) + "\n"), { buffer: "", lines: ["{"] });
  const line = bridgeLogLine({ event: "tools_call", tool: "notion-fetch", reason: "forwarded", page_id: "private", body: "secret" });
  assert.ok(line);
  assert.equal(line.includes("private"), false);
  assert.equal(line.includes("secret"), false);
});
