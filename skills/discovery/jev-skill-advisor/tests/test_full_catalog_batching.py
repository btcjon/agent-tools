from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from jev_skill_advisor.exposure import Capability, MODEL, Registry, scan


class FullCatalogBatchingTests(unittest.TestCase):
    def test_all_eligible_skills_are_scored_across_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "SKILL.md"
            source.write_text("---\nname: shared\ndescription: Shared test source.\n---\n")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            registry = Registry(
                Capability(
                    id=f"warehouse:skill-{index:03d}",
                    kind="skill",
                    description=f"Procedure number {index:03d} for bounded catalog coverage.",
                    source=str(source),
                    source_hash=source_hash,
                    policy_hash="policy",
                    disclose=True,
                )
                for index in range(615)
            )

            def evaluate(payload, _timeout):
                return {
                    "model": MODEL,
                    "usage": {"input_tokens": 1},
                    "answers": {key: {"type": "noul", "noul": 0.1} for key in payload["questions"]},
                }

            result = scan(registry, "Find the right procedure", "", evaluate, max_calls=32)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["reason"], "none")
            self.assertEqual(len(result["eligible"]), 615)
            self.assertEqual(len(result["scored"]), 615)
            self.assertGreaterEqual(result["attempts"], 10)


if __name__ == "__main__":
    unittest.main()
