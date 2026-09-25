"""Per-session immutable capability manifests for the release-root MCP bridge."""
import io
import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path

try:
    from mcp import Client
except ImportError:
    Client = None

from jev_skill_advisor import capability_core
from jev_skill_advisor.capability_core import load_manifest
from jev_skill_advisor.library_cache import LibraryCache
from jev_skill_advisor.mcp_server import build_server, parse_args
from jev_skill_advisor.notion_import import Export
from jev_skill_advisor.notion_mcp_transport import build_manifest_document, write_manifest
from jev_skill_advisor.release import ReleaseError, ReleaseStore, build_release
from jev_skill_advisor.runtime import ServiceRuntime

PAGE_ID = "1234abcd-5678-4abc-8def-1234567890ab"
FETCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"id": {"type": "string"}}, "required": ["id"],
}
HOST = "mac"
HARNESS = "hermes"
TOOLS = {"skill_suggest", "skill_read", "skill_report_outcome", "capability_describe", "notion-fetch"}


class _Bridge:
    def __init__(self, schema):
        self.schema = schema
        self.list_calls = 0
        self.calls = []

    def list_tools(self, server):
        self.list_calls += 1
        return {"tools": [{"name": "notion-fetch", "inputSchema": self.schema, "server": server}]}

    def call_tool(self, server, operation, arguments):
        self.calls.append((server, operation, dict(arguments)))
        return {"text": "synthetic-page"}


def _archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            bundle.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _skill(name, body):
    text = f"---\nname: {name}\ndescription: {name} procedure.\n---\n# {name}\n{body}\n"
    return f"package/{name}/SKILL.md", text.encode()


def _snapshot(tmp_path):
    notion_path, notion_body = _skill("notion", "BODY_NOTION")
    alpha_path, alpha_body = _skill("alpha", "BODY_ALPHA")
    cache = LibraryCache(tmp_path / "cache")
    status = cache.publish([
        Export("skill", "page-notion", "1" * 64, _archive({notion_path: notion_body})),
        Export("skill", "page-alpha", "2" * 64, _archive({alpha_path: alpha_body})),
    ])
    snapshot_root = Path(status["catalog_root"])
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    from jev_skill_advisor.catalog_cli import build_catalog
    catalog = build_catalog(snapshot_root)
    skill_count = len({row["stable_id"] for row in [*catalog["entries"], *catalog.get("exclusions", [])]})
    inventory = evidence / "inventory.json"
    parity = evidence / "parity.json"
    tests = evidence / "tests.json"
    inventory.write_text(json.dumps({"inventory_hash": "inv", "skills": [{"n": index} for index in range(skill_count)]}) + "\n")
    parity.write_text(json.dumps({"exact": True, "snapshot_id": status["snapshot_id"], "inventory_hash": "inv"}) + "\n")
    tests.write_text(json.dumps({"status": "passed", "commit": "rev-1"}) + "\n")
    return {"snapshot_id": status["snapshot_id"], "snapshot_root": snapshot_root, "revision": "rev-1",
            "evidence": {"inventory": inventory, "parity": parity, "tests": tests}}


def _document(summary, description):
    return build_manifest_document(
        [{"name": "notion-fetch", "description": summary, "inputSchema": FETCH_SCHEMA, "read_only": True}],
        source="release-test", description=description,
    )


def _events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _payload(result):
    if result.is_error:
        text = " ".join(getattr(block, "text", str(block)) for block in result.content)
        return {"_error": text}
    body = result.structured_content or json.loads(result.content[0].text)
    if isinstance(body, dict) and "status" not in body and "result" in body:
        return body["result"]
    return body


def _insert_receipt(root, receipt):
    path = root / "runtime" / HARNESS / "advisor.sqlite3"
    payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO receipts VALUES(?,?,?,?,?)",
            (receipt["receipt_id"], receipt["profile_id"], receipt["session_id"], receipt["expires_at"], payload),
        )


@unittest.skipIf(Client is None, "optional mcp dependency not installed")
class ReleaseManifestBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        spec = _snapshot(root)
        self.schema = json.loads(json.dumps(FETCH_SCHEMA))
        self.source_a = root / "source-a.json"
        self.source_b = root / "source-b.json"
        write_manifest(self.source_a, _document("Read one Notion page by id.", "Release A pins."))
        write_manifest(self.source_b, _document("Read one Notion page by id for release B.", "Release B pins."))
        self.release_root = root / "state"
        self.release_a, manifest_a = build_release(
            root=self.release_root, snapshot_id=spec["snapshot_id"], snapshot_root=spec["snapshot_root"],
            evidence=spec["evidence"], revision=spec["revision"], activation="selection",
            capability_manifest=self.source_a,
        )
        self.release_b, manifest_b = build_release(
            root=self.release_root, snapshot_id=spec["snapshot_id"], snapshot_root=spec["snapshot_root"],
            evidence=spec["evidence"], revision=spec["revision"], activation="selection",
            capability_manifest=self.source_b,
        )
        self.release_u, _manifest_u = build_release(
            root=self.release_root, snapshot_id=spec["snapshot_id"], snapshot_root=spec["snapshot_root"],
            evidence=spec["evidence"], revision=spec["revision"], activation="selection",
        )
        self.bound_a = Path(manifest_a["files"]["capability_manifest"]["path"])
        self.bound_b = Path(manifest_b["files"]["capability_manifest"]["path"])
        self.hash_a = load_manifest(self.bound_a).content_hash
        self.hash_b = load_manifest(self.bound_b).content_hash
        self.assertNotEqual(self.hash_a, self.hash_b)
        self.original_a = self.bound_a.read_bytes()
        self.source_a.write_text(self.source_a.read_text(encoding="utf-8").replace(
            "Read one Notion page by id.", "MUTATED SOURCE FALLBACK", 1,
        ), encoding="utf-8")
        self.events = root / "events.jsonl"
        self.bridge = _Bridge(self.schema)
        self.loaded_paths = []
        self.resolutions = []
        self._real_load = capability_core.load_manifest
        self._real_resolve = ReleaseStore.resolve_profile

        def spy_load(path):
            self.loaded_paths.append(str(Path(path).resolve()))
            return self._real_load(path)

        def spy_resolve(store, *, host, harness, session_id):
            self.resolutions.append(session_id)
            return self._real_resolve(store, host=host, harness=harness, session_id=session_id)

        capability_core.load_manifest = spy_load
        ReleaseStore.resolve_profile = spy_resolve
        self.addAsyncCleanup(self._restore_patches)
        store = ReleaseStore(self.release_root)
        store.activate(self.release_a, expected_previous=None)
        self.server = build_server(
            release_root=self.release_root, host=HOST, harness=HARNESS,
            list_tools=self.bridge, notion_bridge=self.bridge, capability_events=self.events,
        )

    async def _restore_patches(self):
        capability_core.load_manifest = self._real_load
        ReleaseStore.resolve_profile = self._real_resolve

    def _assert_bound_only(self):
        source = str(self.source_a.resolve())
        self.assertNotIn(source, self.loaded_paths)
        self.assertNotIn(str(self.source_b.resolve()), self.loaded_paths)
        self.assertTrue(self.loaded_paths)
        inputs = str((self.release_root / "release-inputs").resolve())
        for path in self.loaded_paths:
            self.assertTrue(path.startswith(inputs), path)

    async def _call(self, client, name, payload):
        before = len(self.resolutions)
        try:
            result = await client.call_tool(name, payload)
        except Exception as exc:
            self.assertEqual(len(self.resolutions), before + 1)
            return {"_error": f"{exc.__class__.__name__}: {exc}"}
        body = _payload(result)
        self.assertEqual(len(self.resolutions), before + 1, body)
        return body

    async def _suggest(self, client, session):
        return await self._call(client, "skill_suggest", {
            "protocol_version": 1, "request_id": session, "session_id": session,
            "task": "please notion-fetch the page", "explicit_skills": ["notion"],
        })

    def _receipt(self, session):
        _release_id, _manifest, profile = self._real_resolve(
            ReleaseStore(self.release_root), host=HOST, harness=HARNESS, session_id=session,
        )
        return profile

    async def test_pinned_session_keeps_its_manifest_while_another_release_is_active(self):
        async with Client(self.server, raise_exceptions=True) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            self.assertEqual(names, TOOLS)
            self.assertNotIn("capability_call", names)
            self.assertEqual(self.resolutions, [])
            first = await self._suggest(client, "session-a")
            peer = await self._suggest(client, "session-a2")
            ReleaseStore(self.release_root).activate(self.release_b, expected_previous=self.release_a)
            pinned = await self._suggest(client, "session-a")
            active = await self._suggest(client, "session-b")
            self.assertEqual(first["status"], "explicit_selection")
            self.assertEqual(first["capabilities"][0]["description"], "Read one Notion page by id.")
            self.assertEqual(peer["capabilities"][0]["description"], "Read one Notion page by id.")
            self.assertEqual(pinned["capabilities"][0]["description"], "Read one Notion page by id.")
            self.assertEqual(active["capabilities"][0]["description"], "Read one Notion page by id for release B.")
            profile_a = self._receipt("session-a")
            profile_b = self._receipt("session-b")
            stored_a = ServiceRuntime(profile_a).load_receipt(pinned["receipt_id"])
            stored_b = ServiceRuntime(profile_b).load_receipt(active["receipt_id"])
            self.assertEqual(stored_a["capability_manifest_hash"], self.hash_a)
            self.assertEqual(stored_b["capability_manifest_hash"], self.hash_b)
            self.assertNotEqual(stored_a["profile_id"], stored_b["profile_id"])
            described = await self._call(client, "capability_describe", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": pinned["receipt_id"],
                "capability_id": "notion.mcp.fetch",
            })
            self.assertEqual(described["status"], "described")
            self.assertEqual(described["schema"], FETCH_SCHEMA)
            lists_after_describe = self.bridge.list_calls
            fetched = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": pinned["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(fetched["status"], "called")
            self.assertEqual(self.bridge.calls, [( "notion", "notion-fetch", {"id": PAGE_ID})])
            self.assertGreater(self.bridge.list_calls, lists_after_describe)
            fetched_b = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-b", "receipt_id": active["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(fetched_b["status"], "called")
            rows = [row for row in _events(self.events) if row.get("session_id") in {"session-a", "session-b"}]
            discovery = {(row["session_id"], row["manifest_hash"]) for row in rows if row["stage"] == "discovery"}
            self.assertIn(("session-a", self.hash_a), discovery)
            self.assertIn(("session-b", self.hash_b), discovery)
            invokes = [row for row in rows if row["stage"] == "invoke" and row["outcome"] == "success"]
            self.assertEqual(
                {(row["session_id"], row["manifest_hash"], row["receipt_id"]) for row in invokes},
                {
                    ("session-a", self.hash_a, pinned["receipt_id"]),
                    ("session-b", self.hash_b, active["receipt_id"]),
                },
            )
            calls_before_denial = len(self.bridge.calls)
            lists_before_denial = self.bridge.list_calls
            wrong_receipt = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": "missingreceipt",
                "page_id": PAGE_ID,
            })
            wrong_session = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-a2", "receipt_id": pinned["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(wrong_receipt, {"status": "denied", "reason": "unauthorized_capability"})
            self.assertEqual(wrong_session, {"status": "denied", "reason": "unauthorized_capability"})
            stale = dict(stored_a)
            stale["receipt_id"] = "forgedstalehash01"
            stale["capability_manifest_hash"] = self.hash_b
            _insert_receipt(self.release_root, stale)
            stale_body = await self._call(client, "capability_describe", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": stale["receipt_id"],
                "capability_id": "notion.mcp.fetch",
            })
            self.assertEqual(stale_body, {"status": "denied", "reason": "stale_manifest"})
            mismatched = dict(stored_a)
            mismatched["receipt_id"] = "forgedprofile01"
            mismatched["session_id"] = "session-b"
            _insert_receipt(self.release_root, mismatched)
            profile_body = await self._call(client, "capability_describe", {
                "protocol_version": 1, "session_id": "session-b", "receipt_id": mismatched["receipt_id"],
                "capability_id": "notion.mcp.fetch",
            })
            self.assertEqual(profile_body, {"status": "denied", "reason": "unauthorized_capability"})
            expired = dict(stored_a)
            expired["receipt_id"] = "forgedexpired01"
            expired["expires_at"] = "2000-01-01T00:00:00Z"
            _insert_receipt(self.release_root, expired)
            expired_body = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": expired["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(expired_body, {"status": "denied", "reason": "unauthorized_capability"})
            self.assertEqual(len(self.bridge.calls), calls_before_denial)
            self.assertEqual(self.bridge.list_calls, lists_before_denial)
            self.schema["properties"]["id"]["type"] = "integer"
            drifted = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": pinned["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(drifted, {"status": "denied", "reason": "stale_schema"})
            self.assertEqual(len(self.bridge.calls), calls_before_denial)
            self.assertEqual(self.bridge.list_calls, lists_before_denial + 1)
            drift_rows = [
                row for row in _events(self.events)
                if row.get("session_id") == "session-a" and row.get("stage") == "invoke" and row.get("status") == "denied"
            ]
            self.assertTrue(any(row["manifest_hash"] == self.hash_a and row["reason"] == "schema-drift" for row in drift_rows))
            self.bound_a.write_bytes(b"damaged")
            with self.assertRaisesRegex(ReleaseError, "release_file_tampered"):
                self._real_resolve(
                    ReleaseStore(self.release_root), host=HOST, harness=HARNESS, session_id="session-a",
                )
            damaged = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": pinned["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(damaged, {"status": "denied", "reason": "release_unavailable"})
            self.assertNotIn("synthetic-page", json.dumps(damaged))
            self.assertNotIn(str(self.bound_a), json.dumps(damaged))
            self.assertEqual(len(self.bridge.calls), calls_before_denial)
            self.bound_a.write_bytes(self.original_a)
            self.schema["properties"]["id"]["type"] = "string"
            restored = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": pinned["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(restored["status"], "called")
            self.assertEqual(restored["result"]["text"], "synthetic-page")
            self.bound_a.unlink()
            with self.assertRaisesRegex(ReleaseError, "capability_manifest_missing"):
                self._real_resolve(
                    ReleaseStore(self.release_root), host=HOST, harness=HARNESS, session_id="session-a",
                )
            missing = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-a", "receipt_id": pinned["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(missing, {"status": "denied", "reason": "release_unavailable"})
            self.assertNotIn("synthetic-page", json.dumps(missing))
            self.assertNotIn(str(self.bound_a), json.dumps(missing))
            ReleaseStore(self.release_root).activate(self.release_u, expected_previous=self.release_b)
            names = {tool.name for tool in (await client.list_tools()).tools}
            self.assertEqual(names, TOOLS)
            calls_before_unbound = len(self.bridge.calls)
            unbound_suggest = await self._suggest(client, "session-u")
            self.assertEqual(unbound_suggest["status"], "explicit_selection")
            self.assertNotIn("capabilities", unbound_suggest)
            unbound_describe = await self._call(client, "capability_describe", {
                "protocol_version": 1, "session_id": "session-u", "receipt_id": unbound_suggest["receipt_id"],
                "capability_id": "notion.mcp.fetch",
            })
            unbound_fetch = await self._call(client, "notion-fetch", {
                "protocol_version": 1, "session_id": "session-u", "receipt_id": unbound_suggest["receipt_id"],
                "page_id": PAGE_ID,
            })
            self.assertEqual(unbound_describe, {"status": "denied", "reason": "capability_unbound"})
            self.assertEqual(unbound_fetch, {"status": "denied", "reason": "capability_unbound"})
            self.assertEqual(len(self.bridge.calls), calls_before_unbound)
            self._assert_bound_only()


class ReleaseManifestCliTests(unittest.TestCase):
    def test_release_root_rejects_static_manifest_and_config_keeps_it(self):
        with self.assertRaises(SystemExit):
            parse_args(["--release-root", "/releases", "--capability-manifest", "/manifest.json"])
        with self.assertRaises(SystemExit):
            parse_args(["--config", "profile.json", "--notion-transport", "hermes"])
        release = parse_args([
            "--release-root", "/releases", "--host", "mac", "--harness", "hermes",
            "--notion-transport", "hermes", "--capability-events", "/events.jsonl",
        ])
        self.assertEqual(release.release_root, Path("/releases"))
        self.assertIsNone(release.capability_manifest)
        self.assertEqual(release.notion_transport, "hermes")
        self.assertEqual(release.host, "mac")
        self.assertEqual(release.harness, "hermes")
        config = parse_args([
            "--config", "profile.json", "--capability-manifest", "live.json", "--notion-transport", "hermes",
        ])
        self.assertEqual(config.config, Path("profile.json"))
        self.assertEqual(config.capability_manifest, Path("live.json"))
        with self.assertRaisesRegex(ValueError, "release_root_rejects_static_manifest"):
            build_server(release_root=Path("/releases"), capability_manifest=Path("/manifest.json"))


def _denial_payloads(label):
    session = f"s-{label}"
    return [
        ("skill_suggest", {
            "protocol_version": 1, "request_id": session, "session_id": session,
            "task": "please notion-fetch the page", "explicit_skills": ["notion"],
        }),
        ("skill_read", {
            "protocol_version": 1, "session_id": session, "receipt_id": "receiptunused",
            "skill_id": "warehouse:notion", "expected_content_hash": "0" * 64,
        }),
        ("skill_report_outcome", {
            "protocol_version": 1, "session_id": session, "receipt_id": "receiptunused",
            "event_id": "event-1", "skill_id": "warehouse:notion",
            "outcome": "applied", "evidence": "self_reported",
        }),
        ("capability_describe", {
            "protocol_version": 1, "session_id": session, "receipt_id": "receiptunused",
            "capability_id": "notion.mcp.fetch",
        }),
        ("notion-fetch", {
            "protocol_version": 1, "session_id": session, "receipt_id": "receiptunused",
            "page_id": PAGE_ID,
        }),
    ]


@unittest.skipIf(Client is None, "optional mcp dependency not installed")
class ReleaseDenialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        spec = _snapshot(root)
        self.release_root = root / "state"
        source = root / "source.json"
        write_manifest(source, _document("Read one Notion page by id.", "Release denial pins."))
        self.release_id, manifest = build_release(
            root=self.release_root, snapshot_id=spec["snapshot_id"], snapshot_root=spec["snapshot_root"],
            evidence=spec["evidence"], revision=spec["revision"], activation="selection",
            capability_manifest=source,
        )
        self.bound = Path(manifest["files"]["capability_manifest"]["path"])
        self.original = self.bound.read_bytes()
        self.events = root / "events.jsonl"
        self.bridge = _Bridge(json.loads(json.dumps(FETCH_SCHEMA)))
        self.provider_calls = []

        def evaluator(payload, timeout):
            self.provider_calls.append(timeout)
            raise TimeoutError("typesafe_timeout")

        self.server = build_server(
            release_root=self.release_root, host=HOST, harness=HARNESS,
            list_tools=self.bridge, notion_bridge=self.bridge, capability_events=self.events,
            capability_evaluator=evaluator,
        )
        self.secrets = (
            str(self.bound), str(self.release_root), "BODY_NOTION", PAGE_ID, "ReleaseError",
            "release_file_tampered", "capability_manifest_missing", "no_current_release",
            "Traceback", "typesafe_timeout",
        )

    def _body(self, result, name):
        self.assertFalse(result.is_error, name)
        body = _payload(result)
        self.assertNotIn("_error", body, name)
        return body

    async def _assert_each_tool_denied(self, client, label):
        for name, payload in _denial_payloads(label):
            before = len(_events(self.events))
            calls = len(self.bridge.calls)
            lists = self.bridge.list_calls
            providers = len(self.provider_calls)
            body = self._body(await client.call_tool(name, payload), name)
            self.assertEqual(body, {"status": "denied", "reason": "release_unavailable"}, name)
            self.assertEqual(len(self.bridge.calls), calls, name)
            self.assertEqual(self.bridge.list_calls, lists, name)
            self.assertEqual(len(self.provider_calls), providers, name)
            rows = _events(self.events)
            self.assertEqual(len(rows), before + 1, name)
            row = rows[-1]
            self.assertEqual(row["stage"], "fallback", name)
            self.assertEqual(row["outcome"], "failed", name)
            self.assertEqual(row["fallback_reason"], "other", name)
            self.assertEqual(row["capability_ids"], [], name)
            self.assertEqual(row["harness"], HARNESS, name)
            for hidden in ("session_id", "receipt_id", "manifest_hash", "failure_class", "reason", "status"):
                self.assertNotIn(hidden, row, name)
            blob = json.dumps({"body": body, "event": row})
            for secret in self.secrets:
                self.assertNotIn(secret, blob, name)

    async def test_missing_and_tampered_release_denies_every_tool(self):
        async with Client(self.server, raise_exceptions=True) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            self.assertEqual(names, TOOLS)
            await self._assert_each_tool_denied(client, "missing")
            ReleaseStore(self.release_root).activate(self.release_id, expected_previous=None)
            served = self._body(await client.call_tool("skill_suggest", {
                "protocol_version": 1, "request_id": "served", "session_id": "served",
                "task": "please notion-fetch the page", "explicit_skills": ["notion"],
            }), "served")
            self.assertEqual(served["status"], "explicit_selection")
            self.assertNotEqual(served.get("reason"), "release_unavailable")
            self.assertTrue(served.get("capabilities"))
            self.assertEqual(self.provider_calls, [])
            self.assertEqual(self.bridge.calls, [])
            self.bound.write_bytes(b"damaged")
            await self._assert_each_tool_denied(client, "tampered")
            self.bound.write_bytes(self.original)
            self.bound.unlink()
            await self._assert_each_tool_denied(client, "removed")


if __name__ == "__main__":
    unittest.main()
