# Notion read-only pilot

Use this pilot to prove Notion Agent Skills export fidelity before changing any harness or canonical authority.

Create a host-local JSON configuration containing an absolute `state_dir` and one to five explicitly approved, non-sensitive exports that contain three to five total skills:

```json
{
  "state_dir": "/absolute/host-local/state/notion-library",
  "allowlist": [
    {"kind": "skill", "id": "notion-page-id-1"},
    {"kind": "skill", "id": "notion-page-id-2"},
    {"kind": "skill", "id": "notion-page-id-3"}
  ]
}
```

Run `skill-library --config /path/to/config.json inspect` before `pull`. Neither command prints or stores signed URLs or credentials. `pull` validates archives and atomically publishes an immutable snapshot. `status` works offline. `rollback SNAPSHOT_ID` selects an older verified snapshot.

Generate the Jev catalog from the `catalog_root` returned by `status`. Do not execute imported files, automatically publish them into the canonical warehouse, interpret a missing export as deletion, or enable harness injection during this pilot.

Stop on an unexpected identity/version, unsafe archive, missing `SKILL.md`, partial fetch, attachment mismatch, or unavailable prior snapshot. Preserve the last verified snapshot for offline operation and recovery.

## Approval-gated publication

`skill-library-publish` prepares and verifies a reviewed pilot manifest. Its `plan` command is strictly local: it rereads all canonical `SKILL.md` files, checks source and transformed-content hashes, validates the typed Notion Skills database request, and prints a deterministic `plan_hash`.

```bash
skill-library-publish --manifest /absolute/pilot-manifest.json --state-dir /absolute/host-state plan
```

Publication is deliberately dormant until the user explicitly authorizes the exact plan. `apply` requires all three of these independent conditions:

1. the literal `--apply` switch;
2. `--ack-plan-hash` matching the freshly validated plan; and
3. a host-local authorization record containing `authorized: true`, the plan hash, authorizer and timestamp, exact parent/database, ordered skill IDs, and sole plugin tag.

The authorization record documents consent; the CLI never creates it. On an approved run, the publisher uses Notion's `database_type: skills` schema, verifies the returned data source before creating pages, and atomically checkpoints every returned ID. A failed or ambiguous mutation stops with `stopped_requires_reconciliation`; rerunning `apply` is prohibited until a read-only reconciliation resolves the receipt.

After creation, run `verify --cache-root /absolute/notion-library`. Verification reads the typed schema, every page property and Markdown body, and the uniquely tagged Agent Skills plugin back from Notion. Only then does it download the export through the hardened importer and atomically publish the verified local snapshot. Any mismatch leaves the previously selected cache snapshot unchanged.

This pilot does not change the canonical warehouse, install imported skills into a harness, execute exported MCP configuration, retire symlinks, or grant broader Notion access.
