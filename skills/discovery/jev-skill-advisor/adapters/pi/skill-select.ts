/**
 * Pi startup extension. Runs skill-search before the agent loop and injects
 * one verified skill, or nothing.
 *
 * Install: copy this file to ~/.pi/agent/extensions/skill-select.ts
 * and restart Pi.
 */
import { spawnSync } from "node:child_process";
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

const MARKERS = ["typesafe_api_key=", "jev_api=", "authorization: bearer", "-----begin "];
let lastTask = "";

interface SelectedSkill {
  skill_id?: string;
  snapshot_id?: string;
  hash?: string;
  content?: string;
}

export default function skillSelect(pi: ExtensionAPI) {
  pi.on("before_agent_start", async (event) => {
    const text = typeof event.prompt === "string" ? event.prompt.trim() : "";
    const lowered = text.toLowerCase();
    if (!text || text === lastTask || text.length > 8000 || MARKERS.some((marker) => lowered.includes(marker))) {
      return;
    }
    lastTask = text;
    const proc = spawnSync(join(homedir(), ".local", "bin", "skill-search"), ["--select", "--task", text, "--json"], {
      encoding: "utf8",
      timeout: 22000,
    });
    let selected: SelectedSkill | undefined;
    try {
      selected = JSON.parse(proc.stdout || "{}").selected;
    } catch {
      return;
    }
    const content = selected?.content;
    if (!selected || typeof content !== "string" || !content) {
      return;
    }
    const dir = join(homedir(), ".local", "state", "jev-skill-advisor", "pi-adapter");
    try {
      mkdirSync(dir, { recursive: true });
      appendFileSync(
        join(dir, "events.jsonl"),
        `${JSON.stringify({
          skill_id: selected.skill_id,
          snapshot_id: selected.snapshot_id,
          hash: selected.hash,
          bytes: Buffer.byteLength(content),
        })}\n`,
      );
    } catch {
      // A missing receipt must not block the task.
    }
    return {
      message: {
        customType: "skill-select",
        content: `Selected skill instructions follow. Use them for this task.\n\n<selected-skill id="${selected.skill_id}">\n${content}\n</selected-skill>`,
        display: false,
      },
    };
  });
}
