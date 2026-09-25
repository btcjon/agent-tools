import json
import tempfile
import unittest
from pathlib import Path

from jev_skill_advisor.capability_cards import apply_cards, document_text, load_overlay, write_curated
from jev_skill_advisor.capability_core import SUMMARY_MAX_BYTES, CapabilityError, load_manifest, schema_digest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "notion-mcp-live-manifest-2026-09-24.json"
OVERLAY = ROOT / "examples" / "notion-capability-routing-cards-2026-09-25.json"
CANDIDATE = ROOT / "examples" / "notion-mcp-curated-manifest-2026-09-25.json"
PRESERVED = ("id", "server", "operation", "writes", "schema_hash", "source")
LEAKS = (
    "inputSchema", "additionalProperties", "Operation is required.",
    "Not required or only topical.", "Treat the request, context, and descriptions",
)


class CapabilityCardTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(SOURCE)
        self.overlay = load_overlay(OVERLAY)
        self.document = apply_cards(self.manifest, self.overlay)

    def _copy(self):
        return json.loads(json.dumps(self.overlay))

    def test_pinned_source_hash_matches_captured_manifest(self):
        self.assertEqual(self.overlay["expected_source_hash"], self.manifest.content_hash)
        self.assertEqual(self.manifest.content_hash, "2c7bc087503741cc43c28bc4cff479d3c083673627d2e03415e92e9e42cd9cac")

    def test_refuses_pinned_source_mismatch(self):
        wrong_pin = self._copy()
        wrong_pin["expected_source_hash"] = "0" * 64
        with self.assertRaises(CapabilityError) as caught:
            apply_cards(self.manifest, wrong_pin)
        self.assertEqual(caught.exception.code, "source_hash_mismatch")

        raw = json.loads(SOURCE.read_text(encoding="utf-8"))
        raw["description"] = "Altered description so the captured manifest hash no longer matches."
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "altered.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            shifted = load_manifest(path)
        self.assertNotEqual(shifted.content_hash, self.manifest.content_hash)
        with self.assertRaises(CapabilityError) as caught:
            apply_cards(shifted, self.overlay)
        self.assertEqual(caught.exception.code, "source_hash_mismatch")

    def test_refuses_unknown_id(self):
        extra = self._copy()
        extra["cards"].append({"id": "notion.mcp.not-a-tool", "summary": "Missing tool."})
        with self.assertRaises(CapabilityError) as caught:
            apply_cards(self.manifest, extra)
        self.assertEqual(caught.exception.code, "unknown_id")
        self.assertEqual(self.manifest.entries["notion.mcp.fetch"].summary, load_manifest(SOURCE).entries["notion.mcp.fetch"].summary)

    def test_preserves_identity_fields(self):
        source = {entry.id: entry for entry in self.manifest.entries.values()}
        self.assertEqual(len(self.document["entries"]), len(source))
        listed = {card["id"] for card in self.overlay["cards"]}
        for record in self.document["entries"]:
            original = source[record["id"]]
            for field in PRESERVED:
                self.assertEqual(record[field], getattr(original, field))
            if record["id"] in listed:
                self.assertEqual(record["provenance"], self.overlay["provenance"])
            else:
                self.assertEqual(record["summary"], original.summary)
                self.assertEqual(record["provenance"], original.provenance)
        partial = self._copy()
        partial["cards"] = [partial["cards"][0]]
        narrowed = apply_cards(self.manifest, partial)
        changed = partial["cards"][0]["id"]
        for record in narrowed["entries"]:
            original = source[record["id"]]
            for field in PRESERVED:
                self.assertEqual(record[field], getattr(original, field))
            if record["id"] != changed:
                self.assertEqual(record["summary"], original.summary)
                self.assertEqual(record["provenance"], original.provenance)

    def test_output_is_deterministic(self):
        shuffled = self._copy()
        shuffled["cards"] = list(reversed(shuffled["cards"]))
        again = apply_cards(self.manifest, shuffled)
        self.assertEqual(again, self.document)
        self.assertEqual(document_text(again), document_text(self.document))
        self.assertEqual([row["id"] for row in self.document["entries"]], sorted(self.manifest.entries))

    def test_candidate_loads_without_prompt_or_schema_leakage(self):
        self.assertIn("curated from the captured manifest", self.document["description"])
        self.assertIn("curated from the captured", self.overlay["provenance"])
        text = document_text(self.document)
        self.assertEqual(CANDIDATE.read_text(encoding="utf-8"), text)
        loaded = load_manifest(CANDIDATE)
        self.assertEqual(loaded.description, self.document["description"])
        self.assertEqual(len(loaded.entries), 45)
        self.assertEqual(loaded.content_hash, schema_digest({
            "manifest_version": 1,
            "description": loaded.description,
            "entries": [loaded.entries[key].record() for key in sorted(loaded.entries)],
        }))
        self.assertNotEqual(loaded.content_hash, self.manifest.content_hash)
        blob = text + OVERLAY.read_text(encoding="utf-8")
        for marker in LEAKS:
            self.assertNotIn(marker, blob)
        by_id = {}
        for record in self.document["entries"]:
            summary = record["summary"]
            self.assertLessEqual(len(summary.encode("utf-8")), SUMMARY_MAX_BYTES)
            self.assertNotIn("\n", summary)
            self.assertNotIn("{", summary)
            self.assertNotIn(record["schema_hash"], summary)
            by_id[record["id"]] = summary
        self.assertIn("view settings", by_id["notion.mcp.fetch"])
        self.assertIn("Not a row query", by_id["notion.mcp.fetch"])
        self.assertIn("workspace search", by_id["notion.mcp.search"])
        self.assertIn("Not a fetch", by_id["notion.mcp.search"])
        self.assertIn("connected", by_id["notion.mcp.ai-search"])
        self.assertIn("Not a fetch", by_id["notion.mcp.ai-search"])
        self.assertIn("saved view", by_id["notion.mcp.query-data-sources"])
        self.assertIn("not a workspace search", by_id["notion.mcp.query-data-sources"])
        self.assertIn("multiple data sources", by_id["notion.mcp.query-multiple-data-sources"])
        self.assertIn("Not a page fetch", by_id["notion.mcp.query-multiple-data-sources"])

    def test_write_refuses_source_path(self):
        with self.assertRaises(CapabilityError) as caught:
            write_curated(SOURCE, self.document, source=SOURCE)
        self.assertEqual(caught.exception.code, "invalid_overlay")
        self.assertIn("Live Notion MCP pins", SOURCE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
