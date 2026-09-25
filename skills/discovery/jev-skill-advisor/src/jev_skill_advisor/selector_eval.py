"""Compare the actual implicit CLI selector with a lexical baseline.

Case tasks are read for evaluation but never written to the report.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from .retrieval import retrieve
from .select_cli import RELEASE_ROOT, _open, select_task


def _cases(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(rows, list) or not 4 <= len(rows) <= 30:
        raise ValueError("expected_4_to_30_cases")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] in ids:
            raise ValueError("invalid_case_id")
        ids.add(row["id"])
        if not isinstance(row.get("task"), str) or not row["task"].strip() or len(row["task"]) > 8000:
            raise ValueError("invalid_case_task")
        if row.get("expect") not in {"selection", "abstain"} or not isinstance(row.get("acceptable_ids"), list):
            raise ValueError("invalid_case_label")
        if row["expect"] == "selection" and not row["acceptable_ids"]:
            raise ValueError("missing_acceptable_ids")
        if row["expect"] == "abstain" and row["acceptable_ids"]:
            raise ValueError("abstain_has_acceptable_ids")
        if row.get("explicit_skills"):
            raise ValueError("implicit_cases_only")
    return rows


def _outcome(selected_id: str | None, row: dict, reason: str | None = None, status: str | None = None) -> str:
    if reason in {"absolute_deadline", "deadline"}:
        return "deadline"
    if reason and (reason.endswith("provider_failure") or reason == "provider_unavailable"):
        return "provider_failure"
    if status == "incomplete" or reason in {"ProfileError", "selector_failure", "stale_source", "body_oversize"}:
        return "selector_failure"
    if selected_id:
        return "correct_selection" if selected_id in row["acceptable_ids"] else "wrong_selection"
    return "correct_abstention" if row["expect"] == "abstain" else "miss"


def _metrics(details: list[dict], route: str) -> dict:
    outcomes = Counter(item[route]["outcome"] for item in details)
    choices = outcomes["correct_selection"] + outcomes["wrong_selection"]
    selection_cases = sum(item["expect"] == "selection" for item in details)
    abstain_cases = len(details) - selection_cases
    return {"cases": len(details), "selection_cases": selection_cases, "abstain_cases": abstain_cases,
            "outcomes": dict(outcomes),
            "top1_precision": round(outcomes["correct_selection"] / choices, 4) if choices else None,
            "selection_case_recall": round(outcomes["correct_selection"] / selection_cases, 4) if selection_cases else None,
            "correct_abstention_rate": round(outcomes["correct_abstention"] / abstain_cases, 4) if abstain_cases else None,
            "provider_failure_rate": round(outcomes["provider_failure"] / len(details), 4),
            "deadline_rate": round(outcomes["deadline"] / len(details), 4)}


def evaluate(cases_path: Path, *, profile, selector=select_task, release_root: Path = RELEASE_ROOT,
             profile_path: Path | None = None) -> dict:
    rows = _cases(cases_path)
    allowed = profile.registry(None, implicit_only=True).eligible()
    allowed_ids = {item.id for item in allowed}
    details = []
    for row in rows:
        if not set(row["acceptable_ids"]) <= allowed_ids:
            raise ValueError("case_label_not_eligible")
        lexical = retrieve(profile, row["task"], available_ids=allowed_ids, limit=1)["candidate_ids"]
        baseline_id = lexical[0] if lexical else None
        try:
            result = selector(row["task"], root=release_root, profile_path=profile_path)
        except Exception as exc:
            result = {"selected": None, "reason": type(exc).__name__, "status": "incomplete"}
        if isinstance(result, dict) and isinstance(result.get("eligible_count"), int) and result["eligible_count"] != len(allowed_ids):
            raise ValueError("candidate_universe_mismatch")
        selected = result.get("selected") if isinstance(result, dict) else None
        selected_id = selected.get("skill_id") if isinstance(selected, dict) else None
        reason = result.get("reason") if isinstance(result, dict) and isinstance(result.get("reason"), str) else None
        status = result.get("status") if isinstance(result, dict) and isinstance(result.get("status"), str) else None
        details.append({"id": row["id"], "expect": row["expect"], "acceptable_ids": row["acceptable_ids"],
                        "jev": {"selected_id": selected_id, "outcome": _outcome(selected_id, row, reason, status),
                                "reason": reason, "status": status},
                        "lexical": {"selected_id": baseline_id, "outcome": _outcome(baseline_id, row)}})
    return {"schema_version": 1, "selector": "select_cli.select_task/catalog_choice_scan",
            "baseline": "retrieval.retrieve(limit=1)", "case_count": len(details),
            "lexical_eligible_count": len(allowed_ids),
            "metrics": {route: _metrics(details, route) for route in ("jev", "lexical")}, "cases": details,
            "privacy": "Case tasks and skill bodies omitted from report. Provider failures are not abstentions."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="skill-advisor-selector-eval")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--release-root", type=Path, default=RELEASE_ROOT)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    profile, manifest = _open(args.release_root, args.profile)
    report = evaluate(args.cases, profile=profile, profile_path=args.profile, release_root=args.release_root)
    report["snapshot_id"] = manifest.get("snapshot_id")
    case_metadata = json.loads(args.cases.read_text(encoding="utf-8"))
    report["label_provenance"] = case_metadata.get("label_provenance", "unrecorded")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(args.report.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.report)
    print(json.dumps({"report": str(args.report), "metrics": report["metrics"], "case_count": report["case_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
