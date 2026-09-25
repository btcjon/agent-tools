import json
from pathlib import Path

from jev_skill_advisor.usage import ingest, load_ledger, parse_receipt_line, reclassify, record_cursor_selection, summarize
from jev_skill_advisor.usage_cli import page_index, skill_pages, stable_id_from_markdown, usage_properties


def test_old_receipt_counts_without_inventing_a_date():
    raw = json.dumps({"skill_id": "warehouse:tailscale", "hash": "abc", "bytes": 10})
    rows = parse_receipt_line(raw)
    assert len(rows) == 1
    assert rows[0]["skill_id"] == "warehouse:tailscale"
    assert rows[0]["selected_at"] is None
    again = parse_receipt_line(raw)
    assert again[0]["usage_id"] == rows[0]["usage_id"]


def test_fallback_and_empty_lines_are_ignored():
    assert parse_receipt_line(json.dumps({"status": "fallback", "stable_ids": ["warehouse:tailscale"]})) == []
    assert parse_receipt_line("not json") == []
    assert parse_receipt_line(json.dumps({"status": "emitted", "stable_ids": []})) == []


def test_reimport_and_partial_host_keep_the_ledger(tmp_path):
    ledger = tmp_path / "ledger.json"
    mac = tmp_path / "mac.jsonl"
    dest = tmp_path / "dest.jsonl"
    mac.write_text(json.dumps({
        "usage_id": "mac-1", "adapter": "codex", "harness": "codex", "skill_id": "warehouse:alpha",
        "selected_at": "2026-09-22T15:00:00Z", "host": "mac",
        "session_id": "session-mac", "request_id": "turn-1", "release_id": "release-a",
        "snapshot_id": "snapshot-a", "provenance": "adapter_event",
    }) + "\n")
    dest.write_text(json.dumps({
        "usage_id": "dest-1", "adapter": "hermes", "skill_id": "warehouse:alpha",
        "selected_at": "2026-09-22T16:00:00Z", "host": "dest",
        "session_id": "session-dest", "request_id": "turn-2",
    }) + "\n")
    first = ingest([mac, dest], ledger)
    second = ingest([mac], ledger)
    loaded = load_ledger(ledger)
    totals = summarize(loaded)
    assert first["added"] == 2
    assert second["added"] == 0
    assert totals["skills"]["warehouse:alpha"] == {"count": 2, "last_selected": "2026-09-22T16:00:00Z"}
    assert set(loaded["receipts"]) == {"mac-1", "dest-1"}
    assert loaded["receipts"]["mac-1"]["session_id"] == "session-mac"
    assert loaded["receipts"]["mac-1"]["request_id"] == "turn-1"
    assert loaded["receipts"]["mac-1"]["harness"] == "codex"
    assert loaded["receipts"]["mac-1"]["release_id"] == "release-a"
    assert loaded["receipts"]["mac-1"]["snapshot_id"] == "snapshot-a"
    assert loaded["receipts"]["dest-1"]["harness"] == "hermes"
    assert totals["freshness"]["imported_at"].endswith("Z")


def test_repeat_import_reclassifies_without_deleting(tmp_path):
    ledger = tmp_path / "ledger.json"
    source = tmp_path / "events.jsonl"
    source.write_text(json.dumps({
        "usage_id": "old-1", "adapter": "hermes", "skill_id": "warehouse:alpha",
        "session_id": "session-only", "snapshot_id": "snapshot-a",
    }) + "\n")
    first = ingest([source], ledger)
    assert first["separated_unattributable"] == 1
    source.write_text(json.dumps({
        "usage_id": "old-1", "adapter": "hermes", "skill_id": "warehouse:alpha",
        "session_id": "session-only", "request_id": "turn-9", "snapshot_id": "snapshot-a",
        "release_id": "release-b",
    }) + "\n")
    second = ingest([source], ledger)
    loaded = load_ledger(ledger)
    assert second["added"] == 1
    assert "old-1" not in loaded["unattributable"]
    assert loaded["receipts"]["old-1"]["request_id"] == "turn-9"
    assert loaded["receipts"]["old-1"]["release_id"] == "release-b"
    assert loaded["receipts"]["old-1"]["snapshot_id"] == "snapshot-a"
    again = ingest([source], ledger)
    assert again["added"] == 0
    assert again["skipped"] == 1


def test_cursor_receipt_writes_success_and_null_attempt_without_body(tmp_path):
    path = tmp_path / "events.jsonl"
    assert record_cursor_selection({"selected": None, "reason": "catalog_choice_none", "detail": "SECRET PROVIDER PAYLOAD"}, path) is True
    assert record_cursor_selection({
        "status": "complete",
        "selected": {"skill_id": "warehouse:alpha", "snapshot_id": "snap", "hash": "abc", "bytes": 4, "content": "SECRET BODY"},
    }, path) is True
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert lines[0]["status"] == "abstained"
    assert lines[0]["timestamp"].endswith("Z")
    line = lines[1]
    assert line["adapter"] == "cursor"
    assert line["harness"] == "cursor"
    assert line["skill_id"] == "warehouse:alpha"
    assert line["provenance"] == "cursor_cli_receipt"
    assert "release_id" not in line
    assert "content" not in line
    assert "SECRET" not in path.read_text()
    assert line["selected_at"].endswith("Z")


def test_page_join_uses_catalog_names():
    pages, ambiguous = page_index([
        {"id": "page-a", "properties": {"Skill name": {"title": [{"plain_text": "alpha"}]}}},
        {"id": "page-b", "properties": {"Skill name": {"title": [{"plain_text": "beta"}]}}},
        {"id": "page-c", "properties": {"Skill name": {"title": [{"plain_text": "beta"}]}}},
    ])
    joined = skill_pages({"entries": [
        {"name": "alpha", "stable_id": "warehouse:alpha"},
        {"name": "beta", "stable_id": "warehouse:beta"},
    ]}, pages, {"warehouse:beta": "page-b"})
    assert pages == {"alpha": "page-a"}
    assert ambiguous == {"beta": ["page-b", "page-c"]}
    assert joined["warehouse:alpha"] == "page-a"
    assert joined["warehouse:beta"] == "page-b"
    marker = '## Managed skill identity\n```json\n{"stable_id":"warehouse:plan"}\n```'
    assert stable_id_from_markdown(marker) == "warehouse:plan"
    props = usage_properties(2, None)
    assert props["Selection count"]["number"] == 2
    assert "Last selected" not in props
