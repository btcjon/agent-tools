# RECEIPT — WP-REPAIR

**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`  
**When:** 2026-09-11  
**Worker:** cursor-auto-2

## Verdict

Repair complete. `install.sh` is no longer registered as a hook; AGY/Hermes/Pi/Grok use WP4 authoritative paths; Cursor windowed Read allows when offset/limit are present **or** when Cursor strips them but `agent_message` still indicates a window.

## Fixes

### 1. Install discovery (`src/shunt/install.py`)
- `_SKIP_NAMES` includes `install.sh`, README, snippets, templates.
- Gate scripts only via `registration.json` or `_GATE_NAME_RE`.
- `pi` / `hermes` / `grok` / `agy` → `style: "external"` (no Cursor-shaped JSON merge).
- AGY target hint: `~/.gemini/config/hooks.json`.
- Obsolete cleanup on uninstall: `.agy/hooks.json`, `.pi/agent/hooks.json`, `.hermes/shunt-hooks.json`, `.grok/hooks.json`.
- Refuse any registration blob containing `install.sh`.

### 2. Cursor windowed Read (`src/shunt/hook_runtime.py`)
- Parse `tool_input` / `toolInput` / top-level `path`/`offset`/`limit` / `startLine`/`endLine`.
- If offset+limit absent but `agent_message` matches window language → allow as windowed (`decide_agent_read(..., offset=0, limit=1)`).
- Broader read tool name set.
- Fixture: `adapters/cursor/tests/fixtures/read_windowed_agent_message.json`.

### 3. Hermes installer (`adapters/hermes/install.sh`)
- Idempotent merge into `~/.hermes/config.yaml` `hooks.pre_tool_call` (not sidecar-only).

## Tests

```text
pytest -q → 36 passed, 1 skipped
```

Coverage in `tests/test_repair.py`: skip-names, external plan skips, toolInput camelCase, top-level fields, start/end line, agent_message fallback when offset/limit null.

## Live reinstall

1. `shunt uninstall` — removed shunt entries from cursor/codex/claude; cleaned obsolete noise paths (including `.grok/hooks.json` install.sh).
2. `shunt install` — cursor/codex/claude only; external harnesses skipped with WP4 hints.
3. WP4: `adapters/{pi,grok,agy,hermes}/install.sh`.

### Post-condition (`shunt doctor`)

| Harness | Registered | Path |
|---------|------------|------|
| cursor | yes | `~/.cursor/hooks.json` (Read + shell) |
| codex | yes | `~/.codex/hooks.json` |
| claude | yes | `~/.claude/settings.json` |
| pi | yes | `~/.pi/agent/extensions/shunt-gate.ts` symlink |
| hermes | yes | `~/.hermes/config.yaml` `hooks.pre_tool_call` |
| grok | yes | `~/.grok/hooks/shunt-pretooluse.json` |
| agy | yes | `~/.gemini/config/hooks.json` group `shunt` (herdr preserved) |

**No `install.sh` references** in any of the scanned hook configs above.

Legacy empty shells left after cleanup (harmless, no install.sh): `~/.agy/hooks.json`, `~/.pi/agent/hooks.json`, `~/.hermes/shunt-hooks.json`, `~/.grok/hooks.json` (`preToolUse: []`).

### Cursor gate dry-run (486-line sample)

- Full Read (null offset/limit) → **deny** + bulk-read message  
- Windowed Read (`offset`/`limit`) → **allow**  
- Null offset/limit + agent_message “lines 10 to 50…” → **allow**

## Caveats

- Cursor still drops offset/limit in live preToolUse (upstream). Inference depends on `agent_message` containing window cues; silent full-path Reads without message still deny (correct).
- Hermes may still prompt allowlist/consent on first live fire (`hermes hooks` / `HERMES_ACCEPT_HOOKS=1`).
- Empty legacy JSON files were not deleted (only shunt entries cleared).
