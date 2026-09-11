# Grok adapter — **HARD**

Enforcement mode: **hard** via Grok `PreToolUse` (documented deny / `decision: deny`).

Capability audit (this machine): `~/.grok/docs/user-guide/10-hooks.md` confirms `PreToolUse` can deny tool calls. Therefore this adapter is **not** advisory-only.

## What it does

- Matcher on `Read|read_file|Bash|run_terminal_command|Shell`.
- Shared gate blocks oversized full-file reads; scoped windows allowed.
- On deny, reason tells agent to run `shunt bulk-read <path>`.

## Install

```bash
chmod +x adapters/grok/install.sh adapters/grok/pretooluse.sh
./adapters/grok/install.sh
# writes ~/.grok/hooks/shunt-pretooluse.json and appends [[hooks.PreToolUse]] to config.toml
```

Restart Grok (or start a fresh `-p` session) so PreToolUse loads; `/hooks` to confirm. Stale panes keep the pre-install hook set.

## Honesty

| Claim | Reality |
| --- | --- |
| Hard PreToolUse deny | Yes |
| Fail-closed on hook crash | No — Grok fails open on malformed/timeout; only explicit deny blocks |
| Advisory fallback | Not used (PreToolUse available) |

## Test

```bash
python3 -m pytest adapters/grok/tests -q
```
