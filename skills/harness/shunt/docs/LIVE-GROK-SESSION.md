# Live Grok session — shunt gate test

- Run 3 (repair), date: 2026-09-11
- Worker: cursor-auto-2 WP-REPAIR-PI-GROK
- Fixture: `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/tests/fixtures/live_harness_gate.txt`
- Proof method: **fresh** `grok --always-approve -p …` (not the stale `grok-build-3` pane)

## Diagnosis (why runs 1–2 FAILED)

1. Long-lived Grok panes (`grok --always-approve …`) were started **before** `~/.grok/hooks/shunt-pretooluse.json` existed; hooks are bound at session start — PreToolUse never saw shunt.
2. Hook script itself was already correct (fixture dry-run deny/allow). Live bypass was load timing, not matcher/decision JSON.
3. Installer now also appends idempotent `[[hooks.PreToolUse]]` into `~/.grok/config.toml`, expands matcher (`run_terminal_cmd`), and deny path uses exit `2` plus `decision: deny`.

## Step 1 — Full-file read / cat (expect BLOCK)

1. Tool: `read_file` with only `target_file` (no offset/limit).  
   Result: **BLOCKED** — `Hook denied: Oversized full-file read blocked by shunt. Run: shunt bulk-read …`
2. Tool: `run_terminal_command` with `cat <fixture>`.  
   Result: **BLOCKED**

Verdict: **PASS**

## Step 2 — Windowed read lines 40–60 (expect ALLOW)

- Tool: `read_file` with `offset=40`, `limit=21`.
- Result: **ALLOWED.** `SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT` present; `SENTINEL_LINE_350_OUTSIDE_WINDOW` absent.

Verdict: **PASS**

## Residual

Stale Herdr Grok panes still bypass until **restart** (new process). Fresh `-p` / new interactive sessions enforce the hard gate. Cursor hooks left unchanged (still PASS).

## Artifacts

- `/tmp/shunt-repair-pi-grok-final/grok2_out.txt` (and earlier `/tmp/shunt-repair-pi-grok-16111/grok_stdout.txt`)
- `~/.grok/hooks/shunt-pretooluse.json`
- `~/.grok/config.toml` marker `# --- shunt PreToolUse (WP-REPAIR) ---`
