"""Grok adapter: PreToolUse fixture dry-run."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "adapters" / "grok" / "pretooluse.sh"


def test_grok_denies_oversized_read(tmp_path: Path):
    path = tmp_path / "g-large.txt"
    path.write_text("\n".join(f"L{i}" for i in range(400)) + "\n", encoding="utf-8")
    payload = {
        "hookEventName": "pre_tool_use",
        "toolName": "read_file",
        "toolInput": {"path": str(path)},
    }
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload).encode(),
        capture_output=True,
    )
    assert proc.returncode in (0, 2)  # Grok: exit 2 = explicit deny
    data = json.loads(proc.stdout)
    assert data["decision"] == "deny"
    assert "shunt bulk-read" in data["reason"]


def test_grok_allows_scoped(tmp_path: Path):
    path = tmp_path / "g-large.txt"
    path.write_text("\n".join(f"L{i}" for i in range(400)) + "\n", encoding="utf-8")
    payload = {
        "toolName": "read_file",
        "toolInput": {"path": str(path), "offset": 1, "limit": 20},
    }
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert data["decision"] == "allow"


def test_grok_readme_hard_not_advisory():
    text = (ROOT / "adapters" / "grok" / "README.md").read_text(encoding="utf-8")
    assert "HARD" in text
    assert "advisory-only" not in text.lower() or "not** advisory" in text.lower() or "not" in text.lower()
