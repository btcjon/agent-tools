import io
import json
import tarfile
from pathlib import Path

import pytest

from jev_skill_advisor.capability_core import load_manifest
from jev_skill_advisor.catalog_cli import build_catalog
from jev_skill_advisor.library_cache import LibraryCache
from jev_skill_advisor.notion_import import Export
from jev_skill_advisor.release import ReleaseError, ReleaseStore, build_release
from jev_skill_advisor.release_cli import main

PACKAGE = Path(__file__).resolve().parents[1]
LIVE = PACKAGE / "examples" / "notion-mcp-live-manifest-2026-09-24.json"
PRIOR_KEYS = {
    "schema_version", "activation", "snapshot_id", "snapshot_root", "catalog_hash",
    "skill_count", "eligible_skill_count", "stable_ids_hash", "profiles", "files",
    "implementation_revision",
}


def _archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            bundle.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _snapshot(tmp_path):
    cache = LibraryCache(tmp_path / "cache")
    status = cache.publish([Export("skill", "page-1", "1" * 64, _archive({
        "package/skill-1/SKILL.md": b"---\nname: skill-1\ndescription: Pilot 1.\n---\n",
        "package/skill-1/references/info.txt": b"reference-1",
    }))])
    snapshot_root = Path(status["catalog_root"])
    catalog = build_catalog(snapshot_root)
    skill_count = len({row["stable_id"] for row in [*catalog["entries"], *catalog.get("exclusions", [])]})
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    inventory = evidence / "inventory.json"
    parity = evidence / "parity.json"
    tests = evidence / "tests.json"
    inventory.write_text(json.dumps({"inventory_hash": "inv", "skills": [{"n": index} for index in range(skill_count)]}) + "\n")
    parity.write_text(json.dumps({"exact": True, "snapshot_id": status["snapshot_id"], "inventory_hash": "inv"}) + "\n")
    tests.write_text(json.dumps({"status": "passed", "commit": "rev-1"}) + "\n")
    return {"snapshot_id": status["snapshot_id"], "snapshot_root": snapshot_root, "revision": "rev-1",
            "evidence": {"inventory": inventory, "parity": parity, "tests": tests}}


def _source(tmp_path):
    target = tmp_path / "source-manifest.json"
    target.write_bytes(LIVE.read_bytes())
    return target


def _build(tmp_path, spec, source=None, activation="shadow", root=None):
    return build_release(root=root or (tmp_path / "state"), snapshot_id=spec["snapshot_id"],
                         snapshot_root=spec["snapshot_root"], evidence=spec["evidence"],
                         revision=spec["revision"], activation=activation, capability_manifest=source)


def test_capability_manifest_build_is_reproducible_and_uses_the_copy(tmp_path):
    spec = _snapshot(tmp_path)
    source = _source(tmp_path)
    first_id, first = _build(tmp_path, spec, source)
    second_id, second = _build(tmp_path, spec, source)
    assert first_id == second_id
    assert first == second
    bound = Path(first["files"]["capability_manifest"]["path"])
    assert bound.name == "capability-manifest.json"
    assert (tmp_path / "state" / "release-inputs").resolve() in bound.resolve().parents
    assert bound.resolve() != source.resolve()
    assert bound.read_bytes() == LIVE.read_bytes()
    loaded = load_manifest(bound)
    assert len(loaded.entries) == 45
    assert loaded.content_hash == first["files"]["capability_manifest"]["content_hash"]
    assert first["files"]["capability_manifest"]["sha256"]
    store = ReleaseStore(tmp_path / "state")
    assert store.capability_manifest_path(first_id) == bound
    assert store.load_bound_capability_manifest(first_id).content_hash == loaded.content_hash


def test_source_mutation_mints_a_new_release_and_leaves_the_copy(tmp_path):
    spec = _snapshot(tmp_path)
    source = _source(tmp_path)
    original = source.read_bytes()
    first_id, first = _build(tmp_path, spec, source)
    copied = Path(first["files"]["capability_manifest"]["path"])
    original_hash = first["files"]["capability_manifest"]["content_hash"]
    payload = json.loads(original)
    payload["description"] = "Mutated live Notion MCP pins for the release binding test."
    source.write_text(json.dumps(payload) + "\n")
    second_id, second = _build(tmp_path, spec, source)
    assert second_id != first_id
    assert copied.read_bytes() == original
    assert second["files"]["capability_manifest"]["content_hash"] != original_hash
    store = ReleaseStore(tmp_path / "state")
    assert store.capability_manifest_path(first_id) == copied
    assert store.load_bound_capability_manifest(first_id).content_hash == original_hash
    assert load_manifest(source).content_hash != original_hash
    store.activate(first_id, expected_previous=None)
    pinned, _ = store.resolve(host="h", harness="codex", session_id="pinned")
    assert pinned == first_id
    assert store.load_bound_capability_manifest(pinned).content_hash == original_hash


def test_copied_manifest_tamper_symlink_and_removal_are_rejected(tmp_path):
    spec = _snapshot(tmp_path)
    source = _source(tmp_path)
    release_id, manifest = _build(tmp_path, spec, source)
    copied = Path(manifest["files"]["capability_manifest"]["path"])
    original = copied.read_bytes()
    store = ReleaseStore(tmp_path / "state")
    copied.write_bytes(original + b"\n")
    for call in (store.validate, store.validate_runtime, store.capability_manifest_path, store.load_bound_capability_manifest):
        with pytest.raises(ReleaseError, match="release_file_tampered"):
            call(release_id)
    with pytest.raises(ReleaseError, match="release_file_tampered"):
        store.activate(release_id, expected_previous=None)
    assert not store.pointer.exists()
    with pytest.raises(ReleaseError, match="immutable_release_input_conflict"):
        _build(tmp_path, spec, source)
    copied.write_bytes(original)
    assert store.validate_runtime(release_id)["files"]["capability_manifest"]["sha256"]
    copied.unlink()
    copied.symlink_to(source)
    for call in (store.validate, store.validate_runtime, store.capability_manifest_path, store.load_bound_capability_manifest):
        with pytest.raises(ReleaseError, match="capability_manifest_invalid"):
            call(release_id)
    with pytest.raises(ReleaseError, match="capability_manifest_invalid"):
        store.activate(release_id, expected_previous=None)
    copied.unlink()
    for call in (store.validate, store.validate_runtime, store.capability_manifest_path, store.load_bound_capability_manifest):
        with pytest.raises(ReleaseError, match="capability_manifest_missing"):
            call(release_id)
    with pytest.raises(ReleaseError, match="capability_manifest_missing"):
        store.activate(release_id, expected_previous=None)


def test_release_without_capability_manifest_keeps_prior_behavior(tmp_path):
    spec = _snapshot(tmp_path)
    first_id, first = _build(tmp_path, spec)
    second_id, second = _build(tmp_path, spec)
    explicit_id, explicit = build_release(root=tmp_path / "state", snapshot_id=spec["snapshot_id"],
        snapshot_root=spec["snapshot_root"], evidence=spec["evidence"], revision=spec["revision"],
        activation="shadow", capability_manifest=None)
    assert first_id == second_id == explicit_id
    assert first == second == explicit
    assert set(first) == PRIOR_KEYS
    assert "capability_manifest" not in first["files"]
    assert set(first["files"]) == {"catalog", "evidence:inventory", "evidence:parity", "evidence:tests",
                                   "profile:codex", "profile:generic", "profile:hermes"}
    store = ReleaseStore(tmp_path / "state")
    assert store.capability_manifest_path(first_id) is None
    assert store.load_bound_capability_manifest(first_id) is None
    store.activate(first_id, expected_previous=None)
    assert store.current() == first_id
    pinned, manifest = store.resolve(host="h", harness="generic", session_id="plain")
    assert pinned == first_id
    assert "capability_manifest" not in manifest["files"]


def test_invalid_and_linked_capability_manifest_is_rejected(tmp_path):
    spec = _snapshot(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{}\n")
    with pytest.raises(ReleaseError, match="invalid_capability_manifest"):
        _build(tmp_path, spec, bad, root=tmp_path / "bad-root")
    link = tmp_path / "link.json"
    link.symlink_to(_source(tmp_path))
    with pytest.raises(ReleaseError, match="invalid_capability_manifest"):
        _build(tmp_path, spec, link, root=tmp_path / "link-root")


def test_build_commands_optionally_bind_the_capability_manifest(tmp_path, capsys):
    spec = _snapshot(tmp_path)
    source = _source(tmp_path)
    bound_ids = set()
    for activation in ("shadow", "selection", "delivery"):
        root = tmp_path / activation
        argv = ["--root", str(root), f"build-{activation}", "--snapshot-id", spec["snapshot_id"],
                "--snapshot-root", str(spec["snapshot_root"]), "--revision", spec["revision"],
                "--evidence", f"inventory={spec['evidence']['inventory']}",
                "--evidence", f"parity={spec['evidence']['parity']}",
                "--evidence", f"tests={spec['evidence']['tests']}",
                "--capability-manifest", str(source)]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        item = payload["manifest"]["files"]["capability_manifest"]
        assert Path(item["path"]).read_bytes() == source.read_bytes()
        assert item["content_hash"] == load_manifest(source).content_hash
        bound_ids.add(payload["release_id"])
        bare = tmp_path / f"{activation}-bare"
        bare_argv = [item for item in argv if item not in {"--capability-manifest", str(source)}]
        bare_argv[bare_argv.index(str(root))] = str(bare)
        assert main(bare_argv) == 0
        bare_payload = json.loads(capsys.readouterr().out)
        assert "capability_manifest" not in bare_payload["manifest"]["files"]
        assert bare_payload["release_id"] != payload["release_id"]
    assert len(bound_ids) == 3
