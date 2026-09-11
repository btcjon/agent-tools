# LIVE-CURSOR-COORDINATOR — this Auto-main session

**When:** 2026-09-11  
**Session:** Cursor Auto (coordinator) with user hooks active  

## Oversized Read → shunt deny (PASS)

Attempted `Read` on:
- `…/shunt/src/shunt/install.py` (486 lines)
- `…/shunt/tests/fixtures/live_harness_gate.txt` (400 lines), including with `offset`/`limit` parameters

Hook response (excerpt):

```text
File is oversized for a full agent read (N lines). Do not cat/Read the whole file.
Run: shunt bulk-read <path>
permissionDecision: deny
```

This is a **hook denial**, not model self-restraint.

## Shell `cat` via Cursor Shell tool → bypass (GAP)

`cat` of `live_harness_gate.txt` via the Shell tool returned full file contents (exit 0).  
`beforeShellExecution` did **not** block this path in this session. Fixture-level shell deny still passes when stdin is fed to `before_shell_execution.py` directly (see LIVE-CCC).

## Windowed content proof

`sed -n '40,60p'` on the fixture shows `SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT` and not line 350.

## Bulk-read

`shunt bulk-read` on oversized files returns OpenRouter `google/gemini-3.8-flash` points (`ok:true`, `stub:false`) when secrets are sourced.
