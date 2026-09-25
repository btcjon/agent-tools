"""Fake-transport checks for the per-host read-only Notion route."""
import json
import os
import subprocess
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from mcp import Client
except ImportError:
    Client = None

from jev_skill_advisor.capability_choice import resolve_capabilities
from jev_skill_advisor.capability_core import CapabilityError, load_manifest
from jev_skill_advisor.mcp_server import build_server, parse_args
from jev_skill_advisor.notion_host_transport import (
    CLI_CAPABILITY_ID,
    CLI_SERVER,
    CLI_FETCH_SCHEMA,
    CliNotionTransport,
    DeniedTransport,
    RouteDecision,
    append_route_record,
    default_runner,
    cli_capability_card,
    host_cli_manifest_document,
    markdown_argv,
    parse_page_markdown,
    prepare_notion_runtime,
    read_cli_page,
    resolve_cli_identity,
    resolve_host_route,
    validate_read_argv,
    verify_ntn_executable,
)
from jev_skill_advisor.notion_mcp_transport import write_manifest
from jev_skill_advisor.profile import load_profile
from jev_skill_advisor.runtime import ServiceRuntime

PAGE_ID = "1234abcd-5678-4abc-8def-1234567890ab"
PINNED_WORKSPACE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_WORKSPACE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SECRET = "SECRET_PAGE_TEXT"
_ABSENT = {"available": False, "authenticated": False, "identity_ok": False, "operation_ok": False}
_READY = {"available": True, "authenticated": True, "identity_ok": True, "operation_ok": True}


def _whoami(workspace):
    return {"object": "user", "type": "bot", "id": PAGE_ID, "bot": {"workspace_id": workspace}}


def _page(page_id=PAGE_ID, **extra):
    body = {
        "object": "page_markdown",
        "id": page_id,
        "markdown": "synthetic-markdown",
        "truncated": False,
        "unknown_block_ids": [],
    }
    body.update(extra)
    return body


class _Result:
    def __init__(self, payload, returncode=0):
        self.returncode = returncode
        self.stdout = json.dumps(payload).encode("utf-8")
        self.stderr = b""


class Runner:
    def __init__(self, workspace=PINNED_WORKSPACE_ID, page=None, status=0):
        self.argv = []
        self.workspace = workspace
        self.page = _page() if page is None else page
        self.status = status

    def __call__(self, argv):
        self.argv.append(list(argv))
        if list(argv) == ["ntn", "whoami", "--json"]:
            return _Result(_whoami(self.workspace))
        if self.status:
            return _Result({"status": self.status, "message": SECRET}, self.status)
        return _Result(self.page)


def _probes(**overrides):
    probes = {"mcp": dict(_ABSENT), "cli": dict(_ABSENT)}
    for route, probe in overrides.items():
        probes[route] = probe
    return probes


def _manifest(tmp, schema):
    from jev_skill_advisor.capability_core import schema_digest
    card = cli_capability_card()
    card["schema_hash"] = schema_digest(schema)
    document = {"manifest_version": 1, "description": "Synthetic CLI page-read test", "entries": [card]}
    path = Path(tmp) / "manifest.json"
    write_manifest(path, document)
    return load_manifest(path)


def _receipt(manifest, ids):
    return {
        "authorized_ids": ids,
        "manifest_hash": manifest.content_hash,
        "expires_at": "2099-01-01T00:00:00Z",
    }


def _transport(runner):
    return CliNotionTransport(
        runner=runner, expected_workspace_id=PINNED_WORKSPACE_ID,
        credential_source="env", environ={"NOTION_API_TOKEN": "synthetic-test-value"},
    )


class RouteSelectionTests(unittest.TestCase):
    def test_ready_preferred_route_does_not_use_the_other(self):
        decision = resolve_host_route(
            preferred="mcp",
            probes=_probes(mcp=dict(_READY), cli=dict(_READY)),
            fallback_route="cli",
            fallback_reason="harness_missing",
        )
        self.assertEqual(decision.record(), {"route": "mcp", "fallback_reason": None, "executable": True})

    def test_explicit_fallback_is_recorded_and_a_mismatched_reason_is_not_used(self):
        used = resolve_host_route(
            preferred="mcp",
            probes=_probes(cli=dict(_READY)),
            fallback_route="cli",
            fallback_reason="harness_missing",
        )
        self.assertEqual(used.route, "cli")
        self.assertEqual(used.fallback_reason, "harness_missing")
        self.assertTrue(used.executable)
        blocked = resolve_host_route(
            preferred="cli",
            probes=_probes(mcp=dict(_READY), cli={**_READY, "identity_ok": False}),
            fallback_route="mcp",
            fallback_reason="harness_missing",
        )
        self.assertEqual(blocked.route, "cli")
        self.assertFalse(blocked.executable)
        self.assertEqual(blocked.fallback_reason, "identity_mismatch")

    def test_prepare_does_not_open_cli_when_mcp_is_ready(self):
        runner = Runner()
        args = Namespace(
            notion_transport="hermes", notion_server="notion",
            notion_fallback="cli", notion_fallback_reason="harness_missing",
        )
        bridge, decision = prepare_notion_runtime(
            args, runner=runner, mcp_factory=lambda: "hermes-bridge", mcp_installed=True,
        )
        self.assertEqual(bridge, "hermes-bridge")
        self.assertEqual(decision.route, "mcp")
        self.assertIsNone(decision.fallback_reason)
        self.assertEqual(runner.argv, [])

    def test_route_log_is_content_free(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "routes.jsonl"
            self.assertTrue(append_route_record(
                path, host="mac", harness="codex", route="cli",
                fallback_reason="harness_missing", executable=True,
            ))
            text = path.read_text(encoding="utf-8")
            row = json.loads(text)
            self.assertEqual(row["route"], "cli")
            self.assertEqual(row["fallback_reason"], "harness_missing")
            self.assertNotIn(PAGE_ID, text)
            self.assertNotIn(SECRET, text)
            self.assertNotIn("synthetic-markdown", text)


class CliTransportTests(unittest.TestCase):
    def test_real_os_environ_mapping_is_accepted(self):
        values = {"NOTION_API_TOKEN": "synthetic-test-value", "NOTION_CREDENTIAL_SOURCE": "env",
                  "NOTION_EXPECTED_WORKSPACE_ID": PINNED_WORKSPACE_ID}
        with patch.dict(os.environ, values, clear=True):
            source, workspace = resolve_cli_identity()
            self.assertEqual((source, workspace), ("env", PINNED_WORKSPACE_ID))
            transport = CliNotionTransport(runner=Runner(), expected_workspace_id=workspace,
                                            credential_source=source)
            self.assertTrue(transport.probe()["identity_ok"])

    def test_host_manifest_offers_only_executable_cli_card(self):
        base = {"entries": [{"id": "notion.mcp.fetch", "server": "notion", "operation": "notion-fetch"},
                            {"id": "notion.mcp.update_page", "server": "notion", "operation": "notion-update-page"}]}
        result = host_cli_manifest_document(base)
        self.assertEqual([entry["id"] for entry in result["entries"]], [CLI_CAPABILITY_ID])
        self.assertEqual([entry["id"] for entry in base["entries"]], ["notion.mcp.fetch", "notion.mcp.update_page"])

    def test_mcp_receipt_does_not_authorize_cli_read(self):
        runner = Runner()
        transport = _transport(runner)
        with TemporaryDirectory() as tmp:
            manifest = _manifest(tmp, CLI_FETCH_SCHEMA)
            with self.assertRaises(CapabilityError) as raised:
                read_cli_page(manifest, _receipt(manifest, ["notion.mcp.fetch"]), PAGE_ID, transport)
        self.assertEqual(raised.exception.code, "unauthorized_capability")
        self.assertEqual(runner.argv, [])

    def test_identity_mismatch_does_not_fetch(self):
        runner = Runner(workspace=OTHER_WORKSPACE)
        transport = _transport(runner)
        with self.assertRaises(CapabilityError) as raised:
            transport.call_tool(CLI_SERVER, "notion-fetch", {"id": PAGE_ID})
        self.assertEqual(raised.exception.code, "identity_mismatch")
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertEqual(runner.argv, [["ntn", "whoami", "--json"]])

    def test_schema_mismatch_fails_closed_before_the_page_read(self):
        runner = Runner()
        transport = _transport(runner)
        with TemporaryDirectory() as tmp:
            manifest = _manifest(tmp, {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]})
            with self.assertRaises(CapabilityError) as raised:
                read_cli_page(manifest, _receipt(manifest, [CLI_CAPABILITY_ID]), PAGE_ID, transport)
        self.assertEqual(raised.exception.code, "stale_schema")
        self.assertTrue(all(argv[1] != "api" for argv in runner.argv))

    def test_read_denial_drops_the_body(self):
        runner = Runner(status=403)
        transport = _transport(runner)
        with self.assertRaises(CapabilityError) as raised:
            transport.call_tool(CLI_SERVER, "notion-fetch", {"id": PAGE_ID})
        self.assertEqual(raised.exception.code, "call_unavailable")
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn(SECRET, repr(raised.exception))
        self.assertEqual(runner.argv[0], ["ntn", "whoami", "--json"])
        self.assertEqual(runner.argv[1][3:], ["-X", "GET"])

    def test_null_selection_does_not_call_the_transport(self):
        runner = Runner()
        transport = _transport(runner)
        with TemporaryDirectory() as tmp:
            manifest = _manifest(tmp, CLI_FETCH_SCHEMA)
            with self.assertRaises(CapabilityError) as raised:
                read_cli_page(manifest, _receipt(manifest, []), PAGE_ID, transport)
        self.assertEqual(raised.exception.code, "unauthorized_capability")
        self.assertEqual(runner.argv, [])

    def test_matching_read_keeps_only_the_markdown_contract(self):
        runner = Runner(page=_page(token="not-a-log-field"))
        transport = _transport(runner)
        result = transport.call_tool(CLI_SERVER, "notion-fetch", {"id": PAGE_ID})
        self.assertEqual(result["markdown"], "synthetic-markdown")
        self.assertNotIn("token", result)
        self.assertEqual(runner.argv[1], markdown_argv(PAGE_ID))

    def test_authorized_receipt_reads_only_one_page(self):
        runner = Runner()
        transport = _transport(runner)
        with TemporaryDirectory() as tmp:
            manifest = _manifest(tmp, CLI_FETCH_SCHEMA)
            result = read_cli_page(manifest, _receipt(manifest, [CLI_CAPABILITY_ID]), PAGE_ID, transport)
        self.assertEqual(result["id"], CLI_CAPABILITY_ID)
        self.assertEqual(result["schema_verification"], "static_pinned")
        self.assertEqual(result["result"]["markdown"], "synthetic-markdown")
        self.assertEqual(runner.argv, [["ntn", "whoami", "--json"], markdown_argv(PAGE_ID)])

    def test_truncated_or_foreign_id_is_not_returned(self):
        self.assertEqual(parse_page_markdown(_page(), PAGE_ID)["truncated"], False)
        with self.assertRaises(CapabilityError):
            parse_page_markdown(_page(truncated=True), PAGE_ID)
        with self.assertRaises(CapabilityError) as raised:
            parse_page_markdown(_page(id=OTHER_WORKSPACE), PAGE_ID)
        self.assertEqual(raised.exception.code, "identity_mismatch")
        self.assertNotIn("synthetic-markdown", str(raised.exception))

    def test_oversize_page_is_denied_before_return(self):
        runner = Runner(page=_page(markdown="x" * 65536))
        transport = _transport(runner)
        with self.assertRaises(CapabilityError) as raised:
            transport.call_tool(CLI_SERVER, "notion-fetch", {"id": PAGE_ID})
        self.assertEqual(raised.exception.code, "result_budget_exceeded")
        self.assertNotIn("x" * 100, str(raised.exception))

    def test_argv_allowlist_rejects_writes_and_shell_text(self):
        validate_read_argv(markdown_argv(PAGE_ID))
        for argv in (
            ["ntn", "pages", "create", "--content", "hello"],
            ["ntn", "api", f"v1/pages/{PAGE_ID}/markdown", "-X", "POST"],
            ["ntn", "api", f"v1/pages/{PAGE_ID};touch", "-X", "GET"],
            "ntn api",
        ):
            with self.assertRaises(CapabilityError):
                validate_read_argv(argv)

    def test_default_runner_closes_stdin_and_does_not_use_a_shell(self):
        with patch("jev_skill_advisor.notion_host_transport.subprocess.run", return_value=_Result(_whoami(PINNED_WORKSPACE_ID))) as run:
            default_runner(["ntn", "whoami", "--json"], credential_source="env", environ={"NOTION_API_TOKEN": "synthetic-test-value"})
        self.assertEqual(run.call_args.args[0], ["ntn", "whoami", "--json"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertNotIn("input", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["env"]["NOTION_API_TOKEN"], "synthetic-test-value")

    def test_absolute_ntn_path_and_version_are_required(self):
        with patch("jev_skill_advisor.notion_host_transport.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"ntn 0.23.2\n"
            self.assertEqual(verify_ntn_executable(Path(sys.executable), "0.23.2"), Path(sys.executable))
            run.return_value.stdout = b"ntn 0.24.0\n"
            with self.assertRaises(CapabilityError) as raised:
                verify_ntn_executable(Path(sys.executable), "0.23.2")
            self.assertEqual(raised.exception.code, "stale_schema")
        with self.assertRaises(CapabilityError):
            verify_ntn_executable(Path("ntn"), "0.23.2")
        with patch("jev_skill_advisor.notion_host_transport.subprocess.run", return_value=_Result(_whoami(PINNED_WORKSPACE_ID))) as run:
            default_runner(["ntn", "whoami", "--json"], credential_source="env",
                           environ={"NOTION_API_TOKEN": "synthetic-test-value"}, executable_path=Path(sys.executable))
        self.assertEqual(run.call_args.args[0][0], sys.executable)

    def test_saved_route_strips_inherited_token(self):
        with patch.dict("jev_skill_advisor.notion_host_transport.os.environ", {"NOTION_API_TOKEN": "wrong-inherited-value"}):
            with patch("jev_skill_advisor.notion_host_transport.subprocess.run", return_value=_Result(_whoami(PINNED_WORKSPACE_ID))) as run:
                default_runner(["ntn", "whoami", "--json"], credential_source="saved", environ={})
        self.assertNotIn("NOTION_API_TOKEN", run.call_args.kwargs["env"])

    def test_skill_selection_failure_still_authorizes_nothing(self):
        with TemporaryDirectory() as tmp:
            manifest = _manifest(tmp, CLI_FETCH_SCHEMA)

            def explode(payload, timeout):
                raise TimeoutError(SECRET)

            decision = resolve_capabilities(
                manifest, "read the operations note",
                selected_skills=[{"id": "warehouse:notion", "name": "notion"}],
                evaluator=explode,
            )
        self.assertEqual(decision["status"], "fail_open")
        self.assertEqual(decision["ids"], [])
        self.assertNotIn(SECRET, json.dumps(decision))


@unittest.skipIf(Client is None, "optional mcp dependency not installed")
class StdioToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.addAsyncCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        warehouse = root / "warehouse"
        skill = warehouse / "notion" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: notion\ndescription: Work in Notion\n---\n# Notion\n", encoding="utf-8")
        digest = __import__("hashlib").sha256(skill.read_bytes()).hexdigest()
        catalog = root / "catalog.json"
        catalog.write_text(json.dumps({"entries": [{
            "stable_id": "warehouse:notion", "name": "notion", "description": "Work in Notion",
            "relative_path": "notion", "content_hash": digest,
        }]}))
        self.config = root / "config.json"
        self.config.write_text(json.dumps({
            "config_version": 1, "profile_id": "route-test", "harness": "codex",
            "warehouse_root": str(warehouse), "catalog_path": str(catalog), "state_dir": str(root / "state"),
            "mode": "advisory", "provider_enabled": False, "read_enabled": True,
            "eligible_ids": ["warehouse:notion"], "read_allowlist": ["warehouse:notion"],
        }))
        ServiceRuntime(load_profile(self.config), initialize=True)
        self.manifest_path = root / "capabilities.json"
        write_manifest(self.manifest_path, {
            "manifest_version": 1, "description": "Synthetic CLI page-read test",
            "entries": [cli_capability_card()],
        })
        self.runner = Runner()
        self.bridge = _transport(self.runner)
        self.route_log = root / "routes.jsonl"
        self.server = build_server(
            self.config, host="mac", harness="codex", capability_manifest=self.manifest_path,
            list_tools=self.bridge, notion_bridge=self.bridge,
            notion_route=RouteDecision("cli", None, True, None), route_log=self.route_log,
            capability_evaluator=self._explode,
        )

    def _explode(self, payload, timeout):
        raise TimeoutError(SECRET)

    async def _body(self, result):
        self.assertFalse(result.is_error)
        body = result.structured_content or json.loads(result.content[0].text)
        return body.get("result", body)

    async def test_five_tools_null_selection_and_route_log(self):
        async with Client(self.server, raise_exceptions=True) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            self.assertEqual(names, {
                "skill_suggest", "skill_read", "skill_report_outcome", "capability_describe", "notion-fetch",
            })
            self.assertNotIn("capability_call", names)
            suggested = await self._body(await client.call_tool("skill_suggest", {
                "protocol_version": 1, "request_id": "r1", "session_id": "s-null",
                "task": "read the operations note", "explicit_skills": ["notion"],
            }))
            self.assertEqual(suggested["status"], "explicit_selection")
            self.assertEqual(suggested["capabilities"], [])
            denied = await self._body(await client.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "s-null", "receipt_id": suggested["receipt_id"],
                "page_id": PAGE_ID,
            }))
        self.assertEqual(denied, {"status": "denied", "reason": "unauthorized_capability"})
        self.assertEqual(self.runner.argv, [])
        text = self.route_log.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.splitlines()]
        row = rows[0]
        self.assertEqual(row["route"], "cli")
        self.assertIsNone(row["fallback_reason"])
        self.assertTrue(row["executable"])
        self.assertEqual(rows[1]["result_code"], "unauthorized_capability")
        self.assertEqual(rows[1]["schema_verification"], "static_pinned")
        self.assertNotIn(PAGE_ID, text)
        self.assertNotIn(SECRET, text)

    async def test_closed_route_does_not_call_a_transport(self):
        denied_bridge = DeniedTransport("identity_mismatch")
        server = build_server(
            self.config, host="mac", harness="codex", capability_manifest=self.manifest_path,
            notion_bridge=denied_bridge,
            notion_route=RouteDecision("cli", "identity_mismatch", False, "identity_mismatch"),
            route_log=self.route_log,
        )
        async with Client(server, raise_exceptions=True) as client:
            denied = await self._body(await client.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "s-closed", "receipt_id": "unused-receipt",
                "page_id": PAGE_ID,
            }))
        self.assertEqual(denied["reason"], "identity_mismatch")
        self.assertEqual(denied_bridge.calls, 0)
        rows = [json.loads(line) for line in self.route_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["fallback_reason"], "identity_mismatch")
        self.assertFalse(rows[-1]["executable"])


class ArgumentTests(unittest.TestCase):
    def test_cli_transport_stays_explicit(self):
        args = parse_args([
            "--config", "profile.json", "--capability-manifest", "live.json",
            "--notion-transport", "cli", "--route-log", "routes.jsonl",
        ])
        self.assertEqual(args.notion_transport, "cli")
        self.assertIsNone(args.notion_fallback)
        with self.assertRaises(SystemExit):
            parse_args(["--config", "profile.json", "--notion-transport", "cli", "--notion-fallback", "mcp"])
        with self.assertRaises(SystemExit):
            parse_args([
                "--config", "profile.json", "--capability-manifest", "live.json",
                "--notion-transport", "cli", "--notion-fallback", "cli",
                "--notion-fallback-reason", "harness_missing",
            ])


if __name__ == "__main__":
    unittest.main()
