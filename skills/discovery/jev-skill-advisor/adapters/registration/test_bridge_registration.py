import json
import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bridge_registration import (
    BRIDGE_TOOLS,
    RegistrationError,
    disable_codex,
    disable_cursor,
    inspect_release,
    inspect_ntn,
    inspect_runtime_entry,
    assess,
    bridge_args,
    render_codex,
    render_cursor,
)


class RegistrationRenderingTests(unittest.TestCase):
    def test_runtime_entry_keeps_stable_symlink_path_and_rejects_missing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "python-real"
            target.write_text("#!/bin/sh\nexit 0\n")
            target.chmod(0o700)
            current = root / "current"
            current.symlink_to(target)
            self.assertEqual(inspect_runtime_entry(current, executable=True), str(current))
            with self.assertRaises(RegistrationError) as raised:
                inspect_runtime_entry(root / "missing", executable=True)
            self.assertEqual(raised.exception.code, "runtime_entry_invalid")
            with self.assertRaises(RegistrationError):
                inspect_runtime_entry(Path("relative"), executable=False)

    def test_assess_uses_explicit_host_local_runtime_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = root / "python"
            command.write_text("#!/bin/sh\n")
            command.chmod(0o700)
            launcher = root / "run_bridge.py"
            launcher.write_text("# launcher\n")
            with patch("bridge_registration.inspect_release", return_value={
                "profile_harness": "codex", "release_prefix": "abc", "release_id": "a" * 64,
            }), patch("bridge_registration.inspect_executable", return_value={
                "portable_transport": "cli", "executable": str(root / "skill-advisor-mcp"),
            }), patch("bridge_registration.inspect_ntn", return_value=str(root / "ntn")), \
                patch("run_bridge.load_identity", return_value=("test-token", "workspace")), \
                patch("bridge_registration.inspect_workspace", return_value="match"):
                report = assess(
                    "codex", release_root=root, config_path=None, executable=root / "skill-advisor-mcp",
                    host="mac", actual_host="mac", events_path=root / "events", route_log=root / "routes",
                    credential_file=root / "credential", workspace_file=root / "workspace",
                    python_path=command, launcher_path=launcher,
                )
            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["command"], str(command))
            self.assertEqual(report["args"][:3], ["-I", "-s", str(launcher)])

    def test_bridge_command_pins_release_and_absolute_ntn(self):
        args = bridge_args(Path("/tmp/release-root"), "a" * 64, "mac", "codex",
                           Path("/tmp/events"), Path("/tmp/routes"), Path("/opt/ntn"), "0.23.2")
        self.assertEqual(args[args.index("--expected-release") + 1], "a" * 64)
        self.assertEqual(args[args.index("--notion-cli-path") + 1], "/opt/ntn")
        self.assertEqual(args[args.index("--notion-cli-version") + 1], "0.23.2")

    def test_ntn_version_is_pinned(self):
        with patch("bridge_registration.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "ntn 0.23.2\n"
            self.assertEqual(inspect_ntn(Path(sys.executable)), sys.executable)
            run.return_value.stdout = "ntn 0.24.0\n"
            with self.assertRaises(RegistrationError) as raised:
                inspect_ntn(Path(sys.executable))
            self.assertEqual(raised.exception.code, "ntn_version_mismatch")

    def test_codex_preserves_unrelated_server_and_disables_only_ours(self):
        original = '[mcp_servers.other]\ncommand = "other"\nargs = ["--safe"]\n'
        changed, did_change = render_codex(original, "/bin/echo", ["--release-root", "/tmp/pinned"])
        self.assertTrue(did_change)
        self.assertIn("[mcp_servers.other]", changed)
        self.assertIn("[mcp_servers.jev-capability-bridge]", changed)
        self.assertIn('enabled_tools = ["skill_suggest",', changed)
        repeated, repeated_change = render_codex(changed, "/bin/echo", ["--release-root", "/tmp/pinned"])
        self.assertFalse(repeated_change)
        self.assertEqual(repeated, changed)
        restored, disabled = disable_codex(changed)
        self.assertTrue(disabled)
        self.assertEqual(restored.strip(), original.strip())

    def test_cursor_preserves_unrelated_server_and_disables_only_ours(self):
        original = json.dumps({"mcpServers": {"other": {"command": "/bin/true"}}, "note": "keep"})
        changed, did_change = render_cursor(original, "/bin/echo", ["--release-root", "/tmp/pinned"])
        self.assertTrue(did_change)
        parsed = json.loads(changed)
        self.assertEqual(parsed["mcpServers"]["other"], {"command": "/bin/true"})
        self.assertEqual(parsed["note"], "keep")
        self.assertEqual(set(BRIDGE_TOOLS), {
            "skill_suggest", "skill_read", "skill_report_outcome", "capability_describe", "notion-fetch",
        })
        repeated, repeated_change = render_cursor(changed, "/bin/echo", ["--release-root", "/tmp/pinned"])
        self.assertFalse(repeated_change)
        self.assertEqual(repeated, changed)
        restored, disabled = disable_cursor(changed)
        self.assertTrue(disabled)
        self.assertEqual(json.loads(restored), json.loads(original))

    def test_rejects_existing_unmanaged_entry_and_malformed_config(self):
        with self.assertRaises(RegistrationError):
            render_codex('[mcp_servers.jev-capability-bridge]\ncommand = "someone-else"\n', "/bin/echo", [])
        with self.assertRaises(RegistrationError):
            render_cursor('{"mcpServers": []}', "/bin/echo", [])

    def test_missing_manifest_refuses_registration(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "releases").mkdir()
            (root / "current-release.json").write_text(json.dumps({"schema_version": 1, "release_id": "a" * 64}))
            manifest = {"schema_version": 2, "activation": "delivery", "profiles": ["codex"], "files": {}}
            from bridge_registration import canonical_bytes
            import hashlib
            identity = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
            (root / "current-release.json").write_text(json.dumps({"schema_version": 1, "release_id": identity}))
            path = root / "releases" / identity
            path.mkdir()
            (path / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(RegistrationError) as error:
                inspect_release(root, "codex", identity)
            self.assertIn(error.exception.code, {"release_profile_missing", "capability_manifest_missing"})

    def test_mcp_manifest_cannot_register_as_cli_route(self):
        from jev_skill_advisor.notion_mcp_transport import build_manifest_document, write_manifest
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "release-inputs" / "fixture"
            inputs.mkdir(parents=True)
            profile = inputs / "profile.json"
            profile.write_text(json.dumps({"harness": "codex"}))
            capability = inputs / "capability.json"
            write_manifest(capability, build_manifest_document([{
                "name": "notion-fetch", "description": "Read one page", "inputSchema": {"type": "object"},
                "read_only": True,
            }], source="test"))
            files = {"profile:codex": {"path": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest()},
                     "capability_manifest": {"path": str(capability), "sha256": hashlib.sha256(capability.read_bytes()).hexdigest()}}
            from jev_skill_advisor.capability_core import load_manifest
            files["capability_manifest"]["content_hash"] = load_manifest(capability).content_hash
            manifest = {"schema_version": 2, "activation": "delivery", "profiles": ["codex"], "files": files}
            from bridge_registration import canonical_bytes
            identity = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
            target = root / "releases" / identity
            target.mkdir(parents=True)
            (target / "manifest.json").write_text(json.dumps(manifest))
            (root / "current-release.json").write_text(json.dumps({"schema_version": 1, "release_id": identity}))
            with self.assertRaises(RegistrationError) as raised:
                inspect_release(root, "codex", identity)
            self.assertEqual(raised.exception.code, "wrong_capability_route")


if __name__ == "__main__":
    unittest.main()
