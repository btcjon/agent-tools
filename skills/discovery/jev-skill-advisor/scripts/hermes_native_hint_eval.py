"""Run the fixed, read-only Hermes bridge/native acceptance comparison.

Only allowlisted metadata is saved. Hermes's redacted session export is parsed
on the remote host so prompts, page bodies, and tool arguments never enter the
local result file. Tool auditing detects unexpected calls *after execution*;
it does not prevent a write. The default bridge/gateway configuration is not
changed.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

HOST = "dest"
HERMES = "/home/dev/.hermes/hermes-agent-maintained/venv/bin/hermes"
REMOTE_DIR = "/home/dev/.local/state/jev-skill-advisor/registration-backups/native-hint-20260925-7b375c0"
EVENTS = "/home/dev/.local/state/jev-skill-advisor/capability-events/hermes.jsonl"
PAGE_ID = "3e145e44-58bf-813a-a125-f94953b1c02c"
ROOT = Path(__file__).resolve().parents[1]
NOTION_MANIFEST = ROOT / "examples" / "notion-mcp-live-manifest-2026-09-24.json"
EXPECTED_MODEL = "gpt-6-sol"
_SAFE_WRAPPERS = frozenset({"tool_search", "tool_describe", "tool_call"})
_SAFE_JEV_TOOLS = frozenset({
    "mcp__jev_skill_advisor__notion_fetch",
    "mcp__jev_skill_advisor__skill_suggest",
    "mcp__jev_skill_advisor__skill_read",
    "mcp__jev_skill_advisor__capability_describe",
})


@dataclass(frozen=True)
class Case:
    name: str
    prompt: str
    expected: str
    capability_id: str


CASES = (
    Case("title", f"Read-only check: use the connected Notion page with ID {PAGE_ID}. Reply with only its title, or UNAVAILABLE if you cannot read it. Do not edit, create, or delete anything.", "obsidian-vault-operations", "notion.mcp.fetch"),
    Case("search", "Read-only check: search connected Notion for the exact title obsidian-vault-operations. Reply with only the UUID of the matching page, or UNAVAILABLE if none is found. Do not edit, create, or delete anything.", PAGE_ID, "notion.mcp.search"),
    Case("headings", f"Read-only check: fetch Notion page {PAGE_ID} and count lines in its markdown body that begin exactly with two hash signs followed by a space (level-two headings). Reply with only the count, or UNAVAILABLE if it cannot be read. Do not edit, create, or delete anything.", "13", "notion.mcp.fetch"),
    Case("favorites", r"Read-only check: use connected Notion to list the current user\u0027s favorite pages, first page with a limit of 20. Reply with only the number of results returned, or UNAVAILABLE if the list cannot be read. Do not edit, create, or delete anything.", "0", "notion.mcp.list-favorite-pages"),
    Case("teams", "Read-only check: use connected Notion to list teams in the current workspace. Reply with only the number of joined teams, or UNAVAILABLE if the list cannot be read. Do not edit, create, or delete anything.", "0", "notion.mcp.get-teams"),
)

# Project only fixed read-only prompts. Never accept an arbitrary prompt file.
assert all("Read-only check:" in case.prompt and "Do not edit, create, or delete anything." in case.prompt for case in CASES)


class EvaluationError(RuntimeError):
    pass


def schedule(repeats: int):
    if not 1 <= repeats <= 2:
        raise ValueError("repeats must be 1 or 2")
    for repeat in range(1, repeats + 1):
        arms = ("bridge", "native") if repeat == 1 else ("native", "bridge")
        for case in CASES:
            for arm in arms:
                yield repeat, case, arm


def _ssh(command: str, *, timeout: int = 205) -> str:
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", HOST, command],
            text=True, capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvaluationError("remote_timeout") from exc
    if result.returncode != 0:
        raise EvaluationError(f"remote_exit_{result.returncode}")
    return result.stdout.strip()


def _remote_json(command: str):
    try:
        return json.loads(_ssh(command))
    except json.JSONDecodeError as exc:
        raise EvaluationError("invalid_remote_metadata") from exc


_EXPORT_JQ = (
    'def toolname: (.function.name // .name); '
    'def args: (.function.arguments // .arguments) as $raw | '
    'if ($raw|type)=="string" then (try ($raw|fromjson) catch null) '
    'elif ($raw|type)=="object" then $raw else null end; '
    '{session_id:.id,api_calls:.api_call_count,tool_calls:.tool_call_count,'
    'top_level_tools:[.messages[].tool_calls[]? | toolname],'
    'audit_error: ([.messages[].tool_calls[]? | select(toolname=="tool_call") | args as $a | '
    'if ($a|type)!="object" then "invalid_deferred_arguments" '
    'elif ($a.calls|type)!="array" or ($a.calls|length)==0 then "invalid_deferred_calls" '
    'elif any($a.calls[]; if type!="object" then true '
    'elif (.name|type)!="string" then true else (.name|length)==0 end) '
    'then "invalid_deferred_target" else empty end] | first // null),'
    'invocation_targets:[.messages[].tool_calls[]? | select(toolname=="tool_call") | args as $a | '
    'if ($a|type)=="object" and ($a.calls|type)=="array" then $a.calls[] | .name else empty end],'
    'described_targets:[.messages[].tool_calls[]? | select(toolname=="tool_describe") | '
    'args as $a | if ($a|type)=="object" and ($a.names|type)=="array" '
    'then $a.names[] else empty end]}'
)
_EVENT_JQ = (
    '[.[] | select(.session_id==$sid and .stage=="discovery") | '
    '{latency_ms,route_mode,status,capability_ids}] | last'
)


def _metadata(session_id: str):
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", session_id):
        raise EvaluationError("invalid_session_id")
    exported = _remote_json(
        f"{shlex.quote(HERMES)} sessions export - --format jsonl --session-id "
        f"{shlex.quote(session_id)} --redact 2>/dev/null | jq -c {shlex.quote(_EXPORT_JQ)}"
    )
    event = _remote_json(
        f"jq -sc --arg sid {shlex.quote(session_id)} {shlex.quote(_EVENT_JQ)} {shlex.quote(EVENTS)}"
    )
    if exported.get("session_id") != session_id:
        raise EvaluationError("session_export_mismatch")
    return exported, event


def _read_only_native_names():
    try:
        document = json.loads(NOTION_MANIFEST.read_text(encoding="utf-8"))
        entries = document["entries"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvaluationError("read_only_manifest_unavailable") from exc
    if not isinstance(entries, list) or not entries:
        raise EvaluationError("read_only_manifest_invalid")
    names = set()
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get("writes")) is not bool:
            raise EvaluationError("read_only_manifest_invalid")
        operation = entry.get("operation")
        if not isinstance(operation, str) or not re.fullmatch(r"notion-[a-z0-9-]+", operation):
            raise EvaluationError("read_only_manifest_invalid")
        if entry["writes"] is False:
            names.add("mcp__notion__" + operation.replace("-", "_"))
    if not names:
        raise EvaluationError("read_only_manifest_invalid")
    return frozenset(names)


def audit_tools(top_level, targets, *, read_only_native=None):
    """Fail closed on unexpected calls; this is post-execution detection only."""
    if (not isinstance(top_level, list) or any(not isinstance(item, str) for item in top_level)
            or not isinstance(targets, list) or any(not isinstance(item, str) for item in targets)):
        raise EvaluationError("invalid_tool_targets")
    native = _read_only_native_names() if read_only_native is None else read_only_native
    allowed = native | _SAFE_JEV_TOOLS
    if any(name not in _SAFE_WRAPPERS and name not in allowed for name in top_level):
        raise EvaluationError("unexpected_direct_tool")
    if any(name not in allowed for name in targets):
        raise EvaluationError("unexpected_deferred_tool")


def run_one(repeat: int, case: Case, arm: str, run_id: str):
    usage_path = f"{REMOTE_DIR}/ab-{run_id}-r{repeat}-{case.name}-{arm}.json"
    command = (
        f"JEV_HERMES_CAPABILITY_MODE={shlex.quote(arm)} timeout 180 {shlex.quote(HERMES)} "
        f"-z {shlex.quote(case.prompt)} --usage-file {shlex.quote(usage_path)}"
    )
    started = time.monotonic()
    answer = _ssh(command)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    usage = _remote_json(
        f"jq -c '{{session_id,model,api_calls,input_tokens,output_tokens,cache_read_tokens,"
        f"cache_write_tokens,total_tokens}}' {shlex.quote(usage_path)}"
    )
    session_id = usage.get("session_id")
    export, event = _metadata(session_id)
    if "audit_error" not in export or export["audit_error"] is not None:
        raise EvaluationError("tool_projection_incomplete")
    targets = export.get("invocation_targets")
    audit_tools(export.get("top_level_tools"), targets)
    if usage.get("model") != EXPECTED_MODEL:
        raise EvaluationError("model_mismatch")
    if export.get("api_calls") != usage.get("api_calls"):
        raise EvaluationError("api_call_mismatch")
    if not isinstance(event, dict) or event.get("route_mode") != arm or event.get("status") != "selected":
        raise EvaluationError("discovery_mismatch")
    selected_ids = event.get("capability_ids")
    if not isinstance(selected_ids, list) or case.capability_id not in selected_ids:
        raise EvaluationError("capability_mismatch")
    if answer == case.expected:
        answer_kind = "expected"
    elif answer == "UNAVAILABLE":
        answer_kind = "unavailable"
    else:
        answer_kind = "unexpected"
    return {
        "repeat": repeat, "task": case.name, "arm": arm,
        "session_id": session_id, "correct": answer_kind == "expected", "answer_kind": answer_kind,
        "model": usage.get("model"), "api_calls": usage.get("api_calls"),
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
        "cache_write_tokens": usage.get("cache_write_tokens"), "total_tokens": usage.get("total_tokens"),
        "elapsed_ms": elapsed_ms, "tool_calls": export.get("tool_calls"),
        "top_level_tools": export.get("top_level_tools"),
        "invocation_targets": targets, "described_targets": export.get("described_targets"),
        "discovery": event, "expected_capability": case.capability_id,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New local JSONL result path; never overwrites")
    parser.add_argument("--repeats", type=int, default=2, choices=(1, 2))
    parser.add_argument("--dry-run", action="store_true", help="Print the fixed task and arm order; no remote calls")
    args = parser.parse_args(argv)
    plan = list(schedule(args.repeats))
    if args.dry_run:
        for repeat, case, arm in plan:
            print(f"r{repeat}\t{case.name}\t{arm}")
        return 0
    run_id = uuid.uuid4().hex[:10]
    seen_sessions = set()
    try:
        with args.output.open("x", encoding="utf-8") as handle:
            for repeat, case, arm in plan:
                try:
                    result = run_one(repeat, case, arm, run_id)
                except EvaluationError as exc:
                    handle.write(json.dumps({"repeat": repeat, "task": case.name, "arm": arm, "error": str(exc)}) + "\n")
                    handle.flush()
                    raise
                if result["session_id"] in seen_sessions:
                    handle.write(json.dumps({"repeat": repeat, "task": case.name, "arm": arm, "error": "duplicate_session_id"}) + "\n")
                    handle.flush()
                    raise EvaluationError("duplicate_session_id")
                seen_sessions.add(result["session_id"])
                handle.write(json.dumps(result, sort_keys=True) + "\n")
                handle.flush()
                print(json.dumps({key: result[key] for key in ("repeat", "task", "arm", "correct", "api_calls", "total_tokens")}, sort_keys=True), flush=True)
    except EvaluationError as exc:
        parser.exit(1, f"evaluation stopped: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
