from __future__ import annotations

import argparse
import json
from pathlib import Path

from .library_cache import LibraryCache
from .notion_import import NotionExportClient


def _config(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    allowlist = value.get("allowlist")
    if not isinstance(allowlist, list) or not 1 <= len(allowlist) <= 5:
        raise ValueError("allowlist_requires_1_to_5_exports")
    entries = []
    for row in allowlist:
        if not isinstance(row, dict) or set(row) != {"kind", "id"} or row["kind"] not in {"skill", "plugin"} or not isinstance(row["id"], str):
            raise ValueError("invalid_allowlist_entry")
        entries.append((row["kind"], row["id"]))
    if len(entries) != len(set(entries)):
        raise ValueError("duplicate_allowlist_entry")
    state = value.get("state_dir")
    if not isinstance(state, str) or not Path(state).is_absolute():
        raise ValueError("state_dir_must_be_absolute")
    return Path(state), entries


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-library")
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect"); commands.add_parser("pull"); commands.add_parser("status")
    rollback = commands.add_parser("rollback"); rollback.add_argument("snapshot_id")
    args = parser.parse_args(argv)
    state, allowlist = _config(args.config); cache = LibraryCache(state)
    if args.command == "status":
        result = cache.status()
    elif args.command == "rollback":
        result = cache.rollback(args.snapshot_id)
    else:
        client = NotionExportClient()
        if args.command == "inspect":
            result = {"exports": [{key: value for key, value in client.inspect(kind, identity).items() if key != "url"}
                                  for kind, identity in allowlist]}
        else:
            result = cache.publish(client.fetch(kind, identity) for kind, identity in allowlist)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
