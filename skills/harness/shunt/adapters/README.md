# Harness adapters

Thin registrations that point each harness at the shared `shunt` core (`decide_agent_read` / `shunt check-read`).

| Harness | Status |
| --- | --- |
| `cursor` | Hard hooks: `preToolUse` (Read) + `beforeShellExecution` (cat/head/tail) — WP3 |
| `codex` | Hard Bash PreToolUse; Read advisory (Codex shell-only PreToolUse) — WP3 |
| `claude` | Hard PreToolUse Read + Bash — WP3 |
| `pi` / `hermes` / `grok` / `agy` | WP4 stubs |

Install merges `adapters/<harness>/registration.json` via `shunt install` without removing unrelated hooks.
