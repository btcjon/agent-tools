# Codex adapter

## Hard hooks (available)

Codex `hooks` feature is **stable** on this machine (`codex features list` → `hooks stable true`).

**Limitation (upstream):** Codex PreToolUse currently runs for **shell/Bash only**, not the dedicated Read tool
(see migrate-to-codex `differences.md`). Therefore:

| Coverage | Mode |
| --- | --- |
| `Bash` / shell `cat`, `head`, `tail` of oversized files | **Hard deny** via `PreToolUse` |
| Codex `Read` / `apply_patch` file opens | **Advisory only** — no PreToolUse for Read today |

Script: `pre_tool_use.py`  
Registration: `registration.json` → merge into `~/.codex/hooks.json` (create if missing).

## Advisory (Read tool)

Until Codex exposes Read PreToolUse, agents should prefer:

```bash
shunt bulk-read <path>
```

for files ≥ ~350 lines instead of full reads. The CLI gate still works:

```bash
shunt check-read <path>
```

Status when hard Read hooks are unavailable: **advisory documented; shell hard-gate exits 0 on allow / 2 on deny**.

## Install

```bash
shunt install --dry-run
shunt install   # merges PreToolUse Bash matcher; preserves unrelated hooks
```

## Proof

```bash
python3 adapters/run_fixture.py \
  adapters/codex/pre_tool_use.py \
  adapters/codex/tests/fixtures/bash_cat_oversized.json
# expect: permissionDecision deny + stderr message with shunt bulk-read; exit 2
```
