import hashlib
import io
import json
import tarfile
from pathlib import Path

from jev_skill_advisor.capability_core import load_manifest
from jev_skill_advisor.catalog_choice import MODEL
from jev_skill_advisor.codex_hook import handle_event
from jev_skill_advisor.library_cache import LibraryCache
from jev_skill_advisor.notion_import import Export
from jev_skill_advisor.release import ReleaseStore, build_release
from jev_skill_advisor.select_cli import capability_correlation, select_named, select_task

PACKAGE = Path(__file__).resolve().parents[1]
LIVE = PACKAGE / "examples" / "notion-mcp-capability-manifest.json"
NOTION_TASK = "file the weekly status where the team can find it"


def choice_answer(criteria, choice):
    options = list(criteria)
    if choice not in options:
        choice = "none" if "none" in options else options[0]
    others = [option for option in options if option != choice]
    probabilities = {choice: 1.0 if not others else 0.8}
    if others:
        share = 0.2 / len(others)
        for option in others:
            probabilities[option] = share
        probabilities[others[-1]] += 1 - sum(probabilities.values())
    return {"type": "choice", "choice": choice, "confidence": 0.9, "probabilities": probabilities}


def scripted(winner, use=()):
    calls = []

    def evaluator(payload, timeout):
        calls.append(payload)
        if "winner" in payload["questions"]:
            choice = "none"
            for card in payload["state"]["candidates"]:
                if card["id"] == winner:
                    choice = card["option"]
            answer = choice_answer(payload["questions"]["winner"]["criteria"], choice)
            return {"model": MODEL, "usage": {"input_tokens": 3}, "answers": {"winner": answer}}
        answers = {}
        for index, card in enumerate(payload["state"]["candidates"]):
            answers[f"fit_{index}"] = {"type": "noul", "noul": 0.9 if card["id"] in use else 0.1}
        answers["write_intent"] = {"type": "noul", "noul": 0.9}
        return {"model": MODEL, "usage": {"input_tokens": 4}, "answers": answers}

    evaluator.calls = calls
    return evaluator


def _archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            bundle.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _skill(name, body):
    text = f"---\nname: {name}\ndescription: {name} procedure.\n---\n# {name}\n{body}\n"
    return f"package/{name}/SKILL.md", text.encode()


def _snapshot(tmp_path):
    notion_path, notion_body = _skill("notion", "BODY_NOTION_SENTINEL")
    alpha_path, alpha_body = _skill("alpha", "BODY_ALPHA_SENTINEL")
    cache = LibraryCache(tmp_path / "cache")
    status = cache.publish([
        Export("skill", "page-notion", "1" * 64, _archive({notion_path: notion_body})),
        Export("skill", "page-alpha", "2" * 64, _archive({alpha_path: alpha_body})),
    ])
    snapshot_root = Path(status["catalog_root"])
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    from jev_skill_advisor.catalog_cli import build_catalog
    catalog = build_catalog(snapshot_root)
    skill_count = len({row["stable_id"] for row in [*catalog["entries"], *catalog.get("exclusions", [])]})
    inventory = evidence / "inventory.json"
    parity = evidence / "parity.json"
    tests = evidence / "tests.json"
    inventory.write_text(json.dumps({"inventory_hash": "inv", "skills": [{"n": index} for index in range(skill_count)]}) + "\n")
    parity.write_text(json.dumps({"exact": True, "snapshot_id": status["snapshot_id"], "inventory_hash": "inv"}) + "\n")
    tests.write_text(json.dumps({"status": "passed", "commit": "rev-1"}) + "\n")
    return {"snapshot_id": status["snapshot_id"], "snapshot_root": snapshot_root, "revision": "rev-1",
            "evidence": {"inventory": inventory, "parity": parity, "tests": tests}}


def _source(tmp_path, name="source-manifest.json"):
    target = tmp_path / name
    target.write_bytes(LIVE.read_bytes())
    return target


def _pin(root, release_id):
    (root / "current-release.json").write_text(json.dumps({"schema_version": 1, "release_id": release_id}) + "\n")


def _build(tmp_path, *, capability=True):
    spec = _snapshot(tmp_path)
    source = _source(tmp_path) if capability else None
    root = tmp_path / "state"
    release_id, manifest = build_release(
        root=root, snapshot_id=spec["snapshot_id"], snapshot_root=spec["snapshot_root"],
        evidence=spec["evidence"], revision=spec["revision"], activation="shadow",
        capability_manifest=source,
    )
    _pin(root, release_id)
    bound = None
    if capability:
        bound = Path(manifest["files"]["capability_manifest"]["path"])
    return root, release_id, source, bound


def _event(prompt="Use the alpha workflow", turn="turn-1"):
    return {"hook_event_name": "UserPromptSubmit", "session_id": "session-1", "turn_id": turn,
            "cwd": "/work", "prompt": prompt}


def _skill_payload(root, release_id, name):
    store = ReleaseStore(root)
    manifest = json.loads((store.releases / release_id / "manifest.json").read_text())
    profile = json.loads(Path(manifest["files"]["profile:codex"]["path"]).read_text())
    catalog = json.loads(Path(profile["catalog_path"]).read_text())
    row = next(item for item in catalog["entries"] if item["name"] == name)
    warehouse = Path(profile["warehouse_root"])
    source = warehouse / row["relative_path"] / row["entrypoint"]
    package = warehouse / row.get("package_root", row["relative_path"])
    body = source.read_text(encoding="utf-8")
    return {
        "status": "suggested",
        "receipt_id": "r1",
        "selected_ids": [row["stable_id"]],
        "telemetry": {"provider_attempts": 1},
        "skills": [{
            "id": row["stable_id"],
            "body": body,
            "content_hash": row["content_hash"],
            "canonical_path": str(source),
            "package_root": str(package),
        }],
    }


def test_cli_uses_only_the_bound_copy_for_task_and_named(tmp_path, monkeypatch):
    root, _release_id, source, bound = _build(tmp_path)
    source.write_text(source.read_text(encoding="utf-8").replace("Search", "SEARCHED", 1), encoding="utf-8")
    seen = []
    real = load_manifest

    def spy(path):
        seen.append(Path(path).resolve())
        return real(path)

    monkeypatch.setattr("jev_skill_advisor.select_cli.load_manifest", spy)
    task = select_task(NOTION_TASK, root=root, evaluator=scripted("warehouse:notion", use={"notion.mcp.fetch"}))
    named = select_named("notion", root=root, task=NOTION_TASK, evaluator=scripted("warehouse:notion", use={"notion.mcp.fetch"}))
    assert task["selected"]["skill_id"] == "warehouse:notion"
    assert named["selected"]["skill_id"] == "warehouse:notion"
    assert task["capabilities"]["manifest_hash"] == load_manifest(bound).content_hash
    assert named["capabilities"]["manifest_hash"] == task["capabilities"]["manifest_hash"]
    assert seen == [bound.resolve(), bound.resolve()]
    assert source.resolve() not in seen
    assert "BODY_NOTION_SENTINEL" not in json.dumps(task["capabilities"])


def test_cli_notion_and_non_notion_call_counts(tmp_path):
    root, _release_id, _source, _bound = _build(tmp_path)
    notion = scripted("warehouse:notion", use={"notion.mcp.fetch"})
    result = select_task(NOTION_TASK, root=root, evaluator=notion)
    assert len(notion.calls) == 2
    assert result["capabilities"]["cards"]
    assert result["capabilities"]["authorizes_calls"] is False
    other = scripted("warehouse:alpha", use={"notion.mcp.fetch"})
    plain = select_task("sort the alpha checklist", root=root, evaluator=other)
    assert plain["selected"]["skill_id"] == "warehouse:alpha"
    assert len(other.calls) == 1
    assert plain["capabilities"]["cards"] == []
    assert plain["capabilities"]["reason"] == "not_notion_skill"


def test_cli_without_a_binding_and_with_an_explicit_profile_stays_skill_only(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_CAPABILITY_MANIFEST", raising=False)
    root, _release_id, _source, _bound = _build(tmp_path, capability=False)
    monkeypatch.setattr("jev_skill_advisor.select_cli.load_manifest", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("manifest loaded")))
    evaluator = scripted("warehouse:notion")
    result = select_task(NOTION_TASK, root=root, evaluator=evaluator)
    assert result["selected"]["skill_id"] == "warehouse:notion"
    assert "capabilities" not in result
    assert len(evaluator.calls) == 1
    seen = []

    def runner(request, _source, _timeout):
        seen.append(request.get("capability_manifest"))
        return {"status": "none", "skills": [], "reason": "catalog_choice_none"}

    assert handle_event(_event(), release_root=root, state=tmp_path / "unbound-hook", runner=runner) == {}
    assert seen == [None]


def test_explicit_profile_does_not_read_the_active_binding(tmp_path, monkeypatch):
    root, _release_id, _source, bound = _build(tmp_path)
    loaded = []
    real = load_manifest

    def spy(path):
        loaded.append(Path(path).resolve())
        return real(path)

    monkeypatch.setattr("jev_skill_advisor.select_cli.load_manifest", spy)
    warehouse = tmp_path / "loose-warehouse"
    rows = []
    for name, description in (("notion", "Work in Notion"), ("alpha", "Alpha procedure")):
        source_file = warehouse / name / "SKILL.md"
        source_file.parent.mkdir(parents=True)
        source_file.write_text(f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nBODY\n", encoding="utf-8")
        rows.append({
            "stable_id": f"warehouse:{name}", "name": name, "description": description,
            "relative_path": name, "content_hash": hashlib.sha256(source_file.read_bytes()).hexdigest(),
        })
    catalog = tmp_path / "loose-catalog.json"
    catalog.write_text(json.dumps({"entries": rows}), encoding="utf-8")
    loose = tmp_path / "loose-profile.json"
    loose.write_text(json.dumps({
        "config_version": 1, "profile_id": "loose", "harness": "test",
        "warehouse_root": str(warehouse), "catalog_path": str(catalog), "state_dir": str(tmp_path / "loose-state"),
        "mode": "advisory", "provider_enabled": False, "read_enabled": False,
        "eligible_ids": ["warehouse:notion", "warehouse:alpha"], "read_allowlist": [],
    }), encoding="utf-8")
    result = select_task(NOTION_TASK, root=root, profile_path=loose, evaluator=scripted("warehouse:notion", use={"notion.mcp.fetch"}))
    assert result["selected"]["skill_id"] == "warehouse:notion"
    assert "capabilities" not in result
    assert bound.resolve() not in loaded


def test_cli_override_uses_the_explicit_file_and_tamper_fails_open(tmp_path):
    root, _release_id, source, bound = _build(tmp_path)
    override = _source(tmp_path, "override.json")
    override.write_text(override.read_text(encoding="utf-8").replace("Search", "Lookup", 1), encoding="utf-8")
    chosen = select_task(
        NOTION_TASK, root=root, capability_manifest=override,
        evaluator=scripted("warehouse:notion", use={"notion.mcp.fetch"}),
    )
    assert chosen["capabilities"]["manifest_hash"] == load_manifest(override).content_hash
    assert chosen["capabilities"]["manifest_hash"] != load_manifest(bound).content_hash
    bound.write_bytes(bound.read_bytes() + b"\nTAMPER_SENTINEL_capability_body\n")
    damaged = select_task(NOTION_TASK, root=root, evaluator=scripted("warehouse:notion", use={"notion.mcp.fetch"}))
    blob = json.dumps(damaged)
    assert damaged["selected"]["skill_id"] == "warehouse:notion"
    assert "capabilities" not in damaged
    assert "TAMPER_SENTINEL_capability_body" not in blob
    assert str(source) not in blob
    bound.unlink()
    missing = select_named("notion", root=root, task=NOTION_TASK, evaluator=scripted("unused"))
    assert missing["selected"]["skill_id"] == "warehouse:notion"
    assert "capabilities" not in missing
    assert "TAMPER_SENTINEL_capability_body" not in json.dumps(missing)


def test_hook_passes_the_bound_manifest_once_and_a_miss_adds_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_CAPABILITY_MANIFEST", raising=False)
    root, release_id, source, bound = _build(tmp_path)
    calls = []
    original = ReleaseStore.capability_manifest_path

    def wrapped(self, release_id):
        calls.append(release_id)
        return original(self, release_id)

    monkeypatch.setattr(ReleaseStore, "capability_manifest_path", wrapped)
    seen = []

    def runner(request, _source, _timeout):
        seen.append(dict(request))
        if request["task"] == "missing":
            return {"status": "none", "skills": [], "reason": "catalog_choice_none"}
        return _skill_payload(root, release_id, "alpha")

    emitted = handle_event(_event(), release_root=root, state=tmp_path / "state-hook", runner=runner)
    context = emitted["hookSpecificOutput"]["additionalContext"]
    assert seen[0]["capability_manifest"] == str(bound)
    assert seen[0]["capability_manifest"] != str(source)
    assert calls == [release_id]
    assert "BODY_ALPHA_SENTINEL" in context
    assert "<capability-hint " not in context
    missed = handle_event(_event("missing", "turn-2"), release_root=root, state=tmp_path / "state-hook", runner=runner)
    assert missed == {}
    assert calls == [release_id, release_id]


def test_hook_override_skips_the_bound_copy(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_CAPABILITY_MANIFEST", raising=False)
    root, _release_id, _source, bound = _build(tmp_path)
    calls = []
    monkeypatch.setattr(ReleaseStore, "capability_manifest_path", lambda self, release_id: calls.append(release_id))
    override = tmp_path / "override.json"
    override.write_text("{}", encoding="utf-8")
    seen = []

    def runner(request, _source, _timeout):
        seen.append(request.get("capability_manifest"))
        return {"status": "none", "skills": []}

    handle_event(_event(), release_root=root, state=tmp_path / "hook-env", runner=runner, capability_manifest=override)
    monkeypatch.setenv("JEV_CAPABILITY_MANIFEST", str(override))
    handle_event(_event(turn="turn-2"), release_root=root, state=tmp_path / "hook-env", runner=runner)
    assert seen == [str(override), str(override)]
    assert calls == []
    assert str(bound) not in seen


def test_hook_tamper_and_missing_copy_keep_the_skill_without_leaking(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_CAPABILITY_MANIFEST", raising=False)
    root, release_id, _source, bound = _build(tmp_path)
    bound.write_bytes(bound.read_bytes() + b"\nTAMPER_SENTINEL_capability_body\n")
    state = tmp_path / "hook-tamper"

    def runner(request, _source, _timeout):
        assert "capability_manifest" not in request
        payload = _skill_payload(root, release_id, "notion")
        return payload

    emitted = handle_event(_event("file the weekly status"), release_root=root, state=state, runner=runner)
    context = emitted["hookSpecificOutput"]["additionalContext"]
    assert "BODY_NOTION_SENTINEL" in context
    assert "<capability-hint " not in context
    assert "TAMPER_SENTINEL_capability_body" not in context
    record = (state / "events.jsonl").read_text(encoding="utf-8")
    assert "TAMPER_SENTINEL_capability_body" not in record
    bound.unlink()
    missing = handle_event(_event("file the weekly status", "turn-2"), release_root=root, state=state, runner=runner)
    assert "BODY_NOTION_SENTINEL" in missing["hookSpecificOutput"]["additionalContext"]
    assert "<capability-hint " not in missing["hookSpecificOutput"]["additionalContext"]
