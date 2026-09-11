# RECEIPT — PKG1 (Cursor Shell + windowed Read)

**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`  
**When:** 2026-09-11  
**Worker:** cursor-auto-2

## Verdict

**PASS.** Cursor `preToolUse` now gates **Shell** `cat|head|tail` (not only `beforeShellExecution`). Stripped null offset/limit **denies** (no `agent_message` false-allow). Real offset/limit windowed Read still **allows**.

## Changes

1. `adapters/cursor/registration.json` — `preToolUse` matcher `Read|Shell`
2. `adapters/cursor/pre_tool_use_read.py` — routes via `gate_pre_tool_use` (Read + Shell)
3. `src/shunt/hook_runtime.py` — removed agent_message window inference; deny messages require explicit offset/limit or `shunt bulk-read`; `gate_pre_tool_use` for Shell
4. Tests: `tests/test_pkg1_cursor_shell.py` + updated `test_repair.py`
5. `shunt install` re-run → `~/.cursor/hooks.json` matcher `Read|Shell`
6. Docs: `docs/LIVE-CURSOR-SHELL.md`, this receipt

## Tests

```text
pytest -q → 41 passed, 1 skipped
```

## Live evidence

| Check | Result |
| --- | --- |
| Shell `cat` fixture via `cursor-agent -p` | **DENIED** by PreToolUse (agent reported block; hook log shows Shell payload) |
| Read `offset=40` `limit=21` | **ALLOWED**; sentinel 50 present, 350 absent |
| Stripped null offset/limit + agent_message | **DENIED** with re-issue / bulk-read message |

## Residual

Long-lived Herdr `CURSOR_AGENT=1` panes may not invoke `~/.cursor/hooks.json` for their own tools. Fresh IDE chats and `cursor-agent` sessions do. Project `.cursor/hooks.json` in the worker cwd was used for the live Shell deny proof.

Did not edit Flash routes, VPS, or git commit.
