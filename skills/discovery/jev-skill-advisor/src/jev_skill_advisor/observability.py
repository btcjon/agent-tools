"""Content-free, on-demand selection observability.

An attempt is not proof that an agent followed a skill. Only an adapter's
confirmed context insertion is a delivery; follow-through is self-reported.
"""
from __future__ import annotations

import json
import socket
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .usage import append_jsonl, timestamp

FOLLOWTHROUGH = frozenset({"followed", "partial", "ignored", "not_applicable"})
BENEFIT = frozenset({"better", "same", "worse", "wrong_skill"})
STATUSES = frozenset({"emitted", "abstained", "fallback", "explicit_override", "selected_unconfirmed"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_attempt(path: Path, *, harness: str, status: str, reason: str | None = None,
                   skill_ids: list[str] | None = None, hashes: dict[str, str] | None = None,
                   receipt_id: str | None = None, snapshot_id: str | None = None,
                   snapshot_age_s: int | None = None, latency_ms: float | None = None,
                   host: str | None = None) -> bool:
    """Allowlist fields, never serializing caller/provider payloads."""
    if status not in STATUSES:
        raise ValueError("invalid_attempt_status")
    ids = [item for item in (skill_ids or []) if isinstance(item, str) and item]
    event = {"event_schema": 1, "event": "selection_attempt", "attempt_id": uuid.uuid4().hex,
             "timestamp": _iso(_utc_now()), "host": host or socket.gethostname(),
             "harness": harness, "status": status, "reason": reason if isinstance(reason, str) else None,
             "skill_ids": ids, "hashes": {sid: hashes[sid] for sid in ids if hashes and isinstance(hashes.get(sid), str)},
             "receipt_id": receipt_id if isinstance(receipt_id, str) else None,
             "snapshot_id": snapshot_id if isinstance(snapshot_id, str) else None,
             "snapshot_age_s": snapshot_age_s if isinstance(snapshot_age_s, int) and snapshot_age_s >= 0 else None,
             "latency_ms": round(latency_ms, 3) if isinstance(latency_ms, (int, float)) else None}
    return append_jsonl(path, event)


def append_label(path: Path, *, receipt_id: str, label: str, evidence: str) -> bool:
    allowed = FOLLOWTHROUGH if evidence == "self_report" else BENEFIT if evidence == "human_review" else frozenset()
    if not isinstance(receipt_id, str) or not receipt_id or label not in allowed:
        raise ValueError("invalid_label")
    return append_jsonl(path, {"event_schema": 1, "event": evidence, "receipt_id": receipt_id,
                               "label": label, "timestamp": _iso(_utc_now())})


def _read_rows(paths: list[Path]):
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                row = dict(row)
                if not row.get("adapter") and path.parent.name.endswith("-adapter"):
                    row["adapter"] = path.parent.name.removesuffix("-adapter")
                yield row


def _attempt(row: dict) -> dict | None:
    if row.get("event") == "selection_attempt":
        if row.get("status") not in STATUSES:
            return None
        return {**row, "reason": row.get("reason") or row.get("fallback_reason"),
                "receipt_id": row.get("receipt_id") or row.get("usage_id"),
                "complete_attempt_schema": True}
    # Legacy logs stay visible; an untimestamped row cannot enter a window.
    adapter = row.get("harness") or row.get("adapter")
    if not isinstance(adapter, str) or not adapter:
        return None
    ids = row.get("stable_ids") or ([row["skill_id"]] if isinstance(row.get("skill_id"), str) else [])
    status = "legacy_fallback" if row.get("status") == "fallback" else "legacy_selected" if ids else "legacy_no_selection"
    return {"timestamp": row.get("timestamp") or row.get("selected_at"), "host": row.get("host") or row.get("execution_host"),
            "harness": adapter, "status": status, "reason": row.get("reason") or row.get("fallback_reason"),
            "skill_ids": ids, "receipt_id": row.get("receipt_id") or row.get("usage_id"),
            "complete_attempt_schema": False}


def summary(event_paths: list[Path], *, since_hours: float = 24, label_paths: list[Path] | None = None,
            expected: list[str] | None = None, now: datetime | None = None) -> dict:
    now = now or _utc_now()
    cutoff = now - timedelta(hours=since_hours)
    grouped = defaultdict(list)
    undated = 0
    receipts = set()
    for raw in _read_rows(event_paths):
        row = _attempt(raw)
        if row is None:
            continue
        date = timestamp(row.get("timestamp"))
        if date is None:
            undated += 1
            continue
        instant = datetime.fromisoformat(date.replace("Z", "+00:00"))
        if instant < cutoff or instant > now + timedelta(minutes=5):
            continue
        key = (row.get("host") or "unknown", row.get("harness") or "unknown")
        grouped[key].append((row, instant))
        if row.get("status") in {"emitted", "explicit_override"} and isinstance(row.get("receipt_id"), str):
            receipts.add(row["receipt_id"])
    labels = {"self_report": {}, "human_review": {}}
    for row in _read_rows(label_paths or []):
        kind = row.get("event")
        if kind in labels and row.get("receipt_id") in receipts:
            valid = FOLLOWTHROUGH if kind == "self_report" else BENEFIT
            if row.get("label") in valid:
                labels[kind][row["receipt_id"]] = row["label"]
    sources = []
    for (host, harness), rows in sorted(grouped.items()):
        counts = Counter(item["status"] for item, _ in rows)
        reasons = Counter(item.get("reason") or "unspecified" for item, _ in rows if item["status"] in {"fallback", "abstained", "legacy_fallback", "legacy_no_selection"})
        latest = max(instant for _, instant in rows)
        total = len(rows)
        complete = all(item.get("complete_attempt_schema") for item, _ in rows)
        sources.append({"host": host, "harness": harness, "attempts": total, "status_counts": dict(counts),
                        "measurement_coverage": "complete_attempt_schema" if complete else "legacy_or_mixed_success_biased",
                        "status_rates": {key: round(value / total, 4) for key, value in sorted(counts.items())} if complete else None,
                        "top_reasons": reasons.most_common(5), "last_seen": _iso(latest),
                        "silent_over_24h": now - latest > timedelta(hours=24)})
    seen = {f"{row['host']}:{row['harness']}" for row in sources}
    for source in sorted(set(expected or []) - seen):
        if ":" not in source:
            continue
        host, harness = source.split(":", 1)
        sources.append({"host": host, "harness": harness, "attempts": 0, "status_counts": {},
                        "measurement_coverage": "no_rows", "status_rates": None, "top_reasons": [], "last_seen": None, "silent_over_24h": True})
    follow = Counter(labels["self_report"].get(receipt, "unreported") for receipt in receipts)
    benefit = Counter(labels["human_review"].values())
    delivered = [row for rows in grouped.values() for row, _ in rows if row["status"] in {"emitted", "explicit_override"}]
    return {"window_start": _iso(cutoff), "window_end": _iso(now), "sources": sorted(sources, key=lambda x: (x["host"], x["harness"])),
            "undated_legacy_rows_excluded": undated,
            "evidence": {"delivered_direct": len(delivered),
                         "delivered_without_receipt_id": sum(not isinstance(row.get("receipt_id"), str) for row in delivered),
                         "delivered_without_session_correlation": sum(not isinstance(row.get("session_id"), str) or not row.get("session_id") for row in delivered),
                         "reported_followthrough_self_report": dict(follow), "reviewed_benefit_human": dict(benefit)},
            "caveat": "Legacy rows are only selected/no-selection observations, never confirmed delivery or clean abstention. Their status rates are withheld. No harness automatically writes follow-through labels; missing labels are unreported, not unused."}
