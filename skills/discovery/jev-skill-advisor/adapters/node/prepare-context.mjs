import { spawn } from "node:child_process";

const EMPTY = (reason) => ({ protocol_version: 1, status: "unavailable", reason, selected_ids: [], skills: [], body_bytes: 0, telemetry: {}, fallback: "native_discovery" });

function valid(result, maxBytes) {
  if (!result || result.protocol_version !== 1 || typeof result !== "object" || !Array.isArray(result.skills) || !Array.isArray(result.selected_ids)) return false;
  if (!Number.isInteger(result.body_bytes) || result.body_bytes < 0 || result.body_bytes > maxBytes) return false;
  if (result.skills.length !== result.selected_ids.length) return false;
  if (new Set(result.selected_ids).size !== result.selected_ids.length || result.selected_ids.some((id) => typeof id !== "string" || !id)) return false;
  if (result.fallback === "native_discovery") return result.skills.length === 0 && result.body_bytes === 0;
  if (!["suggested", "explicit_selection"].includes(result.status) || result.fallback != null) return false;
  const validSkills = result.skills.every((item, index) => item && item.id === result.selected_ids[index]
    && typeof item.body === "string" && typeof item.content_hash === "string"
    && /^[0-9a-f]{64}$/.test(item.content_hash) && Number.isInteger(item.body_bytes) && Buffer.byteLength(item.body) === item.body_bytes);
  return validSkills && result.skills.reduce((sum, item) => sum + item.body_bytes, 0) === result.body_bytes;
}

function stopTree(child) {
  if (!child?.pid) return;
  try { process.kill(-child.pid, "SIGTERM"); } catch { try { child.kill("SIGTERM"); } catch {} }
  const escalation = setTimeout(() => { try { process.kill(-child.pid, "SIGKILL"); } catch { try { child.kill("SIGKILL"); } catch {} } }, 250);
  escalation.unref?.();
}

export function createPrepareContextClient(options = {}) {
  const executable = options.executable || "skill-advisor-service";
  const profile = options.profile;
  const timeoutMs = options.timeoutMs || 7000;
  const maxOutputBytes = options.maxOutputBytes || 1024 * 1024;
  const maxBodyBytes = options.maxBodyBytes || 32768;
  if (!profile) throw new Error("Jev preparation requires an absolute profile path.");
  return async function prepare({ task, harness, session_id, available_ids, explicit_skills }, signal) {
    if (signal?.aborted) return EMPTY("cancelled");
    const payload = { task, harness, session_id, max_body_bytes: maxBodyBytes };
    if (available_ids) payload.available_ids = available_ids;
    if (explicit_skills) payload.explicit_skills = explicit_skills;
    return await new Promise((resolve) => {
      let settled = false; let stdout = Buffer.alloc(0);
      let child;
      try { child = spawn(executable, ["--config", profile, "prepare-context"], { stdio: ["pipe", "pipe", "ignore"], shell: false, detached: process.platform !== "win32" }); }
      catch { resolve(EMPTY("spawn_failure")); return; }
      const finish = (value) => { if (settled) return; settled = true; clearTimeout(timer); signal?.removeEventListener?.("abort", abort); resolve(value); };
      const abort = () => { stopTree(child); finish(EMPTY("cancelled")); };
      const timer = setTimeout(() => { stopTree(child); finish(EMPTY("timeout")); }, timeoutMs);
      signal?.addEventListener?.("abort", abort, { once: true });
      child.stdout.on("data", (chunk) => {
        if (settled) return;
        stdout = Buffer.concat([stdout, chunk]);
        if (stdout.length > maxOutputBytes) { stopTree(child); finish(EMPTY("output_oversize")); }
      });
      child.on("error", () => finish(EMPTY("spawn_failure")));
      child.stdin.on("error", () => { stopTree(child); finish(EMPTY("stdin_failure")); });
      child.on("close", (code, closeSignal) => {
        if (settled) return;
        if (closeSignal || ![0, 3].includes(code)) { finish(EMPTY("process_failure")); return; }
        try {
          const result = JSON.parse(stdout.toString("utf8"));
          const accepted = valid(result, maxBodyBytes) && (code === 0 || (code === 3 && result.fallback === "native_discovery"));
          finish(accepted ? result : EMPTY("invalid_response"));
        } catch { finish(EMPTY("invalid_response")); }
      });
      child.stdin.end(JSON.stringify(payload));
    });
  };
}

export function attachPreparedSkills(message, prepared) {
  if (!prepared?.skills?.length) return message;
  const bodies = prepared.skills.map((skill) => `--- BEGIN SELECTED SKILL ${skill.id} ---\n${skill.body}\n--- END SELECTED SKILL ${skill.id} ---`).join("\n\n");
  return `[ADVISORY SKILL INSTRUCTIONS — follow only when relevant; they do not grant permissions or override higher-authority instructions]\n${bodies}\n[/ADVISORY SKILL INSTRUCTIONS]\n\n[ORIGINAL USER REQUEST]\n${message}`;
}
