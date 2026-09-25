"""Read-only dest Hermes candidate canary; prints hashes, never Notion content."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from jev_skill_advisor.notion_mcp_transport import HermesNotionTransport, body_sha256


TOOLS = {"skill_suggest", "skill_read", "skill_report_outcome", "capability_describe", "notion-fetch"}
CAPABILITY = "notion.mcp.fetch"


def _body(result):
    if result.is_error:
        raise ValueError("bridge_tool_error")
    value = result.structured_content or json.loads(result.content[0].text)
    return value.get("result", value)


def _native_hash(page_id: str) -> tuple[str, int]:
    transport = HermesNotionTransport()
    try:
        result = transport.call_tool("notion", "notion-fetch", {"id": page_id})
        return body_sha256(result)
    finally:
        transport.close()


async def _bridge(bundle: Path, release_root: Path, release_id: str, host: str,
                  page_id: str) -> dict:
    params = StdioServerParameters(
        command=str(bundle / "bin" / "skill-advisor-mcp"),
        args=[
            "--release-root", str(release_root), "--expected-release", release_id,
            "--host", host, "--harness", "hermes", "--notion-transport", "hermes",
        ],
        env={"HOME": str(Path.home()), "PATH": "/home/dev/.local/bin:/usr/local/bin:/usr/bin:/bin"},
        cwd=str(bundle),
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            if len(listed.tools) != 5 or {tool.name for tool in listed.tools} != TOOLS:
                raise ValueError("tool_set_mismatch")
            schemas = {tool.name: tool.input_schema for tool in listed.tools}
            schema_sha = hashlib.sha256(json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            session_id = "dest-canary-" + uuid.uuid4().hex
            suggested = _body(await session.call_tool("skill_suggest", {
                "protocol_version": 1, "request_id": "dest-canary-" + uuid.uuid4().hex,
                "session_id": session_id,
                "task": "Read the complete contents of a Notion page by its UUID.",
            }))
            if [card.get("id") for card in suggested.get("capabilities", [])] != [CAPABILITY]:
                raise ValueError("capability_not_selected")
            receipt = suggested.get("receipt_id")
            if not isinstance(receipt, str):
                raise ValueError("receipt_missing")
            fetched = _body(await session.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": session_id,
                "receipt_id": receipt, "page_id": page_id,
            }))
            content = fetched.get("result", fetched)
            digest, chars = body_sha256(content)
            denied = _body(await session.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "forged-" + uuid.uuid4().hex,
                "receipt_id": "forged", "page_id": page_id,
            }))
            return {
                "tool_count": len(listed.tools), "schema_sha256": schema_sha,
                "capability_id": CAPABILITY, "body_sha256": digest,
                "body_chars": chars, "denial": denied.get("reason"),
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--page-id", required=True)
    args = parser.parse_args()
    try:
        native_sha, native_chars = _native_hash(args.page_id)
        report = asyncio.run(_bridge(args.bundle, args.release_root, args.release_id,
                                     args.host, args.page_id))
        report["native_match"] = report["body_sha256"] == native_sha and report["body_chars"] == native_chars
        report["status"] = (
            "pass" if report["native_match"] and report["denial"] == "unauthorized_capability" else "fail"
        )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "pass" else 1
    except Exception:
        print(json.dumps({"status": "fail", "reason": "canary_failed"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
