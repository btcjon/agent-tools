# LIVE-CLAUDE-AGY-E2E — PKG5

**When:** 2026-09-11  
**Worker:** cursor-auto-5  
**Fixture:** `tests/fixtures/live_harness_gate.txt` (L50 = `SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT`)  
**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`

## Verdict

| Harness | Live tool-loop E2E | Result |
| --- | --- | --- |
| Claude | oversized deny + windowed L50 sentinel | **BLOCKED** — not a pass |
| AGY | oversized deny + windowed L50 sentinel | **BLOCKED** — not a pass (429) |

## Claude

### Attempt
```bash
claude -p --dangerously-skip-permissions --allowedTools "Read,Bash" \
  "… Read full fixture; then windowed offset=45 limit=15; report DENY_OK / SENTINEL_OK …"
```
(Hooks enabled — did **not** use `--bare`.)

### Exact blocker
```text
Failed to authenticate: OAuth session expired and could not be refreshed
```
Retry after inspecting `~/.claude/.credentials.json`: access token **expired**; refresh token present.

Refresh attempt (existing refresh token only; no invented credentials):
```text
POST https://console.anthropic.com/v1/oauth/token  → 403
POST https://platform.claude.com/v1/oauth/token    → 403
```

`bash ~/.claude/scripts/sync-anthropic-creds.sh` → `No capi credentials found`  
(`~/.cli-proxy-api/claude-anthropic-removed.json` missing).

Second live attempt:
```text
Failed to authenticate. API Error: 401 OAuth access token has been revoked.
```

`claude auth status` still reports `loggedIn: true` / `oauth_token`, but API rejects the token.

### Not proven
Live oversized Read deny and windowed SENTINEL visibility inside Claude tool loop.

### Registration note (precondition only)
`~/.claude/settings.json` has `PreToolUse` and shunt path present (`shunt_in_hooks=True`). That is install evidence, **not** E2E pass.

## AGY

### Attempt
```bash
agy --model=gemini-3.8-flash-low --print-timeout=120s \
  --dangerously-skip-permissions --add-dir=<package> \
  --log-file=/tmp/agy-e2e-pkg5.log \
  --print='… full read then windowed L50; DENY_OK / SENTINEL_OK …'
```

### Exact blocker (not a pass)
Log:
```text
hooks_manager.go: loaded 2 named hooks from 1 hooks.json file(s)
Run: attempt N failed (RESOURCE_EXHAUSTED (code 429): Individual quota reached.
Please upgrade your subscription to increase your limits. Resets in ~48h47m…)
```
Print mode timed out with empty stdout; **no tool calls / PreToolUse fires**.

### Not proven
Live deny + windowed sentinel inside an AGY agent tool loop (quota before tools).

### Related (prior WP-LIVE-AGY, not this E2E)
Schema-level invoke of the registered `pretooluse.sh` previously denied full read and allowed windowed L50 — see `docs/LIVE-AGY.md`. That does **not** satisfy this PKG5 live tool-loop requirement.

## Residual

1. Re-auth Claude (`claude auth login` interactive / JB) then re-run the Claude prompt.  
2. After Antigravity quota reset (~48h from 2026-09-11T12:56Z), re-run the AGY `--print` prompt.  
3. Do not treat hook registration or schema dry-runs as E2E pass for PKG5.
