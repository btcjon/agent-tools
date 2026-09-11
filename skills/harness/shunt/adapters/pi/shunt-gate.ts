/**
 * Pi shunt gate — HARD enforcement via tool_call interception.
 *
 * Mode: hard (blocks oversized full-file `read` and bash cat of large files).
 * Install: copy this file into ~/.pi/agent/extensions/shunt-gate.ts (see install.sh).
 * Long-lived Pi panes must `/reload` or restart after install.
 *
 * Also registers `shunt_bulk_read` so the model can invoke the CLI after a block.
 */
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

/** Minimal JSON Schema (avoid runtime `typebox` import — jiti fails on Dropbox-symlink paths). */
const BulkReadParams = {
  type: "object",
  required: ["path"],
  properties: {
    path: { type: "string", description: "Absolute or workspace-relative file path" },
    offset: { type: "number" },
    limit: { type: "number" },
  },
} as const;

function findShuntRoot(): string {
  const env = process.env.SHUNT_ROOT?.trim();
  if (env && existsSync(join(env, "adapters", "_common", "native_read_gate.py"))) {
    return env;
  }
  // install.sh writes this next to the copied extension (copy is not under package root).
  try {
    const here = dirname(fileURLToPath(import.meta.url));
    const marker = join(here, "shunt-root.txt");
    if (existsSync(marker)) {
      const marked = readFileSync(marker, "utf8").trim();
      if (marked && existsSync(join(marked, "adapters", "_common", "native_read_gate.py"))) {
        return marked;
      }
    }
    const root = join(here, "..", "..");
    if (existsSync(join(root, "adapters", "_common", "native_read_gate.py"))) return root;
  } catch {
    /* symlink / non-file URL */
  }
  const dropbox = join(
    homedir(),
    "Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt",
  );
  return dropbox;
}

function runGateJson(gate: string, payload: Record<string, unknown>, root: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const env = { ...process.env, SHUNT_ROOT: root };
    // Gate short-circuits when SHUNT_INTERNAL is set (bulk-read recursion guard).
    delete env.SHUNT_INTERNAL;
    // Use spawn+stdin — promisified execFile({input}) silently failed open in live Pi sessions.
    const child = spawn("python3", [gate, "--json-stdin"], {
      env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("gate_timeout"));
    }, 8_000);
    child.stdout.on("data", (d) => {
      stdout += d.toString("utf8");
    });
    child.stderr.on("data", (d) => {
      stderr += d.toString("utf8");
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (stdout.trim()) {
        resolve(stdout);
        return;
      }
      reject(new Error(`gate_exit_${code}:${stderr || "no_stdout"}`));
    });
    child.stdin.write(JSON.stringify(payload));
    child.stdin.end();
  });
}

async function nativeGate(payload: Record<string, unknown>): Promise<{
  block: boolean;
  reason?: string;
  message?: string;
}> {
  const root = findShuntRoot();
  const gate = join(root, "adapters", "_common", "native_read_gate.py");
  try {
    const stdout = await runGateJson(gate, payload, root);
    return JSON.parse(stdout.trim() || "{}");
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { block: false, reason: `gate_error:${msg}` };
  }
}

export default function shuntGate(pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    const name = String(event.toolName || "").toLowerCase();
    if (name === "read") {
      const input = event.input as { path?: string; offset?: number; limit?: number };
      const result = await nativeGate({
        toolName: "read",
        toolInput: input,
      });
      if (result.block) {
        return {
          block: true,
          reason:
            result.message ||
            result.reason ||
            `Oversized full-file read blocked. Run: shunt bulk-read ${input.path ?? "<path>"}`,
        };
      }
      return;
    }

    if (name === "bash") {
      const input = event.input as { command?: string };
      const result = await nativeGate({
        toolName: "bash",
        toolInput: { command: input.command },
      });
      if (result.block) {
        return {
          block: true,
          reason:
            result.message ||
            result.reason ||
            "Oversized full-file cat blocked. Run: shunt bulk-read <path>",
        };
      }
    }
  });

  pi.registerTool({
    name: "shunt_bulk_read",
    label: "Shunt bulk-read",
    description:
      "Gate + OpenRouter google/gemini-3.8-flash points-only summary for oversized files. Use when a native read was blocked by the shunt gate.",
    parameters: BulkReadParams as never,
    async execute(_toolCallId, params) {
      const root = findShuntRoot();
      const args = ["-m", "shunt.cli", "bulk-read", params.path];
      if (params.offset != null) args.push("--offset", String(params.offset));
      if (params.limit != null) args.push("--limit", String(params.limit));
      try {
        const text = await new Promise<string>((resolve, reject) => {
          const child = spawn("python3", args, {
            cwd: root,
            env: {
              ...process.env,
              SHUNT_INTERNAL: "1",
              SHUNT_ROOT: root,
              PYTHONPATH: [join(root, "src"), process.env.PYTHONPATH || ""]
                .filter(Boolean)
                .join(":"),
            },
            stdio: ["ignore", "pipe", "pipe"],
          });
          let stdout = "";
          let stderr = "";
          const timer = setTimeout(() => {
            child.kill("SIGKILL");
            reject(new Error("bulk_read_timeout"));
          }, 60_000);
          child.stdout.on("data", (d) => {
            stdout += d.toString("utf8");
          });
          child.stderr.on("data", (d) => {
            stderr += d.toString("utf8");
          });
          child.on("error", (err) => {
            clearTimeout(timer);
            reject(err);
          });
          child.on("close", () => {
            clearTimeout(timer);
            resolve(stdout || stderr || "(empty)");
          });
        });
        return {
          content: [{ type: "text" as const, text }],
          details: {},
        };
      } catch (err: unknown) {
        const text = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text" as const, text }],
          details: {},
          isError: true,
        };
      }
    },
  });
}
