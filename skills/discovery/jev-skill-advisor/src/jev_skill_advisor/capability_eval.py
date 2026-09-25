"""Score frozen Notion capability cases through resolve_capabilities.

One injected evaluator call per case. The command does not call Jev unless
--live is set, and then it uses the active release profile or --profile.
Reports keep ids, status, and latency only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .capability_choice import _protected, explicit_capability_ids, resolve_capabilities
from .capability_core import load_manifest
from .profile import load_profile
from .runtime import ServiceRuntime

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = PACKAGE_ROOT / "examples" / "notion-capability-eval-cases-2026-09-24.json"
DEFAULT_MANIFEST = PACKAGE_ROOT / "examples" / "notion-mcp-live-manifest-2026-09-24.json"
RELEASE_ROOT = Path.home() / ".local/state/jev-skill-advisor/releases"
NOTION_SKILL = [{"id": "warehouse:notion", "name": "notion"}]
SPANS = frozenset({
    "read", "search", "query", "comment", "attachment", "write",
    "plan_gate", "multi", "paraphrase", "null",
})
ACCOUNT = frozenset({"task_level", "plan_may_block"})
METRIC_NOTES = {
    "top1_precision": "Among status=selected rows, fraction whose first id is an acceptable id. fail_open is excluded.",
    "acceptable_recall": "Macro mean over labeled cases that did not fail open. accept=all is the share of gold ids selected. accept=any is 1 when at least one gold id is selected.",
    "null_accuracy": "Among empty-gold cases that did not fail open, fraction with status none and no ids.",
    "read_to_write_false_positive_rate": "Among safety=read cases, fraction that selected a manifest write. An unknown id counts as a write. Selecting nothing is not a false positive.",
}


def resolve_profile(release_root: Path, profile_path: Path | None):
    """Load the active release profile, or an explicit profile file. Does not create one."""
    if profile_path is not None:
        return load_profile(Path(profile_path))
    current = json.loads((release_root / "current-release.json").read_text(encoding="utf-8"))
    release_id = current["release_id"]
    manifest = json.loads((release_root / "releases" / release_id / "manifest.json").read_text(encoding="utf-8"))
    return load_profile(Path(manifest["files"]["profile:generic"]["path"]))


def load_cases(path: Path, manifest) -> list[dict]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(rows, list) or not 25 <= len(rows) <= 40:
        raise ValueError("expected_25_to_40_cases")
    known = set(manifest.entries)
    writes = {entry.id: entry.writes for entry in manifest.entries.values()}
    seen = set()
    tasks = set()
    covered = set()
    loaded = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_case")
        identifier = row.get("id")
        if not isinstance(identifier, str) or identifier in seen or not 8 <= len(identifier) <= 64:
            raise ValueError("invalid_case_id")
        if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in identifier) or identifier.startswith("-") or identifier.endswith("-"):
            raise ValueError(f"invalid_case_id:{identifier}")
        seen.add(identifier)
        task = row.get("task")
        if not isinstance(task, str) or not 20 <= len(task.strip()) <= 500 or task.strip() != task:
            raise ValueError(f"invalid_case_task:{identifier}")
        if task in tasks:
            raise ValueError(f"duplicate_task:{identifier}")
        tasks.add(task)
        if _protected(task) or explicit_capability_ids(manifest, task):
            raise ValueError(f"case_would_skip_evaluator:{identifier}")
        acceptable = row.get("acceptable_ids")
        if not isinstance(acceptable, list) or any(not isinstance(item, str) or item not in known for item in acceptable):
            raise ValueError(f"invalid_acceptable_ids:{identifier}")
        if len(set(acceptable)) != len(acceptable):
            raise ValueError(f"duplicate_acceptable_id:{identifier}")
        accept = row.get("accept", "all")
        if accept not in {"all", "any"}:
            raise ValueError(f"invalid_accept:{identifier}")
        if not acceptable and accept != "all":
            raise ValueError(f"null_case_accept:{identifier}")
        if accept == "any" and len(acceptable) < 2:
            raise ValueError(f"any_needs_alternatives:{identifier}")
        safety = row.get("safety")
        if safety not in {"read", "write"}:
            raise ValueError(f"invalid_safety:{identifier}")
        gold_writes = [writes[item] for item in acceptable]
        if safety == "read" and any(gold_writes):
            raise ValueError(f"read_case_labels_a_write:{identifier}")
        if safety == "write" and not any(gold_writes):
            raise ValueError(f"write_case_has_no_write:{identifier}")
        account = row.get("account_execution")
        if account not in ACCOUNT:
            raise ValueError(f"invalid_account_execution:{identifier}")
        spans = row.get("spans")
        if not isinstance(spans, list) or not spans or any(item not in SPANS for item in spans):
            raise ValueError(f"invalid_spans:{identifier}")
        if not acceptable and "null" not in spans:
            raise ValueError(f"null_span_missing:{identifier}")
        if acceptable and "null" in spans:
            raise ValueError(f"labeled_case_marked_null:{identifier}")
        rationale = row.get("rationale")
        if not isinstance(rationale, str) or not 40 <= len(rationale) <= 700:
            raise ValueError(f"invalid_rationale:{identifier}")
        covered.update(spans)
        loaded.append({
            "id": identifier, "task": task, "acceptable_ids": list(acceptable), "accept": accept,
            "safety": safety, "account_execution": account, "spans": list(spans), "rationale": rationale,
        })
    missing = SPANS - covered
    if missing:
        raise ValueError("missing_spans:" + ",".join(sorted(missing)))
    return loaded


def _ratio(numerator: int, denominator: int):
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def case_score(row: dict, result: dict, writes: dict[str, bool]) -> dict:
    status = result.get("status") if isinstance(result, dict) else None
    if status not in {"selected", "none", "fail_open"}:
        status = "fail_open"
    raw_ids = result.get("ids") if isinstance(result, dict) else None
    selected = [item for item in raw_ids if isinstance(item, str)] if isinstance(raw_ids, list) else []
    reason = result.get("reason") if isinstance(result, dict) and isinstance(result.get("reason"), str) else ""
    evidence = result.get("evidence") if isinstance(result, dict) else None
    budgets = evidence.get("budgets") if isinstance(evidence, dict) else None
    latency = budgets.get("elapsed_ms") if isinstance(budgets, dict) else None
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        latency = None
    acceptable = list(row["acceptable_ids"])
    gold = set(acceptable)
    failure = status == "fail_open"
    chosen = status == "selected" and bool(selected)
    if failure or not acceptable:
        recall = None
    elif row["accept"] == "any":
        recall = 1.0 if set(selected) & gold else 0.0
    else:
        recall = len(set(selected) & gold) / len(gold)
    if acceptable or failure:
        null_correct = None
    else:
        null_correct = status == "none" and not selected
    return {
        "id": row["id"],
        "safety": row["safety"],
        "accept": row["accept"],
        "acceptable_ids": acceptable,
        "selected_ids": selected,
        "status": status,
        "reason": reason,
        "latency_ms": latency,
        "top1_correct": (selected[0] in gold) if chosen else None,
        "recall": recall,
        "null_correct": null_correct,
        "read_to_write": row["safety"] == "read" and any(writes.get(item, True) for item in selected),
        "provider_failure": failure,
    }


def aggregate(details: list[dict]) -> dict:
    selections = [row for row in details if row["top1_correct"] is not None]
    recalls = [row["recall"] for row in details if row["recall"] is not None]
    nulls = [row["null_correct"] for row in details if row["null_correct"] is not None]
    reads = [row for row in details if row["safety"] == "read"]
    latencies = [row["latency_ms"] for row in details if isinstance(row["latency_ms"], (int, float)) and not isinstance(row["latency_ms"], bool)]
    return {
        "top1_precision": _ratio(sum(row["top1_correct"] is True for row in selections), len(selections)),
        "acceptable_recall": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "null_accuracy": _ratio(sum(item is True for item in nulls), len(nulls)),
        "read_to_write_false_positive_rate": _ratio(sum(row["read_to_write"] for row in reads), len(reads)),
        "provider_failure_rate": _ratio(sum(row["provider_failure"] for row in details), len(details)),
        "selection_count": len(selections),
        "labeled_scored_count": len(recalls),
        "null_case_count": sum(not row["acceptable_ids"] for row in details),
        "null_scored_count": len(nulls),
        "read_case_count": len(reads),
        "mean_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else None,
    }


def _private(report: dict, rows: list[dict], manifest) -> None:
    blob = json.dumps(report)
    banned = ("inputSchema", "schema_hash", "additionalProperties", "Operation is required.", "Not required or only topical.")
    if any(token in blob for token in banned):
        raise RuntimeError("report_leaked_prompt_or_schema")
    for row in rows:
        if row["task"] in blob or row["rationale"] in blob:
            raise RuntimeError("report_leaked_case_text:" + row["id"])
    for entry in manifest.entries.values():
        if entry.summary in blob or entry.schema_hash in blob or entry.operation in blob:
            raise RuntimeError("report_leaked_manifest_entry")


def score(rows: list[dict], manifest, evaluator, *, live: bool = False) -> dict:
    writes = {entry.id: entry.writes for entry in manifest.entries.values()}
    details = []
    for row in rows:
        calls = 0

        def wrapped(payload, timeout, _row=row):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError("evaluator_called_more_than_once:" + _row["id"])
            return evaluator(payload, timeout)

        result = resolve_capabilities(
            manifest, row["task"], context="", selected_skills=NOTION_SKILL, evaluator=wrapped,
        )
        if calls != 1:
            raise RuntimeError("evaluator_not_called_once:" + row["id"])
        details.append(case_score(row, result, writes))
    report = {
        "schema_version": 1,
        "selector": "capability_choice.resolve_capabilities",
        "manifest_hash": manifest.content_hash,
        "manifest_entries": len(manifest.entries),
        "case_count": len(details),
        "live": bool(live),
        "metrics": aggregate(details),
        "metric_notes": dict(METRIC_NOTES),
        "cases": details,
        "privacy": "Tasks, rationales, prompts, tool bodies, and schemas are omitted.",
    }
    _private(report, rows, manifest)
    return report


def evaluate(cases_path: Path, manifest, evaluator, *, live: bool = False) -> dict:
    return score(load_cases(cases_path, manifest), manifest, evaluator, live=live)


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="notion-capability-eval")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--release-root", type=Path, default=RELEASE_ROOT)
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({"error": "live_disabled", "hint": "Pass --live to score with the active Jev profile."}), file=sys.stderr)
        return 2
    if args.report is None:
        print(json.dumps({"error": "report_required"}), file=sys.stderr)
        return 2
    try:
        manifest = load_manifest(args.manifest)
        rows = load_cases(args.cases, manifest)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    try:
        profile = resolve_profile(args.release_root, args.profile)
        runtime = ServiceRuntime(profile, operation_id="capability-eval-live")
    except Exception as exc:
        print(json.dumps({"error": "live_runtime_unavailable", "type": type(exc).__name__}), file=sys.stderr)
        return 2
    report = score(rows, manifest, runtime.evaluator, live=True)
    write_report(args.report, report)
    print(json.dumps({"report": str(args.report), "case_count": report["case_count"], "live": True, "metrics": report["metrics"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
