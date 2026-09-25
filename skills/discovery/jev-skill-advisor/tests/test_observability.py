import json
from datetime import datetime, timedelta, timezone

import pytest

from jev_skill_advisor.observability import append_attempt, append_label, summary
from jev_skill_advisor.usage_cli import _hours


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def test_since_accepts_hours_suffix():
    assert _hours("24h") == 24


def test_summary_separates_delivery_report_and_benefit(tmp_path):
    events = tmp_path / "events.jsonl"
    labels = tmp_path / "labels.jsonl"
    rows = [
        {"timestamp": "2026-09-24T11:00:00Z", "host": "mac", "harness": "codex", "status": "emitted", "usage_id": "r1", "stable_ids": ["warehouse:a"]},
        {"timestamp": "2026-09-24T11:10:00Z", "host": "mac", "harness": "codex", "status": "abstained", "fallback_reason": "catalog_choice_none"},
        {"timestamp": "2026-09-24T11:20:00Z", "host": "mac", "harness": "codex", "status": "fallback", "fallback_reason": "catalog_choice_provider_failure"},
        {"timestamp": "2026-09-24T11:30:00Z", "host": "mac", "harness": "cursor", "status": "complete", "skill_id": "warehouse:b", "usage_id": "r2"},
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    labels.write_text("\n".join(json.dumps(row) for row in [
        {"event": "self_report", "receipt_id": "r1", "label": "partial"},
        {"event": "human_review", "receipt_id": "r1", "label": "same"},
        {"event": "human_review", "receipt_id": "r2", "label": "better"},
    ]) + "\n")
    result = summary([events], label_paths=[labels], expected=["dest:hermes"], now=NOW)
    by_source = {(row["host"], row["harness"]): row for row in result["sources"]}
    assert by_source[("mac", "codex")]["status_counts"] == {"legacy_selected": 1, "legacy_no_selection": 1, "legacy_fallback": 1}
    assert by_source[("mac", "cursor")]["status_counts"] == {"legacy_selected": 1}
    assert by_source[("mac", "cursor")]["status_rates"] is None
    assert by_source[("mac", "cursor")]["measurement_coverage"] == "legacy_or_mixed_success_biased"
    assert by_source[("dest", "hermes")]["silent_over_24h"] is True
    assert result["evidence"]["delivered_direct"] == 0
    assert result["evidence"]["reported_followthrough_self_report"] == {}
    assert result["evidence"]["reviewed_benefit_human"] == {}


def test_attempt_and_label_allowlist_do_not_store_payload(tmp_path):
    events = tmp_path / "events.jsonl"
    assert append_attempt(events, harness="pi", status="fallback", reason="provider_failure", host="mac")
    assert append_label(events, receipt_id="r1", label="followed", evidence="self_report")
    with pytest.raises(ValueError):
        append_label(events, receipt_id="r1", label="SECRET BODY", evidence="self_report")
    assert "SECRET BODY" not in events.read_text()
    assert len(events.read_text().splitlines()) == 2
    report = summary([events], now=datetime.now(timezone.utc))
    assert report["sources"][0]["measurement_coverage"] == "complete_attempt_schema"
    assert report["sources"][0]["status_rates"] == {"fallback": 1.0}


def test_new_delivery_is_direct_but_legacy_failure_is_not(tmp_path):
    now = datetime.now(timezone.utc)
    events = tmp_path / "codex-adapter" / "events.jsonl"
    events.parent.mkdir()
    events.write_text(json.dumps({"timestamp": (now - timedelta(hours=1)).isoformat(), "host": "mac", "status": "fallback",
                                  "fallback_reason": "Exception", "stable_ids": ["warehouse:a"],
                                  "prompt": "SECRET PROMPT", "body": "SECRET BODY"}) + "\n")
    assert append_attempt(events, harness="codex", status="emitted", skill_ids=["warehouse:a"],
                          hashes={"warehouse:a": "abc"}, receipt_id="r1", host="mac")
    report = summary([events], now=now)
    assert report["evidence"]["delivered_direct"] == 1
    assert report["evidence"]["reported_followthrough_self_report"] == {"unreported": 1}
    assert report["sources"][0]["status_rates"] is None
    assert "SECRET PROMPT" not in json.dumps(report)
    assert "SECRET BODY" not in json.dumps(report)
