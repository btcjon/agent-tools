# shunt

Cheap bulk-file reader that returns **points only** (bullets, names, paths, line ranges, coverage) so the main model decides and edits.

Canonical package: [`btcjon/custom-skills`](https://github.com/btcjon/custom-skills) → `skills/harness/shunt/`.

## Purpose

Large files waste main-model context when agents read them whole. `shunt` gates oversized native reads (hooks/extensions) and sends allowed bulk jobs through a locked cheap OpenRouter model that returns structured pointers—not rewrites.

## Install

```bash
cd skills/harness/shunt   # this package
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# OPENROUTER_API_KEY from secrets / .env (never commit)
shunt doctor
shunt install             # merge Cursor/Codex/Claude (+ sidecar targets)
bash adapters/pi/install.sh
bash adapters/grok/install.sh
bash adapters/agy/install.sh
# Hermes: merge adapters/hermes/hooks.snippet.yaml into ~/.hermes/config.yaml
# Then reload sessions — see docs/RELOAD-POLICY.md
```

Hermes skill link (optional):

```bash
ln -sfn "$(pwd)" ~/.hermes/skills/shunt
```

## OpenRouter lock (mandatory)

- Bulk-reader: **`google/gemini-3.8-flash`** via OpenRouter only (pay-per-token).
- **NEVER** fall back to CAPI Gemini OAuth, `gflash*`, or a rate-limited Google account Flash path.

## Commands

| Command | Behavior |
| --- | --- |
| `shunt bulk-read PATH` | Gate + live OpenRouter points (needs `OPENROUTER_API_KEY`) |
| `shunt check-read PATH` | Allow/deny agent full-read (hooks use this) |
| `shunt doctor` | Config, key presence, registration report |
| `shunt install [--dry-run]` | Backup + merge adapter hooks into harness configs |

## Docs

- `docs/ACCEPTANCE.md` / `docs/ACCEPTANCE-LIVE.md` — proof matrices  
- `docs/LIVE-*.md` — per-harness live evidence  
- `docs/HERDR.md`, `docs/RELOAD-POLICY.md` — worker inheritance / reload  
- `docs/PHASE-0-APPLIED.md` — OAuth Flash kill  

## Non-goals

- Not a general coding agent or second decision-maker.
- Does not install the Spotify Portal Claude Code plugin.
- Adapters are thin registrations over shared core—not N hand-forked policies.

## License

MIT
