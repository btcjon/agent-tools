"""Tests for idempotent hook merge / uninstall."""

from __future__ import annotations

import json
from pathlib import Path

from shunt.install import (
    HarnessSpec,
    apply_install,
    apply_uninstall,
    merge_hooks,
    remove_shunt_hooks,
)


def test_merge_preserves_unrelated_cursor_hooks(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    cursor = home / ".cursor"
    cursor.mkdir(parents=True)
    cfg = cursor / "hooks.json"
    cfg.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "sessionStart": [{"command": "/other/orca.sh session"}],
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Point package adapters at a temp tree by monkeypatching package_root
    pkg = tmp_path / "pkg"
    adapters = pkg / "adapters" / "cursor"
    adapters.mkdir(parents=True)
    script = adapters / "gate.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    (adapters / "registration.json").write_text(
        json.dumps(
            {
                "format": "hooks-v1",
                "events": {"preToolUse": [{"command": "{{SCRIPT:gate.sh}}"}]},
            }
        ),
        encoding="utf-8",
    )

    import shunt.install as inst

    monkeypatch.setattr(inst, "package_root", lambda: pkg)
    monkeypatch.setattr(inst, "adapters_root", lambda: pkg / "adapters")

    rc = apply_install(dry_run=False, home=home)
    assert rc == 0
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["hooks"]["sessionStart"][0]["command"] == "/other/orca.sh session"
    assert any("gate.sh" in e.get("command", "") for e in doc["hooks"]["preToolUse"])
    assert doc["hooks"]["preToolUse"][0].get("_shunt") is True

    # Idempotent second install
    rc2 = apply_install(dry_run=False, home=home)
    assert rc2 == 0
    doc2 = json.loads(cfg.read_text(encoding="utf-8"))
    assert len(doc2["hooks"]["preToolUse"]) == 1
    assert len(doc2["hooks"]["sessionStart"]) == 1

    rc3 = apply_uninstall(dry_run=False, home=home)
    assert rc3 == 0
    doc3 = json.loads(cfg.read_text(encoding="utf-8"))
    assert "preToolUse" in doc3["hooks"]
    assert doc3["hooks"]["preToolUse"] == []
    assert doc3["hooks"]["sessionStart"][0]["command"] == "/other/orca.sh session"


def test_merge_claude_style_nested():
    spec = HarnessSpec("claude", ".claude/settings.json", "claude")
    doc = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "/bin/orca.mjs", "timeout": 15}]}
            ]
        }
    }
    root = Path("/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/custom-skills/skills/harness/shunt")
    reg = {
        "events": {
            "PreToolUse": [
                {
                    "matcher": "Read",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(root / "adapters/claude/shunt-pretool.sh"),
                            "timeout": 30,
                        }
                    ],
                }
            ]
        }
    }
    new_doc, changed = merge_hooks(doc, spec, reg)
    assert changed
    assert len(new_doc["hooks"]["UserPromptSubmit"]) == 1
    assert new_doc["hooks"]["PreToolUse"][0]["_shunt"] is True
    removed, ch2 = remove_shunt_hooks(new_doc, spec)
    assert ch2
    assert removed["hooks"]["PreToolUse"] == []
    assert removed["hooks"]["UserPromptSubmit"]
