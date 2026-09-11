# Live Cursor — Shell preToolUse + windowed Read (PKG1)

- Date: 2026-09-11
- Worker: cursor-auto-2
- Fixture: `tests/fixtures/live_harness_gate.txt` (400 lines)

## Registration

`~/.cursor/hooks.json` (via `shunt install`):

| Event | Matcher | Script |
| --- | --- | --- |
| `preToolUse` | `Read\|Shell` | `adapters/cursor/pre_tool_use_read.py` |
| `beforeShellExecution` | `\b(cat\|head\|tail)\b` | `adapters/cursor/before_shell_execution.py` |

## Shell `cat` — expect DENY by hook

### Live (cursor-agent with project `.cursor/hooks.json`)

Nested `cursor-agent -p --force` in the worker cwd, prompt to `cat <fixture> | head -n 3`:

- Hook log: `preToolUse` fired with `tool_name":"Shell"` and the cat command.
- Agent report: **blocked by PreToolUse hook (oversized-file deny)** — command did not succeed.

Artifact: `/tmp/shunt-pkg1-hook-fire.log` (Shell preToolUse payload), agent reply in `/tmp/shunt-pkg1-agent-out.txt`.

### Hook payload dry-run (Cursor 3.20.11 shape)

```json
{"permission":"deny","agent_message":"... Re-issue Read with explicit offset and limit, or run: shunt bulk-read <fixture>"}
```

File: `/tmp/shunt-pkg1-live/shell_pretooluse_deny.json`

### Note on Herdr Auto pane

This long-lived `CURSOR_AGENT=1` pane does **not** invoke user hooks for its own Shell/Read tools (probe: zero fire on local Shell/Read). IDE chats and fresh `cursor-agent` sessions **do** fire `preToolUse` for `Shell`. Prefer project or user `hooks.json` + new agent session for enforcement.

## Windowed Read — expect ALLOW + exact lines

- Live `Read` of fixture with `offset=40`, `limit=21`: **allowed**; output includes `SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT` and excludes `SENTINEL_LINE_350_OUTSIDE_WINDOW`.
- Exact lines 40–60 saved: `/tmp/shunt-pkg1-live/window_exact.txt`
- Hook dry-run with real offset/limit: `permission: allow` (`read_windowed_allow.json`)

## Stripped offset/limit — expect DENY (no agent_message allow)

When `tool_input` has `offset`/`limit` null, gate **denies** even if `agent_message` mentions a window. Message tells agent to re-issue Read with explicit offset/limit or `shunt bulk-read`.

Dry-run: `/tmp/shunt-pkg1-live/read_stripped_deny.json`

## Verdict

**PASS** — Shell oversized `cat` denied by Cursor `preToolUse`; windowed Read with real offset/limit allows and returns the expected window.
