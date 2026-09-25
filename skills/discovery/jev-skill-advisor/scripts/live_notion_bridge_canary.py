"""Opt-in, read-only Notion bridge canary; prints hashes, never page content."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jev_skill_advisor.capability_core import capability_describe, load_manifest, schema_digest
from jev_skill_advisor.notion_mcp_transport import (
    HermesNotionTransport,
    body_sha256,
    read_bridge_call,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--canary-config", type=Path, required=True)
    args = parser.parse_args()
    canary = json.loads(args.canary_config.read_text(encoding="utf-8"))
    canary = canary.get("canary", canary)
    page_id = canary["page_id"]
    expected_hash = canary.get("expected_body_sha256", canary.get("body_sha256"))
    manifest = load_manifest(args.manifest)
    entry = next((item for item in manifest.entries.values() if item.operation == "notion-fetch"), None)
    if entry is None or entry.writes:
        raise SystemExit("fetch_not_read_only")
    receipt = {"authorized_ids": [entry.id], "manifest_hash": manifest.content_hash}
    transport = HermesNotionTransport()
    try:
        described = capability_describe(manifest, receipt, entry.id, transport)
        schema_hash = schema_digest(described["schema"])
        called = read_bridge_call(
            manifest, receipt, page_id, list_tools=transport, call_tool=transport
        )
        body_hash, body_chars = body_sha256(called["result"])
    finally:
        transport.close()
    result = {
        "capability_id": entry.id,
        "schema_match": schema_hash == entry.schema_hash,
        "body_match": body_hash == expected_hash,
        "body_sha256": body_hash,
        "body_chars": body_chars,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["schema_match"] and result["body_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
