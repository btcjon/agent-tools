# Operations

## State boundary

Put the profile under host configuration and use a state directory such as `~/.local/state/jev-skill-advisor`. Initialize `advisor.sqlite3` explicitly; normal service startup refuses a missing, unsupported, or incomplete database. The database uses rollback journaling, foreign keys, a one-second busy timeout, and transactional budget/read reservations. Never put the state directory or credential file under the synchronized control-plane root.

## Lifecycle

1. Create a reviewed catalog with stable IDs and content hashes.
2. Create a host profile from `examples/service-host.example.json`.
3. Run `skill-advisor-admin init-db` and `status`.
4. Start in `shadow` mode and test the JSON or MCP transport.
5. Use `migrate-legacy` once when upgrading from JSON/JSONL storage. It creates a timestamped backup and records source hashes so reruns are idempotent; changed previously imported files fail closed.
6. Run `prune` for expired receipts and cache rows. Pruning never restores consumed budgets.

Rollback by stopping the service, preserving the SQLite database and legacy backup, and restoring the prior package revision/profile. Do not delete runtime evidence during rollback.
