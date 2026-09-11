# AGY adapter — **HARD**

Enforcement mode: **hard** via Antigravity / AGY `PreToolUse` (`decision: deny`).

Capability audit: AGY CLI lives under `~/.gemini` (`agy` binary); hooks at `~/.gemini/config/hooks.json` with `PreToolUse` documented in `antigravity-cli/.../docs/hooks.md`.

## What it does

- Named hook group `"shunt"` merged into `hooks.json` without removing other groups (e.g. `herdr`).
- Blocks oversized full-file reads; allows scoped windows / small files.
- Deny reason points at `shunt bulk-read <path>`.

## Install

```bash
chmod +x adapters/agy/install.sh adapters/agy/pretooluse.sh
./adapters/agy/install.sh
# merge-safe update of ~/.gemini/config/hooks.json
```

Restart AGY / antigravity session to pick up hooks.

## Honesty

| Claim | Reality |
| --- | --- |
| Hard deny | Yes, when PreToolUse fires |
| Path alias `~/.agy` | Not present on this host; real config is `~/.gemini` |
| Advisory | Not used |

## Test

```bash
python3 -m pytest adapters/agy/tests -q
```
