# Pi adapter — **HARD**

Enforcement mode: **hard** via Pi `tool_call` extension (can `{ block: true, reason }`).

## What it does

- Intercepts `read` (full-file, no offset/limit) and `bash` (`cat` of large files).
- Blocks when file ≥ 350 lines (or ≥ 512 KiB) and tells the model to run `shunt bulk-read <path>` or the registered `shunt_bulk_read` tool.
- Scoped reads (`offset`/`limit`, `head`/`tail -n`) are allowed.

## Install

```bash
chmod +x adapters/pi/install.sh
./adapters/pi/install.sh
# → copies adapters/pi/shunt-gate.ts → ~/.pi/agent/extensions/shunt-gate.ts
# → writes ~/.pi/agent/extensions/shunt-root.txt (package root for gate discovery)
```

Then `/reload` or restart Pi (**required** for long-lived panes). Does not mutate Cursor/Codex/Claude configs.

**Note:** Install uses a file **copy** (not symlink). Dropbox-symlink layouts previously broke jiti module resolution for `typebox` imports; the extension now avoids runtime typebox and calls the Python gate via `spawn` + stdin (Node `execFile` `input` did not deliver stdin reliably here).

## Honesty

| Claim | Reality |
| --- | --- |
| Hard block of oversized native reads | Yes, when extension is loaded |
| Blocks every shell path to file contents | No — only patterns parsed as `cat`/`head`/`tail`; exotic pipelines can bypass |
| Live OpenRouter in `shunt_bulk_read` | Depends on WP1 `bulk_read` (may still be stub) |

## Test

```bash
python3 -m pytest adapters/pi/tests -q
```
