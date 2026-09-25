"""Opt-in, content-free capability-path events.

Callers record one JSONL row by passing a path. ``path=None`` writes nothing.
``mcp_server`` emits one discovery row after capability choice and one invoke
row for notion-fetch only when a manifest and an events path are both set.
Pass integers for context and schema sizes. Prompts, skill bodies, tool
arguments, schemas, page ids, OAuth material, and exception text have no field.
``failure_class``, when present, is one fixed token and never exception text.
An invoke row may set ``route`` to ``native``; success then uses reason ``native``
instead of ``bridge_read``.
"""
from __future__ import annotations

import json
import re
import socket
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

EVENT_SCHEMA = 1
EVENT_NAME = "capability_path"
STAGES = ("discovery", "describe", "invoke", "fallback")
HARNESSES = frozenset({"codex", "hermes", "pi", "cursor"})
FALLBACK_REASONS = frozenset({
    "harness", "auth", "unsupported-operation", "schema-drift",
    "provider-unavailable", "approval-denied", "other",
})
_OUTCOMES = {
    "discovery": frozenset({"selected", "absent"}),
    "describe": frozenset({"success", "failed"}),
    "invoke": frozenset({"success", "failed"}),
    "fallback": frozenset({"failed"}),
}
# Mirrors capability_core.SELECTION_MAX_CAPABILITIES without importing it.
_MAX_IDS = 5
_MAX_METRIC = 1_000_000_000
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,96}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_CORRELATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_DENIED = ("bearer", "oauth", "sk-", "secret", "password", "authorization", "api_key", "apikey")
_HASH = re.compile(r"^[0-9a-f]{64}$")
SELECTION_REASONS = frozenset({
    "capability_choice_selected", "capability_choice_none", "not_notion_skill",
    "explicit_tool_authoritative", "protected_input", "invalid_manifest",
    "capability_choice_provider_failure", "oversized_capability_request",
    "invalid_deadline", "capability_choice_failed",
})
SELECTION_STATUSES = frozenset({"selected", "none", "fail_open"})
INVOCATION_STATUSES = frozenset({"success", "denied"})
# Invoke success is bridge_read unless route is native.
INVOKE_SUCCESS_REASONS = frozenset({"bridge_read", "native"})
# Provider-choice diagnostics. Tokens only; never exception text or secrets.
FAILURE_CLASSES = frozenset({
    "missing_credential", "timeout", "transport", "provider",
    "invalid_response", "budget", "other",
})
# Known CapabilityError.code values. Anything else, including exception text, is "other".
_CODE_REASONS = {
    "unauthorized_capability": "auth",
    "invalid_receipt": "auth",
    "stale_schema": "schema-drift",
    "schema_budget_exceeded": "schema-drift",
    "identity_mismatch": "schema-drift",
    "stale_manifest": "schema-drift",
    "write_unapproved": "approval-denied",
    "invalid_approval": "approval-denied",
    "unknown_capability": "unsupported-operation",
    "missing_tool": "unsupported-operation",
    "describe_unavailable": "provider-unavailable",
    "call_unavailable": "provider-unavailable",
    "describe_client_unconfigured": "provider-unavailable",
    "call_client_unconfigured": "provider-unavailable",
}
_OPTIONAL = (
    "receipt_id", "session_id", "context_bytes", "schema_bytes", "fallback_reason",
    "manifest_hash", "status", "reason", "failure_class", "route",
)
_REQUIRED = ("host", "harness", "capability_ids", "stage", "outcome", "latency_ms")


def fallback_reason_for_code(code: object) -> str:
    """Map a capability error code to an allowlisted reason.

    Harness limits are not core error codes; pass ``harness`` directly to
    ``append_capability_event``. Unknown values return ``other``.
    """
    if code in FALLBACK_REASONS:
        return str(code)
    if isinstance(code, str) and code in _CODE_REASONS:
        return _CODE_REASONS[code]
    return "other"


def append_capability_event(path: Path | None, *, harness: str, stage: str, outcome: str,
                            latency_ms: int | float, capability_ids: list[str] | None = None,
                            host: str | None = None, receipt_id: str | None = None,
                            session_id: str | None = None, context_bytes: int | None = None,
                            schema_bytes: int | None = None, fallback_reason: str | None = None,
                            manifest_hash: str | None = None, status: str | None = None,
                            reason: str | None = None, failure_class: str | None = None,
                            route: str | None = None) -> bool:
    """Append one allowlisted event. ``None`` path is the off switch."""
    if path is None:
        return False
    if not isinstance(path, Path):
        raise ValueError("invalid_event_path")
    body = _body(harness=harness, stage=stage, outcome=outcome, latency_ms=latency_ms,
                 capability_ids=capability_ids, host=host or _default_host(), receipt_id=receipt_id,
                 session_id=session_id, context_bytes=context_bytes, schema_bytes=schema_bytes,
                 fallback_reason=fallback_reason, manifest_hash=manifest_hash, status=status,
                 reason=reason, failure_class=failure_class, route=route)
    event = {"event_schema": EVENT_SCHEMA, "event": EVENT_NAME, "timestamp": _now(), **body}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def summary(event_paths: list[Path], *, since_hours: float = 24, expected: list[str] | None = None,
            now: datetime | None = None) -> dict:
    """Aggregate accepted events. Selected ids and successful invokes stay separate."""
    if isinstance(since_hours, bool) or not isinstance(since_hours, (int, float)) or since_hours < 0:
        raise ValueError("invalid_window")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("invalid_window")
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(hours=float(since_hours))
    accepted: list[dict] = []
    rejected = 0
    for path in event_paths:
        if not isinstance(path, Path) or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                rejected += 1
                continue
            if not isinstance(raw, dict) or raw.get("event") != EVENT_NAME:
                continue
            row = _accepted(raw)
            if row is None:
                rejected += 1
                continue
            if row["_instant"] < cutoff or row["_instant"] > now + timedelta(minutes=5):
                continue
            accepted.append(row)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in accepted:
        grouped.setdefault((row["host"], row["harness"]), []).append(row)
    sources = [_source(host, harness, rows) for (host, harness), rows in sorted(grouped.items())]
    expected_pairs, expected_rejected = _expected(expected)
    present = {(row["host"], row["harness"]) for row in sources}
    absence = [{"host": host, "harness": harness, "events": 0, "measurement_coverage": "no_rows"}
               for host, harness in sorted(set(expected_pairs) - present)]
    selected = _ids_where(accepted, "discovery", "selected")
    invoked = _ids_where(accepted, "invoke", "success")
    return {
        "window_start": _iso(cutoff),
        "window_end": _iso(now),
        "rejected_rows": rejected,
        "sources": sources,
        "absence": absence,
        "selection": {
            "selected_ids": selected,
            "invoked_ids": invoked,
            "selected_without_invocation": [item for item in selected if item not in invoked],
        },
        "counts": {
            "events": len(accepted),
            "stage": {stage: sum(row["stage"] == stage for row in accepted) for stage in STAGES},
            "outcome": dict(Counter(row["outcome"] for row in accepted)),
            "fallback_reason": dict(Counter(row["fallback_reason"] for row in accepted if row["stage"] == "fallback")),
            "describe_success": sum(row["stage"] == "describe" and row["outcome"] == "success" for row in accepted),
            "invoke_success": sum(row["stage"] == "invoke" and row["outcome"] == "success" for row in accepted),
        },
        "latency_ms": _metric(accepted, "latency_ms"),
        "context_bytes": _metric(accepted, "context_bytes"),
        "schema_bytes": _metric(accepted, "schema_bytes"),
        "coverage": {
            "events": len(accepted),
            "with_correlation": sum(bool(row.get("receipt_id") or row.get("session_id")) for row in accepted),
            "discovery_events": sum(row["stage"] == "discovery" for row in accepted),
            "discovery_with_context_bytes": sum(row["stage"] == "discovery" and "context_bytes" in row for row in accepted),
            "describe_events": sum(row["stage"] == "describe" for row in accepted),
            "describe_with_schema_bytes": sum(row["stage"] == "describe" and "schema_bytes" in row for row in accepted),
            "expected_sources": len(expected_pairs),
            "expected_rejected": expected_rejected,
            "present_sources": len(present),
            "absent_sources": len(absence),
        },
        "caveat": (
            "Selected ids come from discovery events with outcome selected. "
            "Invoked ids come from invoke events with outcome success. "
            "Describe success stays in describe_success. "
            "Expected host:harness pairs with no accepted rows are absence. "
            "mcp_server writes one discovery row after capability choice and one invoke row for notion-fetch "
            "only when a manifest and events path are configured. capability_core does not emit these events."
        ),
    }


def _body(*, harness: str, stage: str, outcome: str, latency_ms: int | float,
          capability_ids: list[str] | None, host: str, receipt_id: str | None,
          session_id: str | None, context_bytes: int | None, schema_bytes: int | None,
          fallback_reason: str | None, manifest_hash: str | None = None,
          status: str | None = None, reason: str | None = None,
          failure_class: str | None = None, route: str | None = None) -> dict:
    if harness not in HARNESSES:
        raise ValueError("invalid_harness")
    if stage not in _OUTCOMES:
        raise ValueError("invalid_stage")
    if outcome not in _OUTCOMES[stage]:
        raise ValueError("invalid_outcome")
    if stage == "fallback":
        if fallback_reason not in FALLBACK_REASONS:
            raise ValueError("invalid_fallback_reason")
    elif fallback_reason is not None:
        raise ValueError("invalid_fallback_reason")
    if failure_class is not None and failure_class not in FAILURE_CLASSES:
        raise ValueError("invalid_failure_class")
    if stage == "discovery" and context_bytes is None:
        raise ValueError("invalid_context_bytes")
    if stage == "describe" and schema_bytes is None:
        raise ValueError("invalid_schema_bytes")
    body = {
        "host": _token(host, _HOST, "invalid_host"),
        "harness": harness,
        "capability_ids": _capability_ids(capability_ids, stage, outcome),
        "stage": stage,
        "outcome": outcome,
        "latency_ms": _latency(latency_ms),
    }
    _put(body, "receipt_id", _optional(receipt_id, _CORRELATION, "invalid_correlation"))
    _put(body, "session_id", _optional(session_id, _CORRELATION, "invalid_correlation"))
    _put(body, "context_bytes", None if context_bytes is None else _bytes(context_bytes, "invalid_context_bytes"))
    _put(body, "schema_bytes", None if schema_bytes is None else _bytes(schema_bytes, "invalid_schema_bytes"))
    _put(body, "fallback_reason", fallback_reason)
    status, reason, manifest_hash = _bounded(stage, outcome, status, reason, manifest_hash)
    if route is not None and route != "native":
        raise ValueError("invalid_route")
    if route == "native" and (stage != "invoke" or status not in INVOCATION_STATUSES):
        raise ValueError("invalid_route")
    if reason == "native" and route != "native":
        raise ValueError("invalid_reason")
    if route == "native" and status == "success" and reason != "native":
        raise ValueError("invalid_reason")
    _put(body, "status", status)
    _put(body, "reason", reason)
    _put(body, "manifest_hash", manifest_hash)
    _put(body, "failure_class", failure_class)
    _put(body, "route", route)
    return body


def _accepted(raw: dict) -> dict | None:
    allowed = {"event_schema", "event", "timestamp", *_REQUIRED, *_OPTIONAL}
    if set(raw) - allowed or any(key not in raw for key in ("event_schema", "timestamp", *_REQUIRED)):
        return None
    if type(raw.get("event_schema")) is not int or raw["event_schema"] != EVENT_SCHEMA:
        return None
    instant = _parse_time(raw.get("timestamp"))
    if instant is None:
        return None
    try:
        body = _body(
            harness=raw["harness"], stage=raw["stage"], outcome=raw["outcome"], latency_ms=raw["latency_ms"],
            capability_ids=raw["capability_ids"], host=raw["host"], receipt_id=raw.get("receipt_id"),
            session_id=raw.get("session_id"), context_bytes=raw.get("context_bytes"),
            schema_bytes=raw.get("schema_bytes"), fallback_reason=raw.get("fallback_reason"),
            manifest_hash=raw.get("manifest_hash"), status=raw.get("status"), reason=raw.get("reason"),
            failure_class=raw.get("failure_class"), route=raw.get("route"),
        )
    except ValueError:
        return None
    if any(key in raw and key not in body for key in _OPTIONAL):
        return None
    body["_instant"] = instant
    return body


def _capability_ids(value: list[str] | None, stage: str, outcome: str) -> list[str]:
    if value is None:
        value = []
    if type(value) is not list:
        raise ValueError("invalid_capability_id")
    cleaned = [_token(item, _ID, "invalid_capability_id") for item in value]
    if len(cleaned) != len(set(cleaned)) or len(cleaned) > _MAX_IDS:
        raise ValueError("invalid_capability_id")
    if stage == "discovery" and outcome == "selected" and not cleaned:
        raise ValueError("invalid_capability_id")
    if stage == "discovery" and outcome == "absent" and cleaned:
        raise ValueError("invalid_capability_id")
    if stage in {"describe", "invoke"} and len(cleaned) != 1:
        raise ValueError("invalid_capability_id")
    if stage == "fallback" and len(cleaned) > 1:
        raise ValueError("invalid_capability_id")
    return cleaned


def _token(value: object, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(code)
    lowered = value.lower()
    if any(marker in lowered for marker in _DENIED):
        raise ValueError(code)
    return value


def _bounded(stage: str, outcome: str, status: object, reason: object, manifest_hash: object):
    if status is None and reason is None and manifest_hash is None:
        return None, None, None
    if stage == "discovery":
        if status not in SELECTION_STATUSES:
            raise ValueError("invalid_status")
        if (status == "selected") != (outcome == "selected"):
            raise ValueError("invalid_status")
        if reason not in SELECTION_REASONS:
            raise ValueError("invalid_reason")
    elif stage == "invoke":
        if status not in INVOCATION_STATUSES:
            raise ValueError("invalid_status")
        if status == "success" and (outcome != "success" or reason not in INVOKE_SUCCESS_REASONS):
            raise ValueError("invalid_reason")
        if status == "denied" and (outcome != "failed" or reason not in FALLBACK_REASONS):
            raise ValueError("invalid_reason")
    else:
        raise ValueError("invalid_status")
    if not isinstance(manifest_hash, str) or not _HASH.fullmatch(manifest_hash):
        raise ValueError("invalid_manifest_hash")
    return status, reason, manifest_hash


def _optional(value: object, pattern: re.Pattern[str], code: str) -> str | None:
    if value is None:
        return None
    return _token(value, pattern, code)


def _latency(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid_latency")
    number = float(value)
    if number < 0 or number > _MAX_METRIC or number != number or number in {float("inf"), float("-inf")}:
        raise ValueError("invalid_latency")
    rounded = round(number, 3)
    return int(rounded) if rounded.is_integer() else rounded


def _bytes(value: object, code: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_METRIC:
        raise ValueError(code)
    return value


def _put(body: dict, key: str, value: object) -> None:
    if value is not None:
        body[key] = value


def _default_host() -> str:
    try:
        host = socket.gethostname()
    except OSError:
        return "unknown"
    try:
        return _token(host, _HOST, "invalid_host")
    except ValueError:
        return "unknown"


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _now() -> str:
    return _iso(datetime.now(timezone.utc))


def _iso(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ids_where(rows: list[dict], stage: str, outcome: str) -> list[str]:
    found = []
    for row in rows:
        if row["stage"] == stage and row["outcome"] == outcome:
            found.extend(row["capability_ids"])
    return sorted(set(found))


def _metric(rows: list[dict], key: str) -> dict:
    values = [row[key] for row in rows if key in row]
    if not values:
        return {"events": 0, "sum": 0, "max": 0}
    total = sum(values)
    if isinstance(total, float):
        total = round(total, 3)
    return {"events": len(values), "sum": total, "max": max(values)}


def _source(host: str, harness: str, rows: list[dict]) -> dict:
    selected = _ids_where(rows, "discovery", "selected")
    invoked = _ids_where(rows, "invoke", "success")
    missing = sum(not (row.get("receipt_id") or row.get("session_id")) for row in rows)
    return {
        "host": host,
        "harness": harness,
        "events": len(rows),
        "stage_counts": {stage: sum(row["stage"] == stage for row in rows) for stage in STAGES},
        "selected_ids": selected,
        "invoked_ids": invoked,
        "selected_without_invocation": [item for item in selected if item not in invoked],
        "fallback_reasons": dict(Counter(row["fallback_reason"] for row in rows if row["stage"] == "fallback")),
        "latency_ms": _metric(rows, "latency_ms"),
        "context_bytes": _metric(rows, "context_bytes"),
        "schema_bytes": _metric(rows, "schema_bytes"),
        "measurement_coverage": "partial" if missing else "complete",
        "missing_correlation": missing,
    }


def _expected(expected: list[str] | None) -> tuple[list[tuple[str, str]], int]:
    pairs = []
    rejected = 0
    for item in expected or []:
        if not isinstance(item, str) or item.count(":") != 1:
            rejected += 1
            continue
        host, harness = item.split(":", 1)
        try:
            pairs.append((_token(host, _HOST, "invalid_host"), _token(harness, re.compile(r"^[a-z]{1,32}$"), "invalid_harness")))
        except ValueError:
            rejected += 1
            continue
        if pairs[-1][1] not in HARNESSES:
            pairs.pop()
            rejected += 1
    return pairs, rejected
