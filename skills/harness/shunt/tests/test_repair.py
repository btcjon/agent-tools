"""Repair coverage: skip-names + Cursor windowed payload shapes."""

from __future__ import annotations

import json
from pathlib import Path

from shunt.hook_runtime import gate_read_tool, tool_input_of
from shunt.install import list_adapter_scripts, list_gate_scripts, load_registration, plan_install


def test_list_adapter_scripts_skips_install_and_readme():
    # Real package adapters must never surface install.sh as a hook script.
    for harness in ("pi", "hermes", "grok", "agy"):
        names = {p.name for p in list_adapter_scripts(harness)}
        assert "install.sh" not in names
        assert "README.md" not in names
        assert not any(n.endswith(".md") for n in names)


def test_gate_scripts_exclude_noise(tmp_path: Path, monkeypatch):
    import shunt.install as inst

    pkg = tmp_path / "pkg"
    ad = pkg / "adapters" / "demo"
    ad.mkdir(parents=True)
    (ad / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (ad / "README.md").write_text("x", encoding="utf-8")
    (ad / "pre_tool_use.py").write_text("print(1)\n", encoding="utf-8")
    (ad / "notes.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(inst, "package_root", lambda: pkg)
    monkeypatch.setattr(inst, "adapters_root", lambda: pkg / "adapters")
    scripts = list_adapter_scripts("demo")
    assert {p.name for p in scripts} == {"pre_tool_use.py"}
    assert [p.name for p in list_gate_scripts("demo")] == ["pre_tool_use.py"]
    # No registration.json → default registration uses gate script only
    reg = load_registration("demo", scripts)
    assert reg is not None
    blob = json.dumps(reg)
    assert "pre_tool_use.py" in blob
    assert "install.sh" not in blob


def test_plan_install_skips_external_harnesses(tmp_path: Path, monkeypatch):
    import shunt.install as inst

    home = tmp_path / "home"
    home.mkdir()
    pkg = tmp_path / "pkg"
    for h in ("cursor", "pi", "agy"):
        d = pkg / "adapters" / h
        d.mkdir(parents=True)
        if h == "cursor":
            (d / "registration.json").write_text(
                json.dumps({"format": "hooks-v1", "events": {"preToolUse": [{"command": "{{SCRIPT:pre_tool_use_read.py}}"}]}}),
                encoding="utf-8",
            )
            (d / "pre_tool_use_read.py").write_text("x", encoding="utf-8")
        else:
            (d / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (d / "pretooluse.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(inst, "package_root", lambda: pkg)
    monkeypatch.setattr(inst, "adapters_root", lambda: pkg / "adapters")
    items = {i.harness: i for i in plan_install(home)}
    assert items["pi"].action == "skip"
    assert "install.sh" not in items["pi"].detail or "external" in items["pi"].detail
    assert items["agy"].action == "skip"
    assert items["cursor"].action in {"create", "merge"}


def test_cursor_windowed_toolinput_camel_case(tmp_path: Path):
    p = tmp_path / "big.py"
    p.write_text("\n".join(f"line {i}" for i in range(400)) + "\n", encoding="utf-8")
    payload = {
        "toolName": "Read",
        "toolInput": {"file_path": str(p), "offset": 10, "limit": 20},
    }
    tin = tool_input_of(payload)
    assert tin["file_path"] == str(p)
    assert tin["offset"] == 10
    r = gate_read_tool(payload)
    assert r is not None and r.allow and r.reason == "windowed_read"


def test_cursor_windowed_top_level_fields(tmp_path: Path):
    p = tmp_path / "big.py"
    p.write_text("\n".join(f"line {i}" for i in range(400)) + "\n", encoding="utf-8")
    payload = {"tool_name": "Read", "path": str(p), "offset": 5, "limit": 15}
    r = gate_read_tool(payload)
    assert r is not None and r.allow


def test_cursor_windowed_start_end_line(tmp_path: Path):
    p = tmp_path / "big.py"
    p.write_text("\n".join(f"line {i}" for i in range(400)) + "\n", encoding="utf-8")
    payload = {
        "tool_name": "Read",
        "tool_input": {"path": str(p), "startLine": 40, "endLine": 60},
    }
    r = gate_read_tool(payload)
    assert r is not None and r.allow and r.reason == "windowed_read"


def test_cursor_stripped_offset_denies_despite_agent_message(tmp_path: Path):
    """PKG1: null offset/limit is unbounded — never allow from agent_message alone."""
    p = tmp_path / "big.py"
    p.write_text("\n".join(f"line {i}" for i in range(486)) + "\n", encoding="utf-8")
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(p), "offset": None, "limit": None},
        "agent_message": "Reading lines 40 to 60 of the file for context.",
    }
    r = gate_read_tool(payload)
    assert r is not None and r.allow is False
    assert "shunt bulk-read" in (r.message or "")
    assert "offset" in (r.message or "").lower() or "limit" in (r.message or "").lower()
    # Explicit window still allows
    windowed = gate_read_tool(
        {
            "tool_name": "Read",
            "tool_input": {"file_path": str(p), "offset": 40, "limit": 21},
        }
    )
    assert windowed is not None and windowed.allow and windowed.reason == "windowed_read"
