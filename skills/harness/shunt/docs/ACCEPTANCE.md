# ACCEPTANCE — shunt fleet install (WP5)

**When:** 2026-09-11  
**Host:** this Mac  
**Worker:** cursor-auto-5  
**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`  
**Secrets:** sourced `AI-Control-Plane/secrets/secrets.common.env` (`OPENROUTER_API_KEY` present; value never printed). Re-sourced after JB key refresh (2026-09-11); key never printed.

## Verdict

**PASS — Accepted.** Fleet install completed; fixture denies + `check-read` + windowed allow + **live** OpenRouter bulk-read all proved. Gaps below are registration-path mismatches, not live-read failures.

### Pass/fail matrix

| Check | Result | Evidence |
| --- | --- | --- |
| pytest | **PASS** | 29 passed, 1 skipped |
| `shunt install` (+ WP4 installers / Hermes append) | **PASS** | exit 0; configs written; Hermes `hooks.pre_tool_call` parses |
| Fixture denies (cursor/codex/claude) | **PASS** | deny + `shunt bulk-read` in message |
| `check-read` oversized | **PASS** | `allow:false`, exit 2 |
| `check-read` windowed | **PASS** | `allow:true`, exit 0 |
| Live `shunt bulk-read` (initial WP5) | **PASS** | `ok:true`, `stub:false`, HTTP 200 (not 401) |
| Live `shunt bulk-read` after key refresh | **PASS** | re-sourced secrets; `ok:true`, `stub:false`, HTTP 200, 5 points, exit 0 |
| OpenRouter 401 / missing key | **N/A (did not fail)** | no 401 on either live call |

## Proof commands (excerpts)

### 1. Unit tests
```bash
cd …/skills/harness/shunt && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
# → 29 passed, 1 skipped
```

### 2. Install
```bash
shunt install --dry-run   # exit 0
shunt install             # exit 0; backups under ~/.shunt/backups/
bash adapters/pi/install.sh
bash adapters/grok/install.sh
bash adapters/agy/install.sh
# Hermes: append-only `hooks.pre_tool_call` into ~/.hermes/config.yaml (no prior hooks: key)
```

### 3. Fixture denies (cursor / codex / claude)
| Adapter | Command | Result |
| --- | --- | --- |
| Cursor | `pre_tool_use_read.py` + `read_oversized.json` | `permission: deny`, message includes `shunt bulk-read …`, exit 0 |
| Codex | `pre_tool_use.py` + `bash_cat_oversized.json` | `permissionDecision: deny`, exit 2 |
| Claude | `pre_tool_use.py` + `read_oversized.json` | `permissionDecision: deny`, exit 2 |

### 4. `shunt check-read`
```text
shunt check-read adapters/cursor/tests/fixtures/sample_oversized.txt
→ allow:false, reason:oversized_full_read, lines:400, exit 2

shunt check-read …/sample_oversized.txt --offset 1 --limit 20
→ allow:true, reason:windowed_read, exit 0
```

### 5. Live bulk-read (OpenRouter)

Prior WP5 live call already returned HTTP **200** (not 401). After JB refreshed `OPENROUTER_API_KEY`, re-sourced secrets and re-ran smoke:

```text
set -a && source …/secrets/secrets.common.env && set +a   # key never printed
SHUNT_LIVE_TEST=1 shunt bulk-read adapters/cursor/tests/fixtures/sample_oversized.txt
→ ok:true, stub:false, model:google/gemini-3.8-flash, detail:openrouter_ok,
  http_status:200, points: non-empty (5 bullets), content_hash prefix e65b0e3a5bcd, exit 0
```

No CAPI / gflash / Google OAuth fallback used. **No 401.**

### 6. Doctor
```text
shunt doctor
→ OPENROUTER_API_KEY=set; model locked google/gemini-3.8-flash;
  cursor/codex/claude/pi/hermes/grok/agy shunt_registered=yes
```

## Harness table

| Harness | Mode | Registration proved on this Mac | Proof | Gaps |
| --- | --- | --- | --- | --- |
| **Cursor** | **hard** | `~/.cursor/hooks.json` merge via `shunt install` | Fixture deny + check-read | — |
| **Codex** | **hard** (Bash cat/head/tail); Read tool **advisory**/unavailable for PreToolUse | `~/.codex/hooks.json` created | Fixture deny on Bash cat | Platform: no hard Read PreToolUse |
| **Claude** | **hard** | `~/.claude/settings.json` merge | Fixture deny | — |
| **Pi** | **hard** | Extension symlink `~/.pi/agent/extensions/shunt-gate.ts` (WP4 installer) | Fixture dry-run via shared gate (WP4 tests); extension present | `shunt install` also wrote `~/.pi/agent/hooks.json` pointing at `install.sh` (not a real hook) — **inactive/noise** for Pi PreToolUse JSON; real gate is the TS extension |
| **Hermes** | **hard** | Appended `hooks.pre_tool_call` in `~/.hermes/config.yaml` + sidecar `~/.hermes/shunt-hooks.json` | Hook script dry-run (WP4); YAML append present | Sidecar JSON is Cursor-shaped; Hermes loads **config.yaml** hooks. Sidecar alone would be inactive. Allowlist/consent may still prompt on first live fire |
| **Grok** | **hard** | WP4 `~/.grok/hooks/shunt-pretooluse.json` **and** `shunt install` `~/.grok/hooks.json` | WP4 PreToolUse dry-run | `hooks.json` from `shunt install` includes `install.sh` as a command (noise). Prefer the WP4 per-file hook |
| **AGY** | **hard** | WP4 merge into **`~/.gemini/config/hooks.json`** (group `shunt`; `herdr` preserved) | WP4 PreToolUse dry-run | `shunt install` wrote **`~/.agy/hooks.json`** — **inactive** on this host (real AGY config is `~/.gemini`) |
| **Herdr** | **inherit** | Host hooks only (`docs/HERDR.md`) | Documented | Flash workers need restart/drain after host install |

## Gaps / honesty

1. **`shunt install` default script discovery** for pi/hermes/grok/agy picks up `install.sh` as a hook command — wrong surface; WP4 installers are the authoritative hard paths for those harnesses.
2. **AGY path split:** live hard gate = `~/.gemini/config/hooks.json`; `~/.agy/hooks.json` is unused here.
3. **Hermes:** live hard gate = `config.yaml` `hooks.pre_tool_call`; `~/.hermes/shunt-hooks.json` is not the Hermes shell-hook loader.
4. **Codex Read:** still not hard-gateable on this platform (documented WP3).
5. Full end-to-end deny inside a live Cursor/Pi/Grok UI session was **not** re-driven in WP5; proof is fixture + CLI + live OpenRouter HTTP 200.

## Locks confirmed

- Bulk-reader: OpenRouter `google/gemini-3.8-flash` only  
- Never CAPI Gemini OAuth / `gflash*` / Google account Flash fallback  
- Secrets not printed; no git commit
