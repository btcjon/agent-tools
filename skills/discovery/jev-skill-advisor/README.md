# Jev Skill Advisor

A harness-neutral, advisory skill selector powered by TypeSafe Jev. It exposes the same `suggest`, `read`, and `report_outcome` contract through Python, JSON stdin/stdout, and optional stdio MCP.

The package does not execute skills, grant permissions, or automatically inject advice. Explicit skill requests bypass Jev. Runtime state is stored in a host-local SQLite database; credentials, catalogs, prompts, transcripts, and skill bodies are not committed.

```bash
uv run --with-editable . skill-advisor-admin --config /path/to/profile.json init-db
uv run --with-editable . skill-advisor-service --config /path/to/profile.json suggest < request.json
uv run --with 'mcp>=2,<3' --with-editable . skill-advisor-mcp --config /path/to/profile.json
uv run --with 'mcp>=2,<3' --with-editable . python -m unittest discover -s tests
```

See `SKILL.md`, `examples/service-host.example.json`, and `schemas/service-v1.json`.
