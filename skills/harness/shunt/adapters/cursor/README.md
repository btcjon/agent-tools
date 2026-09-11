# Cursor adapter (hard hooks)

Gates oversized full-file reads via Cursor hooks:

| Event | Matcher | Script |
| --- | --- | --- |
| `preToolUse` | `Read\|Shell` | `pre_tool_use_read.py` (Read + Shell cat/head/tail) |
| `beforeShellExecution` | `\b(cat\|head\|tail)\b` | `before_shell_execution.py` (belt-and-suspenders) |

## Semantics

- Full Read / Shell `cat` of a file ≥ `min_lines` (default 350) → **deny**
- Deny message: `shunt bulk-read <path>` **or** re-issue Read with explicit `offset`/`limit`
- Read with real `offset` / `limit` in tool_input → **allow**
- Cursor stripping offset/limit to null → **deny** (never infer allow from `agent_message`)
- `head` / `tail` with an explicit window → **allow**
- Sets `SHUNT_INTERNAL=1` while sizing files (no hook recursion)

## Install

```bash
# from package root (merge-safe; preserves unrelated hooks)
shunt install --dry-run
shunt install
```

Snippet source: `registration.json` (merged into `~/.cursor/hooks.json` by `shunt install`).

## Proof (after install)

```bash
python3 adapters/run_fixture.py \
  adapters/cursor/pre_tool_use_read.py \
  adapters/cursor/tests/fixtures/shell_pretooluse_cat.json
# expect permission deny + shunt bulk-read

SAMPLE=tests/fixtures/live_harness_gate.txt
# Live: Shell `cat $SAMPLE` must be denied by preToolUse (see docs/LIVE-CURSOR-SHELL.md)
```
