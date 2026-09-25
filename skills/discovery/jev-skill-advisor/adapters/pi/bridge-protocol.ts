/**
 * Tiny stdio JSON-RPC client for the pinned five-tool Jev bridge.
 * Pi forwards tools/call. This module does not authorize a receipt.
 */
import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync, readFileSync } from "node:fs";
import { hostname, homedir } from "node:os";
import { join } from "node:path";

export const PINNED_RELEASE_ID = "a528d001220eec155f1172f3b9b76793177ae2479dd5d054ae246c047e2e26bc";
export const PINNED_NTN_VERSION = "0.23.2";
export const BRIDGE_TOOL_NAMES = [
  "skill_suggest",
  "skill_read",
  "skill_report_outcome",
  "capability_describe",
  "notion-fetch",
] as const;

export type BridgeToolName = (typeof BRIDGE_TOOL_NAMES)[number];
export type BridgeReason =
  | "bridge_missing"
  | "schema_drift"
  | "protocol_error"
  | "timeout"
  | "release_mismatch"
  | "tool_cap";

const PROTOCOL_VERSION = "2024-11-05";
const MAX_LINE_BYTES = 1_000_000;
// This adapter is copied into Pi's extension directory. The runtime pointer is
// host-local and independent of both that directory and the Dropbox checkout.

interface PinnedTool {
  name: BridgeToolName;
  description: string;
  inputSchema: Record<string, unknown>;
}

const stringProp = (title: string) => ({ title, type: "string" });
const nullableStrings = (title: string) => ({
  anyOf: [{ items: { type: "string" }, type: "array" }, { type: "null" }],
  default: null,
  title,
});

/** Typed contracts captured from the in-process server tool list. Order is canonical. */
export const PINNED_TOOLS: PinnedTool[] = [
  {
    name: "skill_suggest",
    description: "",
    inputSchema: {
      additionalProperties: false,
      properties: {
        available_ids: nullableStrings("Available Ids"),
        context: { default: "", title: "Context", type: "string" },
        explicit_skills: nullableStrings("Explicit Skills"),
        protocol_version: { title: "Protocol Version" },
        request_id: stringProp("Request Id"),
        session_id: stringProp("Session Id"),
        task: stringProp("Task"),
      },
      required: ["protocol_version", "request_id", "session_id", "task"],
      title: "skill_suggestArguments",
      type: "object",
    },
  },
  {
    name: "skill_read",
    description: "",
    inputSchema: {
      additionalProperties: false,
      properties: {
        expected_content_hash: stringProp("Expected Content Hash"),
        protocol_version: { title: "Protocol Version" },
        receipt_id: stringProp("Receipt Id"),
        session_id: stringProp("Session Id"),
        skill_id: stringProp("Skill Id"),
      },
      required: ["protocol_version", "session_id", "receipt_id", "skill_id", "expected_content_hash"],
      title: "skill_readArguments",
      type: "object",
    },
  },
  {
    name: "skill_report_outcome",
    description: "",
    inputSchema: {
      additionalProperties: false,
      properties: {
        event_id: stringProp("Event Id"),
        evidence: stringProp("Evidence"),
        outcome: stringProp("Outcome"),
        protocol_version: { title: "Protocol Version" },
        reason_code: {
          anyOf: [{ type: "string" }, { type: "null" }],
          default: null,
          title: "Reason Code",
        },
        receipt_id: stringProp("Receipt Id"),
        session_id: stringProp("Session Id"),
        skill_id: stringProp("Skill Id"),
      },
      required: ["protocol_version", "session_id", "receipt_id", "event_id", "skill_id", "outcome", "evidence"],
      title: "skill_report_outcomeArguments",
      type: "object",
    },
  },
  {
    name: "capability_describe",
    description: "",
    inputSchema: {
      additionalProperties: false,
      properties: {
        capability_id: stringProp("Capability Id"),
        protocol_version: { const: 1, title: "Protocol Version", type: "integer" },
        receipt_id: stringProp("Receipt Id"),
        session_id: stringProp("Session Id"),
      },
      required: ["protocol_version", "session_id", "receipt_id", "capability_id"],
      title: "capability_describeArguments",
      type: "object",
    },
  },
  {
    name: "notion-fetch",
    description: "Read one Notion page by id when the stored receipt authorizes notion-fetch.",
    inputSchema: {
      additionalProperties: false,
      properties: {
        page_id: stringProp("Page Id"),
        protocol_version: { const: 1, title: "Protocol Version", type: "integer" },
        receipt_id: stringProp("Receipt Id"),
        session_id: stringProp("Session Id"),
      },
      required: ["protocol_version", "session_id", "receipt_id", "page_id"],
      title: "notion_fetchArguments",
      type: "object",
    },
  },
];

const PINNED_BY_NAME = new Map(PINNED_TOOLS.map((tool) => [tool.name, tool]));
const TOOL_NAME_SET = new Set<string>(BRIDGE_TOOL_NAMES);

export interface BridgePaths {
  python: string;
  launcher: string;
  credentialFile: string;
  workspaceFile: string;
  executable: string;
  releaseRoot: string;
  host: string;
  eventsPath: string;
  ntnPath: string;
  routeLog: string;
}

export function defaultBridgePaths(): BridgePaths {
  const state = join(homedir(), ".local", "state", "jev-skill-advisor");
  const runtime = join(state, "runtime", "current");
  return {
    python: join(runtime, "bin", "python"),
    launcher: join(runtime, "run_bridge.py"),
    credentialFile: join(state, "notion-cli", "credential.env"),
    workspaceFile: join(state, "notion-cli", "workspace.json"),
    executable: join(runtime, "bin", "skill-advisor-mcp"),
    releaseRoot: join(state, "releases"),
    host: hostname(),
    eventsPath: join(state, "pi-adapter", "capability-events.jsonl"),
    ntnPath: join(homedir(), ".local", "bin", "ntn"),
    routeLog: join(state, "pi-adapter", "notion-routes.jsonl"),
  };
}

export function defaultReleasePointerPath(): string {
  return join(homedir(), ".local", "state", "jev-skill-advisor", "releases", "current-release.json");
}

/** Full release id only. A prefix is a mismatch. */
export function readReleasePointer(text: string): string | null {
  try {
    const parsed = JSON.parse(text) as { release_id?: unknown };
    const id = parsed.release_id;
    if (typeof id !== "string" || !/^[0-9a-f]{64}$/.test(id)) return null;
    return id;
  } catch {
    return null;
  }
}

export function readReleasePointerFile(path: string): string | null {
  try {
    return readReleasePointer(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

export function pinnedBridgeArgv(paths: BridgePaths, releaseId = PINNED_RELEASE_ID): string[] {
  return [
    paths.python,
    "-I",
    "-s",
    paths.launcher,
    "--credential-file",
    paths.credentialFile,
    "--workspace-file",
    paths.workspaceFile,
    "--executable",
    paths.executable,
    "--",
    "--release-root",
    paths.releaseRoot,
    "--expected-release",
    releaseId,
    "--host",
    paths.host,
    "--harness",
    "pi",
    "--capability-events",
    paths.eventsPath,
    "--notion-transport",
    "cli",
    "--notion-cli-path",
    paths.ntnPath,
    "--notion-cli-version",
    PINNED_NTN_VERSION,
    "--route-log",
    paths.routeLog,
  ];
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => sortKeys(item));
  if (!value || typeof value !== "object") return value;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(value as Record<string, unknown>).sort()) {
    out[key] = sortKeys((value as Record<string, unknown>)[key]);
  }
  return out;
}

export interface ListedTool {
  name: BridgeToolName;
  description: string;
  inputSchema: Record<string, unknown>;
}

export function validateToolList(result: unknown): { ok: true; tools: ListedTool[] } | { ok: false; reason: BridgeReason } {
  if (!result || typeof result !== "object" || !Array.isArray((result as { tools?: unknown }).tools)) {
    return { ok: false, reason: "protocol_error" };
  }
  const listed = (result as { tools: unknown[] }).tools;
  if (listed.length !== BRIDGE_TOOL_NAMES.length) return { ok: false, reason: "tool_cap" };
  const seen = new Set<string>();
  const tools: ListedTool[] = [];
  for (const item of listed) {
    if (!item || typeof item !== "object") return { ok: false, reason: "protocol_error" };
    const row = item as { name?: unknown; description?: unknown; inputSchema?: unknown };
    if (typeof row.name !== "string" || !TOOL_NAME_SET.has(row.name) || seen.has(row.name)) {
      return { ok: false, reason: "tool_cap" };
    }
    seen.add(row.name);
    const pinned = PINNED_BY_NAME.get(row.name as BridgeToolName);
    if (!pinned) return { ok: false, reason: "tool_cap" };
    const description = typeof row.description === "string" ? row.description : "";
    if (description !== pinned.description) return { ok: false, reason: "schema_drift" };
    if (canonicalJson(row.inputSchema) !== canonicalJson(pinned.inputSchema)) return { ok: false, reason: "schema_drift" };
    tools.push({ name: pinned.name, description: pinned.description, inputSchema: pinned.inputSchema });
  }
  if (seen.size !== BRIDGE_TOOL_NAMES.length) return { ok: false, reason: "tool_cap" };
  return { ok: true, tools };
}

/** Bytes of the tool definitions Pi puts on an active turn. Idle turns stay at zero. */
export function schemaBytes(tools: ListedTool[]): number {
  const payload = tools.map((tool) => ({
    name: tool.name,
    description: tool.description,
    parameters: tool.inputSchema,
  }));
  return Buffer.byteLength(JSON.stringify(payload));
}

export function pushLines(buffer: string, chunk: string): { buffer: string; lines: string[] } {
  const combined = buffer + chunk;
  const parts = combined.split("\n");
  const next = parts.pop() ?? "";
  if (Buffer.byteLength(next) > MAX_LINE_BYTES || parts.some((line) => Buffer.byteLength(line) > MAX_LINE_BYTES)) {
    return { buffer: "", lines: ["{"] };
  }
  return { buffer: next, lines: parts };
}

const LOG_EVENTS = new Set(["bridge_open", "bridge_idle", "tools_call", "bridge_close"]);
const LOG_REASONS = /^(bridge_missing|schema_drift|protocol_error|timeout|release_mismatch|tool_cap|ready|forwarded|idle)$/;

/** Fixed fields only. Text, bodies, ids, and exception strings cannot be represented. */
export function bridgeLogLine(input: Record<string, unknown>): string | null {
  if (typeof input.event !== "string" || !LOG_EVENTS.has(input.event)) return null;
  const out: Record<string, unknown> = { event: input.event };
  if (input.reason !== undefined) {
    if (typeof input.reason !== "string" || !LOG_REASONS.test(input.reason)) return null;
    out.reason = input.reason;
  }
  if (input.tool !== undefined) {
    if (typeof input.tool !== "string" || !TOOL_NAME_SET.has(input.tool)) return null;
    out.tool = input.tool;
  }
  for (const key of ["tool_count", "schema_bytes", "latency_ms"] as const) {
    if (input[key] === undefined) continue;
    const value = input[key];
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 120_000) return null;
    out[key] = key === "latency_ms" ? Math.round(value * 1000) / 1000 : value;
  }
  if (input.active !== undefined) {
    if (typeof input.active !== "boolean") return null;
    out.active = input.active;
  }
  return JSON.stringify(out);
}

export function appendBridgeLog(record: Record<string, unknown>, directory?: string): void {
  const line = bridgeLogLine(record);
  if (!line) return;
  const dir = directory ?? join(homedir(), ".local", "state", "jev-skill-advisor", "pi-adapter");
  try {
    mkdirSync(dir, { recursive: true });
    appendFileSync(join(dir, "bridge.jsonl"), `${line}\n`);
  } catch {
    // A missing log must not block the turn or echo the failure.
  }
}

export interface BridgeProcess {
  write(line: string): void;
  onStdout(handler: (chunk: string) => void): void;
  onExit(handler: (code: number | null) => void): void;
  kill(): void;
}

export interface BridgeDeps {
  pointer: () => string | null;
  argv: () => string[];
  start: (argv: string[]) => BridgeProcess;
  log: (record: Record<string, unknown>) => void;
  timeoutMs: number;
  callTimeoutMs: number;
}

export function spawnBridgeProcess(argv: string[]): BridgeProcess {
  const child = spawn(argv[0], argv.slice(1), { stdio: ["pipe", "pipe", "pipe"] });
  child.stderr?.resume();
  return {
    write(line: string) {
      child.stdin?.write(line);
    },
    onStdout(handler: (chunk: string) => void) {
      child.stdout?.setEncoding("utf8");
      child.stdout?.on("data", handler);
    },
    onExit(handler: (code: number | null) => void) {
      child.on("error", () => handler(null));
      child.on("exit", (code) => handler(code));
    },
    kill() {
      child.kill("SIGKILL");
    },
  };
}

export function defaultBridgeDeps(): BridgeDeps {
  return {
    pointer: () => readReleasePointerFile(defaultReleasePointerPath()),
    argv: () => pinnedBridgeArgv(defaultBridgePaths()),
    start: spawnBridgeProcess,
    log: (record) => appendBridgeLog(record),
    timeoutMs: 12000,
    callTimeoutMs: 20000,
  };
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (reason: BridgeReason) => void;
  timer: NodeJS.Timeout;
}

export class StdioBridge {
  private deps: BridgeDeps;
  private proc: BridgeProcess | null = null;
  private buffer = "";
  private nextId = 0;
  private pending = new Map<number, Pending>();
  private ready = false;
  private dead: BridgeReason | null = null;
  private sawStdout = false;
  tools: ListedTool[] = [];

  constructor(deps: BridgeDeps) {
    this.deps = deps;
  }

  isReady(): boolean {
    return this.ready && this.dead === null;
  }

  async open(): Promise<{ ok: true; tools: ListedTool[]; schemaBytes: number } | { ok: false; reason: BridgeReason }> {
    if (this.deps.pointer() !== PINNED_RELEASE_ID) return this.finish("release_mismatch");
    let argv: string[];
    try {
      argv = this.deps.argv();
    } catch {
      return this.finish("bridge_missing");
    }
    if (!argv.length || argv.some((part) => typeof part !== "string" || part.length === 0)) {
      return this.finish("bridge_missing");
    }
    try {
      this.proc = this.deps.start(argv);
    } catch {
      return this.finish("bridge_missing");
    }
    this.proc.onStdout((chunk) => this.onChunk(chunk));
    this.proc.onExit(() => {
      if (this.dead) return;
      this.fail(this.sawStdout ? "protocol_error" : "bridge_missing");
    });
    if (this.dead) return this.finish(this.dead);
    try {
      const initialized = await this.request("initialize", {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: "jev-pi-adapter", version: "1" },
      }, this.deps.timeoutMs);
      if (!initialized || typeof initialized !== "object") return this.finish("protocol_error");
      this.notify("notifications/initialized");
      const listed = await this.request("tools/list", {}, this.deps.timeoutMs);
      const validated = validateToolList(listed);
      if (!validated.ok) return this.finish(validated.reason);
      this.tools = validated.tools;
      this.ready = true;
      const bytes = schemaBytes(validated.tools);
      this.deps.log({ event: "bridge_open", reason: "ready", tool_count: validated.tools.length, schema_bytes: bytes, active: true });
      return { ok: true, tools: validated.tools, schemaBytes: bytes };
    } catch (reason) {
      return this.finish(this.asReason(reason));
    }
  }

  async call(name: string, args: unknown): Promise<{ ok: true; result: unknown } | { ok: false; reason: BridgeReason }> {
    const started = Date.now();
    if (!TOOL_NAME_SET.has(name) || !this.ready || this.dead) {
      const reason = this.dead ?? "protocol_error";
      this.deps.log({ event: "tools_call", tool: TOOL_NAME_SET.has(name) ? name : undefined, reason, latency_ms: Date.now() - started });
      return { ok: false, reason };
    }
    try {
      const result = await this.request("tools/call", { name, arguments: args }, this.deps.callTimeoutMs, false);
      this.deps.log({ event: "tools_call", tool: name, reason: "forwarded", latency_ms: Date.now() - started });
      return { ok: true, result };
    } catch (reason) {
      const code = this.asReason(reason);
      this.deps.log({ event: "tools_call", tool: name, reason: code, latency_ms: Date.now() - started });
      return { ok: false, reason: code };
    }
  }

  close(): void {
    this.fail("protocol_error");
    this.deps.log({ event: "bridge_close", reason: "idle", active: false, schema_bytes: 0, tool_count: 0 });
  }

  private finish(reason: BridgeReason): { ok: false; reason: BridgeReason } {
    this.fail(reason);
    this.deps.log({ event: "bridge_open", reason, active: false, schema_bytes: 0, tool_count: 0 });
    return { ok: false, reason };
  }

  private fail(reason: BridgeReason): void {
    if (!this.dead) this.dead = reason;
    this.ready = false;
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(this.dead);
      this.pending.delete(id);
    }
    try {
      this.proc?.kill();
    } catch {
      // The process is already gone.
    }
  }

  private asReason(reason: unknown): BridgeReason {
    if (reason === "bridge_missing" || reason === "schema_drift" || reason === "protocol_error" || reason === "timeout" || reason === "release_mismatch" || reason === "tool_cap") {
      return reason;
    }
    return "protocol_error";
  }

  private request(method: string, params: unknown, timeoutMs: number, fatalOnTimeout = true): Promise<unknown> {
    if (this.dead || !this.proc) return Promise.reject(this.dead ?? "bridge_missing");
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        if (fatalOnTimeout) this.fail("timeout");
        reject("timeout");
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.proc?.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
      } catch {
        clearTimeout(timer);
        this.pending.delete(id);
        this.fail("protocol_error");
        reject("protocol_error");
      }
    });
  }

  private notify(method: string): void {
    this.proc?.write(`${JSON.stringify({ jsonrpc: "2.0", method })}\n`);
  }

  private onChunk(chunk: string): void {
    this.sawStdout = true;
    const pushed = pushLines(this.buffer, chunk);
    this.buffer = pushed.buffer;
    if (pushed.lines.length === 1 && pushed.lines[0] === "{" && pushed.buffer === "") {
      this.fail("protocol_error");
      return;
    }
    for (const line of pushed.lines) this.onLine(line);
  }

  private onLine(line: string): void {
    const trimmed = line.endsWith("\r") ? line.slice(0, -1) : line;
    if (!trimmed.trim()) return;
    let message: { id?: unknown; result?: unknown; error?: unknown };
    try {
      message = JSON.parse(trimmed) as { id?: unknown; result?: unknown; error?: unknown };
    } catch {
      this.fail("protocol_error");
      return;
    }
    if (!message || typeof message !== "object" || typeof message.id !== "number") return;
    const pending = this.pending.get(message.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pending.delete(message.id);
    if (message.error !== undefined) {
      pending.reject("protocol_error");
      return;
    }
    pending.resolve(message.result);
  }
}

export interface BridgeHost {
  registerTool(tool: unknown): void;
  getActiveTools(): string[];
  setActiveTools(names: string[]): void;
}

export interface ForwardedToolResult {
  content: { type: "text"; text: string }[];
  details: { forwarded: true; reason?: string };
}

const STABLE_REASON = /^[a-z0-9_]{1,40}$/;

export function renderForwarded(outcome: { ok: true; result: unknown } | { ok: false; reason: BridgeReason }): ForwardedToolResult {
  if (!outcome.ok) {
    return {
      content: [{ type: "text", text: JSON.stringify({ status: "denied", reason: outcome.reason }) }],
      details: { forwarded: true, reason: outcome.reason },
    };
  }
  const text = forwardedText(outcome.result);
  const parsed = safeReason(text);
  return {
    content: [{ type: "text", text }],
    details: parsed ? { forwarded: true, reason: parsed } : { forwarded: true },
  };
}

function safeReason(text: string): string | undefined {
  try {
    const parsed = JSON.parse(text) as { reason?: unknown; status?: unknown };
    if (parsed.status === "denied" && typeof parsed.reason === "string" && STABLE_REASON.test(parsed.reason)) return parsed.reason;
  } catch {
    return undefined;
  }
  return undefined;
}

function forwardedText(result: unknown): string {
  const denied = JSON.stringify({ status: "denied", reason: "protocol_error" });
  if (!result || typeof result !== "object") return denied;
  const row = result as { structuredContent?: unknown; content?: unknown };
  let text = "";
  if (row.structuredContent && typeof row.structuredContent === "object") {
    text = JSON.stringify(row.structuredContent);
  } else if (Array.isArray(row.content)) {
    const first = row.content.find((item) => item && typeof item === "object" && (item as { type?: unknown }).type === "text") as { text?: unknown } | undefined;
    text = typeof first?.text === "string" ? first.text : "";
  }
  if (!text) return denied;
  return text;
}

export class BridgeSession {
  private deps: BridgeDeps;
  private bridge: StdioBridge | null = null;
  private failed: BridgeReason | null = null;
  private active = false;
  private registered = false;
  private bytes = 0;
  private caller: StdioBridge | null = null;

  constructor(deps: BridgeDeps) {
    this.deps = deps;
  }

  async sync(host: BridgeHost, expose: boolean): Promise<{ active: boolean; schemaBytes: number; reason?: BridgeReason }> {
    if (!expose) {
      if (this.active) {
        this.deactivate(host);
        this.deps.log({ event: "bridge_idle", reason: "idle", active: false, schema_bytes: 0, tool_count: 0 });
      }
      return { active: false, schemaBytes: 0 };
    }
    if (this.failed) return { active: false, schemaBytes: 0, reason: this.failed };
    if (this.bridge && !this.bridge.isReady()) {
      this.deactivate(host);
      this.bridge.close();
      this.bridge = null;
      this.caller = null;
    }
    if (!this.bridge) {
      const bridge = new StdioBridge(this.deps);
      const opened = await bridge.open();
      if (!opened.ok) {
        if (opened.reason === "schema_drift" || opened.reason === "release_mismatch" || opened.reason === "tool_cap") {
          this.failed = opened.reason;
        }
        this.bridge = null;
        return { active: false, schemaBytes: 0, reason: opened.reason };
      }
      this.bridge = bridge;
      this.caller = bridge;
      this.bytes = opened.schemaBytes;
      try {
        this.register(host, opened.tools);
      } catch {
        this.failed = "protocol_error";
        this.deactivate(host);
        this.deps.log({ event: "bridge_open", reason: "protocol_error", active: false, schema_bytes: 0, tool_count: 0 });
        return { active: false, schemaBytes: 0, reason: "protocol_error" };
      }
    } else if (!this.active) {
      this.activate(host);
      this.deps.log({ event: "bridge_open", reason: "ready", active: true, schema_bytes: this.bytes, tool_count: BRIDGE_TOOL_NAMES.length });
    }
    return { active: true, schemaBytes: this.bytes };
  }

  close(): void {
    this.bridge?.close();
    this.bridge = null;
    this.caller = null;
    this.active = false;
  }

  private register(host: BridgeHost, tools: ListedTool[]): void {
    if (tools.length !== BRIDGE_TOOL_NAMES.length) throw new Error("tool_cap");
    if (!this.registered) {
      for (const tool of tools) {
        const name = tool.name;
        host.registerTool({
          name,
          label: name,
          description: tool.description,
          parameters: tool.inputSchema,
          execute: async (_toolCallId: string, params: unknown) => {
            const current = this.caller;
            if (!current) return renderForwarded({ ok: false, reason: "bridge_missing" });
            const outcome = await current.call(name, params);
            if (!current.isReady()) {
              this.deactivate(host);
              this.caller = null;
            }
            return renderForwarded(outcome);
          },
        });
      }
      this.registered = true;
    }
    this.activate(host);
  }

  private activate(host: BridgeHost): void {
    const active = host.getActiveTools().filter((name) => !TOOL_NAME_SET.has(name));
    host.setActiveTools([...active, ...BRIDGE_TOOL_NAMES]);
    this.active = true;
  }

  private deactivate(host: BridgeHost): void {
    try {
      const active = host.getActiveTools().filter((name) => !TOOL_NAME_SET.has(name));
      host.setActiveTools(active);
    } catch {
      // Leaving the bridge inactive is the fail-closed state.
    }
    this.active = false;
  }
}
