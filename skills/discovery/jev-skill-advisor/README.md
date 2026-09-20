# Jev Skill Advisor

A harness-neutral, advisory skill selector powered by TypeSafe Jev. It exposes the same `suggest`, `read`, and `report_outcome` contract through Python, JSON stdin/stdout, and optional stdio MCP.

The package does not execute skills, grant permissions, or automatically inject advice. Explicit skill requests bypass Jev. Runtime state is stored in a host-local SQLite database; credentials, catalogs, prompts, transcripts, and skill bodies are not committed.

```bash
uv run --with-editable . skill-advisor-catalog --warehouse /path/to/skills/exported --output /host/state/catalog-v1.json
uv run --with-editable . skill-library --config /host/state/notion-pilot.json status
uv run --with-editable . skill-advisor-admin --config /path/to/profile.json init-db
uv run --with-editable . skill-advisor-service --config /path/to/profile.json suggest < request.json
uv run --with 'mcp>=2,<3' --with-editable . skill-advisor-mcp --config /path/to/profile.json
uv run --with 'mcp>=2,<3' --with-editable . python -m unittest discover -s tests
```

The catalog builder inventories every `SKILL.md`, records stable IDs and source/policy hashes, and explicitly reports excluded skills. Repeated builds over unchanged input are deterministic. The request protocol accepts up to 1,024 available IDs and the scanner evaluates large catalogs in bounded batches.

Agents and operators should begin with [`TRY_IT.md`](TRY_IT.md) and [`HARNESS_INTEGRATION.md`](HARNESS_INTEGRATION.md), then consult `SKILL.md`, `examples/service-host.example.json`, and `schemas/service-v1.json`.

For the read-only Notion Agent Skills snapshot pilot, follow [`references/notion-pilot.md`](references/notion-pilot.md). It does not change the canonical warehouse or harness registrations.
