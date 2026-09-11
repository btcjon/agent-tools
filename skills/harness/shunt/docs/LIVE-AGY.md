# LIVE-AGY — Live harness gate proof (Gemini Antigravity)

**When:** 2026-09-11  
**Worker:** cursor-auto-5  
**Fixture:** `tests/fixtures/live_harness_gate.txt` (400 lines; L50 = `SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT`)  
**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`

## Verdict

| Check | Result |
| --- | --- |
| Authoritative hooks contain `shunt` (`~/.gemini/config/hooks.json`) | **PASS** |
| Real registered PreToolUse script — oversized **deny** | **PASS** |
| Real registered PreToolUse script — windowed **allow** + L50 sentinel readable | **PASS** |
| Full `agy --print` tool-using session completing both tool steps | **BLOCKED** (quota 429) — residual risk noted |

## 1. Authoritative hooks path

```text
~/.gemini/config/hooks.json keys: ['herdr', 'shunt']
shunt.PreToolUse matcher: read_file|Read|view_file|run_command|Bash|Shell
command: …/adapters/agy/pretooluse.sh
```

`~/.agy/hooks.json` also exists (from earlier `shunt install`) but is **not** the AGY loader on this host. Live CLI log:

```text
hooks_manager.go: loaded 2 named hooks from 1 hooks.json file(s)
```

(that file is under `~/.gemini/config/` — herdr + shunt).

## 2. PreToolUse proofs (AGY JSON schema → registered script)

Invoked the **exact** `command` from `~/.gemini/config/hooks.json` with AGY stdin shape `{ "toolCall": { "name", "args" }, "stepIdx" }`.

### Oversized full read → deny
```bash
# view_file / read_file / run_command cat — all deny
{"decision":"deny","reason":"Oversized full-file read blocked by shunt. Run: shunt bulk-read …/live_harness_gate.txt …"}
```

### Windowed read covering L50 → allow
```bash
# view_file path + offset=45 limit=15
{"decision":"allow"}
```

After allow, native window contains sentinel:
```text
SENTINEL_LINE_50_UNIQUE_TOKEN_SHUNT
```

## 3. Live `agy --print` session attempt

```bash
agy --print-timeout=90s --dangerously-skip-permissions --add-dir=<package> \
  --log-file=/tmp/agy-live-shunt-wp.log \
  --print='… full read then windowed L50 sentinel …'
```

Observed:
- Auth succeeded (silent keyring).
- Hooks loaded (2 named groups).
- Model stream hit **`RESOURCE_EXHAUSTED` (429)** — Individual quota reached; retries until print timeout (~90s).
- **No tool call / PreToolUse fire** in that turn (quota before tools).
- Conversation id: `92c4f0e0-d0d9-4090-8f53-760c8381e819` (partial).

## Residual risk

Hook registration and AGY-schema deny/allow are proved against the live registered script. An end-to-end agent tool loop inside AGY was **not** completed here because of Antigravity quota exhaustion. Re-run the same `agy --print` prompt after quota resets to close that residual.

## Honesty

- Mode remains **hard** when PreToolUse fires (`decision: deny`).
- Prefer `~/.gemini/config/hooks.json`; do not treat `~/.agy/hooks.json` as authoritative on this Mac.
