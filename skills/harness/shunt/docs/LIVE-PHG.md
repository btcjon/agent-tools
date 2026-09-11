# LIVE-PHG — Pi + Hermes + Grok live gate proof

**When:** 2026-09-11  
**Worker:** cursor-auto-4  
**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`  
**Fixture:** `tests/fixtures/live_harness_gate.txt` (400 lines; `SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT` @ L50; `SENTINEL_LINE_350_OUTSIDE_WINDOW` @ L350)  
**Secrets:** sourced `AI-Control-Plane/secrets/secrets.common.env` for optional provider calls; **keys never printed**.

## Verdict matrix

| Harness | Registration confirmed | Oversized deny | Windowed allow + L50 | Notes |
| --- | --- | --- | --- | --- |
| **Pi** | Yes — `~/.pi/agent/extensions/shunt-gate.ts` installed (copy) | **PASS** (live `pi -p`) | **PASS** (L50 present, L350 absent) | Fixed load/spawn bugs during this job |
| **Hermes** | Yes — `~/.hermes/config.yaml` `hooks.pre_tool_call` → `pre_tool_call.sh` | **PASS** (hook stdin) | **PASS** (`{}` allow) | Remote `hermes` CLI SSH to `mac-mini` **timed out**; live invoke = installed hook script |
| **Grok** | Yes — `~/.grok/hooks/shunt-pretooluse.json` | **PASS** (hook stdin) / **FAIL-OPEN live** | **PASS** (hook + live window) | Live `grok --single` full Read was **ALLOWED** (hook did not deny); stdin hook denies correctly |

---

## Pi

### Extension present

```text
~/.pi/agent/extensions/shunt-gate.ts
→ copied from adapters/pi/shunt-gate.ts (install.sh now copies; symlink + typebox import previously failed to load)
```

### Live session (`pi -p --no-session --tools read,bash`)

After fixing gate invocation (`spawn` + stdin; `--json-stdin` exit 0; unset `SHUNT_INTERNAL`):

```text
Call1=BLOCKED — "Oversized full-file read blocked by shunt."
Call2=ALLOWED — SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT present; SENTINEL_LINE_350 absent (window 40–60).
```

Transcript file: `/tmp/shunt-live-phg-*/pi_live_spawn.txt` (session scratch).

### Backend gate (same path the extension calls)

```bash
printf '%s' '{"toolName":"read","toolInput":{"path":"<fixture>"}}' \
  | python3 adapters/_common/native_read_gate.py --json-stdin
# → {"block": true, "reason": "oversized_full_read", ... "shunt bulk-read ..."}
```

### Bugs found + repaired in this job

1. Extension failed to load with `import { Type } from "typebox"` under Dropbox-symlink layout → removed runtime typebox; install **copies** the file.
2. Node `execFile(..., { input })` did not feed stdin to Python → switched to `spawn` + `stdin.write`.
3. Gate `--json-stdin` exited `2` on block → Node treated as crash → **fail-open**; now exits `0` with `block` in JSON.

---

## Hermes

### Config confirmation (`~/.hermes/config.yaml`)

```yaml
# --- shunt WP5 append-only (do not remove unrelated hooks) ---
hooks:
  pre_tool_call:
    - command: ".../adapters/hermes/pre_tool_call.sh"
      timeout: 10
      fail_closed: true
```

### Live invoke (hook path — MacBook)

Mac `hermes` wrapper SSHs to `mac-mini` (`100.99.248.72`); **ConnectTimeout** → unreachable this run. Exact local invoke of the configured hook:

```bash
# DENY
printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"<fixture>"}}' \
  | bash adapters/hermes/pre_tool_call.sh
# → {"action":"block","message":"Oversized full-file read blocked by shunt. Run: shunt bulk-read <fixture> ..."}

# ALLOW window
printf '%s' '{"tool_name":"Read","tool_input":{"file_path":"<fixture>","offset":40,"limit":21}}' \
  | bash adapters/hermes/pre_tool_call.sh
# → {}
```

Window content check (same bounds): L50 sentinel **present**, L350 **absent**.

---

## Grok

### Registration

`~/.grok/hooks/shunt-pretooluse.json` — `PreToolUse` matcher `Read|read_file|Bash|run_terminal_command|Shell` → `adapters/grok/pretooluse.sh`.

### Hook stdin proof (hard deny works)

```bash
printf '%s' '{"tool_name":"Read","tool_input":{"path":"<fixture>"}}' \
  | bash adapters/grok/pretooluse.sh
# → {"decision":"deny","reason":"... shunt bulk-read ..."}

printf '%s' '{"tool_name":"Read","tool_input":{"path":"<fixture>","offset":40,"limit":21}}' \
  | bash adapters/grok/pretooluse.sh
# → {"decision":"allow"}
```

### Live `grok --single` session

```text
Call1=ALLOWED (full read; no hook deny)
Call2=ALLOWED
SENTINEL_LINE_50: SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT
```

**Fail-open / non-firing recorded:** live session completed a full Read despite the installed PreToolUse hook. Startup logs showed Cursor hooks.json parse warnings and MCP failures; no shunt deny reason appeared. Grok docs state hooks fail open on crash/timeout; here the outcome matches **hook did not block** the live Read. Stdin proof still shows the script itself denies correctly — treat live Grok enforcement as **not yet proven** until a session surfaces the deny reason.

---

## Windowed L50 proof (shared)

```text
offset=40 limit=21 → lines 40–60
has_L50 True
has_L350 False
excerpt includes: SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT
```

---

## Commands summary

```bash
cd …/skills/harness/shunt
bash adapters/pi/install.sh
# Pi live:
pi -p --no-session --tools read,bash '… read fixture full then offset=40 limit=21 …'
# Hermes / Grok hook:
bash adapters/hermes/pre_tool_call.sh   # stdin JSON
bash adapters/grok/pretooluse.sh        # stdin JSON
# Grok live (observed fail-open on full read):
grok --single '…' --always-approve --cwd "$PWD"
```
