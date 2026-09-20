"""Versioned harness-neutral protocol validation."""
from __future__ import annotations

PROTOCOL_VERSION = 1
SUGGEST_FIELDS = {"protocol_version", "request_id", "session_id", "task", "context", "explicit_skills", "available_ids"}
READ_FIELDS = {"protocol_version", "session_id", "receipt_id", "skill_id", "expected_content_hash"}
OUTCOME_FIELDS = {"protocol_version", "session_id", "receipt_id", "event_id", "skill_id", "outcome", "evidence", "reason_code"}


class ProtocolError(ValueError):
    pass


def _object(value, allowed):
    if not isinstance(value, dict):
        raise ProtocolError("input_not_object")
    unknown = set(value) - allowed
    if unknown:
        raise ProtocolError("unknown_fields:" + ",".join(sorted(unknown)))
    if type(value.get("protocol_version")) is not int or value.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_protocol_version")


def _text(value, name, low=1, high=128):
    if not isinstance(value, str) or not low <= len(value.encode("utf-8")) <= high:
        raise ProtocolError(f"invalid_{name}")
    return value


def _list(value, name, limit):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(x, str) or not x or len(x) > 256 for x in value):
        raise ProtocolError(f"invalid_{name}")
    return value


def validate_suggest(value):
    _object(value, SUGGEST_FIELDS)
    return {
        "protocol_version": 1,
        "request_id": _text(value.get("request_id"), "request_id"),
        "session_id": _text(value.get("session_id"), "session_id"),
        "task": _text(value.get("task"), "task", high=8000),
        "context": _text(value.get("context", ""), "context", low=0, high=2000),
        "explicit_skills": _list(value.get("explicit_skills"), "explicit_skills", 5),
        "available_ids": None if "available_ids" not in value else _list(value.get("available_ids"), "available_ids", 256),
    }


def validate_read(value):
    _object(value, READ_FIELDS)
    return {key: _text(value.get(key), key, high=256) for key in ("session_id", "receipt_id", "skill_id", "expected_content_hash")} | {"protocol_version": 1}


def validate_outcome(value):
    _object(value, OUTCOME_FIELDS)
    result = {key: _text(value.get(key), key, high=256) for key in ("session_id", "receipt_id", "event_id", "skill_id")}
    if value.get("outcome") not in {"read", "applied", "dismissed", "blocked"}:
        raise ProtocolError("invalid_outcome")
    if value.get("evidence") not in {"host_observed", "self_reported"}:
        raise ProtocolError("invalid_evidence")
    reason = value.get("reason_code")
    if reason is not None:
        _text(reason, "reason_code", high=128)
    return {"protocol_version": 1, **result, "outcome": value["outcome"], "evidence": value["evidence"], "reason_code": reason}


def error(code, message=None):
    return {"protocol_version": 1, "status": "error", "error": {"code": code[:128], "message": (message or code)[:400]}}
