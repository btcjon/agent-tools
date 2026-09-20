---
name: shunt
description: Route oversized file reads through the installed shunt CLI (direct Z.ai Coding Plan glm-5.3, with Cerebras gpt-oss-120b fallback). Use when a file is large enough that a full main-model read is wasteful; do not use for small windows, secrets, or as a coding agent.
---

# shunt

## When to use

- File is large (roughly ≥350 lines / configured gate) and you need **orientation points** (names, paths, line ranges, coverage bullets)—not a full rewrite.
- You would otherwise dump a huge file into the main model context.

## When not to use

- Small files, or reads already scoped with offset/limit under the gate threshold.
- Secrets / `.env` / credential paths (gate blocks these).
- As a substitute decision-maker or editor—the **main model** still decides and edits.
- Do not install Spotify Portal Claude Code plugin via this skill.

## How

1. Prefer the installed CLI: `~/.local/bin/shunt doctor`, `~/.local/bin/shunt bulk-read PATH`, `~/.local/bin/shunt install --dry-run`.
2. Lives in **agent-tools**: `skills/harness/shunt/` (this package).
3. If CLI missing: `pip install -e` from this directory (see `references/install.md`).

## Required recovery after a block

- Immediately call the harness-native `shunt_bulk_read` tool when available; otherwise run `~/.local/bin/shunt bulk-read PATH`.
- Do not evade the gate with Python, `sed`, `awk`, or another command that dumps the whole file.
- A normal bounded read is appropriate only when the exact relevant range is already known.
- Operational outcomes are appended without content or clear file paths to `~/.shunt/usage.jsonl`.

## Locks

- Primary: direct Z.ai Coding Plan **`glm-5.3`** using `ZAI_API`.
- Fallback: Cerebras **`gpt-oss-120b`** using `CEREBRAS_API_KEY`.
- **Never** use a third provider or model.
- Requires `ZAI_API` for primary or `CEREBRAS_API_KEY` for fallback live calls.
