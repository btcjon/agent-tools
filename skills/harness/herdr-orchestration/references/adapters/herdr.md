# Herdr adapter (CLI integration)

Depends on the stock **`herdr`** skill / product CLI. This file is minimal integration guidance—not a full CLI manual.

## Discovery

```bash
herdr session list
herdr --session <name> pane list
```

Parse live pane ids and labels. Never hardcode ids from docs or prior jobs.

## Claims on the server host

Run [claims.py](../../scripts/claims.py) where the Herdr **server** process lives so all coordinators share one namespace. Remote coordinators must use the host procedure to execute claims there—not a separate laptop-local state file.

## Fresh process + model verification

1. Claim pane.
2. Ensure idle/done; clear idle agent to shell if needed; verify foreground shell.
3. Start agent with your configured kind/model flags (**your** permission policy).
4. Read startup banner; require expected product + model + effort.
5. Prompt with a **short pointer** to the task file only.

Privileged approval/sandbox bypass flags are **operator configuration**, not part of this package’s default install. Do not copy another site’s bypass flags blindly.

## Safe defaults

- Prefer normal permission modes for examples and first-time setup.
- Keep runtime state (claims, receipts, transcripts) **outside** this git repository.
- Do not launch workers merely to prove installation.

## Stock skill boundary

| Need | Skill |
|---|---|
| `herdr` subcommands, panes, agent start | stock `herdr` |
| Profiles, gates, claims policy, closing alignment | `herdr-orchestration` (this package) |
