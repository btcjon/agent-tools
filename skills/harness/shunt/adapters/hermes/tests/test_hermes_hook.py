"""Hermes adapter: pre_tool_call fixture dry-run."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "adapters" / "hermes" / "pre_tool_call.sh"


def test_hermes_blocks_oversized(tmp_path: Path):
    path = tmp_path / "h-large.txt"
    path.write_text("\n".join(f"L{i}" for i in range(400)) + "\n", encoding="utf-8")
    payload = {
        "hook_event_name": "pre_tool_call",
        "tool_name": "bash",
        "args": {"command": f"cat {path}"},
    }
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert data.get("action") == "block"
    assert "shunt bulk-read" in data.get("message", "")


def test_hermes_allows_small(tmp_path: Path):
    path = tmp_path / "h-small.txt"
    path.write_text("hi\n", encoding="utf-8")
    payload = {
        "tool_name": "read",
        "args": {"path": str(path)},
    }
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=True,
    )
    # empty object = no directive
    data = json.loads(proc.stdout or "{}")
    assert data.get("action") != "block"


def test_hermes_readme_hard():
    text = (ROOT / "adapters" / "hermes" / "README.md").read_text(encoding="utf-8")
    assert "HARD" in text
    assert "install" in text.lower()
