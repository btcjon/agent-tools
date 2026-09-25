/** Read-only installed-Pi bridge parity canary. Prints hashes, never page text or IDs. */
import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const sha = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
const state = join(homedir(), ".local", "state", "jev-skill-advisor");

function body(result: unknown): Record<string, unknown> {
  if (!result || typeof result !== "object") throw new Error("invalid_result");
  const row = result as { structuredContent?: unknown; content?: { text?: string }[] };
  const parsed = row.structuredContent ?? JSON.parse(row.content?.[0]?.text ?? "{}");
  if (!parsed || typeof parsed !== "object") throw new Error("invalid_result");
  const value = parsed as Record<string, unknown>;
  const inner = value.result ?? value;
  if (!inner || typeof inner !== "object") throw new Error("invalid_result");
  return inner as Record<string, unknown>;
}

function pageId(snapshotManifest: string): string {
  const manifest = JSON.parse(readFileSync(snapshotManifest, "utf8")) as {
    packages: { stable_id?: string; notion_id?: string }[];
  };
  const matches = manifest.packages.filter((item) => item.stable_id === "warehouse:approval-gated-migrations");
  if (matches.length !== 1 || !matches[0].notion_id) throw new Error("canary_page_missing");
  return matches[0].notion_id;
}

function nativeRead(id: string): { sha256: string; bytes: number } {
  const raw = readFileSync(join(state, "notion-cli", "credential.env"), "utf8").trim();
  const token = raw.startsWith("NOTION_API_TOKEN=") ? raw.slice("NOTION_API_TOKEN=".length) : "";
  if (!token || token.includes("\n")) throw new Error("credential_invalid");
  const workspace = JSON.parse(readFileSync(join(state, "notion-cli", "workspace.json"), "utf8")) as {
    workspace_id?: string;
  };
  if (!workspace.workspace_id) throw new Error("workspace_invalid");
  const bytes = execFileSync(join(homedir(), ".local", "bin", "ntn"),
    ["api", `v1/pages/${id}/markdown`, "-X", "GET"], {
      env: {
        HOME: homedir(), PATH: "/usr/bin:/bin:/opt/homebrew/bin",
        NOTION_API_TOKEN: token, NOTION_EXPECTED_WORKSPACE_ID: workspace.workspace_id,
        NOTION_CREDENTIAL_SOURCE: "env",
      },
      timeout: 30000, maxBuffer: 1000000,
    });
  const page = JSON.parse(bytes.toString("utf8")) as { object?: string; markdown?: string; truncated?: boolean };
  if (page.object !== "page_markdown" || page.truncated !== false || typeof page.markdown !== "string") {
    throw new Error("native_read_incomplete");
  }
  return { sha256: sha(page.markdown), bytes: Buffer.byteLength(page.markdown, "utf8") };
}

async function main() {
  const { StdioBridge, canonicalJson, defaultBridgeDeps } = await import(
    "file://" + join(homedir(), ".pi", "agent", "extensions", "bridge-protocol.ts")
  );
  const snapshot = process.argv[2];
  if (!snapshot) throw new Error("snapshot_missing");
  const id = pageId(snapshot);
  const direct = nativeRead(id);
  const bridge = new StdioBridge(defaultBridgeDeps());
  try {
    const opened = await bridge.open();
    if (!opened.ok || opened.tools.length !== 5 || opened.schemaBytes !== 2927) {
      throw new Error("tool_set_mismatch");
    }
    const schemas = Object.fromEntries(opened.tools.map((tool) => [tool.name, tool.inputSchema]));
    const schemaSha = sha(canonicalJson(schemas));
    const sessionId = "pi-canary-" + randomUUID();
    const suggested = await bridge.call("skill_suggest", {
      protocol_version: 1, request_id: "pi-canary-" + randomUUID(),
      session_id: sessionId, task: "Read the complete contents of a Notion page by its UUID.",
    });
    if (!suggested.ok) throw new Error("suggest_failed");
    const choice = body(suggested.result);
    const cards = choice.capabilities as { id?: string }[] | undefined;
    if (cards?.length !== 1 || cards[0].id !== "notion.cli.page_read" || typeof choice.receipt_id !== "string") {
      throw new Error("capability_not_selected");
    }
    const fetched = await bridge.call("notion-fetch", {
      protocol_version: 1, session_id: sessionId, receipt_id: choice.receipt_id, page_id: id,
    });
    if (!fetched.ok) throw new Error("fetch_failed");
    const result = body(fetched.result);
    const page = (result.result && typeof result.result === "object" ? result.result : result) as Record<string, unknown>;
    if (typeof page.markdown !== "string" || page.truncated !== false) throw new Error("bridge_read_incomplete");
    const denied = await bridge.call("notion-fetch", {
      protocol_version: 1, session_id: "forged-" + randomUUID(), receipt_id: "forged", page_id: id,
    });
    if (!denied.ok) throw new Error("denial_probe_failed");
    const denial = body(denied.result).reason;
    const bodySha = sha(page.markdown);
    const bodyBytes = Buffer.byteLength(page.markdown, "utf8");
    const match = bodySha === direct.sha256 && bodyBytes === direct.bytes;
    const status = match && denial === "unauthorized_capability" ? "pass" : "fail";
    process.stdout.write(JSON.stringify({
      status, harness: "pi", tool_count: opened.tools.length, schema_sha256: schemaSha,
      capability_id: "notion.cli.page_read", body_sha256: bodySha,
      body_bytes: bodyBytes, native_match: match, denial,
    }) + "\n");
    process.exitCode = status === "pass" ? 0 : 1;
  } finally {
    bridge.close();
  }
}

main().catch(() => {
  process.stdout.write(JSON.stringify({ status: "fail", harness: "pi", reason: "canary_failed" }) + "\n");
  process.exitCode = 1;
});
