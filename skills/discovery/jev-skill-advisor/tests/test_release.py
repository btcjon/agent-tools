import hashlib, json
from concurrent.futures import ThreadPoolExecutor

import pytest

from jev_skill_advisor.release import ReleaseError, ReleaseStore


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    return path


def make_release(tmp_path, store, name):
    snapshot = tmp_path / f"snapshot-{name}"; snapshot.mkdir()
    catalog = write_json(tmp_path / f"catalog-{name}.json", {"entries": [{"stable_id": "warehouse:alpha"}]})
    profile = write_json(tmp_path / f"profile-{name}.json", {"name": name})
    evidence = write_json(tmp_path / f"evidence-{name}.json", {"ok": True})
    return store.create(snapshot_id=name, snapshot_root=snapshot, catalog_path=catalog,
                        profiles={"codex": profile}, evidence={"parity": evidence}, revision="abc123")


def test_release_rejects_tampering_and_guards_pointer(tmp_path):
    store = ReleaseStore(tmp_path / "state")
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
    store = ReleaseStore(tmp_path / "state")
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
    store = ReleaseStore(tmp_path / "state"); one = make_release(tmp_path, store, "one")
    store.activate(one, expected_previous=None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: store.resolve(host="h", harness="generic", session_id="same")[0], range(16)))
    assert set(values) == {one}
    with store._connect() as db:
        assert db.execute("SELECT count(*) FROM pins").fetchone()[0] == 1


def test_missing_pinned_release_does_not_repin(tmp_path):
    store = ReleaseStore(tmp_path / "state"); one = make_release(tmp_path, store, "one"); two = make_release(tmp_path, store, "two")
    store.activate(one, expected_previous=None)
    store.resolve(host="h", harness="hermes", session_id="A")
    store.activate(two, expected_previous=one)
    (store.releases / one / "manifest.json").unlink()
    with pytest.raises(ReleaseError, match="missing_or_invalid_release"):
        store.resolve(host="h", harness="hermes", session_id="A")
