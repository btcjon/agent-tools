# Operations

## State boundary

Put the profile under host configuration and use a state directory such as `~/.local/state/jev-skill-advisor`. Initialize `advisor.sqlite3` explicitly; normal service startup refuses a missing, unsupported, or incomplete database. The database uses rollback journaling, foreign keys, a one-second busy timeout, and transactional budget/read reservations. Never put the state directory or credential file under the synchronized control-plane root.

## Lifecycle

1. Create a reviewed catalog with stable IDs and content hashes.
2. Create a host profile from `examples/service-host.example.json`.
3. Run `skill-advisor-admin init-db` and `status`.
4. Start in `shadow` mode and test the JSON or MCP transport.
5. Use `migrate-legacy` once when upgrading from JSON/JSONL storage. It creates a timestamped backup and records source hashes so reruns are idempotent; changed previously imported files fail closed.
6. Run `prune` for expired receipts and cache rows. Pruning never restores consumed prompt budgets or the historical provider-attempt total.

## Observability

`skill-advisor-admin --config PROFILE status` reports consumed budgets, operation/receipt/read/outcome/cache counts, outcome labels, and the last operation timestamp. `provider_attempts` is the historical total and does not stop later requests. `provider_attempt_limit` applies inside one request. A stop inside that window is `local_provider_attempt_budget`, which is a local budget error, not a provider failure. Provider totals and nearest-rank latency percentiles cover currently retained receipts and include their sample count; pruning changes that reporting window but never restores the historical provider total. `doctor` adds current profile mode, harness, catalog hash, eligible/readable counts, and credential availability. Neither command emits prompts, skill bodies, credentials, or provider payloads.

Use receipt IDs and request/session IDs to correlate the harness's own event log. Keep quality judgments outside the advisor database unless they use the bounded outcome labels. Compare shadow suggestions with the skill actually chosen by the harness and independently judged task quality.

Rollback by stopping the service and preserving the current SQLite database and backup. Restore only a package revision compatible with the database's schema and column order; a pre-v2 build rejects schema v2, and an early v2 build with positional `request_windows` inserts cannot write after the `prompts` column migration. Do not restore an older database over newer runtime evidence. If no compatible build is available, keep this version or prepare a reviewed, data-preserving migration before changing code.

## Notion-backed library

Use `skill-library inspect` before `pull`. A successful pull creates an immutable, fully hashed snapshot and atomically changes `current.json`; a failed pull leaves the prior selection intact. Build each harness profile against the selected snapshot's absolute `catalog_root`, not a mutable Notion URL or synchronized working directory.

Package metadata in `skill-package.json` controls stable identity, aliases, entrypoint, invocation policy, required runtimes, and executable paths. Missing required runtimes make the package unavailable. Declared executable paths are validated as files inside the package before their executable bit is restored. Referenced resources stay beneath the verified package root and are opened progressively by the harness.

The Codex adapter writes content-free events to `~/.local/state/jev-skill-advisor/codex-adapter/events.jsonl`. Correlate session/turn, snapshot, profile/catalog/policy hashes, selected IDs, receipt, provider attempts, latency, fallback reason, and emitted-context hash. Prompt and skill bodies are deliberately absent.

For a current cross-harness view, run `skill-advisor-usage summary --since 24 --remote-host dest --expect vmi3528421:hermes`. It reads Mac adapter logs and the remote Hermes log over BatchMode SSH when requested; it does not publish, import, or alter either log. Pass `--events PATH` for a separate exported source or `--expect HOST:HARNESS` to flag a silent source. A remote read failure appears in `remote_source_errors` rather than silently lowering the denominator. Legacy rows are only `legacy_selected`, `legacy_fallback`, or `legacy_no_selection`; they never count as confirmed delivery or clean abstention. Their `status_rates` are null. Older Codex fallback rows without timestamps are counted in `undated_legacy_rows_excluded`, not assigned invented dates.

The summary separates `delivered_direct`, `reported_followthrough_self_report` (`followed`, `partial`, `ignored`, `not_applicable`, or `unreported`), and `reviewed_benefit_human` (`better`, `same`, `worse`, `wrong_skill`). Delivery only confirms adapter context insertion. Cursor's command receipt is `selected_unconfirmed` until context delivery is observed. Hermes emits a receipt ID but its current hook often lacks a session ID, so `delivered_without_session_correlation` flags the gap. A supporting-file read is not proof that the skill was followed. No harness automatically writes follow-through labels; to record an explicitly observed report without saving task text, run `skill-advisor-usage label RECEIPT_ID followed --evidence self_report` or `... better --evidence human_review`. Do not guess missing labels. No automatic weekly review or Notion digest is scheduled.

Compare the selector that actually runs, not only `service.suggest`, with `skill-advisor-selector-eval --cases examples/live-selector-comparison-cases.json --report /path/to/report.json`. This calls the live `select_cli.select_task` path against a small synthetic labeled set and scores the same eligible snapshot through the lexical baseline. It fails if the Jev and lexical candidate counts differ. The report contains case IDs and selected IDs, not tasks or skill bodies. Provider failures, deadlines, and selector failures are distinct from correct abstentions. The six-case 2026-09-24 report in `references/live-selector-comparison-2026-09-24.json` is a selector-accuracy smoke test, not evidence of downstream benefit; expand with independently labeled real-task cases before judging a rollout win.
