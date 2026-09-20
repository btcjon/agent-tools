# Jev Skill Advisor

![Jev narrows a huge skill catalog to one verified recommendation](assets/hero.webp)

## Give the model the right skill—not the whole warehouse.

Jev Skill Advisor searches a canonical skill catalog and recommends the small, relevant slice a harness should consider for the current request.

That keeps giant catalogs out of the main model context while preserving the boundary that matters: **Jev advises; your harness decides and executes.**

## What you get

- **Less context drag:** scan large catalogs in bounded batches instead of injecting every skill.
- **One portable contract:** the same `suggest`, `read`, and `report_outcome` flow through Python, JSON stdin/stdout, or optional MCP.
- **Tamper-aware reads:** stable IDs and source/policy hashes bind a recommendation to the skill that was actually cataloged.
- **Local evidence:** outcomes live in host-local SQLite so advisory quality can be measured.
- **No authority leak:** Jev cannot run skills, grant permissions, or override an explicit user choice.

## How it works

1. **Catalog:** inventory `SKILL.md` files and record stable IDs plus source and policy hashes.
2. **Suggest:** rank only skills the calling harness says are available.
3. **Read and report:** permit the selected hash-bound read, then record the outcome for evaluation.

It is a metal detector for your skill library—not a robot with the keys to the vault.

## Try it

```bash
cd skills/discovery/jev-skill-advisor
uv run --with-editable . skill-advisor-catalog \
  --warehouse /path/to/skills/exported \
  --output /host/state/catalog-v1.json
uv run --with-editable . skill-advisor-admin \
  --config /path/to/profile.json init-db
uv run --with-editable . skill-advisor-service \
  --config /path/to/profile.json suggest < request.json
```

Start with [`TRY_IT.md`](TRY_IT.md). For harness wiring, read [`HARNESS_INTEGRATION.md`](HARNESS_INTEGRATION.md).

## Safe operating boundary

Keep credentials, catalogs, prompts, transcripts, skill bodies, and `advisor.sqlite3` outside Git and synchronized folders. Begin in shadow mode. Confidence is advice—not permission, proof, or correctness.

## Test it

```bash
uv run --with 'mcp>=2,<3' --with-editable . \
  python -m unittest discover -s tests
```
