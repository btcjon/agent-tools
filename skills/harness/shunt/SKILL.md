---
name: shunt
description: Route oversized file reads through the installed shunt CLI (OpenRouter google/gemini-3.8-flash points-only). Use when a file is large enough that a full main-model read is wasteful; do not use for small windows, secrets, or as a coding agent.
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

1. Prefer the installed CLI: `shunt doctor`, `shunt bulk-read PATH`, `shunt install --dry-run`.
2. Lives in **custom-skills**: `skills/harness/shunt/` (this package).
3. If CLI missing: `pip install -e` from this directory (see `references/install.md`).

## Locks

- Bulk-reader: OpenRouter **`google/gemini-3.8-flash`** only (pay-per-token).
- **Never** fall back to CAPI Gemini OAuth, `gflash*`, or rate-limited Google account Flash.
- Requires `OPENROUTER_API_KEY` for live calls (Phase 1+); Phase 0 stubs OK without network.
