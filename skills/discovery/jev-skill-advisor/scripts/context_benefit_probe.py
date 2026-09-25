"""Summarize paired, independently observed baseline and bridge task results.

This does not collect provider requests or judge task success. In particular,
MCP tools/list bytes are not model-visible startup bytes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path


class EvidenceError(ValueError):
    pass


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ARM_KEYS = {
    "session_id", "startup_tool_bytes", "startup_evidence",
    "tools_list_schema_bytes", "task_success", "task_evidence", "input_tokens", "latency_ms",
}
_STARTUP_EVIDENCE = {"provider_request_capture", "harness_trace"}
_TASK_EVIDENCE = {"human_review", "heldout_answer_check"}
_NUMERIC = ("startup_tool_bytes", "tools_list_schema_bytes", "input_tokens", "latency_ms")


def _nonnegative(value: object, field: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise EvidenceError(f"invalid_{field}")
    if field != "latency_ms" and not isinstance(value, int):
        raise EvidenceError(f"invalid_{field}")
    return value


def _arm(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - _ARM_KEYS:
        raise EvidenceError("invalid_arm")
    session = value.get("session_id")
    if session is not None and (not isinstance(session, str) or not _ID.fullmatch(session)):
        raise EvidenceError("invalid_session_id")
    result = {key: _nonnegative(value.get(key), key) for key in _NUMERIC}
    evidence = value.get("startup_evidence")
    if result["startup_tool_bytes"] is not None:
        if evidence not in _STARTUP_EVIDENCE:
            raise EvidenceError("startup_evidence_required")
        if session is None:
            raise EvidenceError("session_id_required")
    elif evidence is not None:
        raise EvidenceError("startup_bytes_required")
    result["startup_evidence"] = evidence
    success = value.get("task_success")
    if success is not None and not isinstance(success, bool):
        raise EvidenceError("invalid_task_success")
    task_evidence = value.get("task_evidence")
    if success is not None and (task_evidence not in _TASK_EVIDENCE or session is None):
        raise EvidenceError("task_evidence_required")
    if success is None and task_evidence is not None:
        raise EvidenceError("task_success_required")
    result["task_success"] = success
    result["task_evidence"] = task_evidence
    result["session_id"] = session
    return result


def validate(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "cases"}:
        raise EvidenceError("invalid_envelope")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise EvidenceError("unsupported_schema")
    cases = payload["cases"]
    if not isinstance(cases, list):
        raise EvidenceError("invalid_cases")
    seen: set[tuple[str, str]] = set()
    result = []
    for row in cases:
        if not isinstance(row, dict) or set(row) != {"case_id", "harness", "baseline", "bridge"}:
            raise EvidenceError("invalid_case")
        case_id, harness = row["case_id"], row["harness"]
        if not all(isinstance(item, str) and _ID.fullmatch(item) for item in (case_id, harness)):
            raise EvidenceError("invalid_case_id")
        if harness == "all":
            raise EvidenceError("reserved_harness")
        key = (harness, case_id)
        if key in seen:
            raise EvidenceError("duplicate_case")
        seen.add(key)
        baseline, bridge = _arm(row["baseline"]), _arm(row["bridge"])
        if baseline["session_id"] is not None and baseline["session_id"] == bridge["session_id"]:
            raise EvidenceError("same_session")
        result.append({"case_id": case_id, "harness": harness,
                       "baseline": baseline, "bridge": bridge})
    return result


def _paired(rows: list[dict], field: str) -> dict:
    pairs = [(row["baseline"][field], row["bridge"][field]) for row in rows]
    pairs = [(before, after) for before, after in pairs if before is not None and after is not None]
    if not pairs:
        return {"paired_count": 0, "baseline_mean": None, "bridge_mean": None, "delta_mean": None}
    count = len(pairs)
    baseline = sum(before for before, _ in pairs) / count
    bridge = sum(after for _, after in pairs) / count
    return {"paired_count": count, "baseline_mean": baseline, "bridge_mean": bridge,
            "delta_mean": bridge - baseline}


def report(payload: object) -> dict:
    rows = validate(payload)
    groups = {"all": rows}
    for harness in sorted({row["harness"] for row in rows}):
        groups[harness] = [row for row in rows if row["harness"] == harness]
    summaries = {}
    for name, group in groups.items():
        summaries[name] = {
            "case_count": len(group),
            "model_visible_startup_tool_bytes": _paired(group, "startup_tool_bytes"),
            "server_tools_list_schema_bytes": _paired(group, "tools_list_schema_bytes"),
            "input_tokens": _paired(group, "input_tokens"),
            "latency_ms": _paired(group, "latency_ms"),
            "independently_labeled_task_success": _paired(group, "task_success"),
        }
    return {"schema_version": 1, "summary": summaries,
            "caveat": "Paired observations only; tools/list bytes are not startup context, and delivery is not task success."}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        output = report(json.loads(args.input.read_text()))
    except (OSError, json.JSONDecodeError, EvidenceError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
