# RECEIPT-WP3 — Cursor / Codex / Claude adapters

**job:** WP3 adapters (hard hooks)  
**when:** 2026-09-11  
**worker:** cursor-auto-4 (direct; no delegation)  
**package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`

## Delivered

### Shared core
- `src/shunt/agent_read.py` — `decide_agent_read` / shell `cat|head|tail` parse (inverse of bulk-read gate)
- `src/shunt/hook_runtime.py` — stdin JSON helpers; Cursor + Claude decision shapes
- `src/shunt/cli.py` — `shunt check-read PATH [--offset|--limit]` (exit 0 allow / 2 deny)
- Did **not** edit `bulk_read.py`, `HARNESS-HOOKS.md`, or pi/hermes/grok/agy adapters

### Cursor (`adapters/cursor/`)
- `pre_tool_use_read.py` — `preToolUse` matcher `Read`
- `before_shell_execution.py` — `beforeShellExecution` matcher cat/head/tail
- `registration.json` — merge-safe snippet for `~/.cursor/hooks.json`
- Fixtures under `adapters/cursor/tests/fixtures/`

### Codex (`adapters/codex/`)
- `pre_tool_use.py` — hard `PreToolUse` for **Bash** cat/head/tail
- `registration.json` → `~/.codex/hooks.json`
- README documents **advisory** for Read tool: Codex PreToolUse is shell-only upstream (`hooks` feature stable=true on this host)

### Claude (`adapters/claude/`)
- `pre_tool_use.py` — PreToolUse for Read + Bash (Spotify-style)
- `shunt-pretool.sh` — WP2 stub replaced with wrapper → Python gate
- `registration.json` merges into `~/.claude/settings.json` hooks without removing Orca/other entries (via `shunt install` marker)

### Tests / proof helper
- `tests/test_agent_read.py`, `tests/test_adapters_ccc.py`
- `adapters/run_fixture.py` — substitutes `__SAMPLE_OVERSIZED__` and runs a hook

## Verification

```text
pytest -q
→ 29 passed, 1 skipped

shunt check-read adapters/cursor/tests/fixtures/sample_oversized.txt
→ allow:false, exit 2, message includes "shunt bulk-read …"

python3 adapters/run_fixture.py adapters/cursor/pre_tool_use_read.py \
  adapters/cursor/tests/fixtures/read_oversized.json
→ {"permission":"deny", … "shunt bulk-read" …}

python3 adapters/run_fixture.py adapters/codex/pre_tool_use.py \
  adapters/codex/tests/fixtures/bash_cat_oversized.json
→ permissionDecision deny, exit 2

python3 adapters/run_fixture.py adapters/claude/pre_tool_use.py \
  adapters/claude/tests/fixtures/read_oversized.json
→ permissionDecision deny, exit 2

shunt install --dry-run
→ cursor/codex/claude show registration.json merge/create targets
```

## Proof after coordinator `shunt install`

Exact deny check (no live agent required):

```bash
cd /Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt
python3 adapters/run_fixture.py \
  adapters/cursor/pre_tool_use_read.py \
  adapters/cursor/tests/fixtures/read_oversized.json
# expect permission deny + agent_message containing: shunt bulk-read <sample path>
```

Live Cursor: after install, a full `Read` of a ≥350-line file should be denied with the same `shunt bulk-read` guidance; windowed Read (`offset`/`limit`) allowed.

## Notes
- Live harness configs were **not** mutated in this job (install remains coordinator-run).
- Codex Read hard-hook unavailable by platform design; Bash path is hard-gated.
