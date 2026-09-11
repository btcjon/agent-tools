# RECEIPT-WP2 — Installer + HARNESS-HOOKS + Phase 0 inventory

**Status:** done  
**Worker:** cursor-auto-3  
**Task:** `~/.local/share/herdr-workers/cursor-auto-3/tasks/wp2-install-policy.md`  
**Package:** `/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt`  
**When:** 2026-09-11

## Delivered

| Item | Result |
|------|--------|
| `shunt install` / `uninstall` / `doctor` | Real implementation in `src/shunt/install.py` + CLI wiring |
| `--dry-run` | Lists harness targets; no mutate |
| Backups | `~/.shunt/backups/<harness>/` before write |
| Preserve unrelated hooks | Verified: Claude `UserPromptSubmit` mia-recall kept |
| Adapter merge when present | Claude stub `adapters/claude/{registration.json,shunt-pretool.sh}` |
| Cursor/Codex full hooks | Not authored (WP3); install skips until scripts/registration exist |
| `HARNESS-HOOKS.md` | Amended with bounded shunt PreToolUse / read-gate allowance + doctor/parity |
| `docs/PHASE-0-INVENTORY.md` | Kill list with concrete evidence paths (no live CAPI disable) |
| OpenRouter live | Not implemented (WP1 out of scope) |

## Acceptance checks run

```text
pytest: 15 passed, 1 skipped
shunt install --dry-run  → lists targets; claude=[merge]
shunt install            → backup + merge PreToolUse; mia-recall preserved
shunt install (2nd)      → unchanged (idempotent)
shunt doctor             → claude shunt_registered=yes; policy shunt_allowed=True
shunt uninstall --dry-run → claude=[remove]
```

## Key files touched

- `src/shunt/install.py` (new)
- `src/shunt/cli.py` (install/uninstall/doctor)
- `tests/test_install.py` (new)
- `adapters/README.md`, `adapters/claude/*`
- `docs/PHASE-0-INVENTORY.md`
- `AI-Control-Plane/contracts/HARNESS-HOOKS.md`
- `RECEIPT-WP2.md` (this file)

## Live mutate note

`shunt install` updated `~/.claude/settings.json` and wrote backup  
`~/.shunt/backups/claude/settings.json.20260911T115609Z.bak`.  
Claude adapter is a no-op allow stub until WP3 replaces the script body.

## Kill-list reminder (inventory; not applied)

1. Pi `gemini-flash` → `google-gemini-cli/gemini-3-flash-preview`
2. Pi `worker-medium` → `capi/gemini-3-flash-preview`
3. VPS `gflash` alias
4. Mac Antigravity Flash alias
5. `cc-fallback` Gemini Flash targets  

Bulk-reader: OpenRouter `google/gemini-3.8-flash` only — never OAuth Flash fallback.
