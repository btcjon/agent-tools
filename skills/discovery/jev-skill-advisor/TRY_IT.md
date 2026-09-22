# Try it safely

The live command, once a snapshot release is active, is:

```bash
skill-search --select --task "the task in one sentence" --json
```

That returns one verified skill or `selected: null`. The steps below prove the older shadow transport and logging. They do not enable automatic skill injection. Wiring for Codex, Hermes, Cursor, and Pi is in the [how-to](guide/how-to.md) and [harness integration](references/harness-integration.md).

1. Install from this directory: `uv sync --extra mcp`.
2. Build a host-local catalog, then copy `examples/service-host.example.json` to a host-local configuration path. Point it at that catalog and the same canonical warehouse. Replace `warehouse:example` with a real stable ID in both the profile and sample request. Keep `mode` as `shadow`, `read_enabled` false, and `read_allowlist` empty.

   ```bash
   uv run skill-advisor-catalog --warehouse /path/to/skills/exported --output /host/state/catalog-v1.json
   ```

   Review every reported exclusion. An unchanged warehouse must produce the same catalog hash on a repeat build.
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
