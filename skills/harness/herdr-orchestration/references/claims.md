# Claims

Cooperative reservations for Herdr panes. Not enforcement against manual terminal input.

## Rules

1. **Claim before any agent prompt.** Claim failure ⇒ choose another worker or wait; **zero prompts** on the failed pane.
2. Use a unique `owner` (conversation id + job uuid).
3. All cooperating coordinators share one host-local state directory on the Herdr **server host** (default `~/.local/state/herdr-mode`, or a shared `--state-dir`). Separate local files can permit duplicate dispatch.
4. Recheck live occupant and readiness **after** claiming.
5. Never prompt busy, blocked, or foreign-owned panes. Do not move claimed panes.
6. `release` requires the **matching** owner. Idempotent same-owner claim is allowed (`created: false`).
7. **No automatic expiry. No force-steal.** Recover abandoned claims only after verifying the prior job is inactive and resolving ownership.
8. Corrupt state ⇒ fail closed (`corrupt_state`).
9. MVP runtime: **macOS/Linux (POSIX `fcntl`)**. Windows requires a later tested adapter.

## CLI

```bash
python3 <skill>/scripts/claims.py claim   --session S --worker PANE --owner OWNER
python3 <skill>/scripts/claims.py status  --session S --worker PANE
python3 <skill>/scripts/claims.py release --session S --worker PANE --owner OWNER
```

Exit codes (non-zero on failure): `1` blank args, `2` corrupt state, `3` conflict, `4` owner mismatch, `5` internal.

## Multi-server note

If you run multiple Herdr servers, pick an explicit claims namespace per server (distinct `--state-dir` or session naming discipline) and document which coordinators share which namespace.
