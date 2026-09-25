import hashlib, json, tempfile, unittest
from pathlib import Path

try:
    from mcp import Client
except ImportError:
    Client = None

from jev_skill_advisor.capability_core import (
    CALL_ARGUMENT_MAX_BYTES, CAPABILITY_CALL_CONTRACT, DESCRIBE_SCHEMA_MAX_BYTES,
    SELECTION_MAX_CAPABILITIES, STARTUP_CAPABILITY_BUDGET_BYTES, WRITE_APPROVAL_KIND,
    CapabilityError, HarnessWriteVerifier, capability_call, capability_describe, load_manifest,
    schema_digest, selection_cards, startup_disclosure,
)
from jev_skill_advisor.capability_choice import MODEL
from jev_skill_advisor.exposure import Capability, ExposureAdapter, Registry, digest
from jev_skill_advisor.mcp_server import build_server
from jev_skill_advisor.profile import load_profile
from jev_skill_advisor.runtime import ServiceRuntime

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "examples" / "notion-mcp-capability-manifest.json"
SAMPLE_SCHEMAS = {
    "notion.mcp.fetch": {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}}, "required": ["id"]},
    "notion.mcp.search": {
        "type": "object", "additionalProperties": False,
        "properties": {"query": {"type": "string"}, "query_type": {"type": "string", "enum": ["internal"]}},
        "required": ["query"],
    },
    "notion.mcp.query-data-sources": {
        "type": "object", "additionalProperties": False,
        "properties": {"data_source_id": {"type": "string"}}, "required": ["data_source_id"],
    },
    "notion.mcp.get-comments": {
        "type": "object", "additionalProperties": False,
        "properties": {"page_id": {"type": "string"}}, "required": ["page_id"],
    },
    "notion.mcp.create-pages": {
        "type": "object", "additionalProperties": False,
        "properties": {"parent": {"type": "object"}, "pages": {"type": "array"}},
        "required": ["parent", "pages"],
    },
    "notion.mcp.update-page": {
        "type": "object", "additionalProperties": False,
        "properties": {"page_id": {"type": "string"}, "command": {"type": "string"}},
        "required": ["page_id", "command"],
    },
}


def _receipt(manifest, *ids):
    return {"authorized_ids": list(ids), "manifest_hash": manifest.content_hash}


class FakeCatalog:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    def list_tools(self, server):
        self.calls.append(server)
        return [{"name": name, "inputSchema": schema} for name, schema in self.tools.items()]


class CapabilityCoreTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(MANIFEST_PATH)

    def test_startup_and_selection_omit_schemas(self):
        text = MANIFEST_PATH.read_text(encoding="utf-8")
        self.assertNotIn("inputSchema", text)
        self.assertNotIn('"properties"', text)
        self.assertNotIn('"parameters"', text)
        self.assertNotIn('"inputSchema"', text)
        for entry in self.manifest.entries.values():
            self.assertEqual(entry.schema_hash, schema_digest(SAMPLE_SCHEMAS[entry.id]))
        startup = startup_disclosure(self.manifest, "")
        blob = json.dumps(startup)
        self.assertNotIn("inputSchema", blob)
        self.assertNotIn("schema_hash", blob)
        self.assertNotIn("query_type", blob)
        self.assertNotIn("data_source_id", blob)
        self.assertLessEqual(len(startup["capabilities"]), SELECTION_MAX_CAPABILITIES)
        self.assertLessEqual(startup["budget"]["bytes"], STARTUP_CAPABILITY_BUDGET_BYTES)
        self.assertEqual(startup["budget"]["max_bytes"], STARTUP_CAPABILITY_BUDGET_BYTES)
        ids = [card["id"] for card in startup["capabilities"]]
        self.assertIn("notion.mcp.search", ids)
        self.assertNotIn("notion.mcp.create-pages", ids)
        self.assertNotIn("notion.mcp.update-page", ids)
        self.assertTrue(all(set(card) == {"id", "description"} for card in startup["capabilities"]))
        selected = startup_disclosure(self.manifest, "update a page")
        selected_ids = [card["id"] for card in selected["capabilities"]]
        self.assertIn("notion.mcp.update-page", selected_ids)
        self.assertLessEqual(len(selected_ids), SELECTION_MAX_CAPABILITIES)
        self.assertNotIn("inputSchema", json.dumps(selected))

    def test_prepare_does_not_disclose_tool_schema(self):
        schema = {"type": "function", "function": {
            "name": "notion-search",
            "parameters": {"type": "object", "properties": {"marker": {"const": "SCHEMA_MARKER_9f3a"}}, "required": ["marker"]},
        }}
        entry = Capability(
            "notion.mcp.search", "mcp_tool", "Search Notion.", schema=schema, schema_hash=digest(schema),
            server="notion", disclose=True,
        )
        prepared = ExposureAdapter(Registry([entry]), enabled=False).prepare(
            {"messages": [], "tools": []}, "search notion", explicit=("notion.mcp.search",))
        blob = json.dumps(prepared)
        self.assertNotIn("SCHEMA_MARKER_9f3a", blob)
        self.assertNotIn("inputSchema", blob)
        self.assertIn("notion.mcp.search", blob)
        self.assertIn("Search Notion.", blob)
        names = [tool.get("function", {}).get("name") for tool in prepared["request"]["tools"]]
        self.assertIn("capability_describe", names)
        self.assertNotIn("notion-search", names)
        self.assertNotIn("capability_call", names)
        self.assertNotIn("schema", prepared["dispatch"]["notion.mcp.search"])

    def test_describe_returns_exact_live_schema_for_authorized_id(self):
        schema = SAMPLE_SCHEMAS["notion.mcp.search"]
        catalog = FakeCatalog({"notion-search": schema})
        described = capability_describe(
            self.manifest, _receipt(self.manifest, "notion.mcp.search"), "notion.mcp.search", catalog)
        self.assertIs(described["schema"], schema)
        self.assertEqual(described["operation"], "notion-search")
        self.assertLessEqual(described["budget"]["bytes"], DESCRIBE_SCHEMA_MAX_BYTES)
        self.assertEqual(catalog.calls, ["notion"])

    def test_describe_rejects_unauthorized_selected_peer(self):
        catalog = FakeCatalog({"notion-search": SAMPLE_SCHEMAS["notion.mcp.search"]})
        with self.assertRaises(CapabilityError) as caught:
            capability_describe(self.manifest, _receipt(self.manifest, "notion.mcp.search"), "notion.mcp.fetch", catalog)
        self.assertEqual(caught.exception.code, "unauthorized_capability")
        self.assertFalse(hasattr(caught.exception, "schema"))
        self.assertEqual(catalog.calls, [])

    def test_describe_rejects_stale_digest_without_returning_schema(self):
        stale = json.loads(json.dumps(SAMPLE_SCHEMAS["notion.mcp.search"]))
        stale["properties"]["query_type"]["const"] = "STALE_SCHEMA_MARKER"
        catalog = FakeCatalog({"notion-search": stale})
        with self.assertRaises(CapabilityError) as caught:
            capability_describe(self.manifest, _receipt(self.manifest, "notion.mcp.search"), "notion.mcp.search", catalog)
        self.assertEqual(caught.exception.code, "stale_schema")
        self.assertNotIn("STALE_SCHEMA_MARKER", str(caught.exception))

    def test_describe_rejects_unknown_id_without_listing_tools(self):
        catalog = FakeCatalog({"notion-search": SAMPLE_SCHEMAS["notion.mcp.search"]})
        with self.assertRaises(CapabilityError) as caught:
            capability_describe(self.manifest, _receipt(self.manifest, "notion.mcp.search"), "notion.mcp.missing", catalog)
        self.assertEqual(caught.exception.code, "unknown_capability")
        self.assertEqual(catalog.calls, [])

    def test_manifest_rejects_embedded_schema(self):
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        raw["entries"][0]["inputSchema"] = {"type": "object"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(CapabilityError) as caught:
                load_manifest(path)
        self.assertEqual(caught.exception.code, "invalid_manifest")

    def test_unapproved_and_forged_writes_are_rejected_before_transport(self):
        schema = SAMPLE_SCHEMAS["notion.mcp.update-page"]
        arguments = {"page_id": "scratch", "command": "replace_content"}
        calls = []

        def call_tool(server, operation, payload):
            calls.append((server, operation, payload))
            return {"object": "page", "id": "scratch"}

        catalog = FakeCatalog({"notion-update-page": schema})
        receipt = _receipt(self.manifest, "notion.mcp.update-page")
        verifier = HarnessWriteVerifier(b"test-harness-secret")
        unsigned = {
            "kind": WRITE_APPROVAL_KIND, "capability_id": "notion.mcp.update-page",
            "invocation_id": "inv-update-1", "payload_hash": schema_digest(arguments), "approver": "harness-user",
        }
        issued = verifier.issue(
            capability_id="notion.mcp.update-page", invocation_id="inv-update-1",
            arguments=arguments, approver="harness-user",
        )
        forged = dict(issued)
        forged["signature"] = "0" * 64
        other = HarnessWriteVerifier(b"other-harness-secret")
        rejected = (
            (None, None), (True, None), (False, None), ({"approved": True}, None),
            (unsigned, None), (issued, None), (forged, verifier), (issued, other),
            (None, verifier), (unsigned, verifier),
        )
        for approval, checker in rejected:
            with self.assertRaises(CapabilityError) as caught:
                capability_call(
                    self.manifest, receipt, "notion.mcp.update-page", arguments,
                    invocation_id="inv-update-1", list_tools=catalog, call_tool=call_tool,
                    approval=approval, write_verifier=checker,
                )
            self.assertEqual(caught.exception.code, "write_unapproved")
        self.assertEqual(calls, [])
        self.assertEqual(catalog.calls, [])
        self.assertEqual(issued["kind"], CAPABILITY_CALL_CONTRACT["approval_artifact"]["kind"])
        self.assertIn("signature", CAPABILITY_CALL_CONTRACT["approval_artifact"])
        result = capability_call(
            self.manifest, receipt, "notion.mcp.update-page", arguments,
            invocation_id="inv-update-1", list_tools=catalog, call_tool=call_tool,
            approval=issued, write_verifier=verifier,
        )
        self.assertEqual(result["result"], {"object": "page", "id": "scratch"})
        self.assertEqual(calls, [("notion", "notion-update-page", arguments)])
        self.assertLessEqual(_json_size(arguments), CALL_ARGUMENT_MAX_BYTES)

    def test_stale_write_does_not_call_transport(self):
        stale = json.loads(json.dumps(SAMPLE_SCHEMAS["notion.mcp.update-page"]))
        stale["properties"]["command"]["const"] = "STALE_WRITE"
        arguments = {"page_id": "scratch", "command": "replace_content"}
        verifier = HarnessWriteVerifier(b"test-harness-secret")
        approval = verifier.issue(
            capability_id="notion.mcp.update-page", invocation_id="inv-update-2",
            arguments=arguments, approver="harness-user",
        )
        calls = []
        with self.assertRaises(CapabilityError) as caught:
            capability_call(
                self.manifest, _receipt(self.manifest, "notion.mcp.update-page"), "notion.mcp.update-page",
                arguments, invocation_id="inv-update-2", list_tools=FakeCatalog({"notion-update-page": stale}),
                call_tool=lambda *args: calls.append(args), approval=approval, write_verifier=verifier,
            )
        self.assertEqual(caught.exception.code, "stale_schema")
        self.assertEqual(calls, [])

    def test_read_call_returns_exact_result_and_rejects_boolean(self):
        result_body = {"results": [{"id": "page-1"}]}
        calls = []

        def call_tool(server, operation, payload):
            calls.append(payload)
            return result_body

        receipt = _receipt(self.manifest, "notion.mcp.search")
        called = capability_call(
            self.manifest, receipt, "notion.mcp.search", {"query": "pins"},
            invocation_id="inv-search-1", list_tools=FakeCatalog({"notion-search": SAMPLE_SCHEMAS["notion.mcp.search"]}),
            call_tool=call_tool, approval=None,
        )
        self.assertIs(called["result"], result_body)
        with self.assertRaises(CapabilityError) as caught:
            capability_call(
                self.manifest, receipt, "notion.mcp.search", {"query": "pins"},
                invocation_id="inv-search-1", list_tools=FakeCatalog({"notion-search": SAMPLE_SCHEMAS["notion.mcp.search"]}),
                call_tool=call_tool, approval=True,
            )
        self.assertEqual(caught.exception.code, "invalid_approval")
        self.assertEqual(calls, [{"query": "pins"}])


def _json_size(value):
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _jev_response(payload, decisions, confidences=None, probabilities=None):
    answers = {}
    for index, card in enumerate(payload["state"]["candidates"]):
        choice = decisions.get(card["id"], "skip")
        if probabilities and card["id"] in probabilities:
            noul = float(probabilities[card["id"]]["use"])
        elif confidences and card["id"] in confidences:
            noul = float(confidences[card["id"]])
        elif choice == "use":
            noul = 0.9
        else:
            noul = 0.1
        answers[f"fit_{index}"] = {"type": "noul", "noul": noul}
    answers["write_intent"] = {"type": "noul", "noul": 0.9}
    return {"model": MODEL, "answers": answers, "usage": {"input_tokens": 12}}


@unittest.skipIf(Client is None, "optional mcp dependency not installed")
class CapabilityMcpServerTests(unittest.IsolatedAsyncioTestCase):
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
        self.payloads = []
        self.catalog = FakeCatalog({
            entry.operation: SAMPLE_SCHEMAS[entry.id] for entry in load_manifest(MANIFEST_PATH).entries.values()
        })

        def evaluator(payload, timeout):
            self.payloads.append((payload, timeout))
            return _jev_response(payload, {"notion.mcp.create-pages": "use"})

        self.server = build_server(
            self.config, capability_manifest=MANIFEST_PATH, list_tools=self.catalog,
            capability_evaluator=evaluator,
        )

    async def test_server_hides_schemas_until_authorized_describe(self):
        task = "file the weekly status where the team can find it"
        async with Client(self.server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            self.assertEqual(names, {"skill_suggest", "skill_read", "skill_report_outcome", "capability_describe"})
            listed = json.dumps([tool.model_dump() if hasattr(tool, "model_dump") else tool.__dict__ for tool in tools.tools], default=str)
            self.assertNotIn("query_type", listed)
            self.assertNotIn("data_source_id", listed)
            self.assertNotIn("notion-create-pages", listed)
            suggested = await client.call_tool("skill_suggest", {
                "protocol_version": 1, "request_id": "m1", "session_id": "s1", "task": task,
                "explicit_skills": ["notion"],
            })
            self.assertFalse(suggested.is_error)
            response = suggested.structured_content or json.loads(suggested.content[0].text)
            response = response.get("result", response)
            self.assertEqual(response["status"], "explicit_selection")
            self.assertEqual(len(response["selected"]), 1)
            self.assertEqual(response["selected"][0]["id"], "warehouse:notion")
            ids = [card["id"] for card in response["capabilities"]]
            lexical = [card["id"] for card in selection_cards(load_manifest(MANIFEST_PATH), task)]
            self.assertNotIn("notion.mcp.create-pages", lexical)
            self.assertEqual(ids, ["notion.mcp.create-pages"])
            self.assertTrue(all(set(card) == {"id", "description"} for card in response["capabilities"]))
            blob = json.dumps(response)
            self.assertNotIn("inputSchema", blob)
            self.assertNotIn("schema_hash", blob)
            self.assertNotIn("additionalProperties", blob)
            state = json.dumps(self.payloads[0][0]["state"])
            self.assertNotIn("inputSchema", state)
            self.assertNotIn("schema_hash", state)
            self.assertEqual(self.payloads[0][0]["state"]["manifest_hash"], load_manifest(MANIFEST_PATH).content_hash)
            stored = ServiceRuntime(load_profile(self.config)).load_receipt(response["receipt_id"])
            self.assertEqual(stored["capability_ids"], ["notion.mcp.create-pages"])
            self.assertEqual(stored["capability_manifest_hash"], stored["capability_selection"]["manifest_hash"])
            self.assertEqual(stored["capability_selection"]["ids"], stored["capability_ids"])
            self.assertEqual(stored["capability_selection"]["budgets"]["p95_ms"], 2000)
            self.assertLessEqual(
                stored["capability_selection"]["budgets"]["state_bytes"],
                stored["capability_selection"]["budgets"]["state_max_bytes"],
            )
            peer = await client.call_tool("capability_describe", {
                "protocol_version": 1, "session_id": "s1", "receipt_id": response["receipt_id"],
                "capability_id": "notion.mcp.search",
            })
            peer_body = peer.structured_content or json.loads(peer.content[0].text)
            peer_body = peer_body.get("result", peer_body)
            self.assertEqual(peer_body["reason"], "unauthorized_capability")
            self.assertNotIn("schema", peer_body)
            self.assertEqual(self.catalog.calls, [])
            described = await client.call_tool("capability_describe", {
                "protocol_version": 1, "session_id": "s1", "receipt_id": response["receipt_id"],
                "capability_id": "notion.mcp.create-pages",
            })
            self.assertFalse(described.is_error)
            body = described.structured_content or json.loads(described.content[0].text)
            body = body.get("result", body)
            self.assertEqual(body["schema"], SAMPLE_SCHEMAS["notion.mcp.create-pages"])
            unknown = await client.call_tool("capability_describe", {
                "protocol_version": 1, "session_id": "s1", "receipt_id": response["receipt_id"],
                "capability_id": "notion.mcp.missing",
            })
            unknown_body = unknown.structured_content or json.loads(unknown.content[0].text)
            unknown_body = unknown_body.get("result", unknown_body)
            self.assertEqual(unknown_body["reason"], "unknown_capability")
            self.assertNotIn("schema", unknown_body)
            self.assertNotIn("capability_call", names)

    async def test_provider_failure_keeps_the_skill_and_authorizes_nothing(self):
        def explode(payload, timeout):
            raise TimeoutError("typesafe_timeout")

        server = build_server(
            self.config, capability_manifest=MANIFEST_PATH, list_tools=self.catalog,
            capability_evaluator=explode,
        )
        async with Client(server, raise_exceptions=True) as client:
            suggested = await client.call_tool("skill_suggest", {
                "protocol_version": 1, "request_id": "m2", "session_id": "s2",
                "task": "file the weekly status where the team can find it",
                "explicit_skills": ["notion"],
            })
            self.assertFalse(suggested.is_error)
            response = suggested.structured_content or json.loads(suggested.content[0].text)
            response = response.get("result", response)
            self.assertEqual(response["status"], "explicit_selection")
            self.assertEqual(response["selected"][0]["id"], "warehouse:notion")
            self.assertEqual(response["capabilities"], [])
            blob = json.dumps(response)
            self.assertNotIn("inputSchema", blob)
            stored = ServiceRuntime(load_profile(self.config)).load_receipt(response["receipt_id"])
            self.assertEqual(stored["capability_ids"], [])
            self.assertEqual(stored["capability_selection"]["reason"], "capability_choice_provider_failure")
            self.assertEqual(stored["capability_manifest_hash"], load_manifest(MANIFEST_PATH).content_hash)


if __name__ == "__main__":
    unittest.main()
