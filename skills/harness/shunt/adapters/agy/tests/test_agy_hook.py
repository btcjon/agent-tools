"""AGY adapter: PreToolUse fixture dry-run."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "adapters" / "agy" / "pretooluse.sh"


def test_agy_denies_cat_large(tmp_path: Path):
    path = tmp_path / "a-large.txt"
    path.write_text("\n".join(f"L{i}" for i in range(400)) + "\n", encoding="utf-8")
    payload = {
        "toolCall": {
            "name": "run_command",
            "args": {"CommandLine": f"cat {path}"},
        },
        "stepIdx": 1,
    }
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert data["decision"] == "deny"
    assert "shunt bulk-read" in data["reason"]


def test_agy_allows_head(tmp_path: Path):
    path = tmp_path / "a-large.txt"
    path.write_text("\n".join(f"L{i}" for i in range(400)) + "\n", encoding="utf-8")
    payload = {
        "toolCall": {
            "name": "run_command",
            "args": {"CommandLine": f"head -n 5 {path}"},
        }
    }
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert data["decision"] == "allow"


def test_agy_readme_hard():
    text = (ROOT / "adapters" / "agy" / "README.md").read_text(encoding="utf-8")
    assert "HARD" in text
    assert "~/.gemini" in text
