"""Shared helpers for harness hook scripts (stdin JSON → gate → stdout JSON)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from shunt.agent_read import AgentReadResult, decide_agent_read, evaluate_shell_command

STRIPPED_WINDOW_MSG = (
    "File is oversized for a full agent read ({lines} lines). "
    "Cursor omitted offset/limit in the hook payload — this is treated as an unbounded read "
    "(do not infer a window from agent_message). "
    "Re-issue Read with explicit offset and limit, or run: shunt bulk-read {path}"
)

DENY_WITH_WINDOW_HINT = (
    "File is oversized for a full agent read ({lines} lines). "
    "Do not cat/Read the whole file. "
    "Re-issue Read with explicit offset and limit, or run: shunt bulk-read {path}"
)


def read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    return json.loads(raw)


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")


def _as_dict(val: Any) -> dict[str, Any]:
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip().startswith("{"):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def tool_input_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge nested + top-level tool args (Cursor/Claude/Grok field-name variants)."""
    merged: dict[str, Any] = {}
    for key in (
        "tool_input",
        "toolInput",
        "input",
        "arguments",
        "args",
        "parameters",
        "params",
    ):
        part = _as_dict(payload.get(key))
        if part:
            merged.update(part)
    tool_call = payload.get("toolCall") or payload.get("tool_call")
    if isinstance(tool_call, dict):
        for key in ("input", "arguments", "args"):
            part = _as_dict(tool_call.get(key))
            if part:
                merged.update(part)
    for key in (
        "path",
        "file_path",
        "filePath",
        "target_file",
        "offset",
        "limit",
        "start_line",
        "end_line",
        "startLine",
        "endLine",
        "command",
        "cmd",
        "CommandLine",
    ):
        if key in payload and payload.get(key) is not None and key not in merged:
            merged[key] = payload[key]
    return merged


def tool_name_of(payload: dict[str, Any]) -> str:
    name = payload.get("tool_name") or payload.get("toolName") or payload.get("tool")
    if not name:
        tc = payload.get("toolCall") or payload.get("tool_call")
        if isinstance(tc, dict):
            name = tc.get("name") or tc.get("toolName")
    return str(name or "")


def _to_int(val: Any) -> int | None:
    if val is None or val is False:
        return None
    if isinstance(val, str) and not val.strip():
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def read_path_offset_limit(tool_input: dict[str, Any]) -> tuple[str | None, int | None, int | None]:
    path = (
        tool_input.get("path")
        or tool_input.get("file_path")
        or tool_input.get("filePath")
        or tool_input.get("target_file")
        or tool_input.get("targetFile")
    )
    if path is not None:
        path = str(path)

    offset = None
    for key in ("offset", "Offset", "start_line", "startLine"):
        if tool_input.get(key) is not None:
            offset = _to_int(tool_input.get(key))
            if offset is not None:
                break

    limit = None
    for key in ("limit", "Limit", "max_lines", "line_limit"):
        if tool_input.get(key) is not None:
            limit = _to_int(tool_input.get(key))
            if limit is not None:
                break

    end_line = _to_int(
        tool_input.get("end_line") if tool_input.get("end_line") is not None else tool_input.get("endLine")
    )
    if limit is None and offset is not None and end_line is not None and end_line >= offset:
        limit = end_line - offset + 1
    return path, offset, limit


def cursor_decision(result: AgentReadResult) -> dict[str, Any]:
    if result.allow:
        return {"permission": "allow"}
    msg = result.message or "Blocked by shunt read gate."
    return {
        "permission": "deny",
        "user_message": msg,
        "agent_message": msg,
    }


def claude_decision(result: AgentReadResult) -> dict[str, Any]:
    if result.allow:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }
    msg = result.message or "Blocked by shunt read gate."
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }
    }


def _enrich_deny(result: AgentReadResult, *, stripped_nulls: bool) -> AgentReadResult:
    if result.allow:
        return result
    lines = result.lines if result.lines is not None else "?"
    tmpl = STRIPPED_WINDOW_MSG if stripped_nulls else DENY_WITH_WINDOW_HINT
    msg = tmpl.format(lines=lines, path=result.path)
    return AgentReadResult(
        False,
        result.reason,
        result.path,
        lines=result.lines,
        bytes=result.bytes,
        message=msg,
    )


def gate_read_tool(payload: dict[str, Any]) -> AgentReadResult | None:
    """Return None when not a Read-like tool (caller allows)."""
    name = tool_name_of(payload).lower().replace("-", "_")
    tin = tool_input_of(payload)
    path, offset, limit = read_path_offset_limit(tin)
    if not path:
        raw = payload.get("file_path") or payload.get("path") or payload.get("filePath")
        path = str(raw) if raw is not None else None
    if not path:
        return None
    read_names = {
        "read",
        "read_file",
        "readfile",
        "view_file",
        "viewfile",
        "read_file_v2",
    }
    if name and name not in read_names:
        return None
    # PKG1: when Cursor strips offset/limit (null/absent), treat as unbounded.
    # Do NOT infer allow from agent_message — that caused false allows.
    stripped_nulls = offset is None and limit is None and (
        "offset" in tin or "limit" in tin or "file_path" in tin or "path" in tin
    )
    result = decide_agent_read(path, offset=offset, limit=limit)
    if not result.allow:
        return _enrich_deny(result, stripped_nulls=stripped_nulls and offset is None and limit is None)
    return result


def gate_shell(payload: dict[str, Any]) -> AgentReadResult | None:
    tin = tool_input_of(payload)
    command = (
        payload.get("command")
        or tin.get("command")
        or tin.get("cmd")
        or tin.get("CommandLine")
        or ""
    )
    if not command:
        return None
    result = evaluate_shell_command(str(command))
    if result is None:
        return None
    if not result.allow:
        return _enrich_deny(result, stripped_nulls=False)
    return result


def gate_pre_tool_use(payload: dict[str, Any]) -> AgentReadResult | None:
    """Gate Read-like or Shell-like tools for Cursor/Codex preToolUse."""
    name = tool_name_of(payload).lower().replace("-", "_")
    shell_names = {
        "shell",
        "bash",
        "run_terminal_command",
        "run_terminal_cmd",
        "run_command",
        "terminal",
    }
    if name in shell_names or (not name and tool_input_of(payload).get("command")):
        return gate_shell(payload)
    return gate_read_tool(payload)


def ensure_internal_env() -> None:
    os.environ["SHUNT_INTERNAL"] = "1"


def package_root_from_adapter_file(adapter_file: str | Path) -> Path:
    return Path(adapter_file).resolve().parents[2]
