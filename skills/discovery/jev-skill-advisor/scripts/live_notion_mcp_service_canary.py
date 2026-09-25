"""Opt-in end-to-end read canary for the Jev advisor MCP service on dest."""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from mcp import Client

from jev_skill_advisor.capability_core import load_manifest, schema_digest
from jev_skill_advisor.mcp_server import build_server
from jev_skill_advisor.notion_mcp_transport import (
    HermesNotionTransport,
    body_sha256,
)


def _body(result):
    value = result.structured_content or json.loads(result.content[0].text)
    return value.get("result", value)


async def run(profile: Path, manifest_path: Path, *, page_id: str, expected_body_sha256: str) -> dict:
    manifest = load_manifest(manifest_path)
    transport = HermesNotionTransport()
    server = build_server(
        profile,
        capability_manifest=manifest_path,
        list_tools=transport,
        notion_bridge=transport,
    )
    session_id = "canary-" + uuid.uuid4().hex
    try:
        async with Client(server, raise_exceptions=True) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            if "notion-fetch" not in names or "capability_call" in names:
                raise RuntimeError("bridge_toolset_mismatch")
            suggested = _body(await client.call_tool("skill_suggest", {
                "protocol_version": 1,
                "request_id": "canary-" + uuid.uuid4().hex,
                "session_id": session_id,
                "task": "Get contents of a Notion page by its UUID",
                "explicit_skills": ["notion"],
            }))
            ids = [card["id"] for card in suggested.get("capabilities", [])]
            if ids != ["notion.mcp.fetch"]:
                return {"status": "not_selected", "ids": ids, "tool_count": len(names)}
            receipt_id = suggested["receipt_id"]
            described = _body(await client.call_tool("capability_describe", {
                "protocol_version": 1,
                "session_id": session_id,
                "receipt_id": receipt_id,
                "capability_id": ids[0],
            }))
            entry = manifest.entries[ids[0]]
            schema_match = schema_digest(described["schema"]) == entry.schema_hash
            called = _body(await client.call_tool("notion-fetch", {
                "protocol_version": 1,
                "session_id": session_id,
                "receipt_id": receipt_id,
                "page_id": page_id,
            }))
            result_body = called.get("result", called)
            if "text" not in result_body:
                return {
                    "status": "bridge_denied",
                    "reason": called.get("reason"),
                    "keys": sorted(called),
                    "tool_count": len(names),
                    "schema_match": schema_match,
                    "body_match": False,
                }
            body_hash, body_chars = body_sha256(result_body)
            return {
                "status": "complete",
                "ids": ids,
                "tool_count": len(names),
                "schema_match": schema_match,
                "body_match": body_hash == expected_body_sha256,
                "body_sha256": body_hash,
                "body_chars": body_chars,
            }
    finally:
        transport.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--canary-config", type=Path, required=True)
    args = parser.parse_args()
    canary = json.loads(args.canary_config.read_text(encoding="utf-8"))
    canary = canary.get("canary", canary)
    result = asyncio.run(run(
        args.profile, args.manifest, page_id=canary["page_id"],
        expected_body_sha256=canary.get("expected_body_sha256", canary.get("body_sha256")),
    ))
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "complete" and result["schema_match"] and result["body_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
