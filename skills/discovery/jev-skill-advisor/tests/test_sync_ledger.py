import os
import sqlite3

import pytest

from jev_skill_advisor.sync_ledger import SyncLedger


def test_intent_attempt_result_and_idempotent_upsert(tmp_path):
    ledger = SyncLedger(tmp_path / "private" / "sync.sqlite3")
    ledger.create_run("run-1", source_snapshot_id="snapshot-1")
    first = ledger.checkpoint_intent("run-1", "shared:alpha", "upsert", "sha256:a")
    second = ledger.checkpoint_intent("run-1", "shared:alpha", "upsert", "sha256:a", page_id="page-1")
    assert first["status"] == second["status"] == "pending"
    assert second["page_id"] == "page-1"
    assert ledger.record_attempt("run-1", "shared:alpha", "upsert")["attempt_count"] == 1
    result = ledger.record_result("run-1", "shared:alpha", "upsert", upload_id="upload-1",
                                  result_id="version-1", remote_hash="sha256:a")
    assert result["status"] == "complete"
    assert result["upload_id"] == "upload-1"
    assert ledger.resumable_operations("run-1") == []
    assert ledger.finish_run("run-1", "complete")["status"] == "complete"
    assert os.stat(ledger.path).st_mode & 0o777 == 0o600


def test_resume_pending_in_progress_and_due_retry_only(tmp_path):
    ledger = SyncLedger(tmp_path / "sync.sqlite3")
    ledger.create_run("run")
    for skill in ("pending", "active", "due", "later", "fatal"):
        ledger.checkpoint_intent("run", skill, "upsert", f"hash:{skill}")
    ledger.record_attempt("run", "active", "upsert")
    ledger.record_failure("run", "due", "upsert", "rate_limited", retryable=True,
                          next_retry_at="2026-01-01T00:00:00Z")
    ledger.record_failure("run", "later", "upsert", "rate_limited", retryable=True,
                          next_retry_at="2099-01-01T00:00:00Z")
    ledger.record_failure("run", "fatal", "upsert", "permission_denied", retryable=False)
    assert [row["skill_id"] for row in ledger.resumable_operations(
        "run", as_of="2026-06-01T00:00:00Z"
    )] == ["active", "due", "pending"]


def test_operation_requires_prior_intent_and_retry_timestamp(tmp_path):
    ledger = SyncLedger(tmp_path / "sync.sqlite3")
    ledger.create_run("run")
    with pytest.raises(KeyError, match="operation_not_checkpointed"):
        ledger.record_attempt("run", "missing", "upsert")
    ledger.checkpoint_intent("run", "shared:alpha", "upsert", "hash")
    with pytest.raises(ValueError, match="next_retry_at_required"):
        ledger.record_failure("run", "shared:alpha", "upsert", "timeout", retryable=True)
    assert ledger.operation("run","shared:alpha","upsert")["status"]=="pending"
    with pytest.raises(ValueError,match="operation_intent_mismatch"):
        ledger.checkpoint_intent("run","shared:alpha","upsert","different")


def test_schema_version_is_strict_and_database_has_no_content_columns(tmp_path):
    path = tmp_path / "sync.sqlite3"
    SyncLedger(path)
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(sync_operations)")}
        assert not columns & {"prompt", "body", "content", "signed_url", "url"}
        db.execute("INSERT INTO sync_schema_versions(version, applied_at) VALUES(999, 'now')")
    with pytest.raises(RuntimeError, match="unsupported_sync_ledger_schema"):
        SyncLedger(path)


@pytest.mark.parametrize("field_value", ["https://example.test/file", "id?X-Amz-Signature=secret"])
def test_external_ids_reject_urls(field_value, tmp_path):
    ledger = SyncLedger(tmp_path / "sync.sqlite3")
    ledger.create_run("run")
    ledger.checkpoint_intent("run", "skill", "upsert", "hash")
    with pytest.raises(ValueError, match="url_not_allowed"):
        ledger.record_result("run", "skill", "upsert", upload_id=field_value)


def test_incomplete_operations_are_scoped_to_bound_snapshot(tmp_path):
    ledger=SyncLedger(tmp_path/"sync.sqlite3")
    ledger.create_run("one",source_snapshot_id="binding-a",destination_id="destination-a"); ledger.create_run("two",source_snapshot_id="binding-b",destination_id="destination-b")
    ledger.checkpoint_intent("one","skill","create","hash"); ledger.record_attempt("one","skill","create")
    ledger.checkpoint_intent("two","skill","create","hash"); ledger.record_attempt("two","skill","create")
    assert [row["run_id"] for row in ledger.incomplete_skill_operations("skill","create","destination-a")]==["one"]


def test_matching_failed_run_is_reopened_truthfully(tmp_path):
    ledger=SyncLedger(tmp_path/"sync.sqlite3")
    ledger.create_run("run",source_snapshot_id="binding",destination_id="destination")
    ledger.finish_run("run","failed")
    resumed=ledger.create_run("run",source_snapshot_id="binding",destination_id="destination")
    assert resumed["status"]=="running" and resumed["finished_at"] is None
