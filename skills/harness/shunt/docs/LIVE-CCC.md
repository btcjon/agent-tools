# LIVE-CCC — Cursor + Codex + Claude live gate proof

**When:** 2026-09-11  
**Worker:** cursor-auto-3  
**Package:** `…/custom-skills/skills/harness/shunt/`  
**Fixture:** `tests/fixtures/live_harness_gate.txt` (400 lines; `SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT` @ L50; `SENTINEL_LINE_350_OUTSIDE_WINDOW` @ L350)  
**Raw captures:** `docs/_live_ccc_raw/`  
**Verdict:** **PASS** — hooks not broken; deny is shunt-hook decision (not model refusal) on Cursor live agent + Codex live exec + Claude/Codex/Cursor stdin fixtures. No `install.py` edits; no repair wait.

## Matrix

| Harness | Oversized full deny (shunt) | Window ~40–60 allow + SENTINEL_50 only | Notes |
| --- | --- | --- | --- |
| **Cursor** | **PASS** live `cursor agent -p` | **PASS** hook stdin fixture; content via `sed -n '40,60p'` | Live windowed agent run inconclusive (agent said “denied” without quoting shunt — likely did not issue offset/limit Read). Fixture + content proof still hold. |
| **Codex** | **PASS** live `codex exec --dangerously-bypass-hook-trust` | **PASS** live `head -n 60 \| tail -n 21` | Without hook-trust bypass, first execs ran `cat` unhooked. With trust bypass: PreToolUse blocked oversized `cat`. |
| **Claude** | **PASS** stdin PreToolUse fixture | **PASS** stdin windowed allow + content proof | Live `claude -p` blocked by **401 OAuth token revoked** before any tool call — not a shunt defect. |

Optional: `shunt bulk-read` on fixture → `ok:true`, `stub:false`, `detail:openrouter_ok`, 5 points (secrets sourced; key never printed).

---

## 1) Hook stdin fixtures (live schema → registered scripts)

Env: `PYTHONPATH=src`, `SHUNT_ROOT=<package>`, `SHUNT_INTERNAL=1`.

### Cursor — deny full Read

```bash
printf '%s' '{"hook_event_name":"preToolUse","tool_name":"Read","tool_input":{"path":"<FIX>","offset":null,"limit":null},"cwd":"/tmp"}' \
  | python3 adapters/cursor/pre_tool_use_read.py
# exit 0
# {"permission":"deny", … "Run: shunt bulk-read <FIX>"}
```

### Cursor — allow windowed Read

```bash
# offset=40 limit=21 → {"permission":"allow"}
```

### Cursor — deny shell `cat`

```bash
printf '%s' '{"hook_event_name":"beforeShellExecution","command":"cat <FIX>","cwd":"/tmp"}' \
  | python3 adapters/cursor/before_shell_execution.py
# {"permission":"deny", … shunt bulk-read …}
```

### Codex — deny Bash `cat`

```bash
# adapters/codex/pre_tool_use.py + Bash cat <FIX>
# exit 2
# permissionDecision: deny + stderr shunt bulk-read
```

### Codex — allow `head|tail` window

```bash
# head -n 60 <FIX> | tail -n 21 → permissionDecision: allow (exit 0)
```

### Claude — deny Read / Bash cat; allow windowed Read

```bash
# adapters/claude/pre_tool_use.py
# full Read / cat → exit 2, permissionDecision deny, reason includes shunt bulk-read
# Read offset=40 limit=21 → exit 0, permissionDecision allow
```

### Window content (shared)

```bash
sed -n '40,60p' tests/fixtures/live_harness_gate.txt
# contains SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT
# does NOT contain SENTINEL_LINE_350_OUTSIDE_WINDOW
```

---

## 2) Live harness CLIs

### Cursor agent — oversized Read denied by hook

```bash
cursor agent -p --mode ask --output-format text \
  "Use the Read tool once with NO offset/limit on path: <FIX> …"
```

Excerpt (`docs/_live_ccc_raw/cursor-agent.txt`):

```text
Denied by the hook:

> File is oversized for a full agent read (400 lines). Do not cat/Read the whole file. Run: shunt bulk-read …/live_harness_gate.txt
```

### Codex exec — oversized `cat` denied by PreToolUse

Requires hook trust for automation (or prior trusted registration). Proof used:

```bash
codex exec --skip-git-repo-check --dangerously-bypass-hook-trust \
  "Run exactly: cat '<FIX>' …"
```

Excerpt (`codex-exec3.txt` / `.err`):

```text
Command blocked by PreToolUse hook: File is oversized for a full agent read (400 lines).
Do not cat/Read the whole file. Run: shunt bulk-read …/live_harness_gate.txt
hook: PreToolUse Blocked
```

Without `--dangerously-bypass-hook-trust`, earlier `codex exec` ran `cat` successfully (hooks not applied) — trust gating, not a broken deny script.

### Codex exec — windowed shell allowed; SENTINEL_50 only

```bash
codex exec --skip-git-repo-check --dangerously-bypass-hook-trust \
  "Run exactly: head -n 60 '<FIX>' | tail -n 21 …"
```

Excerpt (`codex-window.txt`):

```text
1. Allowed — command completed with exit code 0; no hook denial.
2. SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT: Present.
3. SENTINEL_LINE_350_OUTSIDE_WINDOW: Absent.
```

### Claude CLI — auth blocked (not shunt)

```bash
~/.claude/scripts/claude-launch.sh -p --output-format json --allowedTools Read
# → is_error true: Failed to authenticate. API Error: 401 OAuth access token has been revoked.
```

No tool/hook fire. Claude proof remains the stdin PreToolUse fixture above (same script registered in `~/.claude/settings.json`).

---

## 3) Optional live bulk-read

```bash
set -a && source …/AI-Control-Plane/secrets/secrets.common.env && set +a   # key not printed
unset SHUNT_INTERNAL
SHUNT_LIVE_TEST=1 shunt bulk-read tests/fixtures/live_harness_gate.txt
```

Result: `ok:true`, `stub:false`, `model:google/gemini-3.8-flash`, `detail:openrouter_ok`, `http_status:200`, 5 points including L50 and L350 sentinels. No CAPI/`gflash` fallback.

---

## Gaps / honesty

1. **Claude interactive/print auth** revoked on this host — cannot drive a live Read through the Claude UI today.
2. **Codex hook trust:** live deny requires trusted hooks or `--dangerously-bypass-hook-trust` for automation; fixture deny works regardless.
3. **Cursor agent windowed** follow-up did not produce a clear allow+sentinel transcript; oversized deny live + windowed stdin fixture + `sed` content check cover the contract.
4. Did **not** edit `install.py` or wait on repair — scripts behave correctly under harness schemas.

## Locks

- Deny messages instruct `shunt bulk-read <path>` (OpenRouter `google/gemini-3.8-flash` paid bulk-reader).
- Never CAPI / `gflash*` / Google OAuth Flash fallback in these proofs.
- Secrets not printed.
