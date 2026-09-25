import hashlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from mcp import Client
except ImportError:
    Client = None

from jev_skill_advisor.capability_core import load_manifest
from jev_skill_advisor.capability_observability import append_capability_event, summary
from jev_skill_advisor.mcp_server import build_server
from jev_skill_advisor.notion_mcp_transport import build_manifest_document, write_manifest
from jev_skill_advisor.profile import load_profile
from jev_skill_advisor.runtime import ServiceRuntime

PAGE_ID = "1234abcd-5678-4abc-8def-1234567890ab"
SECRET_TASK = "SECRET TASK eta-phrase"
SECRET_BODY = "SECRET PAGE BODY zeta-phrase"
SECRET_SCHEMA = "SECRET SCHEMA theta-phrase"
SECRET_AUTH = "Bearer SECRET-OAUTH delta-phrase"
FETCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"id": {"type": "string"}}, "required": ["id"],
}
FETCH_ID = "notion.mcp.fetch"


class _Bridge:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def list_tools(self, server):
        return {"tools": [{"name": "notion-fetch", "inputSchema": FETCH_SCHEMA, "server": server}]}

    def call_tool(self, server, operation, arguments):
        self.calls.append((server, operation, arguments))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _body(result):
    if result.is_error:
        raise AssertionError(result.content)
    body = result.structured_content or json.loads(result.content[0].text)
    if isinstance(body, dict) and "status" not in body and "result" in body:
        return body["result"]
    return body


@unittest.skipIf(Client is None, "optional mcp dependency not installed")
class CapabilityTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_facing_protocol_version_is_integer_one(self):
        async with Client(self._server(), raise_exceptions=True) as client:
            tools = (await client.list_tools()).tools
        for tool in tools:
            if tool.name not in {"capability_describe", "notion-fetch"}:
                continue
            field = tool.input_schema["properties"]["protocol_version"]
            self.assertEqual(field.get("const"), 1, tool.name)
            self.assertEqual(field.get("type"), "integer", tool.name)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        warehouse = root / "warehouse"
        for name, description in (("notion", "Work in Notion"), ("alpha", "alpha procedure")):
            skill = warehouse / name / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n", encoding="utf-8")
        entries = []
        for name, description in (("notion", "Work in Notion"), ("alpha", "alpha procedure")):
            body = (warehouse / name / "SKILL.md").read_bytes()
            entries.append({
                "stable_id": f"warehouse:{name}", "name": name, "description": description,
                "relative_path": name, "content_hash": hashlib.sha256(body).hexdigest(),
            })
        catalog = root / "catalog.json"
        catalog.write_text(json.dumps({"entries": entries}))
        self.config = root / "config.json"
        self.config.write_text(json.dumps({
            "config_version": 1, "profile_id": "mcp-telemetry", "harness": "mcp",
            "warehouse_root": str(warehouse), "catalog_path": str(catalog),
            "state_dir": str(root / "state"), "mode": "advisory", "provider_enabled": False,
            "read_enabled": True, "eligible_ids": ["warehouse:notion", "warehouse:alpha"],
            "read_allowlist": ["warehouse:notion", "warehouse:alpha"],
        }))
        ServiceRuntime(load_profile(self.config), initialize=True)
        self.manifest_path = root / "live.json"
        write_manifest(self.manifest_path, build_manifest_document(
            [{"name": "notion-fetch", "description": "Read one Notion page by id.", "inputSchema": FETCH_SCHEMA, "read_only": True}],
            source="examples/notion-mcp-live-manifest-2026-09-24.json",
        ))
        self.events = root / "events.jsonl"
        self.bridge = _Bridge({"text": SECRET_BODY, "auth": SECRET_AUTH, "schema": {"inputSchema": SECRET_SCHEMA}})
        self.manifest_hash = load_manifest(self.manifest_path).content_hash

    def _server(self, events=None, manifest=True):
        return build_server(
            self.config, host="mac", harness="hermes",
            capability_manifest=self.manifest_path if manifest else None,
            list_tools=self.bridge, notion_bridge=self.bridge,
            capability_events=self.events if events is None else events,
        )

    async def _suggest(self, server, task, skill, session):
        result = await server.call_tool("skill_suggest", {
            "protocol_version": 1, "request_id": session, "session_id": session,
            "task": task, "explicit_skills": [skill],
        })
        return _body(result)

    def _assert_private(self, text):
        for secret in (SECRET_TASK, SECRET_BODY, SECRET_SCHEMA, SECRET_AUTH, PAGE_ID, "inputSchema", "eta-phrase"):
            self.assertNotIn(secret, text)

    def test_bounded_fields_reject_unlisted_reason(self):
        path = Path(self.tmp.name) / "direct.jsonl"
        with self.assertRaises(ValueError):
            append_capability_event(
                path, harness="hermes", stage="invoke", outcome="failed", latency_ms=1,
                host="mac", capability_ids=[FETCH_ID], manifest_hash=self.manifest_hash,
                status="denied", reason=SECRET_BODY,
            )
        self.assertFalse(path.exists())
        self.assertTrue(append_capability_event(
            path, harness="hermes", stage="discovery", outcome="absent", latency_ms=1,
            host="mac", capability_ids=[], context_bytes=0, receipt_id="abc123",
            manifest_hash=self.manifest_hash, status="none", reason="not_notion_skill",
        ))
        report = summary([path])
        self.assertEqual(report["rejected_rows"], 0)
        self.assertEqual(report["counts"]["events"], 1)

    async def test_selected_then_fetch_success_correlates_without_content(self):
        async with Client(self._server(), raise_exceptions=True) as client:
            selected = await self._suggest(client, f"{SECRET_TASK} please notion-fetch", "notion", "s-selected")
            self.assertEqual([card["id"] for card in selected["capabilities"]], [FETCH_ID])
            self.assertEqual(set(selected["capabilities"][0]), {"id", "description"})
            fetched = await client.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "s-selected",
                "receipt_id": selected["receipt_id"], "page_id": PAGE_ID,
            })
            body = _body(fetched)
        self.assertEqual(body["status"], "called")
        self.assertEqual(body["result"]["text"], SECRET_BODY)
        rows = _events(self.events)
        self.assertEqual([row["stage"] for row in rows], ["discovery", "invoke"])
        choice, call = rows
        self.assertEqual(choice["status"], "selected")
        self.assertEqual(choice["outcome"], "selected")
        self.assertEqual(choice["capability_ids"], [FETCH_ID])
        self.assertEqual(choice["reason"], "explicit_tool_authoritative")
        self.assertEqual(choice["manifest_hash"], self.manifest_hash)
        self.assertEqual(choice["receipt_id"], selected["receipt_id"])
        self.assertNotIn("task", choice)
        self.assertEqual(call["status"], "success")
        self.assertEqual(call["outcome"], "success")
        self.assertEqual(call["reason"], "bridge_read")
        self.assertEqual(call["capability_ids"], [FETCH_ID])
        self.assertEqual(call["receipt_id"], choice["receipt_id"])
        self.assertEqual(call["manifest_hash"], self.manifest_hash)
        self.assertNotIn("result", call)
        self._assert_private(self.events.read_text(encoding="utf-8"))
        stored = ServiceRuntime(load_profile(self.config)).load_receipt(selected["receipt_id"])
        self.assertEqual(stored["capability_ids"], [FETCH_ID])
        self.assertNotIn(SECRET_TASK, json.dumps(stored["capability_selection"]))

    async def test_none_and_fail_open_each_emit_one_selection(self):
        async with Client(self._server(), raise_exceptions=True) as client:
            none = await self._suggest(client, "summarize the weekly notes", "alpha", "s-none")
            failed = await self._suggest(client, "organize the weekly notes", "notion", "s-fail")
        self.assertEqual(none["capabilities"], [])
        self.assertEqual(failed["capabilities"], [])
        rows = _events(self.events)
        self.assertEqual([row["status"] for row in rows], ["none", "fail_open"])
        self.assertEqual(rows[0]["reason"], "not_notion_skill")
        self.assertEqual(rows[1]["reason"], "capability_choice_provider_failure")
        self.assertEqual(rows[0]["capability_ids"], [])
        self.assertEqual(rows[1]["capability_ids"], [])
        self.assertEqual(rows[0]["outcome"], "absent")
        self.assertEqual(rows[0]["receipt_id"], none["receipt_id"])
        self.assertEqual(rows[1]["receipt_id"], failed["receipt_id"])
        self.assertNotEqual(rows[0]["receipt_id"], rows[1]["receipt_id"])
        self._assert_private(self.events.read_text(encoding="utf-8"))

    async def test_denied_fetch_emits_reason_without_page_id(self):
        async with Client(self._server(), raise_exceptions=True) as client:
            none = await self._suggest(client, "summarize the weekly notes", "alpha", "s-deny")
            denied = await client.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "s-deny",
                "receipt_id": none["receipt_id"], "page_id": PAGE_ID,
            })
            body = _body(denied)
        self.assertEqual(body, {"status": "denied", "reason": "unauthorized_capability"})
        self.assertEqual(self.bridge.calls, [])
        call = [row for row in _events(self.events) if row["stage"] == "invoke"]
        self.assertEqual(len(call), 1)
        self.assertEqual(call[0]["status"], "denied")
        self.assertEqual(call[0]["outcome"], "failed")
        self.assertEqual(call[0]["reason"], "auth")
        self.assertEqual(call[0]["capability_ids"], [FETCH_ID])
        self.assertEqual(call[0]["receipt_id"], none["receipt_id"])
        self._assert_private(self.events.read_text(encoding="utf-8"))

    async def test_no_manifest_emits_nothing(self):
        async with Client(self._server(manifest=False), raise_exceptions=True) as client:
            response = await self._suggest(client, f"{SECRET_TASK} please notion-fetch", "notion", "s-plain")
        self.assertNotIn("capabilities", response)
        self.assertFalse(self.events.exists())

    async def test_telemetry_failure_does_not_grant_or_return_body(self):
        blocked = Path(self.tmp.name)
        async with Client(self._server(events=blocked), raise_exceptions=True) as client:
            selected = await self._suggest(client, "please notion-fetch", "notion", "s-closed")
        self.assertEqual(selected["capabilities"], [])
        stored = ServiceRuntime(load_profile(self.config)).load_receipt(selected["receipt_id"])
        self.assertNotIn("capability_ids", stored)
        self.assertFalse(self.events.exists())
        async with Client(self._server(), raise_exceptions=True) as client:
            granted = await self._suggest(client, "please notion-fetch", "notion", "s-open")
        async with Client(self._server(events=blocked), raise_exceptions=True) as client:
            denied = await client.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "s-open",
                "receipt_id": granted["receipt_id"], "page_id": PAGE_ID,
            })
            body = _body(denied)
        self.assertEqual(body, {"status": "denied", "reason": "telemetry_failed"})
        self.assertNotIn(SECRET_BODY, json.dumps(body))
        self.assertEqual(len(self.bridge.calls), 1)


if __name__ == "__main__":
    unittest.main()
