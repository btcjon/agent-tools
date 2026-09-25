"""Select one skill from the active Notion snapshot. Prints one JSON object."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
import uuid
from pathlib import Path

from .capability_choice import notion_skill_selected, resolve_capabilities
from .capability_core import (
    SELECTION_MAX_CAPABILITIES,
    STARTUP_CAPABILITY_BUDGET_BYTES,
    SUMMARY_MAX_BYTES,
    load_manifest,
)
from .catalog_choice import catalog_choice_scan
from .profile import load_profile
from .runtime import ServiceRuntime
from .usage import record_cursor_selection

RELEASE_ROOT = Path.home() / ".local/state/jev-skill-advisor/releases"
BODY_LIMIT = 32_768
PUBLIC_CAPABILITY_BUDGET_BYTES = STARTUP_CAPABILITY_BUDGET_BYTES
_SKILL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CAPABILITY_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,96}$")
_REASON = re.compile(r"^[a-z0-9_]{1,80}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset({"selected", "none", "fail_open"})


def _active(root: Path):
    current = json.loads((root / "current-release.json").read_text(encoding="utf-8"))
    release_id = current["release_id"]
    manifest = json.loads((root / "releases" / release_id / "manifest.json").read_text(encoding="utf-8"))
    profile_path = Path(manifest["files"]["profile:generic"]["path"])
    return load_profile(profile_path), manifest


def _bound_capability_manifest(root: Path) -> Path | None:
    """Verified release-inputs copy for the active release, or None.

    A missing binding keeps the previous skill-only result. A damaged or
    missing copy fails open the same way. This does not open a mutable source.
    """
    try:
        from .release import ReleaseError, ReleaseStore
        store = ReleaseStore(root)
        release_id = store._current_identity()
        if not release_id:
            return None
        return store.capability_manifest_path(release_id)
    except (ReleaseError, OSError):
        return None


def _resolve_capability_manifest(root: Path, profile_path: Path | None, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    if profile_path is not None:
        return None
    return _bound_capability_manifest(root)


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


def _manifest_path(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text)


def capability_correlation(manifest_hash, skill_id, ids, status):
    """Content-free digest of the suggestion. It does not authorize a tool call."""
    material = {
        "ids": list(ids),
        "manifest_hash": manifest_hash,
        "skill_id": skill_id,
        "status": status,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clean_description(value):
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    text = text.replace("<", "").replace(">", "").replace('"', "").replace("'", "")
    while text and len(text.encode("utf-8")) > SUMMARY_MAX_BYTES:
        text = text[:-1]
    return text.strip()


def _selection_cards(selected):
    rows = selected if isinstance(selected, list) else [selected]
    cards = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifier = row.get("id") if isinstance(row.get("id"), str) and row.get("id") else row.get("skill_id")
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            name = identifier.rsplit(":", 1)[-1]
        cards.append({"id": identifier[:128], "name": name[:128]})
    return cards


def _correlation_skill_id(cards):
    notion = [card for card in cards if notion_skill_selected([card])]
    chosen = (notion or cards)[0]["id"]
    if isinstance(chosen, str) and _SKILL_ID.fullmatch(chosen):
        return chosen
    return ""


def _measured(value):
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def public_capabilities(decision, skill_id):
    """Compact advisory cards. No schema, arguments, receipt, or skill body."""
    status = decision.get("status") if isinstance(decision, dict) else None
    if status not in _STATUSES:
        status = "fail_open"
    reason = decision.get("reason") if isinstance(decision, dict) else None
    if not isinstance(reason, str) or not _REASON.fullmatch(reason):
        reason = "capability_choice_provider_failure"
    manifest_hash = decision.get("manifest_hash") if isinstance(decision, dict) else ""
    if not isinstance(manifest_hash, str) or (manifest_hash and not _HASH.fullmatch(manifest_hash)):
        manifest_hash = ""
    if not isinstance(skill_id, str) or not _SKILL_ID.fullmatch(skill_id):
        skill_id = ""
    cards = []
    raw_cards = decision.get("cards") if isinstance(decision, dict) else None
    if isinstance(raw_cards, list):
        for card in raw_cards:
            if len(cards) >= SELECTION_MAX_CAPABILITIES:
                break
            if not isinstance(card, dict):
                continue
            identifier = card.get("id")
            if not isinstance(identifier, str) or not _CAPABILITY_ID.fullmatch(identifier):
                continue
            description = _clean_description(card.get("description"))
            if not description:
                continue
            cards.append({"id": identifier, "description": description})

    def build(status, reason, cards):
        ids = [card["id"] for card in cards]
        return {
            "advisory": True,
            "authorizes_calls": False,
            "status": status,
            "reason": reason,
            "manifest_hash": manifest_hash,
            "skill_id": skill_id,
            "cards": cards,
            "correlation": capability_correlation(manifest_hash, skill_id, ids, status),
        }

    public = build(status, reason, cards)
    if _measured(public) > PUBLIC_CAPABILITY_BUDGET_BYTES:
        public = build("fail_open", "oversized_capability_response", [])
    return public


def _live_evaluator(profile, profile_path):
    try:
        if profile is None and profile_path is not None:
            profile = load_profile(Path(profile_path))
        if profile is None:
            return None
        return ServiceRuntime(profile, operation_id="cli-cap-" + uuid.uuid4().hex).evaluator
    except Exception:
        return None


def attach_capabilities(selected, task, *, manifest_path, evaluator=None, explicit=False, profile=None, profile_path=None, deadline_s=None):
    """One capability decision after skill selection. Never raises. None if opted out."""
    path = _manifest_path(manifest_path)
    cards = _selection_cards(selected)
    if path is None or not cards:
        return None
    skill_id = _correlation_skill_id(cards)
    try:
        manifest = load_manifest(path)
    except Exception:
        return public_capabilities(
            {"status": "fail_open", "reason": "invalid_manifest", "manifest_hash": "", "cards": []},
            skill_id,
        )
    if explicit and not notion_skill_selected(cards):
        return public_capabilities(
            {"status": "none", "reason": "not_notion_skill", "manifest_hash": manifest.content_hash, "cards": []},
            skill_id,
        )
    if evaluator is None and notion_skill_selected(cards):
        evaluator = _live_evaluator(profile, profile_path)
    try:
        decision = resolve_capabilities(
            manifest,
            task if isinstance(task, str) else "",
            selected_skills=cards,
            evaluator=evaluator,
            deadline_s=deadline_s,
        )
    except Exception:
        decision = {
            "status": "fail_open",
            "reason": "capability_choice_provider_failure",
            "manifest_hash": manifest.content_hash,
            "cards": [],
        }
    return public_capabilities(decision, skill_id)


def _with_capabilities(result, task, *, manifest_path, evaluator, explicit, profile, deadline_s):
    path = _manifest_path(manifest_path)
    selected = result.get("selected") if isinstance(result, dict) else None
    if path is None or not isinstance(selected, dict):
        return result
    capabilities = attach_capabilities(
        [selected],
        task,
        manifest_path=path,
        evaluator=evaluator,
        explicit=explicit,
        profile=profile,
        deadline_s=deadline_s,
    )
    if capabilities is None:
        return result
    return {**result, "capabilities": capabilities}


def select_task(task: str, *, root: Path = RELEASE_ROOT, deadline_s: float = 20.0, profile_path: Path | None = None, capability_manifest: Path | None = None, evaluator=None) -> dict:
    stopped = root / "EMERGENCY_STOP"
    if stopped.exists():
        return {"selected": None, "reason": "emergency_stop"}
    profile, manifest = _open(root, profile_path)
    if not task.strip():
        return {"selected": None, "reason": "missing_context"}
    registry = profile.registry(None, implicit_only=True)
    entries = registry.eligible()
    if evaluator is None:
        runtime = ServiceRuntime(profile, operation_id="cli-" + uuid.uuid4().hex)
        evaluator = runtime.evaluator
    started = time.monotonic()
    receipt = catalog_choice_scan(
        entries, task, "", evaluator, deadline_s=deadline_s, max_calls=8, max_tokens=profile.max_tokens,
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
    result = {"selected": _selected(entry, manifest, profile), **base}
    remaining = deadline_s - (time.monotonic() - started)
    return _with_capabilities(
        result, task, manifest_path=_resolve_capability_manifest(root, profile_path, capability_manifest),
        evaluator=evaluator, explicit=False, profile=profile, deadline_s=remaining,
    )


def select_named(name: str, *, root: Path = RELEASE_ROOT, profile_path: Path | None = None, capability_manifest: Path | None = None, task: str = "", evaluator=None) -> dict:
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
    result = {"selected": _selected(matches[0], manifest, profile), "reason": "explicit_name", **base}
    return _with_capabilities(
        result, task, manifest_path=_resolve_capability_manifest(root, profile_path, capability_manifest),
        evaluator=evaluator, explicit=True, profile=profile, deadline_s=None,
    )


def _cli_manifest(args):
    if args.capability_manifest is not None:
        return args.capability_manifest
    raw = os.environ.get("JEV_CAPABILITY_MANIFEST", "").strip()
    if raw:
        return Path(raw)
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="skill-advisor-select")
    parser.add_argument("--task", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--release-root", type=Path, default=RELEASE_ROOT)
    parser.add_argument("--deadline", type=float, default=20.0)
    parser.add_argument("--receipt", action="store_true")
    parser.add_argument("--capability-manifest", type=Path, default=None)
    args = parser.parse_args(argv)
    manifest = _cli_manifest(args)
    try:
        if args.name.strip():
            result = select_named(args.name, root=args.release_root, capability_manifest=manifest, task=args.task)
        elif args.task.strip():
            result = select_task(args.task, root=args.release_root, deadline_s=args.deadline, capability_manifest=manifest)
        else:
            result = {"selected": None, "reason": "missing_context"}
    except Exception as exc:
        result = {"selected": None, "reason": type(exc).__name__, "detail": str(exc)}
    if args.receipt:
        record_cursor_selection(result)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
