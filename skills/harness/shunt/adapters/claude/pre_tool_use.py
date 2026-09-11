#!/usr/bin/env python3
"""Claude Code PreToolUse — gate Read + Bash cat/head/tail (Spotify-style)."""

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
    gate_read_tool,
    gate_shell,
    read_stdin_json,
    tool_input_of,
    tool_name_of,
)


def main() -> int:
    try:
        payload = read_stdin_json()
    except Exception:
        emit({})
        return 0

    name = tool_name_of(payload)
    name_l = name.lower()
    tin = tool_input_of(payload)

    result = None
    if name_l in {"read", "read_file", ""} and (
        tin.get("file_path") or tin.get("path") or payload.get("file_path")
    ):
        result = gate_read_tool(payload)
    elif name_l in {"bash", "shell", ""}:
        result = gate_shell(payload)
    else:
        # Matcher may have filtered; try both.
        result = gate_read_tool(payload)
        if result is None:
            result = gate_shell(payload)

    if result is None:
        emit({})
        return 0

    emit(claude_decision(result))
    if not result.allow:
        sys.stderr.write((result.message or "Blocked by shunt read gate.") + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
