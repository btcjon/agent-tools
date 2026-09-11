# RECEIPT-WP4 — Adapters: Pi, Hermes, Grok, AGY + Herdr note

**Job:** WP4 adapters (rest)  
**When:** 2026-09-11  
**Worker:** cursor-auto-5 (direct; no delegation)  
**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt/`

## Capability matrix

| Harness | Surface found | Enforcement | Notes |
| --- | --- | --- | --- |
| **Pi** | `tool_call` extension API (`ExtensionAPI.on("tool_call")` can `{block:true}`) + `registerTool` | **hard** | `adapters/pi/shunt-gate.ts`; also registers `shunt_bulk_read`. Installed symlink `~/.pi/agent/extensions/shunt-gate.ts`. Exotic bash pipelines can bypass cat parsing. |
| **Hermes** | Shell `hooks.pre_tool_call` (block / fail_closed) | **hard** | Snippet + `pre_tool_call.sh`; install prints merge YAML (does **not** auto-edit live `config.yaml` to avoid clobber/allowlist). |
| **Grok** | `PreToolUse` deny (docs confirmed) | **hard** | Not advisory — PreToolUse available. `~/.grok/hooks/shunt-pretooluse.json`. Grok fails open on hook crash/timeout. |
| **AGY** | `PreToolUse` deny via `~/.gemini/config/hooks.json` | **hard** | Host path is `~/.gemini` (not `~/.agy`). Merge-safe `"shunt"` group; preserves `herdr`. |
| **Herdr** | inherits host | **n/a (inherit)** | `docs/HERDR.md` — no second shunt; restart/drain Flash workers after host install. |
| Cursor / Codex / Claude | — | **untouched** | WP3 only (per brief). |

No adapter marked **blocked** (all four have a usable hard gate). None left **advisory** after audit.

## Artifacts

- `adapters/_common/native_read_gate.py` — shared oversized-native-read policy  
- `adapters/_common/shunt-hook.sh` — format adapter (hermes / grok / agy / pi-json)  
- `adapters/pi/{shunt-gate.ts,install.sh,README.md,tests/}`  
- `adapters/hermes/{pre_tool_call.sh,hooks.snippet.yaml,install.sh,README.md,tests/}`  
- `adapters/grok/{pretooluse.sh,hooks.json,install.sh,README.md,tests/}`  
- `adapters/agy/{pretooluse.sh,hooks.snippet.json,install.sh,README.md,tests/}`  
- `docs/HERDR.md`  
- Updated `adapters/README.md`

## Checks

```text
.venv/bin/pytest -q adapters/_common adapters/pi/tests adapters/hermes/tests adapters/grok/tests adapters/agy/tests tests/test_gate.py
→ 25 passed
```

## Intentionally not done

- Did not edit Cursor/Codex/Claude adapters, `bulk_read.py` live HTTP, or `HARNESS-HOOKS.md`.
- Hermes config.yaml not auto-mutated (consent/allowlist); operator merges snippet.

## Blocker

None.
