import hashlib
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_guard import GuardError, bind_release, check_python


class RuntimeGuardTests(unittest.TestCase):
    def test_copied_launcher_rejects_interpreter_mismatch_before_server_start(self):
        with TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            (bundle / "bin").mkdir(parents=True)
            (bundle / "bin" / "python").symlink_to(Path(sys.executable).resolve())
            shutil.copy2(Path(__file__).with_name("runtime_guard.py"), bundle / "runtime_guard.py")
            (bundle / "bundle-manifest.json").write_text(json.dumps({
                "release_id": "a" * 64,
                "base_python": {
                    "path": str(Path(sys.executable).resolve()),
                    "sha256": "0" * 64,
                },
            }))
            result = subprocess.run(
                [sys.executable, "-I", "-s", str(bundle / "runtime_guard.py")],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("python_mismatch", result.stderr)
            self.assertNotIn("release_root_missing", result.stderr)

    def test_interpreter_hash_and_path_are_pinned(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            (bundle / "bin").mkdir(parents=True)
            interpreter = bundle / "bin" / "python"
            interpreter.write_bytes(b"one")
            record = {"base_python": {
                "path": str(interpreter.resolve()),
                "sha256": hashlib.sha256(b"one").hexdigest(),
            }}
            check_python(bundle, record)
            interpreter.write_bytes(b"two")
            with self.assertRaisesRegex(GuardError, "python_mismatch"):
                check_python(bundle, record)
            record["base_python"]["path"] = str(root / "other")
            with self.assertRaisesRegex(GuardError, "python_mismatch"):
                check_python(bundle, record)

    def test_release_is_explicitly_bound_and_drift_denied(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = "a" * 64
            pointer = root / "current-release.json"
            pointer.write_text(json.dumps({"schema_version": 1, "release_id": release}))
            args = ["--release-root", str(root), "--harness", "hermes"]
            self.assertEqual(bind_release(args, release)[-2:], ["--expected-release", release])
            pinned = [*args, "--expected-release", release]
            self.assertEqual(bind_release(pinned, release), pinned)
            with self.assertRaisesRegex(GuardError, "expected_release_mismatch"):
                bind_release([*args, "--expected-release", "b" * 64], release)
            pointer.write_text(json.dumps({"schema_version": 1, "release_id": "b" * 64}))
            with self.assertRaisesRegex(GuardError, "release_mismatch"):
                bind_release(args, release)
            with self.assertRaisesRegex(GuardError, "release_root_missing"):
                bind_release([], release)


if __name__ == "__main__":
    unittest.main()
