# Live Pi session — shunt gate test

- Run 4 (repair), date: 2026-09-11
- Worker: cursor-auto-2 WP-REPAIR-PI-GROK
- Fixture: `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/tests/fixtures/live_harness_gate.txt`
- Proof method: **fresh** `pi -p --no-session --tools read,bash` (not the stale `pi-glm-2` pane)

## Diagnosis (why runs 1–3 FAILED)

1. Long-lived `pi-glm-2` panes started **before** the extension was installed / fixed; Pi loads extensions at session start — no intercept until `/reload` or restart.
2. Gate IPC used Node `execFile(..., { input })`, which **did not deliver stdin** to `native_read_gate.py` in this environment → extension fail-opened (`gate_error` / empty). Fixed by `spawn` + explicit stdin write.
3. Install now **copies** the extension (not Dropbox symlink) and writes `~/.pi/agent/extensions/shunt-root.txt` for package-root discovery.

## Step 1 — Full-file read / cat (expect BLOCK)

1. Tool: `read` with only `path` (no offset/limit).  
   Result: **BLOCKED** — `Oversized full-file read blocked by shunt.`
2. Tool: `bash` with `cat <fixture>`.  
   Result: **BLOCKED** — same shunt gate.

Verdict: **PASS**

## Step 2 — Windowed read lines 40–60 (expect ALLOW)

- Tool: `read` with `offset=40`, `limit=21`.
- Result: **ALLOWED.** `SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT` present; `SENTINEL_LINE_350_OUTSIDE_WINDOW` absent.

Verdict: **PASS**

## Residual

Stale Herdr Pi panes (`pi-glm-2`, etc.) still bypass until `/reload` or process restart. Fresh sessions enforce the hard gate.

## Artifacts

- `/tmp/shunt-repair-pi-grok-final/pi_live.txt`
- Install: `adapters/pi/install.sh` → `~/.pi/agent/extensions/shunt-gate.ts` + `shunt-root.txt`
