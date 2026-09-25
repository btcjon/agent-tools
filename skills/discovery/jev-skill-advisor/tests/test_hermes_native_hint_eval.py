import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hermes_native_hint_eval.py"
spec = importlib.util.spec_from_file_location("hermes_native_hint_eval", MODULE_PATH)
runner = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def test_schedule_is_interleaved_and_reversed():
    plan = [(r, case.name, arm) for r, case, arm in runner.schedule(2)]
    assert len(plan) == 20
    assert plan[:4] == [
        (1, "title", "bridge"), (1, "title", "native"),
        (1, "search", "bridge"), (1, "search", "native"),
    ]
    assert plan[10:12] == [(2, "title", "native"), (2, "title", "bridge")]
    assert {case.name for case in runner.CASES} == {"title", "search", "headings", "favorites", "teams"}
    assert all("Read-only check:" in case.prompt for case in runner.CASES)
    assert all("Do not edit, create, or delete anything." in case.prompt for case in runner.CASES)
    with pytest.raises(ValueError):
        list(runner.schedule(3))


def test_dry_run_never_calls_remote(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(runner, "_ssh", lambda *_args, **_kwargs: pytest.fail("remote call"))
    output = tmp_path / "results.jsonl"
    assert runner.main(["--output", str(output), "--dry-run"]) == 0
    assert not output.exists()
    assert len(capsys.readouterr().out.splitlines()) == 20


def test_run_records_only_allowlisted_metadata(monkeypatch):
    commands = []

    def fake_ssh(command, **_kwargs):
        commands.append(command)
        if "--usage-file" in command:
            return runner.CASES[0].expected
        return json.dumps({
            "session_id": "session123", "model": "gpt-6-sol", "api_calls": 3,
            "input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 4,
            "cache_write_tokens": 0, "total_tokens": 16,
        })

    monkeypatch.setattr(runner, "_ssh", fake_ssh)
    monkeypatch.setattr(runner, "_metadata", lambda _sid: (
        {"session_id": "session123", "api_calls": 3, "tool_calls": 2,
         "top_level_tools": ["tool_describe", "tool_call"],
         "audit_error": None,
         "invocation_targets": ["mcp__notion__notion_fetch"],
         "described_targets": ["mcp__notion__notion_fetch"]},
        {"latency_ms": 120, "route_mode": "native", "status": "selected",
         "capability_ids": ["notion.mcp.fetch"]},
    ))
    result = runner.run_one(1, runner.CASES[0], "native", "run123")
    assert result["correct"] is True
    assert result["answer_kind"] == "expected"
    assert "answer" not in result and "prompt" not in result
    assert result["discovery"]["route_mode"] == "native"
    assert "JEV_HERMES_CAPABILITY_MODE=native" in commands[0]
    assert "--usage-file" in commands[0]
    assert "Do not edit, create, or delete anything." in commands[0]


def test_unexpected_answer_is_not_saved(monkeypatch):
    monkeypatch.setattr(runner, "_ssh", lambda command, **_kwargs: (
        "PRIVATE CONTENT SENTINEL" if "--usage-file" in command else json.dumps({"session_id": "session123", "model": "gpt-6-sol"})
    ))
    monkeypatch.setattr(runner, "_metadata", lambda _sid: (
        {"session_id": "session123", "api_calls": None, "tool_calls": 0,
         "top_level_tools": [], "invocation_targets": [], "described_targets": [],
         "audit_error": None},
        {"latency_ms": 10, "route_mode": "bridge", "status": "selected", "capability_ids": ["notion.mcp.fetch"]},
    ))
    result = runner.run_one(1, runner.CASES[0], "bridge", "run123")
    assert result["answer_kind"] == "unexpected"
    assert "PRIVATE CONTENT SENTINEL" not in json.dumps(result)


def test_direct_and_deferred_tool_audit_fails_closed():
    safe = frozenset({"mcp__notion__notion_fetch"})
    runner.audit_tools(["tool_describe", "tool_call"], ["mcp__notion__notion_fetch"], read_only_native=safe)
    with pytest.raises(runner.EvaluationError, match="unexpected_direct_tool"):
        runner.audit_tools(["mcp__notion__notion_update_page"], [], read_only_native=safe)
    with pytest.raises(runner.EvaluationError, match="unexpected_direct_tool"):
        runner.audit_tools(["mcp__jev_skill_advisor__capability_call"], [], read_only_native=safe)
    with pytest.raises(runner.EvaluationError, match="unexpected_direct_tool"):
        runner.audit_tools(["terminal"], [], read_only_native=safe)
    with pytest.raises(runner.EvaluationError, match="unexpected_deferred_tool"):
        runner.audit_tools(["tool_call"], ["mcp__notion__notion_create_pages"], read_only_native=safe)
    with pytest.raises(runner.EvaluationError, match="unexpected_deferred_tool"):
        runner.audit_tools(["tool_call"], ["mcp__jev_skill_advisor__capability_call"], read_only_native=safe)
    with pytest.raises(runner.EvaluationError):
        runner.audit_tools(["tool_call"], [None], read_only_native=safe)


@pytest.mark.parametrize("wrapper,arguments,expected_error", [
    ({"function": {"name": "tool_call"}}, '{"calls":[{"name":"mcp__notion__notion_fetch"}]}', None),
    ({"function": {"name": "tool_call"}}, {"calls": [{"name": "mcp__notion__notion_fetch"}]}, None),
    ({"name": "tool_call"}, '{"calls":[{"name":"mcp__notion__notion_fetch"}]}', None),
    ({"function": {"name": "tool_call"}}, '{not-json', "invalid_deferred_arguments"),
    ({"name": "tool_call"}, {"calls": [{}]}, "invalid_deferred_target"),
    ({"name": "tool_call"}, {"calls": []}, "invalid_deferred_calls"),
])
def test_export_projection_never_silently_drops_deferred_calls(wrapper, arguments, expected_error):
    if "function" in wrapper:
        wrapper["function"]["arguments"] = arguments
    else:
        wrapper["arguments"] = arguments
    payload = {"id": "session123", "messages": [{"tool_calls": [wrapper]}]}
    completed = subprocess.run(
        ["jq", "-c", runner._EXPORT_JQ], input=json.dumps(payload),
        text=True, capture_output=True, check=True,
    )
    projected = json.loads(completed.stdout)
    assert projected["top_level_tools"] == ["tool_call"]
    assert projected["audit_error"] == expected_error
    if expected_error is None:
        assert projected["invocation_targets"] == ["mcp__notion__notion_fetch"]


def test_projection_error_prevents_a_correct_result(monkeypatch):
    monkeypatch.setattr(runner, "_ssh", lambda command, **_kwargs: (
        runner.CASES[0].expected if "--usage-file" in command else json.dumps({
            "session_id": "session123", "model": "gpt-6-sol", "api_calls": 3,
        })
    ))
    monkeypatch.setattr(runner, "_metadata", lambda _sid: (
        {"session_id": "session123", "api_calls": 3, "top_level_tools": ["tool_call"],
         "invocation_targets": [], "audit_error": "invalid_deferred_arguments"},
        {"route_mode": "bridge", "status": "selected", "capability_ids": ["notion.mcp.fetch"]},
    ))
    with pytest.raises(runner.EvaluationError, match="tool_projection_incomplete"):
        runner.run_one(1, runner.CASES[0], "bridge", "run123")


def test_manifest_read_only_names_exclude_writes():
    names = runner._read_only_native_names()
    assert "mcp__notion__notion_fetch" in names
    assert "mcp__notion__notion_search" in names
    assert "mcp__notion__notion_update_page" not in names


@pytest.mark.parametrize("usage_model,event,export_calls,error", [
    ("other-model", {"route_mode": "native", "status": "selected", "capability_ids": ["notion.mcp.fetch"]}, 3, "model_mismatch"),
    ("gpt-6-sol", {"route_mode": "bridge", "status": "selected", "capability_ids": ["notion.mcp.fetch"]}, 3, "discovery_mismatch"),
    ("gpt-6-sol", {"route_mode": "native", "status": "none", "capability_ids": ["notion.mcp.fetch"]}, 3, "discovery_mismatch"),
    ("gpt-6-sol", {"route_mode": "native", "status": "selected", "capability_ids": ["notion.mcp.search"]}, 3, "capability_mismatch"),
    ("gpt-6-sol", {"route_mode": "native", "status": "selected", "capability_ids": ["notion.mcp.fetch"]}, 4, "api_call_mismatch"),
])
def test_selection_and_model_mismatch_do_not_count_as_correct(monkeypatch, usage_model, event, export_calls, error):
    monkeypatch.setattr(runner, "_ssh", lambda command, **_kwargs: (
        runner.CASES[0].expected if "--usage-file" in command else json.dumps({
            "session_id": "session123", "model": usage_model, "api_calls": 3,
        })
    ))
    monkeypatch.setattr(runner, "_metadata", lambda _sid: (
        {"session_id": "session123", "api_calls": export_calls, "tool_calls": 2,
         "top_level_tools": ["tool_call"], "invocation_targets": ["mcp__notion__notion_fetch"],
         "described_targets": [], "audit_error": None}, event,
    ))
    with pytest.raises(runner.EvaluationError, match=error):
        runner.run_one(1, runner.CASES[0], "native", "run123")


def test_remote_failure_is_explicit(monkeypatch):
    class Result:
        returncode = 124
        stdout = ""
        stderr = "SECRET SENTINEL"

    monkeypatch.setattr(runner.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(runner.EvaluationError, match="remote_exit_124") as exc:
        runner._ssh("true")
    assert "SECRET SENTINEL" not in str(exc.value)


def test_missing_session_id_fails_before_remote_call(monkeypatch):
    monkeypatch.setattr(runner, "_remote_json", lambda *_args: pytest.fail("remote call"))
    with pytest.raises(runner.EvaluationError, match="invalid_session_id"):
        runner._metadata(None)


def test_duplicate_session_is_recorded_as_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(runner, "run_one", lambda _r, _case, _arm, _id: {
        "session_id": "same-session", "correct": True, "repeat": 1, "task": "title",
        "arm": "bridge", "api_calls": 3, "total_tokens": 10,
    })
    output = tmp_path / "duplicate.jsonl"
    with pytest.raises(SystemExit) as exc:
        runner.main(["--output", str(output), "--repeats", "1"])
    assert exc.value.code == 1
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[-1]["error"] == "duplicate_session_id"
