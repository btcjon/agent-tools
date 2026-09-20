"""Durable, content-free checkpoints for Notion skill synchronization."""

from __future__ import annotations

import os
import fcntl
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 2
RUN_STATUSES = frozenset({"running", "complete", "failed", "cancelled"})
OPERATION_STATUSES = frozenset({"pending", "in_progress", "retryable", "complete", "failed", "skipped"})
RESUMABLE_STATUSES = ("pending", "in_progress", "retryable")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _identifier(value: str | None, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field}_required")
        return None
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"invalid_{field}")
    lowered = value.lower()
    if "://" in lowered or "x-amz-signature" in lowered or "x-goog-signature" in lowered:
        raise ValueError(f"url_not_allowed_for_{field}")
    return value


class SyncLedger:
    """SQLite ledger whose rows contain identifiers and hashes, never skill content."""

    def __init__(self, path: Path | str, *, timeout: float = 5.0) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self.timeout = timeout
        self._writer_lock = threading.RLock()
        self._initialize()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
        return db

    @contextmanager
    def run_lock(self) -> Iterator[None]:
        lock=self.path.with_suffix(self.path.suffix+".lock")
        with lock.open("a+b") as handle:
            try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc: raise RuntimeError("sync_run_locked") from exc
            try: yield
            finally: fcntl.flock(handle,fcntl.LOCK_UN)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._writer_lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def _initialize(self) -> None:
        with self._write() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sync_schema_versions(
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_runs(
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    source_snapshot_id TEXT,
                    destination_id TEXT,
                    CHECK(status IN ('running','complete','failed','cancelled'))
                );
                CREATE TABLE IF NOT EXISTS sync_operations(
                    run_id TEXT NOT NULL REFERENCES sync_runs(run_id) ON DELETE CASCADE,
                    skill_id TEXT NOT NULL,
                    operation_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    desired_hash TEXT NOT NULL,
                    remote_hash TEXT,
                    page_id TEXT,
                    upload_id TEXT,
                    result_id TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                    next_retry_at TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, skill_id, operation_kind),
                    CHECK(status IN ('pending','in_progress','retryable','complete','failed','skipped'))
                );
                CREATE INDEX IF NOT EXISTS sync_operations_resume
                    ON sync_operations(run_id, status, next_retry_at, skill_id);
                """
            )
            versions = [row[0] for row in db.execute("SELECT version FROM sync_schema_versions ORDER BY version")]
            columns={row[1] for row in db.execute("PRAGMA table_info(sync_runs)")}
            if "destination_id" not in columns:
                db.execute("ALTER TABLE sync_runs ADD COLUMN destination_id TEXT")
            if not versions:
                db.execute("INSERT INTO sync_schema_versions(version, applied_at) VALUES(?, ?)", (SCHEMA_VERSION, _now()))
            elif versions == [1]:
                db.execute("UPDATE sync_schema_versions SET version=?, applied_at=? WHERE version=1",(SCHEMA_VERSION,_now()))
            elif versions != [SCHEMA_VERSION]:
                raise RuntimeError("unsupported_sync_ledger_schema")

    def create_run(self, run_id: str, *, source_snapshot_id: str | None = None, destination_id: str | None = None) -> dict:
        run_id = _identifier(run_id, "run_id", required=True)
        source_snapshot_id = _identifier(source_snapshot_id, "source_snapshot_id")
        destination_id = _identifier(destination_id, "destination_id")
        with self._write() as db:
            db.execute(
                "INSERT INTO sync_runs(run_id,status,started_at,source_snapshot_id,destination_id) VALUES(?, 'running', ?, ?, ?) "
                "ON CONFLICT(run_id) DO NOTHING",
                (run_id, _now(), source_snapshot_id, destination_id),
            )
            row = db.execute("SELECT * FROM sync_runs WHERE run_id=?", (run_id,)).fetchone()
            if row and (row["source_snapshot_id"] != source_snapshot_id or row["destination_id"]!=destination_id): raise ValueError("run_identity_mismatch")
            if row and row["status"] != "running":
                db.execute("UPDATE sync_runs SET status='running', finished_at=NULL WHERE run_id=?", (run_id,))
                row = db.execute("SELECT * FROM sync_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row)

    def finish_run(self, run_id: str, status: str) -> dict:
        run_id = _identifier(run_id, "run_id", required=True)
        if status not in RUN_STATUSES - {"running"}:
            raise ValueError("invalid_terminal_run_status")
        with self._write() as db:
            changed = db.execute(
                "UPDATE sync_runs SET status=?, finished_at=? WHERE run_id=?",
                (status, _now(), run_id),
            ).rowcount
            if not changed:
                raise KeyError("run_not_found")
            row = db.execute("SELECT * FROM sync_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row)

    def run(self, run_id: str) -> dict | None:
        run_id = _identifier(run_id, "run_id", required=True)
        with self._connect() as db:
            row = db.execute("SELECT * FROM sync_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def checkpoint_intent(
        self, run_id: str, skill_id: str, operation_kind: str, desired_hash: str,
        *, remote_hash: str | None = None, page_id: str | None = None,
    ) -> dict:
        """Persist mutation intent; callers must invoke this before external mutation."""
        values = tuple(_identifier(value, name, required=name in {"run_id", "skill_id", "operation_kind", "desired_hash"})
                       for value, name in ((run_id, "run_id"), (skill_id, "skill_id"),
                                           (operation_kind, "operation_kind"), (desired_hash, "desired_hash"),
                                           (remote_hash, "remote_hash"), (page_id, "page_id")))
        run_id, skill_id, operation_kind, desired_hash, remote_hash, page_id = values
        timestamp = _now()
        with self._write() as db:
            existing=db.execute("SELECT desired_hash FROM sync_operations WHERE run_id=? AND skill_id=? AND operation_kind=?",(run_id,skill_id,operation_kind)).fetchone()
            if existing and existing[0]!=desired_hash: raise ValueError("operation_intent_mismatch")
            db.execute(
                """INSERT INTO sync_operations(
                       run_id,skill_id,operation_kind,status,desired_hash,remote_hash,page_id,created_at,updated_at
                   ) VALUES(?,?,?,'pending',?,?,?,?,?)
                   ON CONFLICT(run_id,skill_id,operation_kind) DO UPDATE SET
                       desired_hash=excluded.desired_hash,
                       remote_hash=COALESCE(excluded.remote_hash,sync_operations.remote_hash),
                       page_id=COALESCE(excluded.page_id,sync_operations.page_id),
                       updated_at=excluded.updated_at""",
                (run_id, skill_id, operation_kind, desired_hash, remote_hash, page_id, timestamp, timestamp),
            )
            row = db.execute(
                "SELECT * FROM sync_operations WHERE run_id=? AND skill_id=? AND operation_kind=?",
                (run_id, skill_id, operation_kind),
            ).fetchone()
        return dict(row)

    def record_attempt(self, run_id: str, skill_id: str, operation_kind: str) -> dict:
        return self._update_operation(run_id, skill_id, operation_kind, status="in_progress", increment_attempt=True,
                                      next_retry_at=None, error_code=None)

    def record_result(
        self, run_id: str, skill_id: str, operation_kind: str, *,
        page_id: str | None = None, upload_id: str | None = None,
        result_id: str | None = None, remote_hash: str | None = None,
        status: str = "complete",
    ) -> dict:
        if status not in {"complete", "skipped"}:
            raise ValueError("invalid_result_status")
        return self._update_operation(run_id, skill_id, operation_kind, status=status, page_id=page_id,
                                      upload_id=upload_id, result_id=result_id, remote_hash=remote_hash,
                                      next_retry_at=None, error_code=None)

    def record_failure(
        self, run_id: str, skill_id: str, operation_kind: str, error_code: str, *,
        retryable: bool, next_retry_at: str | None = None,
    ) -> dict:
        error_code = _identifier(error_code, "error_code", required=True)
        if retryable and not next_retry_at:
            raise ValueError("next_retry_at_required")
        return self._update_operation(run_id, skill_id, operation_kind,
                                      status="retryable" if retryable else "failed",
                                      error_code=error_code, next_retry_at=next_retry_at)

    def _update_operation(self, run_id: str, skill_id: str, operation_kind: str, **changes: object) -> dict:
        run_id = _identifier(run_id, "run_id", required=True)
        skill_id = _identifier(skill_id, "skill_id", required=True)
        operation_kind = _identifier(operation_kind, "operation_kind", required=True)
        allowed = {"status", "page_id", "upload_id", "result_id", "remote_hash", "next_retry_at", "error_code"}
        increment = bool(changes.pop("increment_attempt", False))
        if set(changes) - allowed or changes.get("status") not in OPERATION_STATUSES:
            raise ValueError("invalid_operation_update")
        for key in ("page_id", "upload_id", "result_id", "remote_hash", "error_code"):
            if key in changes:
                changes[key] = _identifier(changes[key], key)
        assignments = [f"{key}=?" for key in changes] + ["updated_at=?"]
        params = list(changes.values()) + [_now()]
        if increment:
            assignments.append("attempt_count=attempt_count+1")
        params.extend((run_id, skill_id, operation_kind))
        with self._write() as db:
            changed = db.execute(
                f"UPDATE sync_operations SET {','.join(assignments)} WHERE run_id=? AND skill_id=? AND operation_kind=?",
                params,
            ).rowcount
            if not changed:
                raise KeyError("operation_not_checkpointed")
            row = db.execute(
                "SELECT * FROM sync_operations WHERE run_id=? AND skill_id=? AND operation_kind=?",
                (run_id, skill_id, operation_kind),
            ).fetchone()
        return dict(row)

    def resumable_operations(self, run_id: str, *, as_of: str | None = None) -> list[dict]:
        run_id = _identifier(run_id, "run_id", required=True)
        as_of = as_of or _now()
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM sync_operations
                   WHERE run_id=? AND status IN ('pending','in_progress','retryable')
                     AND (status!='retryable' OR next_retry_at<=?)
                   ORDER BY skill_id, operation_kind""",
                (run_id, as_of),
            ).fetchall()
        return [dict(row) for row in rows]

    def operation(self, run_id: str, skill_id: str, operation_kind: str) -> dict | None:
        run_id = _identifier(run_id, "run_id", required=True); skill_id = _identifier(skill_id, "skill_id", required=True); operation_kind = _identifier(operation_kind, "operation_kind", required=True)
        with self._connect() as db:
            row=db.execute("SELECT * FROM sync_operations WHERE run_id=? AND skill_id=? AND operation_kind=?",(run_id,skill_id,operation_kind)).fetchone()
        return dict(row) if row else None

    def incomplete_skill_operations(self, skill_id: str, operation_kind: str, destination_id: str) -> list[dict]:
        skill_id=_identifier(skill_id,"skill_id",required=True); operation_kind=_identifier(operation_kind,"operation_kind",required=True)
        destination_id=_identifier(destination_id,"destination_id",required=True)
        with self._connect() as db:
            rows=db.execute(
                """SELECT o.* FROM sync_operations o JOIN sync_runs r ON r.run_id=o.run_id
                   WHERE o.skill_id=? AND o.operation_kind=? AND r.destination_id=?
                     AND o.status IN ('pending','in_progress','retryable') ORDER BY o.created_at""",
                (skill_id,operation_kind,destination_id),
            ).fetchall()
        return [dict(row) for row in rows]
