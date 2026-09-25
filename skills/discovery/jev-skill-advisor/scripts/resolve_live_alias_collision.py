"""Stage a pinned Notion census with one proven alias-based replacement.

This is a local export decision only: it does not delete or edit a Notion page.
Any collision other than a new package explicitly aliasing one active primary
identity is refused for manual review.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path, PurePosixPath

from jev_skill_advisor.library_cli import _cached_fetch, _config
from jev_skill_advisor.library_cache import _archive_entries, _package_metadata, _package_roots
from jev_skill_advisor.release import ReleaseStore


class ResolutionError(ValueError):
    pass


class _CachedOnly:
    def fetch(self, *_args):
        raise ResolutionError("uncached_export")


def resolve(source: Path, release_root: Path, destination: Path, evidence: Path):
    state, rows = _config(source)
    store = ReleaseStore(release_root)
    release_id = store.current()
    if release_id is None:
        raise ResolutionError("active_release_missing")
    active = store.validate(release_id)
    old_snapshot = json.loads((Path(active["snapshot_root"]).parent / "manifest.json").read_text())
    old_export_ids = {row["id"] for row in old_snapshot["exports"]}
    by_identity = defaultdict(list)
    for row in rows:
        export = _cached_fetch(_CachedOnly(), row, state / "export-cache")
        files = _archive_entries(export, max_expanded_bytes=600_000_000)
        for package_root in _package_roots(files):
            selected = {PurePosixPath(name).relative_to(package_root).as_posix(): data
                        for name, data in files.items() if package_root in PurePosixPath(name).parents}
            package = _package_metadata(package_root, selected, export)
            for identity in {package["stable_id"], *package["aliases"]}:
                by_identity[identity].append((export.id, package))
    collisions = [(identity, members) for identity, members in by_identity.items() if len(members) > 1]
    if len(collisions) != 1 or len(collisions[0][1]) != 2:
        raise ResolutionError("ambiguous_alias_collision")
    identity, members = collisions[0]
    prior = [item for item in members if item[0] in old_export_ids]
    newer = [item for item in members if item[0] not in old_export_ids]
    if len(prior) != 1 or len(newer) != 1:
        raise ResolutionError("ambiguous_alias_collision")
    old_id, old_package = prior[0]
    new_id, new_package = newer[0]
    if (identity != old_package["stable_id"] or old_package["stable_id"] not in new_package["aliases"]
            or new_package["stable_id"] == old_package["stable_id"]):
        raise ResolutionError("replacement_not_explicit")
    kept = [row for row in rows if row["id"] != old_id]
    if len(kept) != len(rows) - 1 or not any(row["id"] == new_id for row in kept):
        raise ResolutionError("replacement_invalid")
    if destination.exists() or evidence.exists():
        raise ResolutionError("target_exists")
    destination.write_text(json.dumps({"allowlist": kept, "state_dir": str(state)}, sort_keys=True) + "\n")
    evidence.write_text(json.dumps({
        "decision": "explicit_alias_successor", "source_release": release_id,
        "source_count": len(rows), "selected_count": len(kept),
        "excluded_page_id": old_id, "replacement_page_id": new_id,
        "retained_alias": identity, "replacement_primary": new_package["stable_id"],
        "notion_mutation": False,
    }, sort_keys=True) + "\n")
    return {"status": "resolved", "source_count": len(rows), "selected_count": len(kept),
            "collision_count": 1, "notion_mutation": False}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(resolve(args.config, args.release_root, args.output, args.evidence)))


if __name__ == "__main__":
    try:
        main()
    except ResolutionError as exc:
        raise SystemExit(str(exc)) from None
