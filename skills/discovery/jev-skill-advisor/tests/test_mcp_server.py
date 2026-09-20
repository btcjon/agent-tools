import hashlib, json, tempfile, unittest
from pathlib import Path

try:
    from mcp import Client
except ImportError:
    Client = None

from jev_skill_advisor.mcp_server import build_server
from jev_skill_advisor.profile import load_profile
from jev_skill_advisor.runtime import ServiceRuntime


@unittest.skipIf(Client is None, "optional mcp dependency not installed")
class McpServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addAsyncCleanup(self._cleanup)
        root = Path(self.tmp.name); warehouse = root / "warehouse"; skill = warehouse / "alpha" / "SKILL.md"
        skill.parent.mkdir(parents=True); skill.write_text("---\nname: alpha\ndescription: alpha procedure\n---\n# Alpha\n", encoding="utf-8")
        catalog = root / "catalog.json"; catalog.write_text(json.dumps({"entries": [{"stable_id": "warehouse:alpha", "name": "alpha",
            "description": "alpha procedure", "relative_path": "alpha", "content_hash": hashlib.sha256(skill.read_bytes()).hexdigest()}]}))
        self.config = root / "config.json"; self.config.write_text(json.dumps({"config_version": 1, "profile_id": "mcp-test", "harness": "mcp",
            "warehouse_root": str(warehouse), "catalog_path": str(catalog), "state_dir": str(root / "state"), "mode": "shadow",
            "provider_enabled": False, "read_enabled": True, "eligible_ids": ["warehouse:alpha"], "read_allowlist": ["warehouse:alpha"]}))
        ServiceRuntime(load_profile(self.config), initialize=True)

    async def _cleanup(self):
        self.tmp.cleanup()

    async def test_tools_and_explicit_suggest(self):
        async with Client(build_server(self.config), raise_exceptions=True) as client:
            tools = await client.list_tools()
            self.assertEqual({tool.name for tool in tools.tools}, {"skill_suggest", "skill_read", "skill_report_outcome"})
            payload = {"protocol_version": 1, "request_id": "m1", "session_id": "s1", "task": "x", "explicit_skills": ["alpha"]}
            result = await client.call_tool("skill_suggest", payload)
            self.assertFalse(result.is_error)
            response = result.structured_content or json.loads(result.content[0].text)
            response = response.get("result", response)
            self.assertEqual(response["status"], "explicit_selection")
            card = response["selected"][0]
            read = await client.call_tool("skill_read", {"protocol_version": 1, "session_id": "s1",
                "receipt_id": response["receipt_id"], "skill_id": card["id"], "expected_content_hash": card["content_hash"]})
            self.assertFalse(read.is_error)
            outcome = await client.call_tool("skill_report_outcome", {"protocol_version": 1, "session_id": "s1",
                "receipt_id": response["receipt_id"], "event_id": "e1", "skill_id": card["id"],
                "outcome": "applied", "evidence": "self_reported"})
            self.assertFalse(outcome.is_error)
            invalid = await client.call_tool("skill_suggest", {**payload, "unknown": 1})
            self.assertTrue(invalid.is_error)
            invalid_version = await client.call_tool("skill_suggest", {**payload, "protocol_version": True})
            self.assertTrue(invalid_version.is_error)


if __name__ == "__main__": unittest.main()
