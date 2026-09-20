from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from jev_skill_advisor.catalog_cli import build_catalog

class CatalogTests(unittest.TestCase):
    def test_inventory_includes_or_excludes_every_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); good=root/"good"/"SKILL.md"; bad=root/"nested"/"bad"/"SKILL.md"; good.parent.mkdir(); bad.parent.mkdir(parents=True)
            good.write_text("---\nname: good\ndescription: >\n  Useful multi-line\n  routing description.\n---\n# Good\n")
            bad.write_text("# Missing metadata\n")
            result=build_catalog(root)
            self.assertEqual(result["skill_files"],2); self.assertEqual(result["included_count"],1); self.assertEqual(result["excluded_count"],1)
            self.assertEqual(result["entries"][0]["stable_id"],"warehouse:good"); self.assertIn("multi-line routing",result["entries"][0]["description"])
            self.assertEqual(result["exclusions"][0]["stable_id"],"warehouse:nested/bad")

    def test_deterministic_and_policy_aware(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); skill=root/"unicode"/"SKILL.md"; skill.parent.mkdir(); skill.write_text("---\nname: unicode\ndescription: Résumé helper\n---\n")
            sidecar=skill.parent/"agents"/"openai.yaml"; sidecar.parent.mkdir(); sidecar.write_text("allow_implicit_invocation: false\n")
            first=build_catalog(root); second=build_catalog(root)
            self.assertEqual(first,second); self.assertFalse(first["entries"][0]["implicit_eligible"]); self.assertEqual(first["entries"][0]["provider_exclusion_reason"],"explicit_only")

    def test_duplicate_metadata_and_symlink_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); bad=root/"bad"/"SKILL.md"; bad.parent.mkdir(); bad.write_text("---\nname: one\nname: two\ndescription: x\n---\n")
            target=root/"target.md"; target.write_text("---\nname: linked\ndescription: linked\n---\n"); linked=root/"linked"; linked.mkdir(); (linked/"SKILL.md").symlink_to(target)
            result=build_catalog(root); reasons={row["stable_id"]:row["reason"] for row in result["exclusions"]}
            self.assertIn("duplicate_frontmatter_key",reasons["warehouse:bad"]); self.assertEqual(reasons["warehouse:linked"],"symlink_or_path_escape")

    def test_frontmatter_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); skill=root/"large"; skill.mkdir()
            padding="\n".join(f"note_{index}: {'x' * 100}" for index in range(200))
            (skill/"SKILL.md").write_text(f"---\nname: large\ndescription: Valid metadata after a large header.\n{padding}\n---\nBody\n")
            result=build_catalog(root)
            self.assertEqual(result["included_count"],1)
            self.assertEqual(result["excluded_count"],0)

    def test_malformed_yaml_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); skill=root/"broken"; skill.mkdir()
            (skill/"SKILL.md").write_text('---\nname: broken\ndescription: "unterminated\n---\n')
            result=build_catalog(root)
            self.assertEqual(result["included_count"],0)
            self.assertEqual(result["exclusions"][0]["reason"],"malformed_frontmatter")

    def test_missing_warehouse_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,"warehouse_not_directory"):
                build_catalog(Path(directory)/"missing")

if __name__=="__main__": unittest.main()
