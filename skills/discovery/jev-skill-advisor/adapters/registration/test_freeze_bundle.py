import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from freeze_bundle import (
    BundleError,
    REVISION,
    build,
    entries,
    export_source,
    validate_output_root,
    verify,
)


class FrozenBundleTests(unittest.TestCase):
    def test_clean_archive_ignores_worktree_content(self):
        with TemporaryDirectory() as tmp:
            repo = Path(__file__).resolve().parents[5]
            source = export_source(repo, REVISION, Path(tmp))
            archived = source / "pyproject.toml"
            pinned = subprocess.run(
                ["git", "-C", str(repo), "show", f"{REVISION}:skills/discovery/jev-skill-advisor/pyproject.toml"],
                check=True, capture_output=True,
            ).stdout
            self.assertEqual(archived.read_bytes(), pinned)
            self.assertTrue((source / "uv.lock").is_file())
            self.assertFalse((source / "adapters/registration/freeze_bundle.py").exists())

    def test_output_root_refuses_checkout_and_symlink_escape(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(BundleError):
                validate_output_root(Path("relative"))
            with self.assertRaises(BundleError):
                validate_output_root(Path("/Users/jonbennett/Library/CloudStorage/Dropbox/bundles"))
            link = Path(tmp) / "linked"
            link.symlink_to(Path("/Users/jonbennett/Library/CloudStorage/Dropbox"))
            with self.assertRaises(BundleError):
                validate_output_root(link)

    def test_content_gate_rejects_pth_secret_editable_and_checkout_text(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in (
                ("_virtualenv.pth", b"import something"),
                ("credential.env", b"not-a-real-token"),
                ("direct_url.json", b'{"dir_info":{"editable":true}}'),
                ("source.py", b"/Library/CloudStorage/Dropbox/Projects"),
            ):
                path = root / name
                path.write_bytes(body)
                with self.assertRaises(BundleError):
                    entries(root)
                path.unlink()

    def test_manifest_detects_tamper(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "bin/python").write_text("python")
            (root / "bin/skill-advisor-mcp").write_text("mcp")
            manifest = {"schema_version": 1, "release_id": "a" * 64, "files": entries(root)}
            (root / "bundle-manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(verify(root, require_readonly=False)["release_id"], "a" * 64)
            (root / "bin/skill-advisor-mcp").write_text("changed")
            with self.assertRaises(BundleError):
                verify(root, require_readonly=False)

    def test_missing_offline_wheel_fails_without_final(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = Path(__file__).resolve().parents[5]
            python = Path(sys._base_executable)
            with patch("freeze_bundle.run", side_effect=BundleError("offline_artifact_unavailable")):
                with self.assertRaises(BundleError) as raised:
                    build(repo, root, revision=REVISION, release="a" * 64, python=python)
            self.assertEqual(str(raised.exception), "offline_artifact_unavailable")
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
