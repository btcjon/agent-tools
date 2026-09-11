# Hermes adapter — **HARD**

Enforcement mode: **hard** via shell `pre_tool_call` hook (`action: block` / exit semantics; `fail_closed: true` recommended).

## What it does

- Runs before tool execution; JSON stdin from Hermes → shared `native_read_gate`.
- Blocks oversized full-file reads; message tells agent to run `shunt bulk-read <path>`.
- Preserves unrelated `hooks:` / plugins — install only *adds* a `pre_tool_call` entry.

## Install

```bash
chmod +x adapters/hermes/install.sh adapters/hermes/pre_tool_call.sh
./adapters/hermes/install.sh
# Merge printed YAML into ~/.hermes/config.yaml (does not auto-edit; consent/allowlist).
```

Approve the command via `hermes hooks` on first use (or `HERMES_ACCEPT_HOOKS=1` in CI).

## Honesty

| Claim | Reality |
| --- | --- |
| Hard block | Yes, when hook is configured + allowlisted |
| Auto-merge into config.yaml | No — snippet only (avoids clobbering live Hermes config) |
| Plugin `register_hook` alternative | Not shipped; shell hook is enough |

## Test

```bash
python3 -m pytest adapters/hermes/tests -q
```
