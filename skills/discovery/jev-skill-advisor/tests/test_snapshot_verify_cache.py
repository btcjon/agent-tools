"""Security tests for cached snapshot verification."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path

import pytest


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _build_snapshot(root: Path, files: dict[str, bytes], *, modes: dict[str, int] | None = None):
    """Create <root>/snapshots/<id>/manifest.json + skills/<files> that passes LibraryCache._verify."""
    modes = modes or {}
    file_rows = []
    for rel, data in sorted(files.items()):
        mode = modes.get(rel, 0o644)
        file_rows.append({
            "path": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "mode": mode,
        })
    durable = {
        "schema_version": 2,
        "source": "test",
        "exports": [{
            "kind": "skill",
            "id": "test-skill",
            "version_id": "v1",
            "archive_hash": "0" * 64,
            "content_hash": "1" * 64,
            "skill_roots": ["demo"],
        }],
        "skill_count": 1,
        "packages": [],
        "files": file_rows,
    }
    semantic = {
        **durable,
        "exports": [{k: v for k, v in row.items() if k != "archive_hash"} for row in durable["exports"]],
    }
    snapshot_id = _json_hash(semantic)
    snap = root / "snapshots" / snapshot_id
    skills = snap / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        target = skills / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(modes.get(rel, 0o644))
    manifest = {**durable, "snapshot_id": snapshot_id}
    (snap / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return snapshot_id, snap


def _default_files():
    return {"demo/SKILL.md": b"---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n"}


@pytest.fixture
def snapshot_env(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    snapshot_id, snap = _build_snapshot(library, _default_files())
    return {
        "library": library,
        "state_dir": state_dir,
        "snapshot_id": snapshot_id,
        "snap": snap,
        "skills": snap / "skills",
        "skill_file": snap / "skills" / "demo" / "SKILL.md",
    }


def _cache(library):
    from jev_skill_advisor.library_cache import LibraryCache
    return LibraryCache(library)


def _verify(env):
    from jev_skill_advisor.profile import _verify_snapshot_cached
    return _verify_snapshot_cached(_cache(env["library"]), env["snapshot_id"], env["state_dir"])


def _marker(env):
    return env["state_dir"] / "snapshot-verify.json"


def test_1_first_full_verify_writes_marker_second_is_fast(snapshot_env, monkeypatch):
    from jev_skill_advisor.library_cache import LibraryCache

    calls = {"n": 0}
    original = LibraryCache._verify

    def counting(self, identity):
        calls["n"] += 1
        return original(self, identity)

    monkeypatch.setattr(LibraryCache, "_verify", counting)
    content_reads = []
    skill = snapshot_env["skill_file"]
    real_read = Path.read_bytes

    def tracked_read(self):
        resolved = self.resolve()
        if skill.resolve() == resolved or str(resolved).startswith(str(snapshot_env["skills"].resolve())):
            if self.name != "manifest.json":
                content_reads.append(str(self))
        return real_read(self)

    _verify(snapshot_env)
    assert calls["n"] == 1
    marker = _marker(snapshot_env)
    assert marker.is_file()
    assert not marker.is_symlink()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600

    monkeypatch.setattr(Path, "read_bytes", tracked_read)
    content_reads.clear()
    before = calls["n"]
    _verify(snapshot_env)
    assert calls["n"] == before
    assert not any(p.endswith("SKILL.md") or "/skills/" in p for p in content_reads if "manifest.json" not in p)


def test_2_same_size_content_change_raises(snapshot_env):
    from jev_skill_advisor.library_cache import LibraryCacheError

    _verify(snapshot_env)
    time.sleep(0.05)
    original = snapshot_env["skill_file"].read_bytes()
    snapshot_env["skill_file"].write_bytes(b"X" * len(original))
    with pytest.raises(LibraryCacheError, match="^snapshot_content_mismatch$"):
        _verify(snapshot_env)


def test_3_file_added_and_removed_raise(snapshot_env):
    from jev_skill_advisor.library_cache import LibraryCacheError

    _verify(snapshot_env)
    time.sleep(0.05)
    extra = snapshot_env["skills"] / "demo" / "extra.txt"
    extra.write_bytes(b"extra")
    with pytest.raises(LibraryCacheError, match="^snapshot_content_mismatch$"):
        _verify(snapshot_env)
    extra.unlink()
    # restore marker path: rebuild by verifying clean tree then remove a file
    # After failed verify, re-verify clean tree to rewrite marker
    _verify(snapshot_env)
    time.sleep(0.05)
    snapshot_env["skill_file"].unlink()
    with pytest.raises(LibraryCacheError, match="^snapshot_content_mismatch$"):
        _verify(snapshot_env)


def test_4_symlink_under_skills_raises(snapshot_env):
    from jev_skill_advisor.library_cache import LibraryCacheError

    _verify(snapshot_env)
    link = snapshot_env["skills"] / "demo" / "link.txt"
    link.symlink_to("SKILL.md")
    with pytest.raises(LibraryCacheError, match="^snapshot_contains_link$"):
        _verify(snapshot_env)


def test_5_exec_bit_change_raises(snapshot_env):
    from jev_skill_advisor.library_cache import LibraryCacheError

    _verify(snapshot_env)
    time.sleep(0.05)
    snapshot_env["skill_file"].chmod(0o755)
    with pytest.raises(LibraryCacheError, match="^snapshot_content_mismatch$"):
        _verify(snapshot_env)


def test_6_manifest_change_raises_identity_mismatch(snapshot_env):
    from jev_skill_advisor.library_cache import LibraryCacheError

    _verify(snapshot_env)
    time.sleep(0.05)
    manifest = snapshot_env["snap"] / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["skill_count"] = 99
    manifest.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(LibraryCacheError, match="^snapshot_identity_mismatch$"):
        _verify(snapshot_env)


def test_7_mtime_touch_rewrites_marker_then_fast(snapshot_env, monkeypatch):
    from jev_skill_advisor.library_cache import LibraryCache

    _verify(snapshot_env)
    marker = _marker(snapshot_env)
    first = marker.read_bytes()
    time.sleep(0.05)
    os.utime(snapshot_env["skill_file"], None)

    calls = {"n": 0}
    original = LibraryCache._verify

    def counting(self, identity):
        calls["n"] += 1
        return original(self, identity)

    monkeypatch.setattr(LibraryCache, "_verify", counting)
    _verify(snapshot_env)
    assert calls["n"] == 1
    second = marker.read_bytes()
    assert second != first

    before = calls["n"]
    _verify(snapshot_env)
    assert calls["n"] == before


def test_8_marker_tampering_forces_full_verify(snapshot_env, monkeypatch):
    from jev_skill_advisor.library_cache import LibraryCache

    real_verify = LibraryCache._verify
    _verify(snapshot_env)
    marker = _marker(snapshot_env)
    good = json.loads(marker.read_text(encoding="utf-8"))

    cases = []
    bad = dict(good)
    bad["tree_fingerprint"] = "0" * 64
    cases.append(("wrong_fp", json.dumps(bad).encode()))
    bad = dict(good)
    bad["snapshot_id"] = "a" * 64
    cases.append(("other_id", json.dumps(bad).encode()))
    cases.append(("garbage", b"not-json{{{{"))
    bad = dict(good)
    bad["extra"] = "x"
    cases.append(("extra_key", json.dumps(bad).encode()))
    cases.append(("symlink", None))
    cases.append(("oversized", b"{" + b"a" * 5000 + b"}"))

    for name, payload in cases:
        if marker.exists() or marker.is_symlink():
            marker.unlink()
        if name == "symlink":
            target = snapshot_env["state_dir"] / "other.json"
            target.write_text(json.dumps(good), encoding="utf-8")
            marker.symlink_to(target)
        else:
            marker.write_bytes(payload)
            marker.chmod(0o600)

        calls = {"n": 0}

        def counting(self, identity, _calls=calls):
            _calls["n"] += 1
            return real_verify(self, identity)

        monkeypatch.setattr(LibraryCache, "_verify", counting)
        _verify(snapshot_env)
        assert calls["n"] == 1, name
        monkeypatch.setattr(LibraryCache, "_verify", real_verify)
        _verify(snapshot_env)


def test_residual_forged_marker_same_user(snapshot_env):
    """The 15-minute dead-man full check covers it.

    A same-user forged marker whose values match a tampered tree's fingerprint
    is the documented residual risk: the fast path trusts metadata+marker and
    will not re-hash file contents.
    """
    from jev_skill_advisor.profile import _skills_tree_fingerprint, _read_manifest_sha256, _verify_snapshot_cached
    from jev_skill_advisor.library_cache import LibraryCache

    _verify(snapshot_env)
    time.sleep(0.05)
    original = snapshot_env["skill_file"].read_bytes()
    snapshot_env["skill_file"].write_bytes(b"Y" * len(original))
    cache = LibraryCache(snapshot_env["library"])
    path = cache._snapshot_path(snapshot_env["snapshot_id"])
    fp = _skills_tree_fingerprint(path / "skills")
    manifest_sha = _read_manifest_sha256(path)
    forged = {
        "version": 1,
        "snapshot_id": snapshot_env["snapshot_id"],
        "manifest_sha256": manifest_sha,
        "tree_fingerprint": fp,
    }
    marker = _marker(snapshot_env)
    marker.write_text(json.dumps(forged, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    os.chmod(marker, 0o600)
    # Fast path accepts the forged marker without detecting content tampering.
    _verify_snapshot_cached(cache, snapshot_env["snapshot_id"], snapshot_env["state_dir"])


def test_9_failed_verify_does_not_accept_old_marker(snapshot_env):
    from jev_skill_advisor.library_cache import LibraryCacheError

    _verify(snapshot_env)
    marker = _marker(snapshot_env)
    old = marker.read_bytes()
    time.sleep(0.05)
    original = snapshot_env["skill_file"].read_bytes()
    snapshot_env["skill_file"].write_bytes(b"Z" * len(original))
    with pytest.raises(LibraryCacheError, match="^snapshot_content_mismatch$"):
        _verify(snapshot_env)
    # Old marker may still be on disk, but must not make the tampered tree pass.
    assert marker.read_bytes() == old
    with pytest.raises(LibraryCacheError, match="^snapshot_content_mismatch$"):
        _verify(snapshot_env)


def test_10_missing_and_readonly_state_dir(tmp_path, monkeypatch):
    from jev_skill_advisor.library_cache import LibraryCache
    from jev_skill_advisor.profile import _verify_snapshot_cached

    library = tmp_path / "library"
    library.mkdir()
    snapshot_id, _ = _build_snapshot(library, _default_files())
    missing = tmp_path / "no-such-state"
    cache = LibraryCache(library)

    calls = {"n": 0}
    original = LibraryCache._verify

    def counting(self, identity):
        calls["n"] += 1
        return original(self, identity)

    monkeypatch.setattr(LibraryCache, "_verify", counting)
    _verify_snapshot_cached(cache, snapshot_id, missing)
    _verify_snapshot_cached(cache, snapshot_id, missing)
    assert calls["n"] == 2
    assert not missing.exists()

    readonly = tmp_path / "ro-state"
    readonly.mkdir()
    os.chmod(readonly, 0o500)
    calls["n"] = 0
    try:
        _verify_snapshot_cached(cache, snapshot_id, readonly)
        _verify_snapshot_cached(cache, snapshot_id, readonly)
        assert calls["n"] == 2
        assert not (readonly / "snapshot-verify.json").exists()
    finally:
        os.chmod(readonly, 0o700)


def test_11_kill_switch_disables_marker(snapshot_env, monkeypatch):
    from jev_skill_advisor.library_cache import LibraryCache

    monkeypatch.setenv("JEV_SNAPSHOT_VERIFY_CACHE", "0")
    calls = {"n": 0}
    original = LibraryCache._verify

    def counting(self, identity):
        calls["n"] += 1
        return original(self, identity)

    monkeypatch.setattr(LibraryCache, "_verify", counting)
    _verify(snapshot_env)
    _verify(snapshot_env)
    assert calls["n"] == 2
    assert not _marker(snapshot_env).exists()


def test_12_load_profile_maps_to_invalid_library_snapshot(tmp_path):
    """Tampered snapshot raises ProfileError('invalid_library_snapshot') via load_profile.

    Asserts through the verify step: catalog/source hash checks after verify are not
    needed once verify has already failed.
    """
    from jev_skill_advisor.library_cache import LibraryCacheError
    from jev_skill_advisor.profile import ProfileError, load_profile

    library = tmp_path / "library"
    library.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    snapshot_id, snap = _build_snapshot(library, _default_files())
    # Tamper after building a valid snapshot so verify fails.
    time.sleep(0.05)
    (snap / "skills" / "demo" / "SKILL.md").write_bytes(b"tampered-content-here!!")

    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"entries": []}), encoding="utf-8")
    # Keep state outside any AI-Control-Plane path under warehouse.
    profile_path = tmp_path / "profile.json"
    profile = {
        "config_version": 1,
        "profile_id": "test",
        "harness": "test",
        "warehouse_root": str(snap / "skills"),
        "catalog_path": str(catalog_path),
        "state_dir": str(state_dir),
        "mode": "off",
        "provider_enabled": False,
        "read_enabled": False,
        "eligible_ids": [],
        "read_allowlist": [],
    }
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(ProfileError, match="^invalid_library_snapshot$") as excinfo:
        load_profile(profile_path)
    assert isinstance(excinfo.value.__cause__, LibraryCacheError)
