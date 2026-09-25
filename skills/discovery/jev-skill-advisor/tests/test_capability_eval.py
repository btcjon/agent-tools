import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jev_skill_advisor.capability_choice import CAPABILITY_SELECTION_P95_MS, MODEL
from jev_skill_advisor.capability_core import load_manifest
from jev_skill_advisor.capability_eval import aggregate, case_score, evaluate, load_cases, main

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "examples" / "notion-capability-eval-cases-2026-09-24.json"
MANIFEST_PATH = ROOT / "examples" / "notion-mcp-live-manifest-2026-09-24.json"


def jev_response(payload, chosen, scores=None):
    answers = {}
    for index, card in enumerate(payload["state"]["candidates"]):
        noul = float((scores or {}).get(card["id"], 0.9 if card["id"] in chosen else 0.1))
        answers[f"fit_{index}"] = {"type": "noul", "noul": noul}
    answers["write_intent"] = {"type": "noul", "noul": 0.9}
    return {"model": MODEL, "answers": answers, "usage": {"input_tokens": 3}}


def _rows():
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


class CapabilityEvalTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(MANIFEST_PATH)

    def test_metric_formulas(self):
        writes = {"notion.mcp.fetch": False, "notion.mcp.search": False, "notion.mcp.create-pages": True}

        def row(identifier, acceptable, accept, safety):
            return {"id": identifier, "acceptable_ids": acceptable, "accept": accept, "safety": safety}

        def result(status, ids, latency):
            return {"status": status, "ids": ids, "reason": "ok", "evidence": {"budgets": {"elapsed_ms": latency}}}

        details = [
            case_score(row("case-read", ["notion.mcp.fetch"], "all", "read"), result("selected", ["notion.mcp.fetch"], 1), writes),
            case_score(row("case-half", ["notion.mcp.fetch", "notion.mcp.search"], "all", "read"), result("selected", ["notion.mcp.fetch"], 2), writes),
            case_score(row("case-any", ["notion.mcp.fetch", "notion.mcp.search"], "any", "read"), result("selected", ["notion.mcp.search"], 3), writes),
            case_score(row("case-null", [], "all", "read"), result("none", [], 4), writes),
            case_score(row("case-fp", ["notion.mcp.fetch"], "all", "read"), result("selected", ["notion.mcp.create-pages"], 5), writes),
            case_score(row("case-down", [], "all", "write"), result("fail_open", [], 6), writes),
        ]
        metrics = aggregate(details)
        self.assertEqual(metrics["top1_precision"], 0.75)
        self.assertEqual(metrics["acceptable_recall"], 0.625)
        self.assertEqual(metrics["null_accuracy"], 1.0)
        self.assertEqual(metrics["read_to_write_false_positive_rate"], 0.2)
        self.assertEqual(metrics["provider_failure_rate"], round(1 / 6, 4))
        self.assertEqual(metrics["selection_count"], 4)
        self.assertEqual(metrics["null_case_count"], 2)
        self.assertEqual(metrics["null_scored_count"], 1)
        self.assertEqual(metrics["mean_latency_ms"], 3.5)
        self.assertTrue(details[4]["read_to_write"])
        self.assertIsNone(details[5]["null_correct"])
        self.assertIsNone(details[5]["recall"])

    def test_frozen_file_perfect_fake_scores_clean(self):
        rows = _rows()
        by_task = {row["task"]: row for row in rows}
        seen = []

        def perfect(payload, timeout):
            seen.append((payload, timeout))
            row = by_task[payload["state"]["request"]]
            chosen = list(row["acceptable_ids"])
            scores = {identifier: 0.95 - index * 0.01 for index, identifier in enumerate(chosen)}
            return jev_response(payload, set(chosen), scores)

        with patch("jev_skill_advisor.client.evaluate", side_effect=AssertionError("network")), \
             patch("jev_skill_advisor.capability_eval.ServiceRuntime", side_effect=AssertionError("runtime")):
            report = evaluate(CASES, self.manifest, perfect)
        self.assertEqual(len(seen), 39)
        self.assertEqual(len(self.manifest.entries), 45)
        self.assertEqual({len(payload["questions"]) for payload, _ in seen}, {46})
        self.assertEqual({timeout for _, timeout in seen}, {CAPABILITY_SELECTION_P95_MS / 1000})
        self.assertTrue(all(payload["questions"][key]["type"] == "noul" for payload, _ in seen for key in payload["questions"]))
        metrics = report["metrics"]
        self.assertEqual(report["case_count"], 39)
        self.assertEqual(report["manifest_entries"], 45)
        self.assertFalse(report["live"])
        self.assertEqual(metrics["top1_precision"], 1.0)
        self.assertEqual(metrics["acceptable_recall"], 1.0)
        self.assertEqual(metrics["null_accuracy"], 1.0)
        self.assertEqual(metrics["read_to_write_false_positive_rate"], 0.0)
        self.assertEqual(metrics["provider_failure_rate"], 0.0)
        self.assertEqual(metrics["selection_count"], 34)
        self.assertEqual(metrics["null_case_count"], 5)
        self.assertTrue(all(row["reason"] != "explicit_tool_authoritative" for row in report["cases"]))
        blob = json.dumps(report)
        for row in rows:
            self.assertNotIn(row["task"], blob)
            self.assertNotIn(row["rationale"], blob)
        self.assertNotIn("inputSchema", blob)
        self.assertNotIn("schema_hash", blob)
        self.assertNotIn("Operation is required.", blob)
        schema_hash = next(iter(self.manifest.entries.values())).schema_hash
        self.assertNotIn(schema_hash, blob)
        again = evaluate(CASES, self.manifest, perfect)
        self.assertEqual(
            [row["selected_ids"] for row in report["cases"]],
            [row["selected_ids"] for row in again["cases"]],
        )
        self.assertEqual(report["cases"][0]["status"], again["cases"][0]["status"])

    def test_partial_recall_and_write_false_positive(self):
        rows = _rows()
        by_task = {row["task"]: row for row in rows}
        labeled = [row for row in rows if row["acceptable_ids"]]
        multi = [row for row in labeled if row["accept"] == "all" and len(row["acceptable_ids"]) == 2]
        alternatives = [row for row in labeled if row["accept"] == "any"]
        self.assertEqual(len(rows), 39)
        self.assertEqual(len(labeled), 34)
        self.assertEqual(len(multi), 2)
        self.assertEqual(len(alternatives), 2)
        self.assertEqual(
            [row["id"] for row in rows if "notion.mcp.create-pages" in row["acceptable_ids"]],
            ["write-kickoff-page"],
        )

        def first_only(payload, timeout):
            row = by_task[payload["state"]["request"]]
            chosen = sorted(row["acceptable_ids"])[:1]
            return jev_response(payload, set(chosen), {chosen[0]: 0.9} if chosen else None)

        partial = evaluate(CASES, self.manifest, first_only)
        self.assertEqual(partial["metrics"]["top1_precision"], 1.0)
        self.assertEqual(partial["metrics"]["acceptable_recall"], round(33 / 34, 4))
        self.assertEqual(partial["metrics"]["null_accuracy"], 1.0)
        self.assertEqual(partial["metrics"]["read_to_write_false_positive_rate"], 0.0)

        def always_write(payload, timeout):
            return jev_response(payload, {"notion.mcp.create-pages"}, {"notion.mcp.create-pages": 0.99})

        noisy = evaluate(CASES, self.manifest, always_write)
        self.assertEqual(noisy["metrics"]["read_to_write_false_positive_rate"], 1.0)
        self.assertEqual(noisy["metrics"]["null_accuracy"], 0.0)
        self.assertEqual(noisy["metrics"]["top1_precision"], round(1 / 39, 4))
        self.assertEqual(noisy["metrics"]["acceptable_recall"], round(1 / 34, 4))
        self.assertEqual(noisy["metrics"]["selection_count"], 39)

    def test_provider_failure_is_not_a_null_or_a_selection(self):
        def explode(payload, timeout):
            raise TimeoutError("down")

        report = evaluate(CASES, self.manifest, explode)
        metrics = report["metrics"]
        self.assertEqual(metrics["provider_failure_rate"], 1.0)
        self.assertIsNone(metrics["top1_precision"])
        self.assertIsNone(metrics["acceptable_recall"])
        self.assertIsNone(metrics["null_accuracy"])
        self.assertEqual(metrics["read_to_write_false_positive_rate"], 0.0)
        self.assertTrue(all(row["status"] == "fail_open" and row["selected_ids"] == [] for row in report["cases"]))
        self.assertTrue(all(row["reason"] == "capability_choice_provider_failure" for row in report["cases"]))
        self.assertNotIn("TimeoutError", json.dumps(report))

    def test_explicit_tool_name_never_reaches_the_evaluator(self):
        document = json.loads(CASES.read_text(encoding="utf-8"))
        document["cases"][0]["task"] = "Please run notion-fetch on the spec page now."
        calls = []

        def fake(payload, timeout):
            calls.append(payload)
            raise AssertionError("evaluator")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                evaluate(path, self.manifest, fake)
        self.assertIn("case_would_skip_evaluator", str(caught.exception))
        self.assertEqual(calls, [])

    def test_cli_does_not_call_live_unless_asked(self):
        with patch("jev_skill_advisor.capability_eval.ServiceRuntime", side_effect=AssertionError("runtime")), \
             patch("jev_skill_advisor.client.evaluate", side_effect=AssertionError("network")):
            code = main([])
        self.assertEqual(code, 2)

    def test_live_flag_uses_the_injected_profile_runtime(self):
        sentinel = object()
        seen = {}

        def resolve_profile(root, profile_path):
            seen["profile"] = profile_path
            return sentinel

        rows = _rows()
        by_task = {row["task"]: row for row in rows}

        class Runtime:
            def __init__(self, profile, operation_id=None):
                seen["runtime_profile"] = profile
                seen["operation_id"] = operation_id

            def evaluator(self, payload, timeout):
                row = by_task[payload["state"]["request"]]
                return jev_response(payload, set(row["acceptable_ids"]))

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            profile_path = Path(tmp) / "active.json"
            with patch("jev_skill_advisor.capability_eval.resolve_profile", resolve_profile), \
                 patch("jev_skill_advisor.capability_eval.ServiceRuntime", Runtime), \
                 patch("jev_skill_advisor.client.evaluate", side_effect=AssertionError("network")):
                code = main(["--live", "--profile", str(profile_path), "--report", str(report_path)])
            self.assertEqual(code, 0)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(seen["profile"], profile_path)
        self.assertIs(seen["runtime_profile"], sentinel)
        self.assertTrue(saved["live"])
        self.assertEqual(saved["metrics"]["top1_precision"], 1.0)
        blob = json.dumps(saved)
        self.assertNotIn("Will it rain in Lisbon tomorrow morning?", blob)
        self.assertNotIn("inputSchema", blob)

    def test_live_rejects_a_bad_case_file_before_runtime(self):
        document = json.loads(CASES.read_text(encoding="utf-8"))
        document["cases"][0]["safety"] = "write"
        constructed = []

        class Runtime:
            def __init__(self, *args, **kwargs):
                constructed.append(True)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with patch("jev_skill_advisor.capability_eval.ServiceRuntime", Runtime):
                code = main(["--live", "--cases", str(path), "--report", str(Path(tmp) / "report.json")])
        self.assertEqual(code, 2)
        self.assertEqual(constructed, [])

    def test_labels_cover_required_spans_and_manifest_ids(self):
        rows = load_cases(CASES, self.manifest)
        covered = {span for row in rows for span in row["spans"]}
        self.assertEqual(covered, {
            "read", "search", "query", "comment", "attachment", "write",
            "plan_gate", "multi", "paraphrase", "null",
        })
        self.assertTrue(json.loads(CASES.read_text(encoding="utf-8"))["frozen_before_outcomes"])
        self.assertEqual(len(self.manifest.entries), 45)
        plan_blocked = [row["id"] for row in rows if row["account_execution"] == "plan_may_block"]
        self.assertIn("query-sprint-meeting-actions", plan_blocked)
        self.assertIn("find-connected-launch-talk", plan_blocked)
        self.assertIn("query-two-risk-sources", plan_blocked)
        self.assertTrue(all(row["safety"] == "read" or row["acceptable_ids"] for row in rows))
