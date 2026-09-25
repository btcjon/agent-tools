import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "context_benefit_probe.py"
SPEC = importlib.util.spec_from_file_location("context_benefit_probe", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def arm(**values):
    return {"session_id": None, "startup_tool_bytes": None,
            "startup_evidence": None, "tools_list_schema_bytes": None,
            "task_success": None, "task_evidence": None,
            "input_tokens": None, "latency_ms": None, **values}


class ContextBenefitProbeTests(unittest.TestCase):
    def test_separates_model_context_from_tools_list_and_uses_pairs_only(self):
        payload = {"schema_version": 1, "cases": [
            {"case_id": "one", "harness": "hermes",
             "baseline": arm(session_id="base-1", startup_tool_bytes=5000, startup_evidence="harness_trace",
                             tools_list_schema_bytes=6000, task_success=True,
                             task_evidence="heldout_answer_check", input_tokens=100),
             "bridge": arm(session_id="bridge-1", startup_tool_bytes=1000, startup_evidence="provider_request_capture",
                           tools_list_schema_bytes=2000, task_success=True,
                           task_evidence="heldout_answer_check", input_tokens=80)},
            {"case_id": "two", "harness": "hermes",
             "baseline": arm(session_id="base-2", tools_list_schema_bytes=6000, task_success=True,
                             task_evidence="human_review"),
             "bridge": arm(session_id="bridge-2", tools_list_schema_bytes=2000, task_success=False,
                           task_evidence="human_review")},
        ]}
        result = MODULE.report(payload)["summary"]["hermes"]
        self.assertEqual(result["model_visible_startup_tool_bytes"]["paired_count"], 1)
        self.assertEqual(result["model_visible_startup_tool_bytes"]["delta_mean"], -4000)
        self.assertEqual(result["server_tools_list_schema_bytes"]["paired_count"], 2)
        self.assertEqual(result["independently_labeled_task_success"]["bridge_mean"], 0.5)
        self.assertEqual(result["input_tokens"]["paired_count"], 1)

    def test_rejects_unproven_startup_and_extra_content_fields(self):
        row = {"case_id": "one", "harness": "codex", "baseline": arm(),
               "bridge": arm(session_id="bridge-1")}
        row["bridge"]["startup_tool_bytes"] = 10
        with self.assertRaisesRegex(MODULE.EvidenceError, "startup_evidence_required"):
            MODULE.report({"schema_version": 1, "cases": [row]})
        row["bridge"]["startup_evidence"] = "tools_list"
        with self.assertRaisesRegex(MODULE.EvidenceError, "startup_evidence_required"):
            MODULE.report({"schema_version": 1, "cases": [row]})
        row["bridge"]["startup_evidence"] = "harness_trace"
        row["bridge"]["page_body"] = "private"
        with self.assertRaisesRegex(MODULE.EvidenceError, "invalid_arm"):
            MODULE.report({"schema_version": 1, "cases": [row]})

    def test_missing_outcomes_are_not_assumed_failures(self):
        result = MODULE.report({"schema_version": 1, "cases": [
            {"case_id": "one", "harness": "pi", "baseline": arm(), "bridge": arm()}
        ]})["summary"]["pi"]
        self.assertEqual(result["independently_labeled_task_success"]["paired_count"], 0)
        self.assertIsNone(result["independently_labeled_task_success"]["delta_mean"])

    def test_rejects_reserved_harness_and_reused_session(self):
        row = {"case_id": "one", "harness": "all", "baseline": arm(), "bridge": arm()}
        with self.assertRaisesRegex(MODULE.EvidenceError, "reserved_harness"):
            MODULE.report({"schema_version": 1, "cases": [row]})
        row["harness"] = "cursor"
        row["baseline"]["session_id"] = "shared-session"
        row["bridge"]["session_id"] = "shared-session"
        with self.assertRaisesRegex(MODULE.EvidenceError, "same_session"):
            MODULE.report({"schema_version": 1, "cases": [row]})


if __name__ == "__main__":
    unittest.main()
