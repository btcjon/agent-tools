import hashlib
import json
from pathlib import Path

from jev_skill_advisor.capability_core import STARTUP_CAPABILITY_BUDGET_BYTES, load_manifest
from jev_skill_advisor.catalog_choice import MODEL
from jev_skill_advisor.select_cli import (
    PUBLIC_CAPABILITY_BUDGET_BYTES,
    attach_capabilities,
    capability_correlation,
    main,
    select_named,
    select_task,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples" / "notion-mcp-capability-manifest.json"
TASK = "file the weekly status where the team can find it"


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


def scripted(winner, use=(), fail=False):
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
        if fail:
            raise TimeoutError("typesafe_timeout")
        answers = {}
        for index, card in enumerate(payload["state"]["candidates"]):
            answers[f"fit_{index}"] = {"type": "noul", "noul": 0.9 if card["id"] in use else 0.1}
        answers["write_intent"] = {"type": "noul", "noul": 0.9}
        return {"model": MODEL, "usage": {"input_tokens": 4}, "answers": answers}

    evaluator.calls = calls
    return evaluator


def profile(tmp_path):
    warehouse = tmp_path / "warehouse"
    rows = []
    for name, body in (
        ("alpha", "---\nname: alpha\ndescription: Alpha procedure\n---\n# Alpha\nBODY_ALPHA\n"),
        ("notion", "---\nname: notion\ndescription: Work in Notion\n---\n# Notion\nBODY_NOTION_SENTINEL\n"),
    ):
        source = warehouse / name / "SKILL.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(body, encoding="utf-8")
        rows.append({
            "stable_id": f"warehouse:{name}",
            "name": name,
            "description": "Alpha procedure" if name == "alpha" else "Work in Notion",
            "relative_path": name,
            "content_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
        })
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"entries": rows}), encoding="utf-8")
    config = tmp_path / "profile.json"
    config.write_text(json.dumps({
        "config_version": 1, "profile_id": "select-test", "harness": "test",
        "warehouse_root": str(warehouse), "catalog_path": str(catalog), "state_dir": str(tmp_path / "state"),
        "mode": "advisory", "provider_enabled": False, "read_enabled": False,
        "eligible_ids": ["warehouse:alpha", "warehouse:notion"], "read_allowlist": [],
    }), encoding="utf-8")
    return config


def test_correlation_vector_is_ascii_canonical():
    digest = capability_correlation("ab" * 32, "warehouse:notion", ["notion.mcp.fetch"], "selected")
    assert digest == "9607ea6dbb78580be5234954cf534e0613528df0cd043951baf1ef5c40dcf22a"


def test_notion_hit_makes_one_capability_call(tmp_path):
    evaluator = scripted("warehouse:notion", use={"notion.mcp.fetch"})
    result = select_task(TASK, root=tmp_path, profile_path=profile(tmp_path), capability_manifest=MANIFEST, evaluator=evaluator)
    assert result["selected"]["skill_id"] == "warehouse:notion"
    assert "BODY_NOTION_SENTINEL" in result["selected"]["content"]
    assert set(evaluator.calls[0]["questions"]) == {"winner"}
    assert set(evaluator.calls[1]["questions"]) == {f"fit_{index}" for index in range(6)} | {"write_intent"}
    assert all(question["type"] == "noul" for question in evaluator.calls[1]["questions"].values())
    capabilities = result["capabilities"]
    assert capabilities["cards"] == [{"id": "notion.mcp.fetch", "description": "Read one Notion page, database, or view by id or URL."}]
    assert capabilities["status"] == "selected"
    assert capabilities["reason"] == "capability_choice_selected"
    assert capabilities["manifest_hash"] == load_manifest(MANIFEST).content_hash
    assert capabilities["authorizes_calls"] is False
    assert capabilities["advisory"] is True
    assert capabilities["correlation"] == capability_correlation(
        capabilities["manifest_hash"], "warehouse:notion", ["notion.mcp.fetch"], "selected",
    )
    blob = json.dumps(capabilities)
    assert "BODY_NOTION_SENTINEL" not in blob
    assert "inputSchema" not in blob
    assert "schema_hash" not in blob
    assert "7717dd85" not in blob
    assert "authorized_ids" not in capabilities
    assert set(capabilities["cards"][0]) == {"id", "description"}
    assert "inputSchema" not in json.dumps(evaluator.calls[1]["state"])


def test_non_notion_does_not_make_a_second_call(tmp_path):
    evaluator = scripted("warehouse:alpha", use={"notion.mcp.fetch"})
    result = select_task(
        "sort the alpha checklist", root=tmp_path, profile_path=profile(tmp_path),
        capability_manifest=MANIFEST, evaluator=evaluator,
    )
    assert result["selected"]["skill_id"] == "warehouse:alpha"
    assert len(evaluator.calls) == 1
    assert "winner" in evaluator.calls[0]["questions"]
    assert result["capabilities"]["cards"] == []
    assert result["capabilities"]["reason"] == "not_notion_skill"
    assert result["capabilities"]["authorizes_calls"] is False


def test_capability_timeout_keeps_the_skill(tmp_path):
    evaluator = scripted("warehouse:notion", fail=True)
    result = select_task(TASK, root=tmp_path, profile_path=profile(tmp_path), capability_manifest=MANIFEST, evaluator=evaluator)
    assert len(evaluator.calls) == 2
    assert result["selected"]["skill_id"] == "warehouse:notion"
    assert "BODY_NOTION_SENTINEL" in result["selected"]["content"]
    assert result["reason"] == "catalog_choice_selected"
    assert result["capabilities"]["cards"] == []
    assert result["capabilities"]["status"] == "fail_open"
    assert result["capabilities"]["reason"] == "capability_choice_provider_failure"
    assert "typesafe_timeout" not in json.dumps(result["capabilities"])
    assert "BODY_NOTION_SENTINEL" not in json.dumps(result["capabilities"])


def test_public_cards_stay_inside_the_byte_budget(tmp_path):
    manifest = load_manifest(MANIFEST)
    evaluator = scripted("warehouse:notion", use=set(manifest.entries))
    result = select_task(TASK, root=tmp_path, profile_path=profile(tmp_path), capability_manifest=MANIFEST, evaluator=evaluator)
    capabilities = result["capabilities"]
    assert len(capabilities["cards"]) == 5
    assert len(manifest.entries) == 6
    assert "notion.mcp.update-page" not in {card["id"] for card in capabilities["cards"]}
    assert len(json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()) <= STARTUP_CAPABILITY_BUDGET_BYTES
    assert PUBLIC_CAPABILITY_BUDGET_BYTES == STARTUP_CAPABILITY_BUDGET_BYTES
    assert "schema_hash" not in json.dumps(capabilities)
    assert "BODY_NOTION_SENTINEL" not in json.dumps(capabilities)


def test_oversize_capability_text_is_dropped_and_the_skill_remains(tmp_path, monkeypatch):
    monkeypatch.setattr("jev_skill_advisor.select_cli.PUBLIC_CAPABILITY_BUDGET_BYTES", 40)

    def huge(manifest, task, **kwargs):
        return {
            "status": "selected", "reason": "capability_choice_selected", "manifest_hash": manifest.content_hash,
            "cards": [{"id": "notion.mcp.fetch", "description": "X" * 5000, "inputSchema": {"type": "object"}}],
        }

    monkeypatch.setattr("jev_skill_advisor.select_cli.resolve_capabilities", huge)
    evaluator = scripted("warehouse:notion")
    result = select_task(TASK, root=tmp_path, profile_path=profile(tmp_path), capability_manifest=MANIFEST, evaluator=evaluator)
    assert "BODY_NOTION_SENTINEL" in result["selected"]["content"]
    assert result["capabilities"]["cards"] == []
    assert result["capabilities"]["reason"] == "oversized_capability_response"
    assert result["capabilities"]["authorizes_calls"] is False
    blob = json.dumps(result["capabilities"])
    assert "XXXX" not in blob
    assert "inputSchema" not in blob


def test_explicit_non_notion_skill_gets_no_capability(tmp_path):
    def explode(payload, timeout):
        raise AssertionError("explicit non-notion skill must not call jev")

    result = select_named(
        "alpha", root=tmp_path, profile_path=profile(tmp_path), capability_manifest=MANIFEST,
        task="please run notion-fetch before writing", evaluator=explode,
    )
    assert result["selected"]["skill_id"] == "warehouse:alpha"
    assert result["reason"] == "explicit_name"
    assert result["capabilities"]["cards"] == []
    assert result["capabilities"]["reason"] == "not_notion_skill"
    assert result["capabilities"]["authorizes_calls"] is False
    assert "notion.mcp.fetch" not in json.dumps(result["capabilities"]["cards"])
    assert "BODY_ALPHA" not in json.dumps(result["capabilities"])


def test_missing_manifest_preserves_the_skill_json_shape(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("manifest loaded without an explicit path")

    monkeypatch.setattr("jev_skill_advisor.select_cli.load_manifest", explode)
    evaluator = scripted("warehouse:notion", use={"notion.mcp.fetch"})
    result = select_task(TASK, root=tmp_path, profile_path=profile(tmp_path), evaluator=evaluator)
    assert result["selected"]["skill_id"] == "warehouse:notion"
    assert "capabilities" not in result
    assert len(evaluator.calls) == 1
    abstained = select_task(
        "say hello", root=tmp_path, profile_path=profile(tmp_path), capability_manifest=MANIFEST,
        evaluator=scripted("missing"),
    )
    assert abstained["selected"] is None
    assert "capabilities" not in abstained


def test_cli_accepts_an_explicit_manifest_path(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_task(task, **kwargs):
        seen["task_manifest"] = kwargs.get("capability_manifest")
        return {"selected": None, "reason": "catalog_choice_none"}

    def fake_named(name, **kwargs):
        seen["name"] = name
        seen["name_manifest"] = kwargs.get("capability_manifest")
        seen["name_task"] = kwargs.get("task")
        return {"selected": None, "reason": "name_not_found"}

    monkeypatch.setattr("jev_skill_advisor.select_cli.select_task", fake_task)
    monkeypatch.setattr("jev_skill_advisor.select_cli.select_named", fake_named)
    monkeypatch.delenv("JEV_CAPABILITY_MANIFEST", raising=False)
    assert main(["--task", "hello"]) == 0
    assert seen["task_manifest"] is None
    path = tmp_path / "pins.json"
    other = tmp_path / "other.json"
    path.write_text("{}", encoding="utf-8")
    other.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("JEV_CAPABILITY_MANIFEST", str(path))
    assert main(["--task", "hello"]) == 0
    assert seen["task_manifest"] == path
    assert main(["--task", "hello", "--capability-manifest", str(other)]) == 0
    assert seen["task_manifest"] == other
    assert main(["--name", "alpha", "--task", "please run notion-fetch", "--capability-manifest", str(path)]) == 0
    assert seen["name"] == "alpha"
    assert seen["name_manifest"] == path
    assert seen["name_task"] == "please run notion-fetch"
    captured = capsys.readouterr()
    assert "inputSchema" not in captured.out


def test_hook_explicit_non_notion_does_not_authorize_a_tool(monkeypatch, tmp_path):
    from jev_skill_advisor.codex_hook import _run

    def fake_named(name, **kwargs):
        assert kwargs.get("capability_manifest") is None
        return {
            "selected": {
                "skill_id": "warehouse:alpha", "name": "alpha", "content": "BODY", "hash": "abc",
                "path": str(tmp_path / "SKILL.md"),
            },
            "reason": "explicit_name", "status": "complete",
        }

    monkeypatch.setattr("jev_skill_advisor.select_cli.select_named", fake_named)
    result = _run(
        {"task": "please run notion-fetch", "explicit_skills": ["alpha"], "capability_manifest": str(MANIFEST)},
        tmp_path / "profile.json", 4,
    )
    assert result["capabilities"]["cards"] == []
    assert result["capabilities"]["reason"] == "not_notion_skill"
    assert result["capabilities"]["authorizes_calls"] is False
    assert "BODY" not in json.dumps(result["capabilities"])
