# Try it safely

These steps prove transport and logging in shadow mode. They do not enable automatic skill injection.

1. Install from this directory: `uv sync --extra mcp`.
2. Copy `examples/service-host.example.json` to a host-local configuration path. Point it at a reviewed catalog and canonical warehouse. Replace `warehouse:example` with a real stable ID in both the profile and the sample request below. Keep `mode` as `shadow`, `read_enabled` false, and `read_allowlist` empty.
3. Export `TYPESAFE_API_KEY` without placing it in the repository.
4. Initialize and inspect host-local state:

   ```bash
   uv run skill-advisor-admin --config /path/to/profile.json init-db
   uv run skill-advisor-admin --config /path/to/profile.json doctor
   ```

5. Make one real Jev shadow request (this contacts TypeSafe):

   ```bash
   printf '%s\n' '{"protocol_version":1,"request_id":"manual-1","session_id":"manual","task":"Choose the documented procedure for diagnosing a failed MCP connection","available_ids":["warehouse:example"]}' |
     uv run skill-advisor-service --config /path/to/profile.json suggest
   ```

6. Repeat the identical request with a new `request_id` to exercise the response cache. Then inspect aggregate evidence:

   ```bash
   uv run skill-advisor-admin --config /path/to/profile.json status
   ```

Expected safe behavior: explicit selections use zero provider calls; protected input uses zero provider calls; none/uncertain/incomplete loads nothing; shadow suggestions produce receipts and telemetry but do not alter agent behavior. Stop on scope leaks, stale hashes, repeated failures, or exhausted budgets. Roll back by stopping the adapter and leaving its database intact.

For MCP, start `uv run skill-advisor-mcp --config /path/to/profile.json` and register it as a stdio server exposing exactly `skill_suggest`, `skill_read`, and `skill_report_outcome`. Persistent harness registration should use the absolute package directory and executable path rather than assuming its working directory or `PATH`.
