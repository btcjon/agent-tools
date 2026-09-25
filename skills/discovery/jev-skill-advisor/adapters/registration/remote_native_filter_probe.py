"""Read-only in-process Hermes Notion filter/transport probe; emits hashes only.

This does not start an agent turn or edit the live Hermes configuration.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--hermes-source", type=Path, default=Path("/home/dev/.hermes/hermes-agent"))
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(args.hermes_source.resolve()))
    from hermes_cli import mcp_config
    from tools.mcp_tool_registration import _make_tool_filter
    from jev_skill_advisor.notion_mcp_transport import HermesNotionTransport, body_sha256

    original_get = mcp_config._get_mcp_servers
    servers = original_get()
    notion = servers.get("notion") if isinstance(servers, dict) else None
    if not isinstance(notion, dict):
        print(json.dumps({"status": "fail", "reason": "notion_unconfigured"}))
        return 1
    filtered = {**notion, "tools": {"include": []}}
    should_register = _make_tool_filter("notion", filtered)
    filtered_transport = None
    direct_transport = None
    try:
        mcp_config._get_mcp_servers = lambda: {**servers, "notion": filtered}
        filtered_transport = HermesNotionTransport()
        listed = filtered_transport.list_tools("notion")["tools"]
        visible = sum(bool(should_register(tool["name"])) for tool in listed)
        filtered_hash, filtered_chars = body_sha256(
            filtered_transport.call_tool("notion", "notion-fetch", {"id": args.page_id})
        )
        mcp_config._get_mcp_servers = original_get
        filtered_transport.close()
        filtered_transport = None
        direct_transport = HermesNotionTransport()
        direct_hash, direct_chars = body_sha256(
            direct_transport.call_tool("notion", "notion-fetch", {"id": args.page_id})
        )
        matched = filtered_hash == direct_hash and filtered_chars == direct_chars
        print(json.dumps({
            "status": "partial_in_process" if visible == 0 and matched else "fail",
            "filter_registered_native_count": visible,
            "independent_transport_tool_count": len(listed),
            "body_sha256": filtered_hash,
            "body_chars": filtered_chars,
            "direct_match": matched,
            "agent_turn_tested": False,
            "live_config_changed": False,
        }, sort_keys=True))
        return 0 if visible == 0 and matched else 1
    except Exception:
        print(json.dumps({"status": "fail", "reason": "probe_failed"}, sort_keys=True))
        return 1
    finally:
        mcp_config._get_mcp_servers = original_get
        if filtered_transport is not None:
            filtered_transport.close()
        if direct_transport is not None:
            direct_transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
