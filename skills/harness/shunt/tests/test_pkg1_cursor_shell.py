"""PKG1: Cursor Shell preToolUse + stripped-offset deny."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from shunt.hook_runtime import gate_pre_tool_use, gate_read_tool

ROOT = Path(__file__).resolve().parents[1]
CURSOR_PRE = ROOT / "adapters" / "cursor" / "pre_tool_use_read.py"
FIXTURE = ROOT / "tests" / "fixtures" / "live_harness_gate.txt"


def _run(payload: dict) -> dict:
    env = os.environ.copy()
    env["SHUNT_ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(CURSOR_PRE)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_shell_pretooluse_cat_denies_live_fixture():
    assert FIXTURE.is_file()
    out = _run(
        {
            "hook_event_name": "preToolUse",
            "tool_name": "Shell",
            "tool_input": {"command": f"cat {FIXTURE}"},
        }
    )
    assert out["permission"] == "deny"
    assert "shunt bulk-read" in out["agent_message"]


def test_shell_pretooluse_head_allows():
    out = _run(
        {
            "tool_name": "Shell",
            "tool_input": {"command": f"head -n 20 {FIXTURE}"},
        }
    )
    assert out["permission"] == "allow"


def test_gate_pre_tool_use_routes_shell():
    r = gate_pre_tool_use(
        {"toolName": "Shell", "toolInput": {"command": f"cat {FIXTURE}"}}
    )
    assert r is not None and r.allow is False


def test_registration_matcher_includes_shell():
    reg = json.loads(
        (ROOT / "adapters" / "cursor" / "registration.json").read_text(encoding="utf-8")
    )
    matchers = [e.get("matcher", "") for e in reg["events"]["preToolUse"]]
    assert any("Shell" in m and "Read" in m for m in matchers)


def test_stripped_null_deny_message(tmp_path: Path):
    p = tmp_path / "big.txt"
    p.write_text("\n".join(f"L{i}" for i in range(400)) + "\n", encoding="utf-8")
    r = gate_read_tool(
        {
            "tool_name": "Read",
            "tool_input": {"path": str(p), "offset": None, "limit": None},
            "agent_message": "please read with offset 10 limit 20",
        }
    )
    assert r is not None and not r.allow
    msg = r.message or ""
    assert "shunt bulk-read" in msg
    assert "Re-issue Read" in msg or "explicit offset" in msg
