import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jev_skill_advisor.capability_observability import (
    ROUTE_CORRELATION_NOTE,
    ROUTE_DEDUPE_RULE,
    ROUTE_EXCLUSION_NOTE,
    ROUTE_INFERENCE,
    append_capability_event,
    fallback_reason_for_code,
    summary,
)

HASH = "a" * 64
PAGE_ID = "0123456789abcdef0123456789abcdef"

SECRET_PROMPT = "SECRET PROMPT alpha-phrase"
SECRET_BODY = "SECRET SKILL BODY beta-phrase"
SECRET_ARGS = "SECRET ARGUMENTS gamma-phrase"
SECRET_OAUTH = "Bearer SECRET-OAUTH delta-phrase"
SECRET_EXC = "Traceback SECRET EXCEPTION epsilon-phrase sk-live-abc123"
SECRETS = (SECRET_PROMPT, SECRET_BODY, SECRET_ARGS, SECRET_OAUTH, SECRET_EXC)


def _assert_hidden(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


def test_payload_cannot_leak(tmp_path):
    path = tmp_path / "events.jsonl"
    assert append_capability_event(None, harness="codex", stage="discovery", outcome="absent",
                                   latency_ms=1, context_bytes=4, host="mac") is False
    assert not path.exists()
    with pytest.raises(TypeError) as extra:
        append_capability_event(path, harness="codex", stage="discovery", outcome="absent",
                                latency_ms=1, context_bytes=4, host="mac", prompt=SECRET_PROMPT,
                                schema={"type": SECRET_BODY}, arguments={"q": SECRET_ARGS})
    _assert_hidden(str(extra.value))
    rejected = [
        dict(harness="codex", stage="discovery", outcome="selected", latency_ms=1, context_bytes=1,
             host="mac", capability_ids=[SECRET_PROMPT]),
        dict(harness="codex", stage="discovery", outcome="absent", latency_ms=1, context_bytes=1,
             host="mac", receipt_id=SECRET_OAUTH),
        dict(harness="codex", stage="discovery", outcome="absent", latency_ms=1, context_bytes=1,
             host="mac", session_id=SECRET_BODY),
        dict(harness="codex", stage="fallback", outcome="failed", latency_ms=1, host="mac",
             capability_ids=["notion.mcp.fetch"], fallback_reason=SECRET_EXC),
    ]
    for kwargs in rejected:
        with pytest.raises(ValueError) as caught:
            append_capability_event(path, **kwargs)
        _assert_hidden(str(caught.value))
    assert not path.exists()
    assert fallback_reason_for_code(SECRET_EXC) == "other"
    assert fallback_reason_for_code("write_unapproved") == "approval-denied"
    assert fallback_reason_for_code("stale_schema") == "schema-drift"
    assert fallback_reason_for_code("unauthorized_capability") == "auth"
    assert fallback_reason_for_code("missing_tool") == "unsupported-operation"
    assert fallback_reason_for_code("call_unavailable") == "provider-unavailable"
    _assert_hidden(fallback_reason_for_code(SECRET_EXC))
    assert append_capability_event(path, harness="codex", stage="invoke", outcome="success",
                                   latency_ms=3, host="mac", capability_ids=["notion.mcp.fetch"],
                                   receipt_id="receipt-1")
    stored = path.read_text(encoding="utf-8")
    _assert_hidden(stored)
    row = json.loads(stored)
    assert set(row) <= {
        "event_schema", "event", "timestamp", "host", "harness", "capability_ids",
        "receipt_id", "session_id", "stage", "outcome", "latency_ms",
        "context_bytes", "schema_bytes", "fallback_reason",
    }
    for banned in ("prompt", "schema", "arguments", "oauth", "skill_body", "exception", "error", "body"):
        assert banned not in row
    path.write_text(stored + json.dumps({
        "event": "capability_path", "event_schema": 1, "stage": "invoke", "outcome": "success",
        "harness": "codex", "host": "mac", "capability_ids": ["notion.mcp.fetch"],
        "timestamp": "2026-09-24T12:00:00Z", "latency_ms": 1,
        "prompt": SECRET_PROMPT, "schema": {"input": SECRET_BODY}, "arguments": {"q": SECRET_ARGS},
        "oauth": SECRET_OAUTH, "error": SECRET_EXC,
    }) + "\n", encoding="utf-8")
    report = summary([path], expected=["mac:pi", "mac:" + SECRET_PROMPT], now=datetime.now(timezone.utc))
    _assert_hidden(json.dumps(report))
    assert report["rejected_rows"] == 1
    assert report["coverage"]["expected_rejected"] == 1
    assert report["selection"]["invoked_ids"] == ["notion.mcp.fetch"]


def test_native_route_is_allowlisted_and_never_logs_tool_payload(tmp_path):
    path = tmp_path / "native.jsonl"
    base = dict(
        harness="hermes", stage="invoke", outcome="success", latency_ms=4,
        host="dest-host", capability_ids=["notion.mcp.fetch"],
        receipt_id="receipt-1", session_id="session-1",
        manifest_hash="a" * 64, status="success", reason="native", route="native",
    )
    assert append_capability_event(path, **base)
    row = json.loads(path.read_text().splitlines()[0])
    assert row["route"] == "native" and row["reason"] == "native"
    rejected = [
        {**base, "route": SECRET_PROMPT},
        {**base, "route": "bridge"},
        {**base, "route": None},
        {**base, "stage": "discovery", "outcome": "selected", "context_bytes": 0},
    ]
    for item in rejected:
        with pytest.raises(ValueError):
            append_capability_event(path, **item)
    assert len(path.read_text().splitlines()) == 1
    _assert_hidden(path.read_text())


def test_malformed_events_rejected(tmp_path):
    path = tmp_path / "events.jsonl"
    base = dict(harness="codex", stage="discovery", outcome="absent", latency_ms=1, context_bytes=0, host="mac")
    cases = [
        {**base, "stage": "execute"},
        {**base, "outcome": "success"},
        {**base, "fallback_reason": "auth"},
        {**base, "latency_ms": True},
        {**base, "context_bytes": -1},
        {**base, "schema_bytes": 1.5},
        dict(harness="codex", stage="fallback", outcome="failed", latency_ms=1, host="mac"),
        dict(harness="codex", stage="invoke", outcome="success", latency_ms=1, host="mac",
             capability_ids=["notion.mcp.fetch", "notion.mcp.search"]),
        dict(harness="grok", stage="discovery", outcome="absent", latency_ms=1, context_bytes=0, host="mac"),
    ]
    for kwargs in cases:
        with pytest.raises(ValueError):
            append_capability_event(path, **kwargs)
    assert not path.exists()
    assert append_capability_event(path, **base, receipt_id="receipt-1", session_id="session-1")
    path.write_text("\n".join([
        path.read_text(encoding="utf-8").rstrip("\n"),
        json.dumps({"event": "selection_attempt", "prompt": SECRET_PROMPT}),
        json.dumps({"event": "capability_path", "event_schema": 1, "prompt": SECRET_BODY}),
        "not-json " + SECRET_ARGS,
        "",
    ]) + "\n", encoding="utf-8")
    report = summary([path], now=datetime.now(timezone.utc))
    _assert_hidden(json.dumps(report))
    assert report["rejected_rows"] == 2
    assert report["counts"]["events"] == 1
    assert report["counts"]["stage"]["discovery"] == 1
    assert report["sources"][0]["measurement_coverage"] == "complete"


def test_summary_separates_selection_from_invocation(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({
        "event_schema": 1, "event": "capability_path", "timestamp": "2020-01-01T00:00:00Z",
        "host": "mac", "harness": "codex", "capability_ids": ["notion.mcp.get-comments"],
        "stage": "discovery", "outcome": "selected", "latency_ms": 999, "context_bytes": 999,
        "receipt_id": "old-receipt",
    }) + "\n", encoding="utf-8")
    rows = [
        dict(harness="codex", stage="discovery", outcome="selected", latency_ms=10, context_bytes=100,
             host="mac", capability_ids=["notion.mcp.fetch", "notion.mcp.search"],
             receipt_id="receipt-1", session_id="session-1"),
        dict(harness="codex", stage="describe", outcome="success", latency_ms=20, schema_bytes=250,
             host="mac", capability_ids=["notion.mcp.fetch"], receipt_id="receipt-1", session_id="session-1"),
        dict(harness="codex", stage="invoke", outcome="success", latency_ms=40, host="mac",
             capability_ids=["notion.mcp.fetch"], receipt_id="receipt-1", session_id="session-1"),
        dict(harness="codex", stage="invoke", outcome="failed", latency_ms=5, host="mac",
             capability_ids=["notion.mcp.search"], receipt_id="receipt-1"),
        dict(harness="codex", stage="fallback", outcome="failed", latency_ms=1, schema_bytes=250,
             host="mac", capability_ids=["notion.mcp.search"], fallback_reason="schema-drift",
             receipt_id="receipt-1"),
        dict(harness="hermes", stage="discovery", outcome="absent", latency_ms=2, context_bytes=0, host="mac"),
    ]
    for row in rows:
        assert append_capability_event(path, **row)
    report = summary([path], expected=["mac:pi", "mac:cursor"], now=datetime.now(timezone.utc))
    by_source = {(row["host"], row["harness"]): row for row in report["sources"]}
    assert by_source[("mac", "codex")]["stage_counts"] == {
        "discovery": 1, "describe": 1, "invoke": 2, "fallback": 1}
    assert by_source[("mac", "codex")]["selected_ids"] == ["notion.mcp.fetch", "notion.mcp.search"]
    assert by_source[("mac", "codex")]["invoked_ids"] == ["notion.mcp.fetch"]
    assert by_source[("mac", "codex")]["selected_without_invocation"] == ["notion.mcp.search"]
    assert by_source[("mac", "codex")]["fallback_reasons"] == {"schema-drift": 1}
    assert by_source[("mac", "codex")]["measurement_coverage"] == "complete"
    assert by_source[("mac", "hermes")]["stage_counts"]["discovery"] == 1
    assert by_source[("mac", "hermes")]["measurement_coverage"] == "partial"
    assert by_source[("mac", "hermes")]["missing_correlation"] == 1
    assert report["selection"] == {
        "selected_ids": ["notion.mcp.fetch", "notion.mcp.search"],
        "invoked_ids": ["notion.mcp.fetch"],
        "selected_without_invocation": ["notion.mcp.search"],
    }
    assert "notion.mcp.get-comments" not in json.dumps(report["selection"])
    assert report["counts"]["describe_success"] == 1
    assert report["counts"]["invoke_success"] == 1
    assert report["counts"]["fallback_reason"] == {"schema-drift": 1}
    assert report["latency_ms"] == {"events": 6, "sum": 78, "max": 40}
    assert report["context_bytes"] == {"events": 2, "sum": 100, "max": 100}
    assert report["schema_bytes"] == {"events": 2, "sum": 500, "max": 250}
    assert report["absence"] == [
        {"host": "mac", "harness": "cursor", "events": 0, "measurement_coverage": "no_rows"},
        {"host": "mac", "harness": "pi", "events": 0, "measurement_coverage": "no_rows"},
    ]
    assert report["coverage"]["with_correlation"] == 5
    assert report["coverage"]["discovery_with_context_bytes"] == 2
    assert report["coverage"]["describe_with_schema_bytes"] == 1
    assert report["coverage"]["absent_sources"] == 2
    assert report["rejected_rows"] == 0
    routes = report["invocation_routes"]
    assert routes["successful_invokes"]["raw"]["unknown"] == 1
    assert routes["successful_invokes"]["raw"]["bridge"] == 0
    assert routes["successful_invokes"]["raw"]["native"] == 0
    assert routes["inference"] == ROUTE_INFERENCE


def _walk_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str)
            yield key
            yield from _walk_strings(item)
    elif isinstance(value, str):
        yield value
    elif isinstance(value, int) and not isinstance(value, bool):
        return
    else:
        raise AssertionError(type(value))


def _append(path, **kwargs):
    assert append_capability_event(path, host="mac", latency_ms=1, **kwargs)


def test_invocation_routes_report_both_routes_duplicates_and_exclusions(tmp_path):
    path = tmp_path / "events.jsonl"
    fetch = ["notion.mcp.fetch"]
    bridge = dict(status="success", reason="bridge_read", manifest_hash=HASH)
    native = dict(status="success", reason="native", route="native", manifest_hash=HASH)
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=fetch, session_id="b1-session", receipt_id="b1-receipt")
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=fetch, session_id="b1-session", receipt_id="b1-receipt")
    for receipt in ("b1-receipt", "b1-receipt", "b1-r2"):
        _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=fetch,
                session_id="b1-session", receipt_id=receipt, **bridge)
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=["notion.mcp.search"],
            session_id="b1-session", receipt_id="b1-search", **bridge)
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=["notion.mcp.fetch", "notion.mcp.search"], session_id="n1-session",
            receipt_id="n1-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=fetch,
            session_id="n1-session", receipt_id="n1-receipt", **native)
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=fetch, session_id="idle-session", receipt_id="idle-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="failed", capability_ids=fetch,
            session_id="idle-session", receipt_id="idle-receipt", manifest_hash=HASH,
            status="denied", reason="other", route="native")
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=fetch, session_id="both-session", receipt_id="both-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=fetch,
            session_id="both-session", receipt_id="both-receipt", **bridge)
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=fetch,
            session_id="both-session", receipt_id="both-receipt", **native)
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=fetch, session_id="unk-session", receipt_id="unk-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=fetch,
            session_id="unk-session", receipt_id="unk-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=fetch,
            session_id="unk-session")
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=fetch)
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=4,
            capability_ids=fetch, session_id="x1-session", receipt_id="x1-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=fetch,
            session_id="x1-session", receipt_id="x1-receipt", **native)
    stored = path.read_text(encoding="utf-8")
    path.write_text(stored + "\n".join([
        json.dumps({
            "event_schema": 1, "event": "capability_path", "timestamp": "2020-01-01T00:00:00Z",
            "host": "mac", "harness": "hermes", "capability_ids": ["notion.mcp.get-comments"],
            "stage": "invoke", "outcome": "success", "latency_ms": 1,
            "receipt_id": "old-receipt", "session_id": "old-session", "manifest_hash": HASH,
            "status": "success", "reason": "native", "route": "native",
        }),
        json.dumps({
            "event": "capability_path", "event_schema": 1, "stage": "invoke", "outcome": "success",
            "harness": "hermes", "host": "mac", "capability_ids": fetch, "latency_ms": 1,
            "timestamp": "2026-09-25T00:00:00Z", "status": "success", "reason": "bridge_read",
            "route": "native", "manifest_hash": HASH, "page_id": PAGE_ID,
            "arguments": {"q": SECRET_ARGS}, "error": SECRET_EXC, "task": SECRET_PROMPT,
        }),
        "not-json " + SECRET_BODY,
        json.dumps({"event": "selection_attempt", "task": SECRET_PROMPT, "page_id": PAGE_ID}),
        "",
    ]) + "\n", encoding="utf-8")
    now = datetime.now(timezone.utc)
    excluded = ["x1-session", "x1-session", "n1"]
    report = summary([path, tmp_path / "missing.jsonl"], now=now, exclude_session_ids=excluded)
    again = summary([tmp_path / "missing.jsonl", path], now=now, exclude_session_ids=["n1", "x1-session"])
    assert report == again
    assert report["counts"]["events"] == 19
    assert report["counts"]["invoke_success"] == 10
    assert report["rejected_rows"] == 2
    assert report["selection"]["invoked_ids"] == ["notion.mcp.fetch", "notion.mcp.search"]
    assert "notion.mcp.get-comments" not in report["selection"]["invoked_ids"]
    untouched = summary([path], now=now)
    assert untouched["counts"] == report["counts"]
    assert untouched["selection"] == report["selection"]
    routes = report["invocation_routes"]
    assert routes["excluded_session_id_count"] == 2
    assert routes["excluded_events"] == 2
    assert routes["denominator"] == {
        "rows_after_exclusion": 17,
        "successful_invokes_raw": 9,
        "successful_invokes_deduped": 8,
        "selected_sessions": 5,
        "selected_session_capabilities": 6,
        "selected_discovery_events_without_session_id": 1,
        "selected_discovery_events_without_receipt_id": 0,
    }
    assert routes["successful_invokes"] == {
        "raw": {"bridge": 5, "native": 2, "unknown": 2, "total": 9},
        "deduped": {"bridge": 4, "native": 2, "unknown": 2, "total": 8},
    }
    assert routes["selected_sessions"]["denominator"] == 5
    assert routes["selected_sessions"]["with_bridge_success"] == 2
    assert routes["selected_sessions"]["with_native_success"] == 2
    assert routes["selected_sessions"]["with_unknown_success"] == 1
    assert routes["selected_sessions"]["with_any_success"] == 4
    assert routes["selected_sessions"]["without_invocation"] == 1
    assert sum(routes["selected_sessions"]["partition"].values()) == 5
    assert routes["selected_sessions"]["partition"]["bridge"] == 1
    assert routes["selected_sessions"]["partition"]["native"] == 1
    assert routes["selected_sessions"]["partition"]["unknown"] == 1
    assert routes["selected_sessions"]["partition"]["bridge+native"] == 1
    assert routes["selected_sessions"]["partition"]["without_invocation"] == 1
    assert routes["selected_session_capabilities"]["denominator"] == 6
    assert routes["selected_session_capabilities"]["without_invocation"] == 2
    assert routes["selected_session_capabilities"]["partition"]["bridge+native"] == 1
    assert sum(routes["selected_session_capabilities"]["partition"].values()) == 6
    assert routes["uncorrelated_successful_invokes_raw"] == 3
    assert untouched["invocation_routes"]["successful_invokes"]["raw"]["native"] == 3
    rendered = json.dumps(routes)
    for secret in (*SECRETS, PAGE_ID, "b1-session", "x1-session", "old-session", "b1-r1", "old-receipt", HASH):
        assert secret not in rendered
    assert set(item for item in _walk_strings(routes) if item in {
        ROUTE_DEDUPE_RULE, ROUTE_EXCLUSION_NOTE, ROUTE_CORRELATION_NOTE, ROUTE_INFERENCE,
    }) == {ROUTE_DEDUPE_RULE, ROUTE_EXCLUSION_NOTE, ROUTE_CORRELATION_NOTE, ROUTE_INFERENCE}
    for item in _walk_strings(routes):
        if item in {ROUTE_DEDUPE_RULE, ROUTE_EXCLUSION_NOTE, ROUTE_CORRELATION_NOTE, ROUTE_INFERENCE}:
            continue
        assert item.replace("_", "").replace("+", "").isalnum()
    hidden = summary([path], now=now, exclude_session_ids=[SECRET_PROMPT])
    _assert_hidden(json.dumps(hidden["invocation_routes"]))
    with pytest.raises(ValueError) as caught:
        summary([path], exclude_session_ids=["ok", 3])
    _assert_hidden(str(caught.value))
    with pytest.raises(ValueError) as bad_shape:
        summary([path], exclude_session_ids=SECRET_OAUTH)
    _assert_hidden(str(bad_shape.value))


def test_route_report_cli_is_read_only(tmp_path):
    path = tmp_path / "events.jsonl"
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=["notion.mcp.fetch"], session_id="live-session", receipt_id="live-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=["notion.mcp.fetch"],
            session_id="live-session", receipt_id="live-receipt", manifest_hash=HASH,
            status="success", reason="native", route="native")
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=["notion.mcp.fetch"], session_id="test-session", receipt_id="test-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=["notion.mcp.fetch"],
            session_id="test-session", receipt_id="test-receipt", manifest_hash=HASH,
            status="success", reason="bridge_read")
    path.write_text(path.read_text(encoding="utf-8") + json.dumps({
        "event": "capability_path", "page_id": PAGE_ID, "error": SECRET_EXC, "task": SECRET_PROMPT,
    }) + "\n", encoding="utf-8")
    before = path.read_bytes()
    script = Path(__file__).resolve().parents[1] / "scripts" / "capability_route_report.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(script.parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(script), str(path), "--exclude-session-id", "test-session", "--since-hours", "24"],
        check=False, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert path.read_bytes() == before
    payload = json.loads(proc.stdout)
    assert set(payload) == {"window_start", "window_end", "invocation_routes"}
    assert payload["invocation_routes"]["successful_invokes"]["raw"] == {
        "bridge": 0, "native": 1, "unknown": 0, "total": 1,
    }
    assert payload["invocation_routes"]["selected_sessions"]["partition"]["native"] == 1
    assert payload["invocation_routes"]["excluded_events"] == 2
    assert payload["invocation_routes"]["inference"] == ROUTE_INFERENCE
    rendered = proc.stdout
    for secret in (*SECRETS, PAGE_ID, "live-session", "test-session", "live-receipt", HASH):
        assert secret not in rendered
    refused = subprocess.run(
        [sys.executable, str(script), str(path), "--since-hours", "-1"],
        check=False, capture_output=True, text=True, env=env,
    )
    assert refused.returncode == 2
    assert refused.stderr.strip() == "invalid_window"
    assert path.read_bytes() == before


def test_route_correlation_requires_matching_receipt(tmp_path):
    path = tmp_path / "events.jsonl"
    fetch = ["notion.mcp.fetch"]
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=fetch, session_id="same-session", receipt_id="first-receipt")
    _append(path, harness="hermes", stage="invoke", outcome="success", capability_ids=fetch,
            session_id="same-session", receipt_id="later-receipt", manifest_hash=HASH,
            status="success", reason="native", route="native")
    _append(path, harness="hermes", stage="discovery", outcome="selected", context_bytes=0,
            capability_ids=fetch, session_id="no-receipt-session")
    routes = summary([path])["invocation_routes"]
    assert routes["successful_invokes"]["raw"]["native"] == 1
    assert routes["selected_sessions"]["denominator"] == 1
    assert routes["selected_sessions"]["without_invocation"] == 1
    assert routes["uncorrelated_successful_invokes_raw"] == 1
    assert routes["denominator"]["selected_discovery_events_without_receipt_id"] == 1
