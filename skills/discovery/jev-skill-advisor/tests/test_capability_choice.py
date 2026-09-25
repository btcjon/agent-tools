import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jev_skill_advisor.capability_choice import (
    CAPABILITY_SELECTION_P95_MS, CAPABILITY_STATE_BUDGET_BYTES, CAPABILITY_WIRE_BUDGET_BYTES,
    MODEL, _fits, capability_choice_envelope, explicit_capability_ids, resolve_capabilities,
    select_capabilities,
)
from jev_skill_advisor.capability_core import load_manifest, selection_cards
from jev_skill_advisor.client import AdvisorError, validate_response

ROOT_TASK = "file the weekly status where the team can find it"
MANIFEST = Path(__file__).resolve().parents[1] / "examples" / "notion-mcp-capability-manifest.json"
NOTION = [{"id": "warehouse:notion", "name": "notion", "description": "Work in Notion"}]
ALPHA = [{"id": "warehouse:alpha", "name": "alpha", "description": "Mentions Notion only in prose"}]


def jev_response(payload, decisions, confidences=None, probabilities=None, write_intent=0.9):
    answers = {}
    for index, card in enumerate(payload["state"]["candidates"]):
        choice = decisions.get(card["id"], "skip")
        if probabilities and card["id"] in probabilities:
            noul = float(probabilities[card["id"]]["use"])
        elif confidences and card["id"] in confidences:
            noul = float(confidences[card["id"]])
        elif choice == "use":
            noul = 0.9
        else:
            noul = 0.1
        answers[f"fit_{index}"] = {"type": "noul", "noul": noul}
    answers["write_intent"] = {"type": "noul", "noul": write_intent}
    return {"model": MODEL, "answers": answers, "usage": {"input_tokens": 12}}


class CapabilityChoiceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(MANIFEST)
        self.seen = []

    def _select(self, decisions, task=ROOT_TASK, selected=None, confidences=None, probabilities=None, evaluator=None, write_intent=0.9):
        def fake(payload, timeout):
            self.seen.append((payload, timeout))
            return jev_response(payload, decisions, confidences, probabilities, write_intent)
        return resolve_capabilities(
            self.manifest, task, context="", selected_skills=NOTION if selected is None else selected,
            evaluator=evaluator or fake,
        )

    def test_semantic_choice_is_not_keyword_overlap(self):
        lexical = [card["id"] for card in selection_cards(self.manifest, ROOT_TASK)]
        self.assertNotIn("notion.mcp.create-pages", lexical)
        self.assertTrue(lexical)
        result = self._select({"notion.mcp.create-pages": "use"})
        self.assertEqual(result["ids"], ["notion.mcp.create-pages"])
        self.assertEqual(result["manifest_hash"], self.manifest.content_hash)
        self.assertEqual(result["evidence"]["manifest_hash"], self.manifest.content_hash)
        self.assertEqual(result["reason"], "capability_choice_selected")
        payload, timeout = self.seen[0]
        self.assertLessEqual(timeout, CAPABILITY_SELECTION_P95_MS / 1000)
        self.assertEqual(set(payload["questions"]), {f"fit_{index}" for index in range(6)} | {"write_intent"})
        for question in payload["questions"].values():
            self.assertEqual(question["type"], "noul")
            self.assertEqual(set(question["criteria"]), {"true", "false"})
        self.assertEqual(result["evidence"]["budgets"]["p95_basis"], "enforced_deadline_not_measured_percentile")
        for card in payload["state"]["candidates"]:
            self.assertEqual(set(card), {"option", "id", "description", "writes"})
        blob = json.dumps(payload["state"])
        self.assertNotIn("inputSchema", blob)
        self.assertNotIn("schema_hash", blob)
        self.assertNotIn("additionalProperties", blob)
        self.assertEqual(payload["state"]["manifest_hash"], self.manifest.content_hash)
        self.assertLessEqual(result["evidence"]["budgets"]["state_bytes"], CAPABILITY_STATE_BUDGET_BYTES)
        self.assertEqual(result["evidence"]["budgets"]["p95_ms"], CAPABILITY_SELECTION_P95_MS)
        self.assertIn("p95_ms", result["evidence"]["budgets"])
        again = load_manifest(MANIFEST)
        self.assertEqual(again.content_hash, self.manifest.content_hash)

    def test_clear_null_does_not_fall_back_to_lexical_cards(self):
        lexical = [card["id"] for card in selection_cards(self.manifest, ROOT_TASK)]
        self.assertTrue(lexical)
        result = self._select({})
        self.assertEqual(result["status"], "none")
        self.assertEqual(result["reason"], "capability_choice_none")
        self.assertEqual(result["ids"], [])
        self.assertEqual(result["cards"], [])
        self.assertFalse(set(result["ids"]) & set(lexical))

    def test_uncertain_answer_authorizes_nothing(self):
        result = self._select(
            {"notion.mcp.search": "use"},
            probabilities={"notion.mcp.search": {"use": 0.55, "skip": 0.45}},
            confidences={"notion.mcp.search": 0.9},
        )
        self.assertEqual(result["ids"], [])
        self.assertEqual(result["reason"], "capability_choice_none")
        self.assertEqual(result["status"], "none")
        search = next(row for row in result["evidence"]["decisions"] if row["id"] == "notion.mcp.search")
        self.assertEqual(search["choice"], "abstain")

    def test_read_only_intent_cannot_surface_a_write_tool(self):
        result = self._select(
            {"notion.mcp.query-data-sources": "use", "notion.mcp.update-page": "use"},
            task="Read rows from the data source and keep links in the response.",
            write_intent=0.1,
        )
        self.assertEqual(result["ids"], ["notion.mcp.query-data-sources"])
        update = next(row for row in result["evidence"]["decisions"] if row["id"] == "notion.mcp.update-page")
        self.assertEqual(update["choice"], "write_intent_abstain")

    def test_bounded_to_five_highest_confidence_ids(self):
        ids = list(self.manifest.entries)
        confidences = {
            "notion.mcp.search": 0.99,
            "notion.mcp.query-data-sources": 0.98,
            "notion.mcp.get-comments": 0.97,
            "notion.mcp.create-pages": 0.96,
            "notion.mcp.update-page": 0.95,
            "notion.mcp.fetch": 0.70,
        }
        result = self._select({identifier: "use" for identifier in ids}, confidences=confidences)
        self.assertEqual(len(result["ids"]), 5)
        self.assertNotIn("notion.mcp.fetch", result["ids"])
        self.assertEqual(result["ids"][0], "notion.mcp.search")
        self.assertEqual(len(self.manifest.entries), 6)

    def test_provider_timeout_fail_open_without_lexical_ids(self):
        def explode(payload, timeout):
            self.seen.append(payload)
            raise TimeoutError("typesafe_timeout")

        lexical = [card["id"] for card in selection_cards(self.manifest, "")]
        result = self._select({}, evaluator=explode)
        self.assertEqual(result["ids"], [])
        self.assertEqual(result["reason"], "capability_choice_provider_failure")
        self.assertTrue(lexical)
        self.assertEqual(self.seen, [self.seen[0]])

    def test_missing_provider_fail_open(self):
        result = resolve_capabilities(self.manifest, ROOT_TASK, selected_skills=NOTION, evaluator=None)
        self.assertEqual(result["ids"], [])
        self.assertEqual(result["reason"], "capability_choice_provider_failure")

    def test_non_notion_skill_does_not_receive_notion_operations(self):
        def explode(payload, timeout):
            raise AssertionError("jev should not be called")

        result = resolve_capabilities(
            self.manifest, ROOT_TASK, selected_skills=ALPHA, evaluator=explode,
        )
        self.assertEqual(result["ids"], [])
        self.assertEqual(result["reason"], "not_notion_skill")

    def test_explicit_tool_name_is_authoritative(self):
        def explode(payload, timeout):
            raise AssertionError("explicit name must not call jev")

        task = "please run notion-update-page on the spec"
        result = resolve_capabilities(self.manifest, task, selected_skills=ALPHA, evaluator=explode)
        self.assertEqual(result["ids"], ["notion.mcp.update-page"])
        self.assertEqual(result["reason"], "explicit_tool_authoritative")
        self.assertEqual(result["manifest_hash"], self.manifest.content_hash)
        self.assertNotIn("inputSchema", json.dumps(result))
        named = explicit_capability_ids(self.manifest, "use notion.mcp.fetch before anything else")
        self.assertEqual(named, ["notion.mcp.fetch"])

    def test_explicit_names_are_capped_at_five(self):
        task = " ".join(entry.operation for entry in self.manifest.entries.values())
        ids = explicit_capability_ids(self.manifest, task)
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(self.manifest.entries), 6)

    def test_oversized_request_does_not_use_lexical_fallback(self):
        import jev_skill_advisor.capability_choice as choice

        def explode(payload, timeout):
            raise AssertionError("oversized request must not call jev")

        original = choice.CAPABILITY_STATE_BUDGET_BYTES
        choice.CAPABILITY_STATE_BUDGET_BYTES = 10
        try:
            result = select_capabilities(
                self.manifest, ROOT_TASK, selected_skills=NOTION, evaluator=explode,
            )
        finally:
            choice.CAPABILITY_STATE_BUDGET_BYTES = original
        self.assertEqual(result["ids"], [])
        self.assertEqual(result["reason"], "oversized_capability_request")

    def test_protected_task_is_not_sent_to_jev(self):
        def explode(payload, timeout):
            raise AssertionError("secret task must not be sent")

        result = resolve_capabilities(
            self.manifest, "authorization: bearer secret", selected_skills=NOTION, evaluator=explode,
        )
        self.assertEqual(result["ids"], [])
        self.assertEqual(result["reason"], "protected_input")

    def test_forty_five_tools_fit_one_noul_pass(self):
        """Full catalog in one parallel noul pass, inside the declared byte budgets.

        p95_ms is the deadline passed to the evaluator, not a measured percentile.
        elapsed_ms is only the local fake-call duration.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(_scale_manifest()), encoding="utf-8")
            manifest = load_manifest(path)
        self.assertEqual(len(manifest.entries), 45)
        lexical = [card["id"] for card in selection_cards(manifest, ROOT_TASK)]
        target = "notion.mcp.t44"
        peer = "notion.mcp.t01"
        self.assertTrue(lexical)
        self.assertNotIn(target, lexical)
        calls = []

        def fake(payload, timeout):
            calls.append((payload, timeout))
            answers = {}
            for index, card in enumerate(payload["state"]["candidates"]):
                if card["id"] == target:
                    noul = 0.91
                elif card["id"] == peer:
                    noul = 0.5
                else:
                    noul = 0.08
                answers[f"fit_{index}"] = {"type": "noul", "noul": noul}
            answers["write_intent"] = {"type": "noul", "noul": 0.9}
            response = {"model": MODEL, "answers": answers, "usage": {"input_tokens": 45}}
            validate_response(response, payload["questions"], MODEL)
            return response

        result = resolve_capabilities(manifest, ROOT_TASK, context="", selected_skills=NOTION, evaluator=fake)
        self.assertEqual(len(calls), 1)
        payload, timeout = calls[0]
        self.assertEqual(timeout, CAPABILITY_SELECTION_P95_MS / 1000)
        self.assertEqual(len(payload["state"]["candidates"]), 45)
        self.assertEqual(len(payload["questions"]), 46)
        self.assertEqual([card["id"] for card in payload["state"]["candidates"]][44], target)
        self.assertEqual(payload["state"]["candidates"][44]["description"], "Compose a parented document.")
        self.assertTrue(all(question["type"] == "noul" for question in payload["questions"].values()))
        self.assertTrue(all(
            manifest.entries[card["id"]].operation in payload["questions"][f"fit_{index}"]["instructions"]
            for index, card in enumerate(payload["state"]["candidates"])
        ))
        state_bytes, wire_bytes, fits = _fits(payload)
        self.assertTrue(fits)
        self.assertLessEqual(state_bytes, CAPABILITY_STATE_BUDGET_BYTES)
        self.assertLessEqual(wire_bytes, CAPABILITY_WIRE_BUDGET_BYTES)
        self.assertEqual(result["evidence"]["budgets"]["state_bytes"], state_bytes)
        self.assertEqual(result["evidence"]["budgets"]["wire_bytes"], wire_bytes)
        self.assertEqual(result["evidence"]["budgets"]["p95_ms"], CAPABILITY_SELECTION_P95_MS)
        self.assertEqual(result["evidence"]["budgets"]["p95_basis"], "enforced_deadline_not_measured_percentile")
        self.assertGreaterEqual(result["evidence"]["budgets"]["elapsed_ms"], 0.0)
        blob = json.dumps(payload["state"])
        self.assertNotIn("inputSchema", blob)
        self.assertNotIn("schema_hash", blob)
        self.assertEqual(result["ids"], [target])
        self.assertEqual(result["status"], "selected")
        self.assertNotIn(peer, result["ids"])
        by_id = {row["id"]: row["choice"] for row in result["evidence"]["decisions"]}
        self.assertEqual(by_id[target], "use")
        self.assertEqual(by_id[peer], "abstain")

        def skip_all(payload, timeout):
            calls.append((payload, timeout))
            answers = {
                f"fit_{index}": {"type": "noul", "noul": 0.08}
                for index in range(len(payload["state"]["candidates"]))
            }
            answers["write_intent"] = {"type": "noul", "noul": 0.1}
            response = {"model": MODEL, "answers": answers, "usage": {"input_tokens": 45}}
            validate_response(response, payload["questions"], MODEL)
            return response

        empty = resolve_capabilities(manifest, ROOT_TASK, context="", selected_skills=NOTION, evaluator=skip_all)
        self.assertEqual(len(calls), 2)
        self.assertEqual(empty["ids"], [])
        self.assertEqual(empty["cards"], [])
        self.assertEqual(empty["status"], "none")
        self.assertEqual(empty["reason"], "capability_choice_none")
        self.assertTrue(_fits(calls[1][0])[2])

    def test_provider_failure_classes_omit_exception_text(self):
        secret = "sk-live-SUPERSECRET"
        cases = [
            (AdvisorError("missing_api_key"), "missing_credential"),
            (AdvisorError(f"cannot read selected env file: /Users/a/{secret}"), "missing_credential"),
            (TimeoutError(f"timed out Bearer {secret}"), "timeout"),
            (AdvisorError("typesafe_timeout"), "timeout"),
            (AdvisorError("typesafe_transport_failure"), "transport"),
            (OSError(f"connect failed {secret}"), "transport"),
            (AdvisorError("typesafe_http_503"), "provider"),
            (AdvisorError(f"typesafe_http_401 {secret}"), "other"),
            (AdvisorError("typesafe_invalid_json"), "invalid_response"),
            (AdvisorError("response is not an object"), "invalid_response"),
            (json.JSONDecodeError(secret, secret, 0), "invalid_response"),
            (AdvisorError("local_provider_attempt_budget"), "budget"),
            (RuntimeError(f"TYPESAFE_API_KEY={secret}"), "other"),
        ]
        for exc, expected in cases:
            calls = []

            def explode(payload, timeout, exc=exc):
                calls.append(1)
                raise exc

            result = select_capabilities(
                self.manifest, ROOT_TASK, selected_skills=NOTION, evaluator=explode,
            )
            blob = json.dumps(result)
            self.assertEqual(result["status"], "fail_open", expected)
            self.assertEqual(result["reason"], "capability_choice_provider_failure", expected)
            self.assertEqual(result["ids"], [], expected)
            self.assertEqual(result["cards"], [], expected)
            self.assertEqual(result["failure_class"], expected, type(exc).__name__)
            self.assertEqual(result["evidence"]["failure_class"], expected)
            self.assertEqual(calls, [1], expected)
            self.assertNotIn(secret, blob)
            self.assertNotIn("TYPESAFE_API_KEY", blob)
            self.assertNotIn("Bearer", blob)
            message = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
            if message and message != expected:
                self.assertNotIn(message, blob, expected)
        absent = resolve_capabilities(self.manifest, ROOT_TASK, selected_skills=NOTION, evaluator=None)
        self.assertEqual(absent["reason"], "capability_choice_provider_failure")
        self.assertNotIn("failure_class", absent)
        self.assertNotIn("failure_class", absent["evidence"])
        clean = self._select({"notion.mcp.fetch": "use"})
        self.assertNotIn("failure_class", clean)

    def test_invalid_provider_payload_is_one_call_and_fail_open(self):
        calls = []

        def bad(payload, timeout):
            calls.append(payload)
            return {"model": "not-the-pinned-model", "answers": {}, "usage": {"input_tokens": 1}}

        result = select_capabilities(self.manifest, ROOT_TASK, selected_skills=NOTION, evaluator=bad)
        blob = json.dumps(result)
        self.assertEqual(calls, [calls[0]])
        self.assertEqual(result["failure_class"], "invalid_response")
        self.assertEqual(result["ids"], [])
        self.assertNotIn("not-the-pinned-model", blob)
        self.assertNotIn("returned model", blob)


def _scale_manifest():
    entries = []
    for index in range(45):
        if index == 0:
            summary = "Find the weekly status file for the team."
        elif index == 1:
            summary = "Archive a discussion thread."
        elif index == 44:
            summary = "Compose a parented document."
        else:
            summary = f"Hold marker {index:02d} aside."
        operation = f"notion-t{index:02d}"
        entries.append({
            "id": f"notion.mcp.t{index:02d}",
            "server": "notion",
            "operation": operation,
            "summary": summary,
            "writes": index == 44,
            "schema_hash": hashlib.sha256(operation.encode()).hexdigest(),
            "source": "examples/synthetic-scale.json",
            "provenance": "deterministic-scale-fixture",
        })
    return {"manifest_version": 1, "description": "Forty-five short capability cards.", "entries": entries}


if __name__ == "__main__":
    unittest.main()
