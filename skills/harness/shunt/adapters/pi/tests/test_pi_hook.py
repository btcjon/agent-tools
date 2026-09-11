"""Pi adapter: fixture dry-run via shared gate + pi-json hook shape."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "adapters" / "_common" / "shunt-hook.sh"
GATE = ROOT / "adapters" / "_common" / "native_read_gate.py"


def _large(tmp_path: Path) -> Path:
    p = tmp_path / "pi-large.txt"
    p.write_text("\n".join(f"L{i}" for i in range(400)) + "\n", encoding="utf-8")
    return p


def test_pi_extension_gate_blocks(tmp_path: Path):
    path = _large(tmp_path)
    payload = {"toolName": "read", "toolInput": {"path": str(path)}}
    proc = subprocess.run(
        [sys.executable, str(GATE), "--json-stdin"],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env={k: v for k, v in os.environ.items() if k != "SHUNT_INTERNAL"},
    )
    # JSON protocol: exit 0 always; block flag in body (Node spawn/execFile-safe).
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["block"] is True


def test_pi_json_hook_dry_run(tmp_path: Path):
    path = _large(tmp_path)
    payload = {"toolName": "read", "toolInput": {"path": str(path)}}
    proc = subprocess.run(
        ["bash", str(HOOK), "pi-json"],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert data["block"] is True
    assert "shunt bulk-read" in data["reason"]


def test_pi_readme_states_hard():
    readme = (ROOT / "adapters" / "pi" / "README.md").read_text(encoding="utf-8")
    assert "**HARD**" in readme or "hard" in readme.lower()
    assert "install" in readme.lower()
