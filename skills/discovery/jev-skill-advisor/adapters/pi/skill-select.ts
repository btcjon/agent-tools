/**
 * Pi startup extension. Runs skill-search before the agent loop and injects
 * one verified skill, or nothing. After a selected capability, it registers
 * the five pinned bridge tools for that turn. See adapters/pi/INTEGRATION.md.
 */
import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { appendFileSync, mkdirSync, readFileSync } from "node:fs";
import { homedir, hostname } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { BridgeSession, defaultBridgeDeps, type BridgeDeps } from "./bridge-protocol.ts";

const MARKERS = ["typesafe_api_key=", "jev_api=", "authorization: bearer", "-----begin "];
let lastTask = "";

function selectorEnv(): NodeJS.ProcessEnv {
  const env = { ...process.env };
  if (env.TYPESAFE_API_KEY) return env;
  if (env.JEV_API) {
    env.TYPESAFE_API_KEY = env.JEV_API;
    return env;
  }
  try {
    const path = join(homedir(), "Dropbox", "AI-Control-Plane", "secrets", "secrets.common.env");
    for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
      const match = line.match(/^\s*(?:export\s+)?(?:TYPESAFE_API_KEY|JEV_API)\s*=\s*(.*?)\s*$/);
      if (match?.[1]) {
        env.TYPESAFE_API_KEY = match[1].replace(/^(?:"|')|(?:"|')$/g, "");
        break;
      }
    }
  } catch {
    // The selector can still return no skill when the existing key is unavailable.
  }
  return env;
}

interface SelectedSkill {
  skill_id?: string;
  snapshot_id?: string;
  hash?: string;
  content?: string;
}

const CAPABILITY_ID = /^[a-z0-9][a-z0-9._:-]{0,96}$/;
const MANIFEST_HASH = /^[0-9a-f]{64}$/;

/** Same canonical JSON as select_cli.capability_correlation. ASCII ids only. */
export function capabilityCorrelation(manifestHash: string, skillId: string, ids: string[], status: string): string {
  const canonical = JSON.stringify({
    ids,
    manifest_hash: manifestHash,
    skill_id: skillId,
    status,
  });
  return createHash("sha256").update(canonical).digest("hex");
}

interface AcceptedCapabilities {
  cards: { id: string; description: string }[];
  manifestHash: string;
  skillId: string;
  status: string;
}

export function acceptCapabilities(capabilities: unknown, skillId: string | undefined): AcceptedCapabilities | null {
  if (!capabilities || typeof capabilities !== "object" || !skillId) return null;
  const caps = capabilities as Record<string, unknown>;
  if (caps.advisory !== true || caps.authorizes_calls !== false) return null;
  if (!Array.isArray(caps.cards) || caps.cards.length === 0 || caps.cards.length > 5) return null;
  if (typeof caps.skill_id !== "string" || caps.skill_id !== skillId) return null;
  if (caps.status !== "selected" && caps.status !== "none" && caps.status !== "fail_open") return null;
  if (typeof caps.manifest_hash !== "string" || (caps.manifest_hash !== "" && !MANIFEST_HASH.test(caps.manifest_hash))) return null;
  const cards: { id: string; description: string }[] = [];
  for (const card of caps.cards) {
    if (!card || typeof card !== "object") return null;
    const row = card as Record<string, unknown>;
    if (typeof row.id !== "string" || !CAPABILITY_ID.test(row.id)) return null;
    if (typeof row.description !== "string" || row.description.length === 0 || Buffer.byteLength(row.description) > 160) return null;
    if (row.description.includes("inputSchema") || row.description.includes("schema_hash") || /[\n<>]/.test(row.description)) return null;
    cards.push({ id: row.id, description: row.description.split(/\s+/).join(" ").trim() });
  }
  const expected = capabilityCorrelation(caps.manifest_hash, caps.skill_id, cards.map((card) => card.id), caps.status);
  if (caps.correlation !== expected) return null;
  return { cards, manifestHash: caps.manifest_hash, skillId: caps.skill_id, status: caps.status };
}

export function formatCapabilityHint(capabilities: unknown, skillId: string | undefined): string {
  const accepted = acceptCapabilities(capabilities, skillId);
  if (!accepted) return "";
  const lines = accepted.cards.map((card) => `${card.id}: ${card.description}`);
  return `\n\n<capability-hint advisory="true" authorizes-calls="false" manifest-hash="${accepted.manifestHash}">\n${lines.join("\n")}\n</capability-hint>`;
}

export function contentFreeCapabilityFields(capabilities: unknown, skillId: string | undefined) {
  const accepted = acceptCapabilities(capabilities, skillId);
  if (!accepted) return undefined;
  return {
    capability_ids: accepted.cards.map((card) => card.id),
    capability_manifest_hash: accepted.manifestHash,
    capability_status: accepted.status,
    capability_surface: "cli" as const,
  };
}

export function createSkillSelect(deps: BridgeDeps = defaultBridgeDeps()) {
  const session = new BridgeSession(deps);
  return function skillSelect(pi: ExtensionAPI) {
    pi.on("session_shutdown", () => {
      session.close();
    });
    pi.on("before_agent_start", async (event) => {
    const text = typeof event.prompt === "string" ? event.prompt.trim() : "";
    const lowered = text.toLowerCase();
    if (!text || text === lastTask || text.length > 8000 || MARKERS.some((marker) => lowered.includes(marker))) {
      try {
        await session.sync(pi, false);
      } catch {
        // A skipped turn must not retain the previous turn's optional tools.
      }
      return;
    }
    lastTask = text;
    const started = performance.now();
    const proc = spawnSync(join(homedir(), ".local", "bin", "skill-search"), ["--select", "--task", text, "--json"], {
      encoding: "utf8",
      timeout: 22000,
      env: selectorEnv(),
    });
    let selected: SelectedSkill | undefined;
    let capabilities: unknown;
    let reason = proc.error ? "selector_failure" : "invalid_selector_output";
    try {
      const result = JSON.parse(proc.stdout || "{}");
      selected = result.selected;
      capabilities = result.capabilities;
      if (typeof result.reason === "string" && /^[a-z0-9_]+$/.test(result.reason)) reason = result.reason;
    } catch {
      // Log a failed attempt without prompt or provider response.
    }
    const content = selected?.content;
    const delivered = !!selected && typeof content === "string" && !!content;
    const accepted = delivered ? acceptCapabilities(capabilities, selected?.skill_id) : null;
    let bridgeActive = false;
    try {
      bridgeActive = (await session.sync(pi, accepted?.status === "selected")).active;
    } catch {
      // Tool exposure fails closed. The skill text can still be injected.
    }
    const hint = bridgeActive ? formatCapabilityHint(capabilities, selected?.skill_id) : "";
    const capabilityFields = bridgeActive ? contentFreeCapabilityFields(capabilities, selected?.skill_id) : undefined;
    const status = delivered ? "emitted" : reason === "catalog_choice_none" ? "abstained" : "fallback";
    const dir = join(homedir(), ".local", "state", "jev-skill-advisor", "pi-adapter");
    const receiptId = randomUUID().replace(/-/g, "");
    try {
      mkdirSync(dir, { recursive: true });
      appendFileSync(
        join(dir, "events.jsonl"),
        `${JSON.stringify({
          usage_id: delivered ? receiptId : undefined,
          event_schema: 1,
          event: "selection_attempt",
          attempt_id: receiptId,
          receipt_id: delivered ? receiptId : undefined,
          adapter: "pi",
          harness: "pi",
          host: hostname(),
          timestamp: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
          selected_at: delivered ? new Date().toISOString().replace(/\.\d{3}Z$/, "Z") : undefined,
          status,
          skill_ids: delivered && selected?.skill_id ? [selected.skill_id] : [],
          reason: delivered ? undefined : reason,
          skill_id: delivered ? selected?.skill_id : undefined,
          snapshot_id: selected?.snapshot_id,
          hash: delivered ? selected?.hash : undefined,
          bytes: delivered ? Buffer.byteLength(content as string) : undefined,
          latency_ms: Math.round((performance.now() - started) * 1000) / 1000,
          provenance: "pi_extension",
          ...capabilityFields,
        })}\n`,
      );
    } catch {
      // A missing receipt must not block the task.
    }
    if (!delivered) return;
    return {
      message: {
        customType: "skill-select",
        content: `Selected skill instructions follow. Use them for this task.\n\n<selected-skill id="${selected.skill_id}">\n${content}\n</selected-skill>${hint}`,
        display: false,
      },
    };
    });
  };
}

export default createSkillSelect();
