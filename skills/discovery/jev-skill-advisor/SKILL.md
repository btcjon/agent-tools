---
name: jev-skill-advisor
description: Configure, operate, test, or integrate the harness-neutral Jev service that selects relevant skills without loading the full catalog into the main model context.
---

# Jev skill advisor

Use this package when a harness needs bounded semantic skill selection over a canonical skill catalog.

Keep Jev advisory: explicit user selections remain authoritative, the harness enforces availability and permissions, and only the selected hash-bound skill may be read. Do not treat confidence as permission or correctness.

Use a host profile to point at the canonical warehouse and catalog. Keep credentials and `advisor.sqlite3` outside Git and synchronized folders. Run `skill-advisor-admin init-db` before starting the JSON CLI or MCP server. Use shadow mode until measured outcomes justify advisory injection.

Read [references/operations.md](references/operations.md) for installation, profiles, migration, and rollback. Read [references/skillranker-ideas.md](references/skillranker-ideas.md) only when planning future product improvements.
