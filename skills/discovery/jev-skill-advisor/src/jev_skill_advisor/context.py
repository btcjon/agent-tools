"""Shared pre-model context preparation contract."""
from __future__ import annotations

import uuid
import re
import sqlite3
from pathlib import Path

FIELDS = {"task", "harness", "session_id", "available_ids", "explicit_skills", "max_body_bytes"}


def native_fallback(reason="advisor_failure"):
    return {"protocol_version": 1, "status": "unavailable", "reason": reason, "receipt_id": None,
            "selected_ids": [], "skills": [], "body_bytes": 0, "telemetry": {}, "fallback": "native_discovery"}


def validate_prepare_request(value):
    if not isinstance(value, dict) or set(value) - FIELDS or not {"task", "harness", "session_id"} <= set(value):
        raise ValueError("invalid_prepare_context_fields")
    if any(not isinstance(value[key], str) or not value[key].strip() for key in ("task", "harness", "session_id")):
        raise ValueError("invalid_prepare_context_text")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value["harness"]):
        raise ValueError("invalid_prepare_context_harness")
    for key in ("available_ids", "explicit_skills"):
        items = value.get(key)
        if items is not None and (not isinstance(items, list) or any(not isinstance(item, str) or not item for item in items)):
            raise ValueError("invalid_prepare_context_ids")
    budget = value.get("max_body_bytes", 32768)
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 32768:
        raise ValueError("invalid_prepare_context_budget")
    return {**value, "max_body_bytes": budget}


def prepare_context(service, *, task, harness, session_id, available_ids=None,
                    explicit_skills=None, max_body_bytes=32768):
    validated = validate_prepare_request({"task": task, "harness": harness, "session_id": session_id,
        "available_ids": available_ids, "explicit_skills": explicit_skills, "max_body_bytes": max_body_bytes})
    task, harness, session_id, max_body_bytes = (validated[key] for key in ("task", "harness", "session_id", "max_body_bytes"))
    request = {"protocol_version": 1, "request_id": f"prepare-{uuid.uuid4().hex}",
               "session_id": session_id, "task": task,
               "context": f"harness={harness}", "explicit_skills": explicit_skills or []}
    if available_ids is not None:
        request["available_ids"] = available_ids
    try:
        suggestion = service.suggest(request)
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        return native_fallback()
    result = {"protocol_version": 1, "status": suggestion["status"],
              "reason": suggestion["reason"], "receipt_id": suggestion["receipt_id"],
              "catalog_hash": suggestion.get("catalog_hash"), "policy_hash": suggestion.get("policy_hash"),
              "selected_ids": [], "skills": [], "body_bytes": 0,
              "telemetry": suggestion.get("telemetry", {}), "fallback": None}
    if suggestion["status"] not in {"suggested", "explicit_selection"}:
        result["fallback"] = "native_discovery"
        return result
    seen = set()
    for card in suggestion.get("selected", []):
        sid = card["id"]
        if sid in seen:
            continue
        seen.add(sid)
        try:
            read = service.read({"protocol_version": 1, "session_id": session_id,
                                 "receipt_id": suggestion["receipt_id"], "skill_id": sid,
                                 "expected_content_hash": card["content_hash"]})
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            read = {"status": "unavailable"}
        if read.get("status") != "read" or result["body_bytes"] + read["body_bytes"] > max_body_bytes:
            result.update(status="unavailable", reason="skill_body_unavailable", selected_ids=[], skills=[], body_bytes=0,
                          fallback="native_discovery")
            return result
        result["selected_ids"].append(sid)
        result["skills"].append({"id": sid, "name": card["name"], "content_hash": read["content_hash"],
                                 "body": read["body"], "body_bytes": read["body_bytes"],
                                 "canonical_path": read["canonical_path"],
                                 "package_root": read["package_root"]})
        result["body_bytes"] += read["body_bytes"]
    if not result["skills"]:
        result.update(status="none", reason="no_readable_selection", fallback="native_discovery")
    return result
