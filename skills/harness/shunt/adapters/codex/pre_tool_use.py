#!/usr/bin/env python3
"""Codex PreToolUse hook — hard gate for Bash cat/head/tail (shell-only PreToolUse)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
os.environ.setdefault("SHUNT_ROOT", str(_ROOT))
os.environ["SHUNT_INTERNAL"] = "1"

from shunt.hook_runtime import (  # noqa: E402
    claude_decision,
    emit,
    gate_shell,
    read_stdin_json,
    tool_name_of,
)


def main() -> int:
    try:
        payload = read_stdin_json()
    except Exception:
        emit({})
        return 0

    name = tool_name_of(payload).lower()
    # Codex PreToolUse currently fires for shell/Bash only.
    if name and name not in {"bash", "shell", "run_terminal_cmd", ""}:
        emit({})
        return 0

    result = gate_shell(payload)
    if result is None:
        emit({})
        return 0
    out = claude_decision(result)
    emit(out)
    # Also support exit-2 deny for hosts that ignore JSON.
    if not result.allow:
        msg = result.message or "Blocked by shunt read gate."
        sys.stderr.write(msg + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
