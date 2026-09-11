# RECEIPT — WP-REPAIR-PI-GROK

**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`  
**When:** 2026-09-11  
**Worker:** cursor-auto-2

## Verdict

**PASS** on fresh Pi and Grok sessions. Prior LIVE FAIL was stale panes + Pi gate IPC fail-open — not missing registration in doctor.

## Root causes

| Harness | Why live full-read ALLOWED | Fix |
|---------|----------------------------|-----|
| **Pi** | (1) Long-lived panes never reloaded extensions after install. (2) `execFile({input})` did not feed stdin to `native_read_gate.py` → fail-open. | `spawn` + stdin; copy install + `shunt-root.txt`; docs demand `/reload`. |
| **Grok** | Long-lived `--always-approve` panes started before hook file existed; PreToolUse never loaded shunt. | Keep `~/.grok/hooks/shunt-pretooluse.json`; also append `[[hooks.PreToolUse]]` to `config.toml`; exit `2` on deny; restart required. |

Cursor/Claude not weakened (`~/.cursor/hooks.json` still has shunt Read hook).

## Code / installer changes

- `adapters/pi/shunt-gate.ts` — spawn stdin gate; `@mariozechner/pi-coding-agent` types; `shunt-root.txt` discovery
- `adapters/pi/install.sh` — copy + write `shunt-root.txt` + reload warning
- `adapters/grok/install.sh` — JSON hook + idempotent `config.toml` append
- `adapters/grok/hooks.json` — matcher includes `run_terminal_cmd`
- `adapters/_common/shunt-hook.sh` — Grok deny exits `2`
- READMEs + LIVE docs updated

## Tests

```text
adapters/pi + grok + _common + test_repair → 20 passed
```

## Live proofs (fresh processes)

**Pi** (`pi -p --no-session`): Call1=BLOCKED, Cat1=BLOCKED, Call2=ALLOWED (sentinel 50 yes / 350 no).  
**Grok** (`grok --always-approve -p`): Call1=BLOCKED, Cat1=BLOCKED, Call2=ALLOWED.

Artifacts under `/tmp/shunt-repair-pi-grok-final/`.

## Residual blocker

Herdr panes `pi-glm-2` / `grok-build-3` that were already running still bypass until **reload/restart**. New sessions enforce hard deny.
