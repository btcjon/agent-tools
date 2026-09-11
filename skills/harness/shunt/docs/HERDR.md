# Herdr + shunt

## Inheritance

Herdr **workers inherit the host harness hooks**. There is no second shunt implementation inside Herdr.

| Host | Shunt surface workers inherit |
| --- | --- |
| Cursor / Codex / Claude | WP3 adapters (when installed on host) |
| Pi | `~/.pi/agent/extensions/shunt-gate.ts` |
| Hermes | `hooks.pre_tool_call` in host `config.yaml` |
| Grok | `~/.grok/hooks/shunt-pretooluse.json` |
| AGY | `~/.gemini/config/hooks.json` group `shunt` |

Do **not** ship a parallel Herdr-only gate. Policy stays in the shared `shunt` core + thin adapters.

## Flash / drain note

Flash-tier Herdr workers that already have a session open **do not** pick up new host hooks until restart or drain:

1. Install / symlink adapters on the host.
2. Drain or restart Flash workers (and any long-lived Pi/Hermes/Grok/AGY sessions).
3. Confirm with a fixture oversized read — expect deny + `shunt bulk-read` guidance.

See **`docs/RELOAD-POLICY.md`** for exact reload commands and  
`scripts/herdr-shunt-reload-hint.sh` (dry-run list of candidate panes; never interrupts busy/foreign panes).

Bulk-read itself remains OpenRouter `google/gemini-3.8-flash` points-only (never CAPI / `gflash*` / Google OAuth Flash).
