import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "snapshot_skill_search.py"


def load_script():
    spec = importlib.util.spec_from_file_location("snapshot_skill_search", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_active_entries_includes_nested_catalog_skills(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot" / "skills"
    nested = snapshot / "package" / "SKILL.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("---\nname: tdd\ndescription: nested test skill\n---\n")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "warehouse_root": str(snapshot),
        "entries": [{
            "stable_id": "warehouse:open-pstack/skills/tdd",
            "name": "tdd",
            "description": "nested test skill",
            "relative_path": "package",
            "entrypoint": "SKILL.md",
        }],
    }))
    release_root = tmp_path / "releases"
    release = release_root / "releases" / "a"
    release.mkdir(parents=True)
    (release_root / "current-release.json").write_text(json.dumps({"release_id": "a"}))
    (release / "manifest.json").write_text(json.dumps({
        "snapshot_root": str(snapshot),
        "files": {"catalog": {"path": str(catalog)}},
    }))
    module = load_script()
    monkeypatch.setattr(module, "RELEASE_ROOT", release_root)
    root, entries = module.active_entries()
    assert root == snapshot.resolve()
    assert [entry["stable_id"] for entry in entries] == ["warehouse:open-pstack/skills/tdd"]
