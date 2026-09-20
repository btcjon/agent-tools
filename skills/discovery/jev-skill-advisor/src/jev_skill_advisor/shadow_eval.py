"""Bounded shadow evaluation over a verified Notion-backed skill snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path

from .catalog_cli import build_catalog, frontmatter
from .library_cache import LibraryCache
from .profile import load_profile
from .service import SkillAdvisorService
from .runtime import ServiceRuntime


class ShadowEvalError(ValueError):
    pass


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix=".shadow-", suffix=".json", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, sort_keys=True, indent=2); handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_profile(*, cache_root, state_dir, profile_path, harness="pilot", credential_file=None):
    status = LibraryCache(cache_root).status()
    if status.get("status") != "ready": raise ShadowEvalError("snapshot_not_ready")
    catalog = build_catalog(Path(status["catalog_root"]))
    if catalog["excluded_count"] or catalog["included_count"] != status["skill_count"]:
        raise ShadowEvalError("snapshot_catalog_incomplete")
    state = Path(state_dir).resolve(); catalog_path = state / "catalog.json"
    _atomic_json(catalog_path, catalog)
    profile = {
        "config_version": 1, "profile_id": f"notion-shadow-{status['snapshot_id'][:12]}", "harness": harness,
        "warehouse_root": status["catalog_root"], "catalog_path": str(catalog_path), "state_dir": str(state / "runtime"),
        "mode": "shadow", "provider_enabled": True, "read_enabled": False,
        "eligible_ids": [row["stable_id"] for row in catalog["entries"]], "read_allowlist": [],
        "credential_env": "TYPESAFE_API_KEY", "deadline_s": 5, "max_calls": 8, "max_tokens": 50000,
        "receipt_ttl_s": 86400, "prompt_limit": 20, "provider_attempt_limit": 20,
    }
    if credential_file is not None:
        credential_path = Path(credential_file).expanduser().resolve()
        if not credential_path.is_file():
            raise ShadowEvalError("credential_file_not_found")
        profile["credential_file"] = str(credential_path)
    _atomic_json(profile_path, profile)
    return {"snapshot_id": status["snapshot_id"], "catalog_hash": catalog["catalog_hash"], "profile_path": str(Path(profile_path).resolve())}


def _cases(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) not in ({"schema_version", "snapshot_id", "cases"}, {"schema_version", "snapshot_id", "cases", "provider_attempt_cap"}) or raw["schema_version"] != 1:
        raise ShadowEvalError("invalid_cases_file")
    rows = raw["cases"]
    if not isinstance(rows, list) or not 4 <= len(rows) <= 8:
        raise ShadowEvalError("shadow_requires_4_to_8_cases")
    cap = raw.get("provider_attempt_cap", 20)
    if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 20:
        raise ShadowEvalError("invalid_provider_attempt_cap")
    ids = set()
    for row in rows:
        required = {"id", "task", "context", "acceptable_ids", "expect", "harness"}
        if not isinstance(row, dict) or set(row) - (required | {"explicit_skills", "replay_of", "forbidden_ids"}) or not required <= set(row):
            raise ShadowEvalError("invalid_shadow_case")
        if row["id"] in ids or row["expect"] not in {"selection", "abstain"}:
            raise ShadowEvalError("invalid_shadow_case")
        if any(not isinstance(row.get(key), str) for key in ("id", "task", "context", "harness")):
            raise ShadowEvalError("invalid_shadow_case")
        if any(not isinstance(row.get(key, []), list) or any(not isinstance(item, str) for item in row.get(key, []))
               for key in ("acceptable_ids", "explicit_skills", "forbidden_ids")):
            raise ShadowEvalError("invalid_shadow_case")
        ids.add(row["id"])
    if any(row.get("replay_of") not in ids for row in rows if row.get("replay_of")):
        raise ShadowEvalError("invalid_replay_reference")
    return raw


def _mapping(profile):
    result = {}
    for sid, entry in sorted(profile.entries.items()):
        metadata = frontmatter(Path(entry.source).read_text(encoding="utf-8"))
        page_id = metadata.get("notion_page_id")
        if not isinstance(page_id, str) or not page_id:
            raise ShadowEvalError("missing_notion_page_mapping")
        result[sid] = page_id
    return result


def run_shadow(*, cache_root, profile_path, cases_path, report_path, service_factory=SkillAdvisorService):
    status = LibraryCache(cache_root).status()
    if status.get("status") != "ready": raise ShadowEvalError("snapshot_not_ready")
    cases = _cases(cases_path)
    if cases["snapshot_id"] != status["snapshot_id"]: raise ShadowEvalError("snapshot_provenance_mismatch")
    profile = load_profile(Path(profile_path))
    if profile.mode != "shadow" or profile.read_enabled or profile.read_allowlist:
        raise ShadowEvalError("profile_not_strict_shadow")
    if profile.provider_attempt_limit > 20 or profile.prompt_limit > 20:
        raise ShadowEvalError("shadow_budget_too_large")
    if profile.warehouse_root != Path(status["catalog_root"]).resolve():
        raise ShadowEvalError("profile_snapshot_mismatch")
    catalog = build_catalog(profile.warehouse_root)
    stored_catalog = json.loads(profile.catalog_path.read_text())
    if catalog != stored_catalog:
        raise ShadowEvalError("catalog_provenance_mismatch")
    eligible = sorted(profile.eligible_ids)
    if eligible != sorted(row["stable_id"] for row in catalog["entries"]):
        raise ShadowEvalError("profile_eligibility_mismatch")
    mapping = _mapping(profile)
    case_hash = _json_hash(cases)
    if service_factory is SkillAdvisorService:
        consumed = ServiceRuntime(profile).counts().get("provider_attempts", 0)
        remaining = max(0, profile.provider_attempt_limit - consumed)
        allowance = min(cases.get("provider_attempt_cap", 20), remaining)
        if allowance < 1:
            raise ShadowEvalError("provider_attempt_budget_exhausted")
        profile = replace(profile, provider_attempt_limit=consumed + allowance)
    service = service_factory(profile)
    results = []; detail = []; counts = {key: 0 for key in ("correct_selections", "abstentions", "misses", "wrong_harness_selections", "disagreements", "provider_failures")}
    totals = {key: 0 for key in ("provider_attempts", "cache_hits", "input_tokens", "unknown_usage")}; latencies = []
    stopped_reason = None
    for index, case in enumerate(cases["cases"]):
        request = {"protocol_version": 1, "request_id": f"shadow-{index}-{uuid.uuid4().hex[:8]}", "session_id": f"notion-shadow-{case_hash[:12]}",
                   "task": case["task"], "context": f"harness={case['harness']}\n{case['context']}", "available_ids": eligible,
                   "explicit_skills": case.get("explicit_skills", [])}
        response = service.suggest(request)
        selected = [row["id"] for row in response.get("selected", [])]
        telemetry = response.get("telemetry", {})
        decision_audit = response.get("decision_audit")
        if response.get("reason") == "detail_uncertain" and not isinstance(decision_audit, dict):
            raise ShadowEvalError("missing_detail_decision_audit")
        if decision_audit is not None:
            required_audit = {"winner_confidence", "finalist_fit", "confidence_floor", "fit_floor", "confidence_pass", "fit_pass", "passed", "failed_predicates", "decision", "fit_operator", "provider_evidence"}
            if set(decision_audit) != required_audit or decision_audit["provider_evidence"] not in {"original_response", "cache_replay"} or decision_audit["decision"] not in {"selection", "none"}:
                raise ShadowEvalError("invalid_detail_decision_audit")
        for key in totals: totals[key] += int(telemetry.get(key, 0))
        if isinstance(telemetry.get("elapsed_ms"), (int, float)): latencies.append(float(telemetry["elapsed_ms"]))
        if totals["provider_attempts"] > cases.get("provider_attempt_cap", 20): raise ShadowEvalError("provider_attempt_budget_exceeded")
        reason = str(response.get("reason"))
        provider_failure = response.get("status") == "incomplete" or reason in {"protected_input", "provider_unavailable", "prompt_budget", "absolute_deadline", "worker_failure"} or reason.endswith("provider_failure")
        if provider_failure:
            stopped_reason = "shadow_provider_or_policy_stop:" + reason
            counts["provider_failures"] += 1
            results.append({"id": case["id"], "expect": case["expect"], "acceptable_ids": sorted(case["acceptable_ids"]),
                            "selected_ids": selected, "status": response.get("status"), "reason": reason, "label": "provider_failure",
                            "provider_attempts": int(telemetry.get("provider_attempts", 0)), "cache_hits": int(telemetry.get("cache_hits", 0)),
                            "input_tokens": int(telemetry.get("input_tokens", 0)), "unknown_usage": int(telemetry.get("unknown_usage", 0)),
                            "elapsed_ms": telemetry.get("elapsed_ms")})
            detail.append({"case": case, "request": request, "response": response})
            break
        acceptable = set(case["acceptable_ids"]); forbidden = set(case.get("forbidden_ids", []))
        if case["expect"] == "abstain" and not selected:
            label = "abstention"; counts["abstentions"] += 1
        elif selected and set(selected) <= acceptable:
            label = "correct_selection"; counts["correct_selections"] += 1
        elif selected and forbidden.intersection(selected):
            label = "wrong_harness_selection"; counts["wrong_harness_selections"] += 1
        elif not selected and acceptable:
            label = "miss"; counts["misses"] += 1
        else:
            label = "disagreement"; counts["disagreements"] += 1
        row = {"id": case["id"], "expect": case["expect"], "acceptable_ids": sorted(acceptable), "selected_ids": selected,
               "status": response.get("status"), "reason": response.get("reason"), "label": label,
               "provider_attempts": int(telemetry.get("provider_attempts", 0)), "cache_hits": int(telemetry.get("cache_hits", 0)),
               "input_tokens": int(telemetry.get("input_tokens", 0)), "unknown_usage": int(telemetry.get("unknown_usage", 0)),
               "elapsed_ms": telemetry.get("elapsed_ms"), "decision_audit": decision_audit}
        results.append(row); detail.append({"case": case, "request": request, "response": response})
    explicit = [row for row in results if next(case for case in cases["cases"] if case["id"] == row["id"]).get("explicit_skills")]
    if any(row["provider_attempts"] for row in explicit): raise ShadowEvalError("explicit_selection_used_provider")
    replays = [row for row in results if next(case for case in cases["cases"] if case["id"] == row["id"]).get("replay_of")]
    report = {"schema_version": 1, "mode": "shadow", "run_status": "failed" if stopped_reason else "complete", "stopped_reason": stopped_reason,
              "snapshot_id": status["snapshot_id"], "catalog_hash": profile.catalog_hash,
              "policy_hash": profile.policy_hash, "cases_hash": case_hash, "notion_page_mapping": mapping,
              "skill_bodies_delivered": 0, "cases": results, "summary": {**counts, **totals, "case_count": len(results),
                  "latency_ms_total": round(sum(latencies), 3), "explicit_zero_call": bool(explicit) and all(not row["provider_attempts"] for row in explicit),
                  "replay_cache_hit": bool(replays) and all(row["cache_hits"] > 0 for row in replays)},
              "interpretation": "Selection evidence only; no measured main-model token-savings claim."}
    detail_path = Path(profile.state_dir) / "shadow-details" / f"{case_hash}.json"
    _atomic_json(detail_path, {"schema_version": 1, "responses": detail})
    _atomic_json(report_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-advisor-shadow")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    for command in (prepare,):
        command.add_argument("--cache-root", type=Path, required=True); command.add_argument("--state-dir", type=Path, required=True); command.add_argument("--profile", type=Path, required=True); command.add_argument("--harness", default="pilot"); command.add_argument("--credential-file", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--cache-root", type=Path, required=True); run.add_argument("--profile", type=Path, required=True)
    run.add_argument("--cases", type=Path, required=True); run.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    result = write_profile(cache_root=args.cache_root, state_dir=args.state_dir, profile_path=args.profile, harness=args.harness, credential_file=args.credential_file) if args.command == "prepare" else run_shadow(cache_root=args.cache_root, profile_path=args.profile, cases_path=args.cases, report_path=args.report)
    print(json.dumps(result, sort_keys=True, indent=2)); return 3 if result.get("run_status") == "failed" else 0


if __name__ == "__main__": raise SystemExit(main())
