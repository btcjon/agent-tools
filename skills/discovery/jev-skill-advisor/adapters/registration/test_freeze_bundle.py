import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from freeze_bundle import (
    ENTRYPOINT,
    BundleError,
    REVISION,
    build,
    entries,
    export_source,
    interpreter_identity,
    validate_output_root,
    verify,
)

FAKE_INTERPRETER = """#!/bin/sh
printf '%s\\n' '3.12.0 (test)' 'LIBPYTHON:'
"""


class FrozenBundleTests(unittest.TestCase):
    def test_build_cli_requires_explicit_release(self):
        with TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("freeze_bundle.py")),
                 "build", "--repo", tmp, "--output-root", tmp, "--revision", REVISION],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {"status": "refused", "reason": "build_inputs_missing"})

    def test_build_cli_requires_explicit_revision(self):
        with TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("freeze_bundle.py")),
                 "build", "--repo", tmp, "--output-root", tmp, "--release", "a" * 64],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {"status": "refused", "reason": "build_inputs_missing"})

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

    def _write_bundle(self, root: Path, python: Path) -> dict:
        bindir = root / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        if not python.exists():
            python.write_text(FAKE_INTERPRETER)
            python.chmod(0o755)
        entry = bindir / "skill-advisor-mcp"
        entry.write_text(ENTRYPOINT)
        entry.chmod(0o755)
        manifest = {
            "schema_version": 1,
            "release_id": "a" * 64,
            "base_python": interpreter_identity(python),
            "files": entries(root),
        }
        (root / "bundle-manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_manifest_detects_tamper(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / "bin" / "python"
            self._write_bundle(root, python)
            self.assertEqual(verify(root, require_readonly=False)["release_id"], "a" * 64)
            (root / "bin/skill-advisor-mcp").write_text("changed")
            with self.assertRaises(BundleError) as raised:
                verify(root, require_readonly=False)
            self.assertEqual(str(raised.exception), "manifest_mismatch")

    def test_wrapper_realpath_keeps_physical_prefix_and_caller_cwd(self):
        base = Path(sys._base_executable)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            subprocess.run([str(base), "-m", "venv", "--without-pip", str(bundle)], check=True, capture_output=True)
            entry = bundle / "bin" / "skill-advisor-mcp"
            entry.write_text(ENTRYPOINT)
            entry.chmod(0o755)
            (bundle / "runtime_guard.py").write_text(
                "import os,sys\nprint(sys.prefix)\nprint(os.getcwd())\n"
            )
            current = root / "runtime" / "current"
            current.parent.mkdir()
            current.symlink_to(bundle)
            work = root / "caller work"
            work.mkdir()
            result = subprocess.run(
                [str(current / "bin" / "skill-advisor-mcp")],
                cwd=work, check=True, capture_output=True, text=True,
            )
            prefix, cwd = result.stdout.splitlines()
            self.assertEqual(Path(prefix).resolve(), bundle.resolve())
            self.assertEqual(Path(cwd).resolve(), work.resolve())
            self.assertNotIn(str(current), prefix)

    def test_external_interpreter_tamper_and_mismatch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            outside = root / "outside"
            outside.mkdir()
            real_a = outside / "python-a"
            real_b = outside / "python-b"
            real_a.write_text(FAKE_INTERPRETER)
            real_b.write_text(FAKE_INTERPRETER.replace("3.12.0", "3.12.1"))
            real_a.chmod(0o755)
            real_b.chmod(0o755)
            hop = outside / "python-hop"
            hop.symlink_to(real_a)
            python = bundle / "bin" / "python"
            (bundle / "bin").mkdir(parents=True)
            python.symlink_to(hop)
            manifest = self._write_bundle(bundle, python)
            self.assertEqual(manifest["base_python"]["path"], str(real_a.resolve()))
            self.assertEqual(verify(bundle, require_readonly=False)["base_python"]["path"], str(real_a.resolve()))

            recorded = json.loads((bundle / "bundle-manifest.json").read_text())
            recorded["base_python"]["version"] = "9.9.9 (edited)"
            (bundle / "bundle-manifest.json").write_text(json.dumps(recorded))
            with self.assertRaises(BundleError) as raised:
                verify(bundle, require_readonly=False)
            self.assertEqual(str(raised.exception), "python_mismatch")

            hop.unlink()
            hop.symlink_to(real_b)
            (bundle / "bundle-manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(BundleError) as raised:
                verify(bundle, require_readonly=False)
            self.assertEqual(str(raised.exception), "python_mismatch")

            hop.unlink()
            hop.symlink_to(real_a)
            real_a.write_text(FAKE_INTERPRETER + "\n# tampered\n")
            real_a.chmod(0o755)
            (bundle / "bundle-manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(BundleError) as raised:
                verify(bundle, require_readonly=False)
            self.assertEqual(str(raised.exception), "python_mismatch")

            lib = outside / "libpython-fake"
            lib.write_bytes(b"original-lib")
            linked = outside / "python-lib"
            script = FAKE_INTERPRETER.replace(
                "LIBPYTHON:", f"LIBPYTHON:{lib.resolve()}",
            )
            linked.write_text(script)
            linked.chmod(0o755)
            hop.unlink()
            hop.symlink_to(linked)
            fresh = self._write_bundle(bundle, python)
            self.assertEqual(fresh["base_python"]["libpython_path"], str(lib.resolve()))
            lib.write_bytes(b"replaced-lib")
            (bundle / "bundle-manifest.json").write_text(json.dumps(fresh))
            with self.assertRaises(BundleError) as raised:
                verify(bundle, require_readonly=False)
            self.assertEqual(str(raised.exception), "python_mismatch")

            bare = dict(fresh)
            bare.pop("base_python")
            (bundle / "bundle-manifest.json").write_text(json.dumps(bare))
            with self.assertRaises(BundleError) as raised:
                verify(bundle, require_readonly=False)
            self.assertEqual(str(raised.exception), "python_unpinned")

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
