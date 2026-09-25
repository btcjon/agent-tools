import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from provision_identity import ProvisionError, extract_token, main


class ProvisionIdentityTests(unittest.TestCase):
    def test_extracts_exact_key_as_data_not_shell(self):
        self.assertEqual(extract_token("OTHER=skip\nNOTION_PAT='synthetic-token'\nBAD=$(echo no)\n"), "synthetic-token")
        with self.assertRaises(ProvisionError):
            extract_token("NOTION_PAT=one\nNOTION_PAT=two\n")
        with self.assertRaises(ProvisionError):
            extract_token("NOTION_PAT=with space\n")

    def test_private_files_and_no_secret_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "shared.env"
            source.write_text("OTHER=skip\nNOTION_PAT=synthetic-token\n")
            source.chmod(0o600)
            target = root / "private"
            credential = target / "credential.env"
            workspace = target / "workspace.json"
            with patch("provision_identity._workspace_from_token", return_value="11111111-2222-4333-8444-555555555555") as probe, patch("builtins.print") as printed:
                self.assertEqual(main([
                    "--shared-env", str(source),
                    "--credential-file", str(credential),
                    "--workspace-file", str(workspace),
                ]), 0)
            probe.assert_called_once_with("synthetic-token")
            self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
            self.assertEqual(workspace.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(workspace.read_text())["workspace_id"], "11111111-2222-4333-8444-555555555555")
            self.assertNotIn("synthetic-token", str(printed.call_args))
            with self.assertRaises(ProvisionError):
                main([
                    "--shared-env", str(source),
                    "--credential-file", str(credential),
                    "--workspace-file", str(workspace),
                ])


if __name__ == "__main__":
    unittest.main()
