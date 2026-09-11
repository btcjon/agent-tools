from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CURSOR_READ = ROOT / "adapters" / "cursor" / "pre_tool_use_read.py"
CURSOR_SHELL = ROOT / "adapters" / "cursor" / "before_shell_execution.py"
CODEX = ROOT / "adapters" / "codex" / "pre_tool_use.py"
CLAUDE = ROOT / "adapters" / "claude" / "pre_tool_use.py"


def _lines(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(n)) + "\n"


def _run(script: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SHUNT_ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


@pytest.fixture()
def oversized(tmp_path: Path) -> Path:
    p = tmp_path / "oversized.py"
    p.write_text(_lines(400), encoding="utf-8")
    return p


@pytest.fixture()
def small(tmp_path: Path) -> Path:
    p = tmp_path / "small.py"
    p.write_text(_lines(20), encoding="utf-8")
    return p


def test_cursor_read_denies_oversized(oversized: Path):
    proc = _run(
        CURSOR_READ,
        {
            "hook_event_name": "preToolUse",
            "tool_name": "Read",
            "tool_input": {"path": str(oversized)},
        },
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["permission"] == "deny"
    assert "shunt bulk-read" in out["agent_message"]


def test_cursor_read_allows_offset_limit(oversized: Path):
    proc = _run(
        CURSOR_READ,
        {
            "tool_name": "Read",
            "tool_input": {"path": str(oversized), "offset": 1, "limit": 40},
        },
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["permission"] == "allow"


def test_cursor_shell_cat_denies(oversized: Path):
    proc = _run(
        CURSOR_SHELL,
        {"hook_event_name": "beforeShellExecution", "command": f"cat {oversized}"},
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["permission"] == "deny"
    assert "shunt bulk-read" in out["agent_message"]


def test_cursor_shell_head_allows(oversized: Path):
    proc = _run(
        CURSOR_SHELL,
        {"command": f"head -n 10 {oversized}"},
    )
    assert json.loads(proc.stdout)["permission"] == "allow"


def test_codex_bash_cat_denies(oversized: Path):
    proc = _run(
        CODEX,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"cat {oversized}"},
        },
    )
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "shunt bulk-read" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_claude_read_denies(oversized: Path):
    proc = _run(
        CLAUDE,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(oversized)},
        },
    )
    assert proc.returncode == 2
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_claude_read_allows_small(small: Path):
    proc = _run(
        CLAUDE,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(small)},
        },
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_fixture_files_parse():
    """Static fixtures under adapters/*/tests/fixtures are valid JSON."""
    for harness in ("cursor", "codex", "claude"):
        fix = ROOT / "adapters" / harness / "tests" / "fixtures"
        assert fix.is_dir()
        for f in fix.glob("*.json"):
            json.loads(f.read_text(encoding="utf-8"))
