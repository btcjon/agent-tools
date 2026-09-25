"""Selection receipts and an idempotent usage ledger.

A receipt means a harness supplied a verified skill. It does not mean the
model followed it. Receipts store skill identity, time, host, and adapter.
They never store prompts or skill bodies.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

LEDGER_VERSION = 2
SUCCESS_STATUS = frozenset({"emitted", "complete", "selected_unconfirmed", "explicit_override"})
IDENTITY_FIELDS = ("session_id", "request_id", "harness", "release_id")


def default_ledger() -> Path:
    return Path.home() / ".local" / "state" / "jev-skill-advisor" / "usage" / "ledger.json"


def cursor_receipt_path() -> Path:
    return Path.home() / ".local" / "state" / "jev-skill-advisor" / "cursor-adapter" / "events.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp(value: object) -> str | None:
    """Return a UTC timestamp already present on a receipt. Never invent one."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_jsonl(path: Path, record: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError:
        return False


def record_cursor_selection(result: object, path: Path | None = None) -> bool:
    """Write one content-free attempt; a CLI selection is not confirmed delivery."""
    try:
        if not isinstance(result, dict):
            return False
        selected = result.get("selected")
        selected = selected if isinstance(selected, dict) else None
        skill_id = selected.get("skill_id") if selected else None
        if selected and (not isinstance(skill_id, str) or not skill_id):
            return False
        reason = result.get("reason") if isinstance(result.get("reason"), str) else None
        attempt_status = "selected_unconfirmed" if selected else "abstained" if reason in {"catalog_choice_none", "no_selection", "name_not_found"} else "fallback"
        record = {
            "usage_id": uuid.uuid4().hex,
            "event_schema": 1,
            "event": "selection_attempt",
            "adapter": "cursor",
            "harness": "cursor",
            "host": socket.gethostname(),
            "timestamp": _now(),
            "status": attempt_status,
            "attempt_status": attempt_status,
            "reason": reason,
            "selected_at": _now() if selected else None,
            "skill_id": skill_id,
            "snapshot_id": selected.get("snapshot_id") if selected and isinstance(selected.get("snapshot_id"), str) else result.get("snapshot_id"),
            "release_id": result.get("release_id") if isinstance(result.get("release_id"), str) else None,
            "session_id": result.get("session_id") if isinstance(result.get("session_id"), str) else None,
            "request_id": result.get("request_id") if isinstance(result.get("request_id"), str) else None,
            "hash": selected.get("hash") if selected and isinstance(selected.get("hash"), str) else None,
            "bytes": selected.get("bytes") if selected and isinstance(selected.get("bytes"), int) else None,
            "latency_ms": result.get("elapsed_ms") if isinstance(result.get("elapsed_ms"), (int, float)) else None,
            "provenance": "cursor_cli_receipt",
        }
        record["attempt_id"] = record["usage_id"]
        record["receipt_id"] = record["usage_id"] if selected else None
        record["skill_ids"] = [skill_id] if selected else []
        record = {key: value for key, value in record.items() if value is not None}
        return append_jsonl(path or cursor_receipt_path(), record)
    except Exception:
        return False


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _skill_ids(obj: dict) -> list[str]:
    skill_id = obj.get("skill_id")
    if isinstance(skill_id, str) and skill_id:
        return [skill_id]
    stable = obj.get("stable_ids")
    if isinstance(stable, list):
        return [item for item in stable if isinstance(item, str) and item]
    return []


def _identity(obj: dict) -> dict:
    """Keep identity the receipt already has. A snapshot is not a release."""
    harness = _text(obj.get("harness")) or _text(obj.get("adapter"))
    return {
        "session_id": _text(obj.get("session_id")),
        "request_id": _text(obj.get("request_id")) or _text(obj.get("turn_id")),
        "harness": harness,
        "release_id": _text(obj.get("release_id")),
        "snapshot_id": _text(obj.get("snapshot_id")),
    }


def parse_receipt_line(raw: str) -> list[dict]:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    status = obj.get("status")
    if isinstance(status, str) and status not in SUCCESS_STATUS:
        return []
    skills = _skill_ids(obj)
    if not skills:
        return []
    selected_at = timestamp(obj.get("selected_at"))
    host = obj.get("host") if isinstance(obj.get("host"), str) else obj.get("execution_host")
    host = host if isinstance(host, str) else ""
    adapter = obj.get("adapter") if isinstance(obj.get("adapter"), str) else ""
    usage_id = obj.get("usage_id") if isinstance(obj.get("usage_id"), str) else ""
    identity = _identity(obj)
    attributable = bool(identity["session_id"] and identity["request_id"])
    provenance = _text(obj.get("provenance")) or ("adapter_event" if attributable else "unattributable_historical")
    rows = []
    for skill_id in skills:
        if usage_id and len(skills) == 1:
            key = usage_id
        elif usage_id:
            key = f"{usage_id}:{skill_id}"
        else:
            key = hashlib.sha256(raw.encode() + b"\n" + skill_id.encode()).hexdigest()
        rows.append({
            "usage_id": key,
            "skill_id": skill_id,
            "selected_at": selected_at,
            "host": host,
            "adapter": adapter,
            "attributable": attributable,
            "provenance": provenance,
            **identity,
        })
    return rows


def load_ledger(path: Path) -> dict:
    if not path.is_file():
        return {"version": LEDGER_VERSION, "receipts": {}, "unattributable": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    receipts = data.get("receipts") if isinstance(data, dict) else None
    if not isinstance(receipts, dict):
        raise ValueError("invalid_usage_ledger")
    unattributable = data.get("unattributable") if isinstance(data, dict) else None
    if unattributable is None:
        unattributable = {}
    if not isinstance(unattributable, dict):
        raise ValueError("invalid_usage_ledger")
    imported_at = data.get("imported_at") if isinstance(data.get("imported_at"), str) else None
    return {"version": LEDGER_VERSION, "receipts": receipts, "unattributable": unattributable, "imported_at": imported_at}


def _write_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _stored_row(row: dict) -> dict:
    return {
        "skill_id": row["skill_id"],
        "selected_at": row["selected_at"],
        "host": row["host"],
        "adapter": row["adapter"],
        "session_id": row["session_id"],
        "request_id": row["request_id"],
        "harness": row["harness"],
        "release_id": row["release_id"],
        "snapshot_id": row["snapshot_id"],
        "provenance": row["provenance"],
    }


def reclassify(ledger_path: Path) -> dict:
    """Move stored rows that lack a session and request out of the published count."""
    ledger = load_ledger(ledger_path)
    moved = 0
    for key, row in list(ledger["receipts"].items()):
        if not isinstance(row, dict):
            continue
        if _text(row.get("session_id")) and _text(row.get("request_id")):
            continue
        ledger["unattributable"][key] = row
        ledger["receipts"].pop(key)
        moved += 1
    if moved:
        ledger["imported_at"] = _now()
        _write_ledger(ledger_path, ledger)
    return {"reclassified": moved, "receipts": len(ledger["receipts"]), "unattributable": len(ledger["unattributable"])}


def ingest(paths: list[Path], ledger_path: Path) -> dict:
    """Add receipt lines that are not already in the ledger. Never delete entries."""
    ledger = load_ledger(ledger_path)
    receipts = ledger["receipts"]
    unattributable = ledger["unattributable"]
    added = 0
    separated = 0
    skipped = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for raw in text.splitlines():
            if not raw.strip():
                continue
            rows = parse_receipt_line(raw)
            if not rows:
                skipped += 1
                continue
            for row in rows:
                stored = _stored_row(row)
                existing_receipt = receipts.get(row["usage_id"])
                existing_unattributed = unattributable.get(row["usage_id"])
                if existing_receipt == stored or existing_unattributed == stored:
                    skipped += 1
                    continue
                if existing_receipt is not None and not row["attributable"]:
                    skipped += 1
                    continue
                if existing_unattributed is not None and row["attributable"]:
                    receipts[row["usage_id"]] = stored
                    unattributable.pop(row["usage_id"], None)
                    added += 1
                    continue
                if existing_receipt is not None or existing_unattributed is not None:
                    skipped += 1
                    continue
                target = receipts if row["attributable"] else unattributable
                target[row["usage_id"]] = stored
                if row["attributable"]:
                    added += 1
                else:
                    separated += 1
    if added or separated:
        ledger["imported_at"] = _now()
        _write_ledger(ledger_path, ledger)
    return {
        "added": added,
        "separated_unattributable": separated,
        "skipped": skipped,
        "receipts": len(receipts),
        "unattributable": len(unattributable),
    }


def summarize(ledger: dict) -> dict:
    totals: dict[str, dict] = {}
    adapters: dict[str, int] = {}
    for row in ledger.get("receipts", {}).values():
        if not isinstance(row, dict):
            continue
        skill_id = row.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            continue
        slot = totals.setdefault(skill_id, {"count": 0, "last_selected": None})
        slot["count"] += 1
        selected_at = row.get("selected_at")
        if isinstance(selected_at, str) and (slot["last_selected"] is None or selected_at > slot["last_selected"]):
            slot["last_selected"] = selected_at
        adapter = row.get("harness") if isinstance(row.get("harness"), str) and row.get("harness") else row.get("adapter")
        adapter = adapter if isinstance(adapter, str) else ""
        adapters[adapter or "unlabeled"] = adapters.get(adapter or "unlabeled", 0) + 1
    imported_at = ledger.get("imported_at") if isinstance(ledger.get("imported_at"), str) else None
    return {
        "skills": totals,
        "adapters": adapters,
        "unattributable": len(ledger.get("unattributable") or {}),
        "freshness": {
            "imported_at": imported_at,
            "counts_are": "selections supplied through the imported receipts, not proof a model followed the skill",
            "stale_after": "the next harness selection that has not been imported",
        },
    }
