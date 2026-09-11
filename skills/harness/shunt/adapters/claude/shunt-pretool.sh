#!/usr/bin/env bash
# Claude PreToolUse entrypoint — delegates to pre_tool_use.py (WP3).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export SHUNT_ROOT="${SHUNT_ROOT:-$ROOT}"
export SHUNT_INTERNAL=1
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "${ROOT}/adapters/claude/pre_tool_use.py"
