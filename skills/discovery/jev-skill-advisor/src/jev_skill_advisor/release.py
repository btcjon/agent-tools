"""Immutable releases, atomic activation, and sticky per-session resolution."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time


class ReleaseError(ValueError):
    pass


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(_canonical(value)); handle.flush(); os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ReleaseStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.releases = self.root / "releases"
        self.pointer = self.root / "current-release.json"
        self.lock = self.root / ".release.lock"
        self.db = self.root / "session-pins.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.releases.mkdir(exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS pins(host TEXT, harness TEXT, session_id TEXT, release_id TEXT, created_at REAL, PRIMARY KEY(host,harness,session_id))")

    def _connect(self):
        db = sqlite3.connect(self.db, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def _locked(self):
        self.lock.touch(mode=0o600, exist_ok=True)
        with self.lock.open("r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def create(self, *, snapshot_id: str, snapshot_root: Path, catalog_path: Path,
               profiles: dict[str, Path], evidence: dict[str, Path], revision: str,
               _test_unbound: bool = False) -> str:
        if not _test_unbound and set(evidence) != {"inventory", "parity", "tests"}:
            raise ReleaseError("release_evidence_missing")
        snapshot_root = Path(snapshot_root).resolve(); catalog_path = Path(catalog_path).resolve()
        files = {"catalog": {"path": str(catalog_path), "sha256": _digest(catalog_path)}}
        files.update({f"profile:{name}": {"path": str(Path(path).resolve()), "sha256": _digest(Path(path))}
                      for name, path in sorted(profiles.items())})
        files.update({f"evidence:{name}": {"path": str(Path(path).resolve()), "sha256": _digest(Path(path))}
                      for name, path in sorted(evidence.items())})
        catalog = json.loads(catalog_path.read_text())
        ids = sorted({row["stable_id"] for row in [*catalog.get("entries", []), *catalog.get("exclusions", [])]})
        manifest = {"schema_version": 0 if _test_unbound else 1, "snapshot_id": snapshot_id, "snapshot_root": str(snapshot_root),
                    "catalog_hash": files["catalog"]["sha256"], "skill_count": len(ids),
                    "eligible_skill_count": len(catalog.get("entries", [])),
                    "stable_ids_hash": hashlib.sha256(_canonical(ids)).hexdigest(),
                    "profiles": sorted(profiles), "files": files, "implementation_revision": revision}
        release_id = hashlib.sha256(_canonical(manifest)).hexdigest()
        destination = self.releases / release_id
        with self._locked():
            if destination.exists():
                self.validate(release_id)
                return release_id
            destination.mkdir(mode=0o700)
            _atomic(destination / "manifest.json", manifest)
        self.validate(release_id)
        return release_id

    def validate(self, release_id: str) -> dict:
        if not isinstance(release_id, str) or len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
            raise ReleaseError("invalid_release_id")
        path = self.releases / release_id / "manifest.json"
        try:
            manifest = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseError("missing_or_invalid_release") from exc
        if hashlib.sha256(_canonical(manifest)).hexdigest() != release_id:
            raise ReleaseError("release_manifest_tampered")
        for item in manifest.get("files", {}).values():
            candidate = Path(item["path"])
            if not candidate.is_file() or _digest(candidate) != item["sha256"]:
                raise ReleaseError("release_file_tampered")
        if manifest.get("schema_version") == 0:
            return manifest
        if manifest.get("schema_version") != 1:
            raise ReleaseError("invalid_release_schema")
        snapshot_root = Path(manifest["snapshot_root"])
        if not snapshot_root.is_dir():
            raise ReleaseError("release_snapshot_missing")
        if snapshot_root.name != "skills" or snapshot_root.parent.name != manifest["snapshot_id"]:
            raise ReleaseError("release_snapshot_binding_mismatch")
        from .library_cache import LibraryCache, LibraryCacheError
        try:
            LibraryCache(snapshot_root.parent.parent.parent)._verify(manifest["snapshot_id"])
        except LibraryCacheError as exc:
            raise ReleaseError("release_snapshot_tampered") from exc
        catalog_path = Path(manifest["files"]["catalog"]["path"])
        catalog = json.loads(catalog_path.read_text())
        if Path(catalog.get("warehouse_root", "")).resolve() != snapshot_root.resolve():
            raise ReleaseError("release_catalog_binding_mismatch")
        ids = sorted({row["stable_id"] for row in [*catalog.get("entries", []), *catalog.get("exclusions", [])]})
        if len(ids) != manifest["skill_count"] or hashlib.sha256(_canonical(ids)).hexdigest() != manifest["stable_ids_hash"]:
            raise ReleaseError("release_catalog_coverage_mismatch")
        for harness in manifest["profiles"]:
            raw = json.loads(Path(manifest["files"][f"profile:{harness}"]["path"]).read_text())
            if (raw.get("harness") != harness or Path(raw.get("warehouse_root", "")).resolve() != snapshot_root.resolve()
                    or Path(raw.get("catalog_path", "")).resolve() != catalog_path.resolve()
                    or raw.get("mode") != "shadow" or raw.get("read_enabled") is not False):
                raise ReleaseError("release_profile_binding_mismatch")
        inventory_item = manifest["files"].get("evidence:inventory")
        parity_item = manifest["files"].get("evidence:parity")
        tests_item = manifest["files"].get("evidence:tests")
        if not all((inventory_item, parity_item, tests_item)): raise ReleaseError("release_evidence_missing")
        inventory = json.loads(Path(inventory_item["path"]).read_text())
        parity = json.loads(Path(parity_item["path"]).read_text())
        tests = json.loads(Path(tests_item["path"]).read_text())
        if (parity.get("exact") is not True or parity.get("snapshot_id") != manifest["snapshot_id"]
                or parity.get("inventory_hash") != inventory.get("inventory_hash")
                or len(inventory.get("skills", [])) != manifest["skill_count"]
                or tests.get("status") != "passed" or tests.get("commit") != manifest["implementation_revision"]):
            raise ReleaseError("release_evidence_binding_mismatch")
        return manifest

    def current(self) -> str | None:
        if not self.pointer.exists():
            return None
        try:
            value = json.loads(self.pointer.read_text())
            release_id = value["release_id"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise ReleaseError("invalid_current_pointer") from exc
        self.validate(release_id)
        return release_id

    def _current_identity(self) -> str | None:
        if not self.pointer.exists():
            return None
        try:
            value = json.loads(self.pointer.read_text())
            release_id = value["release_id"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise ReleaseError("invalid_current_pointer") from exc
        if not isinstance(release_id, str):
            raise ReleaseError("invalid_current_pointer")
        return release_id

    def activate(self, release_id: str, *, expected_previous: str | None) -> None:
        self.validate(release_id)
        with self._locked():
            actual = self._current_identity()
            if actual != expected_previous:
                raise ReleaseError("current_release_changed")
            _atomic(self.pointer, {"schema_version": 1, "release_id": release_id})

    def resolve(self, *, host: str, harness: str, session_id: str) -> tuple[str, dict]:
        if not all(isinstance(v, str) and v for v in (host, harness, session_id)):
            raise ReleaseError("invalid_session_identity")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT release_id FROM pins WHERE host=? AND harness=? AND session_id=?",
                             (host, harness, session_id)).fetchone()
            if row is None:
                release_id = self.current()
                if release_id is None:
                    db.execute("ROLLBACK")
                    raise ReleaseError("no_current_release")
                db.execute("INSERT INTO pins VALUES(?,?,?,?,?)", (host, harness, session_id, release_id, time.time()))
            else:
                release_id = row["release_id"]
            db.execute("COMMIT")
        # Never silently repin when a retained release is damaged or missing.
        return release_id, self.validate(release_id)

    def resolve_profile(self, *, host: str, harness: str, session_id: str):
        """Resolve and validate the profile bound to this session's release."""
        from .profile import load_profile
        release_id, manifest = self.resolve(host=host, harness=harness, session_id=session_id)
        selected = harness if harness in manifest["profiles"] else "generic"
        if selected not in manifest["profiles"]:
            raise ReleaseError("release_profile_missing")
        profile_path = Path(manifest["files"][f"profile:{selected}"]["path"])
        profile = load_profile(profile_path)
        if profile.harness != selected:
            raise ReleaseError("release_profile_harness_mismatch")
        return release_id, manifest, profile


def build_shadow_release(*, root: Path, snapshot_id: str, snapshot_root: Path,
                         evidence: dict[str, Path], revision: str,
                         harnesses=("codex", "hermes", "generic"),
                         _test_unbound: bool = False) -> tuple[str, dict]:
    """Build a deterministic, read-disabled release without activating it."""
    from .catalog_cli import build_catalog
    from .production_cli import atomic_json
    from .profile import load_profile
    from .runtime import ServiceRuntime

    root = Path(root).resolve(); snapshot_root = Path(snapshot_root).resolve()
    catalog = build_catalog(snapshot_root)
    stable_ids = sorted(row["stable_id"] for row in catalog["entries"])
    all_ids = sorted({row["stable_id"] for row in [*catalog["entries"], *catalog.get("exclusions", [])]})
    if len(all_ids) != catalog["included_count"] + catalog["excluded_count"]:
        raise ReleaseError("catalog_identity_coverage_mismatch")
    seed = {"snapshot_id": snapshot_id, "catalog_hash": catalog["catalog_hash"],
            "revision": revision, "harnesses": sorted(harnesses),
            "evidence": {name: _digest(Path(path)) for name, path in sorted(evidence.items())}}
    input_id = hashlib.sha256(_canonical(seed)).hexdigest()
    inputs = root / "release-inputs" / input_id
    inputs.mkdir(parents=True, exist_ok=True)
    catalog_path = inputs / "catalog.json"
    if catalog_path.exists() and json.loads(catalog_path.read_text()) != catalog:
        raise ReleaseError("immutable_release_input_conflict")
    atomic_json(catalog_path, catalog)
    profiles = {}
    for harness in sorted(harnesses):
        path = inputs / f"profile-{harness}.json"
        value = {"config_version": 1, "profile_id": f"{harness}-{input_id[:12]}", "harness": harness,
                 "warehouse_root": str(snapshot_root), "catalog_path": str(catalog_path),
                 "state_dir": str(root / "runtime" / harness), "mode": "shadow",
                 "provider_enabled": True, "read_enabled": False, "eligible_ids": stable_ids,
                 "read_allowlist": [], "credential_env": "TYPESAFE_API_KEY", "deadline_s": 5,
                 "max_calls": 32, "max_tokens": 200000, "receipt_ttl_s": 86400,
                 "prompt_limit": 20, "provider_attempt_limit": 160}
        if path.exists() and json.loads(path.read_text()) != value:
            raise ReleaseError("immutable_release_input_conflict")
        atomic_json(path, value); profiles[harness] = path
        profile = load_profile(path)
        if len(profile.entries) != len(stable_ids):
            raise ReleaseError("profile_catalog_coverage_mismatch")
        ServiceRuntime(profile, initialize=True)
    release_id = ReleaseStore(root).create(snapshot_id=snapshot_id, snapshot_root=snapshot_root,
        catalog_path=catalog_path, profiles=profiles, evidence=evidence, revision=revision,
        _test_unbound=_test_unbound)
    return release_id, ReleaseStore(root).validate(release_id)
