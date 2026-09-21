from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from .library_cache import LibraryCache,_archive_entries
from .notion_import import Export,NotionExportClient,NotionImportError


def _config(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    allowlist = value.get("allowlist")
    if not isinstance(allowlist, list) or not 1 <= len(allowlist) <= 1024:
        raise ValueError("allowlist_requires_1_to_1024_exports")
    entries = []
    for row in allowlist:
        if (not isinstance(row,dict) or not {"kind","id"}<=set(row) or set(row)-{"kind","id","version_id","stable_id"}
                or row["kind"] not in {"skill","plugin"} or not isinstance(row["id"],str)):
            raise ValueError("invalid_allowlist_entry")
        if "version_id" in row and (not isinstance(row["version_id"],str) or len(row["version_id"])!=64): raise ValueError("invalid_pinned_version")
        entries.append(dict(row))
    if len(entries) != len({(row["kind"],row["id"]) for row in entries}):
        raise ValueError("duplicate_allowlist_entry")
    state = value.get("state_dir")
    if not isinstance(state, str) or not Path(state).is_absolute():
        raise ValueError("state_dir_must_be_absolute")
    return Path(state), entries


def _cached_fetch(client,row,root,*,max_total_bytes=2_000_000_000):
    root.mkdir(parents=True,exist_ok=True,mode=0o700); pinned=row.get("version_id")
    key=hashlib.sha256(f"{row['kind']}\0{row['id']}\0{pinned or ''}".encode()).hexdigest(); archive_path=root/f"{key}.tar.gz"; receipt_path=root/f"{key}.json"
    if archive_path.is_file() and receipt_path.is_file():
        receipt=json.loads(receipt_path.read_text()); data=archive_path.read_bytes()
        if (receipt.get("kind"),receipt.get("id"),receipt.get("version_id"),receipt.get("bytes"),receipt.get("sha256"))==(row["kind"],row["id"],pinned,len(data),hashlib.sha256(data).hexdigest()):
            export=Export(row["kind"],row["id"],pinned,data); _archive_entries(export,max_files=10_000,max_expanded_bytes=600_000_000); return export
        raise ValueError("corrupt_export_cache")
    transient={"notion_api_failure","archive_download_failure"}; export=None
    for attempt in range(3):
        try: export=client.fetch(row["kind"],row["id"])
        except NotionImportError as exc:
            if str(exc) not in transient or attempt==2: raise
            time.sleep(2**attempt); continue
        if pinned and export.version_id!=pinned: raise ValueError("pinned_export_version_changed")
        break
    _archive_entries(export,max_files=10_000,max_expanded_bytes=600_000_000)
    data=export.archive; existing=sum(path.stat().st_size for path in root.glob("*.tar.gz"))
    if existing+len(data)>max_total_bytes: raise ValueError("export_cache_budget_exceeded")
    handle=tempfile.NamedTemporaryFile("wb",dir=root,prefix=".archive-",delete=False); temporary=Path(handle.name)
    try:
        with handle: handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary,archive_path)
    finally: temporary.unlink(missing_ok=True)
    receipt={"kind":row["kind"],"id":row["id"],"version_id":export.version_id,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
    handle=tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=root,prefix=".receipt-",delete=False); temporary=Path(handle.name)
    try:
        with handle: json.dump(receipt,handle,sort_keys=True); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary,receipt_path)
    finally: temporary.unlink(missing_ok=True)
    return export


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-library")
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect"); commands.add_parser("pull"); commands.add_parser("stage"); commands.add_parser("status")
    promote = commands.add_parser("promote"); promote.add_argument("snapshot_id")
    rollback = commands.add_parser("rollback"); rollback.add_argument("snapshot_id")
    args = parser.parse_args(argv)
    state, allowlist = _config(args.config); cache = LibraryCache(state)
    if args.command == "status":
        result = cache.status()
    elif args.command in {"rollback", "promote"}:
        result = cache.rollback(args.snapshot_id) if args.command == "rollback" else cache.promote(args.snapshot_id)
    else:
        client = NotionExportClient(max_archive_bytes=600_000_000)
        if args.command == "inspect":
            result = {"exports": [{key: value for key, value in client.inspect(row["kind"],row["id"]).items() if key != "url"}
                                  for row in allowlist]}
        elif args.command == "stage":
            result = cache.stage(_cached_fetch(client,row,state/"export-cache") for row in allowlist)
        else:
            result = cache.publish(_cached_fetch(client,row,state/"export-cache") for row in allowlist)
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
