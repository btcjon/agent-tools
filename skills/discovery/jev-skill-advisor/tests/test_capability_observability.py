import json
from datetime import datetime, timezone

import pytest

from jev_skill_advisor.capability_observability import (
    append_capability_event,
    fallback_reason_for_code,
    summary,
)

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
