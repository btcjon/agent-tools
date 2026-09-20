# Shunt reload policy (Pi / Grok / Herdr)

After `shunt install` (or adapter `install.sh`), **already-open agent sessions do not reliably pick up new hooks/extensions**. Herdr workers inherit the **host** harness; there is no second shunt inside Herdr.

## What needs a fresh session?

| Surface | Install artifact | Needs fresh session? | Exact reload / verify |
| --- | --- | --- | --- |
| **Pi** | `~/.pi/agent/extensions/shunt-gate.ts` | **Yes** — extensions load at session start | Exit Pi (`/exit` or Ctrl+C) → start new `pi` (or Herdr: restart agent in pane). Verify: full Read of `tests/fixtures/live_harness_gate.txt` → blocked + `shunt bulk-read`. |
| **Grok** | `~/.grok/hooks/shunt-pretooluse.json` | **Yes** — hooks scanned at session start | Exit Grok → `grok` / Herdr restart. Optional: `/hooks` to confirm registration. Verify: oversized Read denied (when live hooks fire). |
| **Hermes** | `hooks.pre_tool_call` in `~/.hermes/config.yaml` | **Yes** for long-lived agents | Restart Hermes agent / Desktop session so config is re-read. Verify: hook stdin deny or live tool block. |
| **Cursor** | `~/.cursor/hooks.json` | **Usually yes** (or Hooks reload) | Cursor: save `hooks.json` (watched) or restart agent chat. Verify fixture deny via hook script / agent Read. |
| **Codex** | `~/.codex/hooks.json` | **Yes** | New Codex turn/session after install. |
| **Claude** | `~/.claude/settings.json` hooks | **Yes** | New Claude Code session. |
| **AGY / Gemini** | `~/.gemini/config/hooks.json` | **Yes** | New AGY session. |
| **Herdr Flash / long-lived workers** | inherit host above | **Drain or restart** worker panes after host install | Do **not** interrupt `working` / foreign-claimed panes. Prefer idle/done panes. Hint script lists candidates (dry-run). |

## Exact reload commands (Mac)

```bash
# 1) Install / refresh adapters (host)
cd /Users/jonbennett/Library/CloudStorage/Dropbox/Projects/agent-tools/skills/harness/shunt
shunt install                 # cursor/codex/claude merge
bash adapters/pi/install.sh
bash adapters/grok/install.sh
bash adapters/hermes/install.sh   # or ensure config.yaml pre_tool_call entry
bash adapters/agy/install.sh

# 2) List panes that likely still run pre-install sessions (dry-run; no interrupts)
./scripts/herdr-shunt-reload-hint.sh
# or: ./scripts/herdr-shunt-reload-hint.sh --apply-hints   # still does NOT send keys; only prints suggested commands

# 3) Per-harness fresh sessions (operator / coordinator)
# Pi (standalone):
pi -p --no-session --tools read '…oversized fixture…'   # expect BLOCKED after reload
# Pi (Herdr pane — only if idle and you own the claim):
#   herdr agent start <name> --kind pi --pane <pane-id> …
# Grok:
grok --single '…' --cwd "$PWD"
# Cursor / Codex / Claude: open a **new** agent chat/session after hooks merge.
```

## Safety rules (parallel-safe)

1. **Never** `herdr pane send-keys` / `agent prompt` into `agent_status=working` or foreign-owned claims.
2. Hint script is **dry-run by default**: list only.
3. Busy / unknown / foreign panes are labeled `DO_NOT_INTERRUPT`.
4. Idle/done Pi & Grok panes are labeled `RELOAD_CANDIDATE` with a suggested restart command — human/coordinator executes.

## Related

- `docs/HERDR.md` — inheritance model  
- `docs/LIVE-PHG.md` — live Pi/Hermes/Grok proofs  
- `docs/PARITY-MAC-VPS.md` — cross-host install parity
