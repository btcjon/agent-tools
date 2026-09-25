import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_bridge import LauncherError, load_identity, main


class LauncherTests(unittest.TestCase):
    def _files(self, root):
        credential = root / "credential.env"
        workspace = root / "workspace.json"
        credential.write_text("NOTION_API_TOKEN=synthetic-test-token\n")
        workspace.write_text(json.dumps({"workspace_id": "11111111-2222-4333-8444-555555555555"}))
        credential.chmod(0o600)
        workspace.chmod(0o600)
        return credential, workspace

    def test_requires_private_exact_identity_files(self):
        with TemporaryDirectory() as tmp:
            credential, workspace = self._files(Path(tmp))
            token, identity = load_identity(credential, workspace)
            self.assertEqual(token, "synthetic-test-token")
            self.assertEqual(identity, "11111111-2222-4333-8444-555555555555")
            credential.chmod(0o644)
            with self.assertRaises(LauncherError):
                load_identity(credential, workspace)
            credential.chmod(0o600)
            credential.write_text("NOTION_API_TOKEN=synthetic\nOTHER=value\n")
            with self.assertRaises(LauncherError):
                load_identity(credential, workspace)

    def test_exec_passes_only_declared_identity_and_server_args(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            credential, workspace = self._files(root)
            executable = root / "server"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o700)
            with patch("run_bridge.os.execve") as execute:
                status = main([
                    "--credential-file", str(credential),
                    "--workspace-file", str(workspace),
                    "--executable", str(executable),
                    "--", "--release-root", "/tmp/pinned", "--harness", "codex",
                ])
            self.assertEqual(status, 0)
            command, args, env = execute.call_args.args
            self.assertEqual(command, str(executable))
            self.assertEqual(args, [str(executable), "--release-root", "/tmp/pinned", "--harness", "codex"])
            self.assertEqual(env["NOTION_API_TOKEN"], "synthetic-test-token")
            self.assertEqual(env["NOTION_CREDENTIAL_SOURCE"], "env")
            self.assertNotIn(str(credential), args)


if __name__ == "__main__":
    unittest.main()
