import hashlib, json, tempfile, unittest
from pathlib import Path

try:
    from mcp import Client
except ImportError:
    Client = None

from jev_skill_advisor.capability_core import (
    CapabilityError, HarnessWriteVerifier, load_manifest, schema_digest,
)
from jev_skill_advisor.mcp_server import build_server, parse_args
from jev_skill_advisor.notion_mcp_transport import (
    CONNECT_TIMEOUT_S, FETCH_OPERATION, FETCH_TIMEOUT_S, SAMPLE_MANIFEST_NAME, SCHEMA_TIMEOUT_S,
    HermesNotionTransport, body_candidates, body_sha256, build_manifest_document,
    markdown_body, read_bridge_call, write_manifest, writes_flag,
)
from jev_skill_advisor.profile import load_profile
from jev_skill_advisor.runtime import ServiceRuntime

FETCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"id": {"type": "string"}}, "required": ["id"],
}
CANARY_PAGE_ID = "12345678-1234-4234-8234-123456789abc"
CANARY_BODY_SHA256 = hashlib.sha256(b"different synthetic body").hexdigest()
UPDATE_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"page_id": {"type": "string"}}}


def _tools():
    return [
        {"name": "notion-fetch", "description": "Read one Notion page by id or URL.", "inputSchema": FETCH_SCHEMA, "read_only": True},
        {"name": "notion-update-page", "description": "Update a Notion page body or properties.", "inputSchema": UPDATE_SCHEMA, "read_only": False},
        {"name": "notion-create-pages", "description": "Create pages. inputSchema {parent}", "inputSchema": {"type": "object"}, "read_only": True},
    ]


class ManifestTests(unittest.TestCase):
    def test_hermes_read_timeouts_are_bounded(self):
        self.assertLessEqual(CONNECT_TIMEOUT_S, 10)
        self.assertLessEqual(SCHEMA_TIMEOUT_S, 10)
        self.assertLessEqual(FETCH_TIMEOUT_S, 10)

    def test_compact_manifest_pins_hashes_and_write_flags(self):
        document = build_manifest_document(_tools(), source="examples/notion-mcp-live-manifest-2026-09-24.json")
        self.assertEqual(len(document["entries"]), 3)
        text = json.dumps(document)
        self.assertNotIn("inputSchema", text)
        self.assertNotIn(CANARY_PAGE_ID, text)
        for row in document["entries"]:
            for key in ("summary", "source", "provenance"):
                self.assertNotIn("{", row[key])
                self.assertNotIn("inputSchema", row[key])
        fetch = next(row for row in document["entries"] if row["operation"] == FETCH_OPERATION)
        update = next(row for row in document["entries"] if row["operation"] == "notion-update-page")
        create = next(row for row in document["entries"] if row["operation"] == "notion-create-pages")
        self.assertFalse(fetch["writes"])
        self.assertTrue(update["writes"])
        self.assertTrue(create["writes"])
        self.assertEqual(fetch["schema_hash"], schema_digest(FETCH_SCHEMA))
        self.assertEqual(writes_flag("notion-search", True), False)
        self.assertEqual(writes_flag("notion-search", None), True)
        wide = []
        for index in range(45):
            name = "notion-fetch" if index == 0 else f"notion-read-{index:02d}"
            wide.append({"name": name, "description": "x" * 400, "inputSchema": {"type": "object", "n": index}, "read_only": True})
        wide_document = build_manifest_document(wide, source="examples/notion-mcp-live-manifest-2026-09-24.json")
        self.assertEqual(len(wide_document["entries"]), 45)
        self.assertTrue(all(len(row["summary"].encode("utf-8")) <= 160 for row in wide_document["entries"]))

    def test_write_refuses_existing_and_sample_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = build_manifest_document(_tools(), source="examples/notion-mcp-live-manifest-2026-09-24.json")
            target = root / "notion-mcp-live-manifest-2026-09-24.json"
            write_manifest(target, document)
            loaded = load_manifest(target)
            self.assertEqual(len(loaded.entries), 3)
            self.assertNotIn("inputSchema", target.read_text(encoding="utf-8"))
            with self.assertRaises(FileExistsError):
                write_manifest(target, document)
            self.assertEqual(len(load_manifest(target).entries), 3)
            with self.assertRaises(FileExistsError):
                write_manifest(root / SAMPLE_MANIFEST_NAME, document)


class _FakeClient:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    def list_tools(self, server):
        return {"tools": [{"name": name, "inputSchema": schema, "server": server} for name, schema in self.tools.items()]}

    def call_tool(self, server, operation, arguments):
        self.calls.append((server, operation, arguments))
        return {"title": "example-page", "text": "<content>\n## Example section\nkept\n</content>"}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "manifest.json"
        write_manifest(self.path, build_manifest_document(_tools(), source="examples/notion-mcp-live-manifest-2026-09-24.json"))
        self.manifest = load_manifest(self.path)
        self.client = _FakeClient({
            "notion-fetch": FETCH_SCHEMA,
            "notion-update-page": UPDATE_SCHEMA,
            "notion-create-pages": {"type": "object"},
        })
        self.receipt = {"authorized_ids": ["notion.mcp.fetch"], "manifest_hash": self.manifest.content_hash}

    def test_forged_approval_does_not_call_a_write(self):
        verifier = HarnessWriteVerifier(b"0123456789abcdef")
        approval = verifier.issue(
            capability_id="notion.mcp.update-page", invocation_id="inv-update-1",
            arguments={"page_id": CANARY_PAGE_ID}, approver="forged",
        )
        with self.assertRaises(CapabilityError) as caught:
            read_bridge_call(
                self.manifest, self.receipt, CANARY_PAGE_ID, list_tools=self.client, call_tool=self.client,
                approval=approval, write_verifier=verifier, capability_id="notion.mcp.update-page",
            )
        self.assertEqual(caught.exception.code, "write_unapproved")
        self.assertEqual(self.client.calls, [])
        with self.assertRaises(CapabilityError) as forged_read:
            read_bridge_call(
                self.manifest, self.receipt, CANARY_PAGE_ID, list_tools=self.client, call_tool=self.client,
                approval={"kind": "forged"}, capability_id="notion.mcp.fetch",
            )
        self.assertEqual(forged_read.exception.code, "write_unapproved")
        self.assertEqual(self.client.calls, [])

    def test_fetch_requires_receipt_and_live_schema(self):
        called = read_bridge_call(
            self.manifest, self.receipt, CANARY_PAGE_ID, list_tools=self.client, call_tool=self.client,
        )
        self.assertEqual(called["status"], "called")
        self.assertEqual(called["operation"], FETCH_OPERATION)
        self.assertEqual(self.client.calls, [("notion", FETCH_OPERATION, {"id": CANARY_PAGE_ID})])
        digest, chars = body_sha256(called["result"])
        self.assertEqual(chars, len("## Example section\nkept"))
        self.assertNotEqual(digest, CANARY_BODY_SHA256)
        self.assertTrue(body_candidates(called["result"]))
        self.assertNotIn("kept", json.dumps(body_candidates(called["result"])))
        stale = _FakeClient({"notion-fetch": {"type": "object", "properties": {}}})
        with self.assertRaises(CapabilityError) as caught:
            read_bridge_call(self.manifest, self.receipt, CANARY_PAGE_ID, list_tools=stale, call_tool=stale)
        self.assertEqual(caught.exception.code, "stale_schema")
        self.assertEqual(stale.calls, [])
        denied = dict(self.receipt, authorized_ids=["notion.mcp.update-page"])
        with self.assertRaises(CapabilityError) as unauthorized:
            read_bridge_call(self.manifest, denied, CANARY_PAGE_ID, list_tools=self.client, call_tool=self.client)
        self.assertEqual(unauthorized.exception.code, "unauthorized_capability")

    def test_transport_rejects_writes_before_rpc(self):
        calls = []

        def connector():
            return _tools(), (lambda operation, arguments: calls.append((operation, arguments)) or {"ok": True}), (lambda: None)

        transport = HermesNotionTransport(connector=connector)
        with self.assertRaises(CapabilityError) as caught:
            transport.call_tool("notion", "notion-update-page", {"id": CANARY_PAGE_ID})
        self.assertEqual(caught.exception.code, "write_unapproved")
        self.assertEqual(calls, [])
        transport.call_tool("notion", FETCH_OPERATION, {"id": CANARY_PAGE_ID})
        self.assertEqual(calls, [(FETCH_OPERATION, {"id": CANARY_PAGE_ID})])

    def test_each_bridge_fetch_refreshes_live_schema_before_rpc(self):
        calls = []
        live = {"schema": FETCH_SCHEMA}

        def connector():
            def refresh():
                return [{
                    "name": FETCH_OPERATION, "description": "Read one Notion page.",
                    "inputSchema": live["schema"], "read_only": True,
                }]
            return _tools(), (lambda operation, arguments: calls.append((operation, arguments)) or {"ok": True}), (lambda: None), refresh

        transport = HermesNotionTransport(connector=connector)
        first = read_bridge_call(
            self.manifest, self.receipt, CANARY_PAGE_ID, list_tools=transport, call_tool=transport,
        )
        self.assertEqual(first["status"], "called")
        self.assertEqual(len(calls), 1)
        live["schema"] = {"type": "object", "properties": {"id": {"type": "integer"}}}
        with self.assertRaises(CapabilityError) as stale:
            read_bridge_call(
                self.manifest, self.receipt, CANARY_PAGE_ID, list_tools=transport, call_tool=transport,
            )
        self.assertEqual(stale.exception.code, "stale_schema")
        self.assertEqual(len(calls), 1)

    def test_markdown_body_uses_content_region(self):
        body = markdown_body({"text": "preamble <content>\n## Example section\nkept\n</content>\n"})
        self.assertEqual(body, "## Example section\nkept")
        self.assertEqual(hashlib.sha256(body.encode("utf-8")).hexdigest(), body_sha256({"text": "preamble <content>\n## Example section\nkept\n</content>\n"})[0])


class ParseTests(unittest.TestCase):
    def test_transport_flag_is_opt_in(self):
        args = parse_args(["--config", "profile.json"])
        self.assertIsNone(args.notion_transport)
        self.assertIsNone(args.capability_manifest)
        with self.assertRaises(SystemExit):
            parse_args(["--config", "profile.json", "--notion-transport", "hermes"])
        enabled = parse_args(["--config", "profile.json", "--notion-transport", "hermes", "--capability-manifest", "live.json"])
        self.assertEqual(enabled.notion_transport, "hermes")
        self.assertEqual(enabled.notion_server, "notion")


@unittest.skipIf(Client is None, "optional mcp dependency not installed")
class BridgeServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        warehouse = root / "warehouse"
        skill = warehouse / "notion" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: notion\ndescription: Work in Notion\n---\n# Notion\n", encoding="utf-8")
        catalog = root / "catalog.json"
        catalog.write_text(json.dumps({"entries": [{
            "stable_id": "warehouse:notion", "name": "notion", "description": "Work in Notion",
            "relative_path": "notion", "content_hash": hashlib.sha256(skill.read_bytes()).hexdigest(),
        }]}))
        self.config = root / "config.json"
        self.config.write_text(json.dumps({
            "config_version": 1, "profile_id": "mcp-capability", "harness": "mcp",
            "warehouse_root": str(warehouse), "catalog_path": str(catalog), "state_dir": str(root / "state"),
            "mode": "advisory", "provider_enabled": False, "read_enabled": True,
            "eligible_ids": ["warehouse:notion"], "read_allowlist": ["warehouse:notion"],
        }))
        ServiceRuntime(load_profile(self.config), initialize=True)
        self.manifest_path = root / "live.json"
        write_manifest(self.manifest_path, build_manifest_document(_tools(), source="examples/notion-mcp-live-manifest-2026-09-24.json"))
        self.client_tools = _FakeClient({
            "notion-fetch": FETCH_SCHEMA,
            "notion-update-page": UPDATE_SCHEMA,
            "notion-create-pages": {"type": "object"},
        })
        self.server = build_server(
            self.config, capability_manifest=self.manifest_path,
            list_tools=self.client_tools, notion_bridge=self.client_tools,
        )

    async def _body(self, result):
        self.assertFalse(result.is_error)
        body = result.structured_content or json.loads(result.content[0].text)
        if isinstance(body, dict) and "status" not in body and "result" in body:
            body = body["result"]
        return body

    async def test_model_tool_is_fetch_bound_to_receipt(self):
        async with Client(self.server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            self.assertIn("notion-fetch", names)
            self.assertIn("capability_describe", names)
            self.assertNotIn("capability_call", names)
            self.assertNotIn("notion-update-page", names)
            suggested = await client.call_tool("skill_suggest", {
                "protocol_version": 1, "request_id": "m1", "session_id": "s1",
                "task": "notion-fetch the operations page", "explicit_skills": ["notion"],
            })
            response = await self._body(suggested)
            self.assertEqual(response["status"], "explicit_selection")
            self.assertEqual(response["capabilities"][0]["id"], "notion.mcp.fetch")
            described = await client.call_tool("capability_describe", {
                "protocol_version": 1, "session_id": "s1", "receipt_id": response["receipt_id"],
                "capability_id": "notion.mcp.fetch",
            })
            described_body = await self._body(described)
            self.assertEqual(described_body["schema"], FETCH_SCHEMA)
            fetched = await client.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "s1", "receipt_id": response["receipt_id"],
                "page_id": CANARY_PAGE_ID,
            })
            fetched_body = await self._body(fetched)
            self.assertEqual(fetched_body["status"], "called")
            self.assertEqual(fetched_body["operation"], FETCH_OPERATION)
            self.assertEqual(self.client_tools.calls, [("notion", FETCH_OPERATION, {"id": CANARY_PAGE_ID})])
            rejected = await client.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "s1", "receipt_id": response["receipt_id"],
                "page_id": CANARY_PAGE_ID, "approval": {"kind": "forged"},
            })
            self.assertTrue(rejected.is_error)
            other = await client.call_tool("skill_suggest", {
                "protocol_version": 1, "request_id": "m2", "session_id": "s2",
                "task": "notion-update-page the operations page", "explicit_skills": ["notion"],
            })
            update = await self._body(other)
            denied = await client.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "s2", "receipt_id": update["receipt_id"],
                "page_id": CANARY_PAGE_ID,
            })
            denied_body = await self._body(denied)
            self.assertEqual(denied_body["reason"], "unauthorized_capability")
            self.assertEqual(len(self.client_tools.calls), 1)


if __name__ == "__main__":
    unittest.main()
