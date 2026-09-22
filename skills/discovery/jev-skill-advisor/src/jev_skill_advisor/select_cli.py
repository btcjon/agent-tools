"""Select one skill from the active Notion snapshot. Prints one JSON object."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
from pathlib import Path

from .catalog_choice import catalog_choice_scan
from .profile import load_profile
from .runtime import ServiceRuntime

RELEASE_ROOT = Path.home() / ".local/state/jev-skill-advisor/releases"
BODY_LIMIT = 32_768


def _active(root: Path):
    current = json.loads((root / "current-release.json").read_text(encoding="utf-8"))
    release_id = current["release_id"]
    manifest = json.loads((root / "releases" / release_id / "manifest.json").read_text(encoding="utf-8"))
    profile_path = Path(manifest["files"]["profile:generic"]["path"])
    return load_profile(profile_path), manifest


def _open(root: Path, profile_path: Path | None):
    if profile_path is None:
        return _active(root)
    profile = load_profile(profile_path)
    parent = profile.warehouse_root.parent.name
    snapshot = parent if len(parent) == 64 and all(char in "0123456789abcdef" for char in parent) else None
    return profile, {"snapshot_id": snapshot}


def _body(entry):
    source = Path(entry.source)
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry.source_hash:
        raise ValueError("stale_source")
    if len(raw) > BODY_LIMIT:
        raise ValueError("body_oversize")
    return raw.decode("utf-8", errors="replace"), str(source)


def _selected(entry, manifest, profile):
    content, path = _body(entry)
    package_root = profile.package_roots.get(entry.id) or str(Path(path).parent)
    return {
        "skill_id": entry.id,
        "name": entry.id.rsplit(":", 1)[-1],
        "description": entry.description,
        "snapshot_id": manifest.get("snapshot_id"),
        "hash": entry.source_hash,
        "path": path,
        "package_root": str(package_root),
        "content": content,
    }


def _match(registry, name: str):
    needle = name.strip()
    if needle.startswith("$"):
        needle = needle[1:]
    matches = [
        entry for entry in registry.entries.values()
        if needle in (entry.id, entry.id.rsplit(":", 1)[-1])
    ]
    return matches


def select_task(task: str, *, root: Path = RELEASE_ROOT, deadline_s: float = 20.0, profile_path: Path | None = None) -> dict:
    stopped = root / "EMERGENCY_STOP"
    if stopped.exists():
        return {"selected": None, "reason": "emergency_stop"}
    profile, manifest = _open(root, profile_path)
    if not task.strip():
        return {"selected": None, "reason": "missing_context"}
    registry = profile.registry(None, implicit_only=True)
    entries = registry.eligible()
    runtime = ServiceRuntime(profile)
    receipt = catalog_choice_scan(
        entries, task, "", runtime.evaluator, deadline_s=deadline_s, max_calls=8, max_tokens=profile.max_tokens,
    )
    selected = receipt.get("selected") or []
    base = {
        "reason": receipt.get("reason"),
        "status": receipt.get("status"),
        "eligible_count": len(entries),
        "attempts": receipt.get("attempts"),
        "batches": receipt.get("batches"),
        "deadline_s": deadline_s,
        "elapsed_ms": receipt.get("elapsed_ms"),
        "snapshot_id": manifest.get("snapshot_id"),
        "host": socket.gethostname(),
    }
    if receipt.get("status") != "complete" or len(selected) != 1:
        return {"selected": None, **base}
    entry = registry.entries[selected[0]]
    return {"selected": _selected(entry, manifest, profile), **base}


def select_named(name: str, *, root: Path = RELEASE_ROOT, profile_path: Path | None = None) -> dict:
    stopped = root / "EMERGENCY_STOP"
    if stopped.exists():
        return {"selected": None, "reason": "emergency_stop"}
    profile, manifest = _open(root, profile_path)
    registry = profile.registry(None, implicit_only=False)
    matches = _match(registry, name)
    base = {"snapshot_id": manifest.get("snapshot_id"), "host": socket.gethostname(), "status": "complete"}
    if not matches:
        return {"selected": None, "reason": "name_not_found", **base}
    if len(matches) != 1:
        return {"selected": None, "reason": "name_ambiguous", **base}
    return {"selected": _selected(matches[0], manifest, profile), "reason": "explicit_name", **base}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="skill-advisor-select")
    parser.add_argument("--task", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--release-root", type=Path, default=RELEASE_ROOT)
    parser.add_argument("--deadline", type=float, default=20.0)
    args = parser.parse_args(argv)
    try:
        if args.name.strip():
            result = select_named(args.name, root=args.release_root)
        elif args.task.strip():
            result = select_task(args.task, root=args.release_root, deadline_s=args.deadline)
        else:
            result = {"selected": None, "reason": "missing_context"}
    except Exception as exc:
        result = {"selected": None, "reason": type(exc).__name__, "detail": str(exc)}
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
