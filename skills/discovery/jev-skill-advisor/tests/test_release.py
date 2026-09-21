import hashlib, json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from jev_skill_advisor.release import ReleaseError, ReleaseStore, build_release, build_shadow_release


class TestReleaseStore(ReleaseStore):
    __test__ = False
    def validate(self, release_id):
        path=self.releases/release_id/"manifest.json"
        try: manifest=json.loads(path.read_text())
        except (OSError,json.JSONDecodeError) as exc: raise ReleaseError("missing_or_invalid_release") from exc
        if manifest.get("schema_version") == 0:
            from jev_skill_advisor.release import _canonical
            if hashlib.sha256(_canonical(manifest)).hexdigest() != release_id:
                raise ReleaseError("release_manifest_tampered")
            for item in manifest["files"].values():
                if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
                    raise ReleaseError("release_file_tampered")
            return manifest
        return super().validate(release_id)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return path


def make_release(tmp_path, store, name):
    # Unit-only create fixtures bypass semantic validation by testing malformed
    # manifests through the full builder elsewhere.
    snapshot = tmp_path / f"snapshot-{name}"; snapshot.mkdir()
    catalog = write_json(tmp_path / f"catalog-{name}.json", {"entries": [{"stable_id": "warehouse:alpha"}]})
    profile = write_json(tmp_path / f"profile-{name}.json", {"name": name})
    evidence = write_json(tmp_path / f"evidence-{name}.json", {"ok": True})
    release = store.create(snapshot_id=name, snapshot_root=snapshot, catalog_path=catalog,
                        profiles={"codex": profile}, evidence={"parity": evidence}, revision="abc123",
                        _test_unbound=True)
    return release


def test_release_rejects_tampering_and_guards_pointer(tmp_path):
    store = TestReleaseStore(tmp_path / "state")
    one = make_release(tmp_path, store, "one"); two = make_release(tmp_path, store, "two")
    store.activate(one, expected_previous=None)
    with pytest.raises(ReleaseError, match="current_release_changed"):
        store.activate(two, expected_previous=None)
    store.activate(two, expected_previous=one)
    assert store.current() == two
    manifest = store.releases / two / "manifest.json"
    changed = json.loads(manifest.read_text()); changed["implementation_revision"] = "tampered"
    manifest.write_text(json.dumps(changed))
    with pytest.raises(ReleaseError, match="release_manifest_tampered"):
        store.validate(two)


def test_session_pins_survive_promote_and_rollback(tmp_path):
    store = TestReleaseStore(tmp_path / "state")
    one = make_release(tmp_path, store, "one"); two = make_release(tmp_path, store, "two")
    store.activate(one, expected_previous=None)
    assert store.resolve(host="h", harness="codex", session_id="A")[0] == one
    store.activate(two, expected_previous=one)
    assert store.resolve(host="h", harness="codex", session_id="A")[0] == one
    assert store.resolve(host="h", harness="codex", session_id="B")[0] == two
    store.activate(one, expected_previous=two)
    assert store.resolve(host="h", harness="codex", session_id="B")[0] == two
    assert store.resolve(host="h", harness="codex", session_id="C")[0] == one


def test_concurrent_first_pin_is_single_and_sticky(tmp_path):
    store = TestReleaseStore(tmp_path / "state"); one = make_release(tmp_path, store, "one")
    store.activate(one, expected_previous=None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: store.resolve(host="h", harness="generic", session_id="same")[0], range(16)))
    assert set(values) == {one}
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM pins").fetchone()[0] == 1


def test_missing_pinned_release_does_not_repin(tmp_path):
    store = TestReleaseStore(tmp_path / "state"); one = make_release(tmp_path, store, "one"); two = make_release(tmp_path, store, "two")
    store.activate(one, expected_previous=None)
    store.resolve(host="h", harness="hermes", session_id="A")
    store.activate(two, expected_previous=one)
    (store.releases / one / "manifest.json").unlink()
    with pytest.raises(ReleaseError, match="missing_or_invalid_release"):
        store.resolve(host="h", harness="hermes", session_id="A")


def test_unbound_test_release_cannot_activate_in_production_store(tmp_path):
    store=ReleaseStore(tmp_path/"state")
    release=make_release(tmp_path,store,"unsafe")
    with pytest.raises(ReleaseError,match="test_release_not_activatable"):
        store.activate(release,expected_previous=None)
    assert not store.pointer.exists()


def test_profile_resolution_uses_harness_then_generic_and_rejects_mismatch(tmp_path):
    snapshot = tmp_path / "snapshot"; skill = snapshot / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True); skill.write_text("---\nname: alpha\ndescription: alpha procedure\n---\n")
    catalog = write_json(tmp_path / "catalog.json", {"entries": [{"stable_id": "warehouse:alpha", "name": "alpha",
        "description": "alpha procedure", "relative_path": "alpha", "content_hash": hashlib.sha256(skill.read_bytes()).hexdigest()}]})
    profiles = {}
    for harness in ("codex", "generic"):
        profiles[harness] = write_json(tmp_path / f"{harness}.json", {"config_version": 1, "profile_id": harness,
            "harness": harness, "warehouse_root": str(snapshot), "catalog_path": str(catalog),
            "state_dir": str(tmp_path / f"state-{harness}"), "mode": "shadow", "provider_enabled": True,
            "read_enabled": False, "eligible_ids": ["warehouse:alpha"], "read_allowlist": []})
    evidence = write_json(tmp_path / "evidence.json", {"ok": True})
    store = TestReleaseStore(tmp_path / "releases")
    release = store.create(snapshot_id="s", snapshot_root=snapshot, catalog_path=catalog,
                           profiles=profiles, evidence={"parity": evidence}, revision="abc", _test_unbound=True)
    store.activate(release, expected_previous=None)
    assert store.resolve_profile(host="h", harness="codex", session_id="a")[2].harness == "codex"
    assert store.resolve_profile(host="h", harness="unknown", session_id="b")[2].harness == "generic"
    raw = json.loads(profiles["codex"].read_text()); raw["harness"] = "wrong"; profiles["codex"].write_text(json.dumps(raw))
    with pytest.raises(ReleaseError, match="release_file_tampered"):
        store.resolve_profile(host="h", harness="codex", session_id="c")


def test_shadow_release_has_full_profile_coverage_and_is_repeatable(tmp_path):
    snapshot = tmp_path / "snapshot"; skill = snapshot / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True); skill.write_text("---\nname: alpha\ndescription: alpha procedure\n---\n")
    evidence = write_json(tmp_path / "parity.json", {"count": 1})
    first, manifest = build_shadow_release(root=tmp_path / "state", snapshot_id="s", snapshot_root=snapshot,
                                            evidence={"parity": evidence}, revision="abc", _test_unbound=True)
    second, repeated = build_shadow_release(root=tmp_path / "state", snapshot_id="s", snapshot_root=snapshot,
                                             evidence={"parity": evidence}, revision="abc", _test_unbound=True)
    assert first == second
    assert manifest == repeated
    assert manifest["skill_count"] == 1
    assert manifest["eligible_skill_count"] == 1
    assert manifest["profiles"] == ["codex", "generic", "hermes"]
    preserved=Path(manifest["files"]["evidence:parity"]["path"])
    assert preserved.parent.name and preserved.read_bytes()==evidence.read_bytes()
    evidence.write_text('{"count":999}')
    assert preserved.read_text()!=evidence.read_text()
    for harness in manifest["profiles"]:
        profile = json.loads(Path(manifest["files"][f"profile:{harness}"]["path"]).read_text())
        assert profile["mode"] == "shadow" and profile["read_enabled"] is False
        assert profile["eligible_ids"] == ["warehouse:alpha"]


def test_release_activation_profiles_are_strict_and_repeatable(tmp_path):
    snapshot = tmp_path / "snapshot"; skill = snapshot / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True); skill.write_text("---\nname: alpha\ndescription: alpha procedure\n---\n")
    evidence = write_json(tmp_path / "parity.json", {"count": 1})
    for activation, mode, read_enabled in (("selection", "advisory", False), ("delivery", "advisory", True)):
        release_id, manifest = build_release(root=tmp_path / activation, snapshot_id="s", snapshot_root=snapshot,
            evidence={"parity": evidence}, revision="abc", activation=activation, _test_unbound=True)
        assert release_id and manifest["activation"] == activation
        for harness in manifest["profiles"]:
            profile = json.loads(Path(manifest["files"][f"profile:{harness}"]["path"]).read_text())
            assert profile["mode"] == mode and profile["read_enabled"] is read_enabled
            assert profile["read_allowlist"] == (["warehouse:alpha"] if read_enabled else [])
            assert profile["emergency_stop_file"].endswith("EMERGENCY_STOP")
