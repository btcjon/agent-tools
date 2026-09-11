# Architecture

## Layers

1. **Core (`src/shunt`)** — gate heuristics, OpenRouter bulk-read client, CLI. One policy surface.
2. **Adapters (`adapters/<harness>`)** — thin generated registrations (hooks / skill pointers). No forked rules.
3. **Warehouse skill (`AI-Control-Plane/skills/exported/shunt`)** — concise router: when to invoke the installed CLI; not a full dump of the warehouse.

## Data flow

```
agent wants large file
  → gate(path, size, offset/limit)
  → allow? OpenRouter google/gemini-3.8-flash (points only)
  → main model uses points to decide/edit
```

## Locks

- OpenRouter pay-per-token only; model id fixed in config (`google/gemini-3.8-flash`).
- Never CAPI Gemini OAuth / `gflash*` / Google account Flash fallback.
- `SHUNT_INTERNAL=1` skips network (recursion guard during a live call).
- Failures return `guidance` for bounded Read offset/limit — not an alternate provider.
