import time
import unittest

from jev_skill_advisor import catalog_choice
from jev_skill_advisor.catalog_choice import _envelope, catalog_choice_scan, pack_batches


class Entry:
    def __init__(self, sid, description="Applies to this kind of task."):
        self.id = sid
        self.description = description


class CatalogChoiceTests(unittest.TestCase):
    def test_past_the_old_limit_of_12_can_win(self):
        entries = [Entry(f"warehouse:skill-{index}") for index in range(13)]

        def evaluator(payload, _timeout):
            criteria = payload["questions"]["winner"]["criteria"]
            choice = max((name for name in criteria if name != "none"), key=lambda name: int(name[1:]))
            probabilities = {name: (0.8 if name == choice else 0.2 / (len(criteria) - 1)) for name in criteria}
            return {"model": "jev-1.13.0", "answers": {"winner": {
                "type": "choice", "choice": choice, "confidence": 0.8, "probabilities": probabilities,
            }}, "usage": {"input_tokens": 12}}

        receipt = catalog_choice_scan(entries, "use skill 12", "", evaluator, deadline_s=5, max_calls=4)
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(receipt["selected"], ["warehouse:skill-12"])
        self.assertGreaterEqual(len(entries), 13)

    def test_none_adds_nothing(self):
        entries = [Entry("warehouse:alpha"), Entry("warehouse:beta")]

        def evaluator(payload, _timeout):
            criteria = payload["questions"]["winner"]["criteria"]
            probabilities = {name: (0.9 if name == "none" else 0.1 / (len(criteria) - 1)) for name in criteria}
            return {"model": "jev-1.13.0", "answers": {"winner": {
                "type": "choice", "choice": "none", "confidence": 0.9, "probabilities": probabilities,
            }}, "usage": {"input_tokens": 8}}

        receipt = catalog_choice_scan(entries, "nothing applies", "", evaluator)
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(receipt["selected"], [])

    def _answer(self, payload):
        criteria = payload["questions"]["winner"]["criteria"]
        choice = max((name for name in criteria if name != "none"), key=lambda name: int(name[1:]))
        probabilities = {name: (0.8 if name == choice else 0.2 / (len(criteria) - 1)) for name in criteria}
        return {"model": "jev-1.13.0", "answers": {"winner": {
            "type": "choice", "choice": choice, "confidence": 0.8, "probabilities": probabilities,
        }}, "usage": {"input_tokens": 12}}

    def test_later_round_reduces_winners(self):
        entries = [Entry("warehouse:a"), Entry("warehouse:b"), Entry("warehouse:c")]
        calls = []

        def evaluator(payload, _timeout):
            calls.append([card["id"] for card in payload["state"]["candidates"]])
            return self._answer(payload)

        original = catalog_choice.STATE_BUDGET
        catalog_choice.STATE_BUDGET = 450
        try:
            receipt = catalog_choice_scan(entries, "pick the last applicable skill", "", evaluator, deadline_s=5, max_calls=8)
        finally:
            catalog_choice.STATE_BUDGET = original
        self.assertGreater(len(calls), 1)
        self.assertIn("warehouse:c", calls[-1])
        self.assertGreater(len(calls[-1]), 1)
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(receipt["selected"], ["warehouse:c"])
        self.assertTrue(all(len(batch) <= 254 for batch in calls))

    def test_deadline_adds_nothing(self):
        entries = [Entry("warehouse:a"), Entry("warehouse:b")]

        def evaluator(payload, _timeout):
            time.sleep(0.05)
            return self._answer(payload)

        original = catalog_choice.STATE_BUDGET
        catalog_choice.STATE_BUDGET = 400
        try:
            receipt = catalog_choice_scan(entries, "needs two calls", "", evaluator, deadline_s=0.01, max_calls=8)
        finally:
            catalog_choice.STATE_BUDGET = original
        self.assertEqual(receipt["status"], "incomplete")
        self.assertEqual(receipt["selected"], [])
        self.assertEqual(receipt["reason"], "deadline_or_token_budget")

    def test_provider_failure_adds_nothing(self):
        def evaluator(_payload, _timeout):
            raise RuntimeError("down")

        receipt = catalog_choice_scan([Entry("warehouse:alpha")], "check the tailnet", "", evaluator)
        self.assertEqual(receipt["status"], "incomplete")
        self.assertEqual(receipt["selected"], [])

    def test_no_batch_exceeds_option_cap(self):
        entries = [Entry(f"warehouse:s{index}") for index in range(300)]
        batches = pack_batches("check something", "", entries)
        self.assertTrue(batches)
        self.assertTrue(all(len(batch) <= 254 for batch in batches))
        self.assertEqual([entry.id for batch in batches for entry in batch], [entry.id for entry in entries])

    def test_instructions_prefer_abstention_and_keep_a_short_clear_task(self):
        payload = _envelope(
            "Ask whether this system is as good as it can be.",
            "",
            [Entry("warehouse:messaging-on-behalf", "Draft and send messages.")],
        )
        instructions = payload["questions"]["winner"]["instructions"]
        none = payload["questions"]["winner"]["criteria"]["none"]
        option = payload["questions"]["winner"]["criteria"]["o0"]
        self.assertIn("weak", instructions)
        self.assertIn("ordinary question", instructions)
        self.assertIn("clearly applies still applies when the task is short", instructions)
        self.assertIn("weak", none)
        self.assertIn("not enough", option)

    def test_batches_cover_every_entry(self):
        entries = [Entry(f"warehouse:s{index}") for index in range(5)]
        original = catalog_choice.STATE_BUDGET
        catalog_choice.STATE_BUDGET = 400
        try:
            batches = pack_batches("task", "", entries)
        finally:
            catalog_choice.STATE_BUDGET = original
        covered = [entry.id for batch in batches for entry in batch]
        self.assertEqual(covered, [entry.id for entry in entries])
        self.assertGreater(len(batches), 1)


if __name__ == "__main__":
    unittest.main()
