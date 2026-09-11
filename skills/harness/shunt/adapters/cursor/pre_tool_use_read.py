#!/usr/bin/env python3
"""Cursor preToolUse hook — gate oversized Read and Shell cat|head|tail."""

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
    cursor_decision,
    emit,
    gate_pre_tool_use,
    read_stdin_json,
)


def main() -> int:
    try:
        payload = read_stdin_json()
    except Exception:
        emit({"permission": "allow"})
        return 0
    result = gate_pre_tool_use(payload)
    if result is None:
        emit({"permission": "allow"})
        return 0
    emit(cursor_decision(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
