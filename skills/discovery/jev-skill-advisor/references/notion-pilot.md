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
