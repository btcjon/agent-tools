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

## Observability

`skill-advisor-admin --config PROFILE status` reports consumed budgets, operation/receipt/read/outcome/cache counts, outcome labels, and the last operation timestamp. Provider totals and nearest-rank latency percentiles cover currently retained receipts and include their sample count; pruning changes that reporting window but never restores lifetime budgets. `doctor` adds current profile mode, harness, catalog hash, eligible/readable counts, and credential availability. Neither command emits prompts, skill bodies, credentials, or provider payloads.

Use receipt IDs and request/session IDs to correlate the harness's own event log. Keep quality judgments outside the advisor database unless they use the bounded outcome labels. Compare shadow suggestions with the skill actually chosen by the harness and independently judged task quality.

Rollback by stopping the service, preserving the SQLite database and legacy backup, and restoring the prior package revision/profile. Do not delete runtime evidence during rollback.

## Notion-backed library

Use `skill-library inspect` before `pull`. A successful pull creates an immutable, fully hashed snapshot and atomically changes `current.json`; a failed pull leaves the prior selection intact. Build each harness profile against the selected snapshot's absolute `catalog_root`, not a mutable Notion URL or synchronized working directory.

Package metadata in `skill-package.json` controls stable identity, aliases, entrypoint, invocation policy, required runtimes, and executable paths. Missing required runtimes make the package unavailable. Declared executable paths are validated as files inside the package before their executable bit is restored. Referenced resources stay beneath the verified package root and are opened progressively by the harness.

The Codex adapter writes content-free events to `~/.local/state/jev-skill-advisor/codex-adapter/events.jsonl`. Correlate session/turn, snapshot, profile/catalog/policy hashes, selected IDs, receipt, provider attempts, latency, fallback reason, and emitted-context hash. Prompt and skill bodies are deliberately absent.
