# Notion Agent Skills bulk synchronization

Use `skill-library-sync` to freeze, compare, synchronize, and eventually promote the canonical warehouse. State, ledgers, inventories, plans, and downloaded archives remain host-local.

## Confirmed API constraints

- The destination must be a typed `database_type: skills` database with the returned data-source schema: `Skill name`, `Description`, `Files`, `Tags`, and generated `Created by`.
- Page content is created with `markdown` and fully replaced through `PATCH /v1/pages/{id}/markdown` using `type: replace_content`.
- `Files` accepts at most 100 uploads. Each managed skill therefore uses two attachments: one deterministic package bundle and one transport manifest. This preserves arbitrary relative paths and packages with more than 100 source files.
- A skill export contains Notion's generated `SKILL.md` plus the attachments. The verified cache reconstructs canonical bytes from the bundle; the manifest verifies every path, byte count, hash, and POSIX mode.
- Each tag becomes a plugin. A plugin export contains at most 100 skills, so the planner assigns deterministic shards of 80 and verifies skills individually.
- `version_id` is opaque. Equality means the export is unchanged.
- Plugin listing is cursor-paginated. A partial or failed listing is never deletion evidence.
- Rate-limit responses may be `429` or `529`; honor `Retry-After` and checkpoint before waiting or retrying.

## Commands

```bash
skill-library-sync inventory --warehouse /canonical/skills --output /host/state/inventory.json
skill-library-sync remote --data-source-id DATA_SOURCE --bootstrap /host/state/bootstrap.json --output /host/state/remote.json
skill-library-sync plan --inventory /host/state/inventory.json --remote /host/state/remote.json --database-id DATABASE --data-source-id DATA_SOURCE --output /host/state/plan.json
skill-library-sync sync --inventory /host/state/inventory.json --plan /host/state/plan.json --ledger /host/state/ledger.sqlite3 --run-id RUN
```

`--only-id` and `--limit` are canary controls, not substitutes for complete verification. Missing local or remote entries become retirement candidates and are never automatically archived.

After the bootstrap run, feed the preceding successful `remote.json` back through `--bootstrap`. Its pinned export versions are the concurrency baseline; a later version change without a matching local synchronization is a conflict, even if the managed marker remains unchanged.

Managed markers are declarations, not proof. A remote entry without a durable baseline is a conflict until its exported package is compared read-only. Runs are bound to the inventory hash, plan hash, database and data source; changing any of them requires a new run. Interrupted or ambiguous mutations stop for reconciliation rather than guessing.

Nested `SKILL.md` files under a package's `skills/` directory are independently selectable while remaining preserved inside the parent package. Nested `SKILL.md` files elsewhere are treated as package resources, not independent skills. Symlink-bearing packages and unsupported custom entrypoints remain explicit inventory exclusions until a lossless representation is available. Bundle and export expansion are bounded at 600 MB.

## Authority boundary

The canonical warehouse is currently the authoring authority. Notion is a portable distribution mirror and readable index. The page body may be normalized by Notion, but consumers materialize the exact canonical bytes from the verified bundle. Editing a Notion page does not silently modify or override the canonical skill. A future bidirectional authoring workflow must introduce explicit conflict review before changing this rule.

Generic Notion clients can read the page and attachments but will not automatically unpack this transport bundle. Cross-harness consumers should use the shared `skill-library` adapter, which validates and materializes the standard skill directory before selection.

## Promotion gate

Do not activate a bulk run merely because the plan is conflict-free. Require resumable create/update reconciliation, a mutation-free second run, complete per-skill export verification, immutable cache staging without changing `current.json`, then a release that binds the snapshot, generated catalog, profiles, and acceptance evidence. Promotion and rollback must select that release atomically, and sessions must remain pinned to the release they started with.

Use `skill-library --config CONFIG stage` to build and validate an immutable snapshot without changing `current.json`. Run harness checks against the returned `catalog_root`. The current `promote` command changes only the cache pointer; it is not yet the production release gate and must not be used to claim atomic profile activation or session pinning. `pull` remains a cache-only stage-and-promote convenience path until the release mechanism is complete.
