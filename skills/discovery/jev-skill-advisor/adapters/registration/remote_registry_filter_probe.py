"""Isolated-process Hermes MCP registry test with native Notion filtered in memory."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

EXPECTED_BRIDGE_NAMES = {
    "mcp__jev_skill_advisor__capability_describe",
    "mcp__jev_skill_advisor__notion_fetch",
    "mcp__jev_skill_advisor__skill_read",
    "mcp__jev_skill_advisor__skill_report_outcome",
    "mcp__jev_skill_advisor__skill_suggest",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-source", type=Path, default=Path("/home/dev/.hermes/hermes-agent"))
    parser.add_argument("--native-visible", action="store_true", help="Measure the unchanged Notion registration as baseline")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, str(args.hermes_source.resolve()))
    from hermes_cli.mcp_config import _get_mcp_servers
    from tools.mcp_tool_discovery import register_mcp_servers
    from tools.mcp_tool_lifecycle import shutdown_mcp_servers
    from tools.registry import registry
    from tools.tool_search import assemble_tool_defs, load_config_readonly
    from hermes_cli.prompt_size import compute_prompt_breakdown

    configured = _get_mcp_servers()
    notion = configured.get("notion") if isinstance(configured, dict) else None
    bridge = configured.get("jev-skill-advisor") if isinstance(configured, dict) else None
    if not isinstance(notion, dict) or not isinstance(bridge, dict):
        print(json.dumps({"status": "fail", "reason": "server_unconfigured"}))
        return 1
    notion_tools = {**(notion.get("tools") or {}), "resources": False, "prompts": False}
    if not args.native_visible:
        notion_tools["include"] = []
    filtered = {**notion, "tools": notion_tools}
    bridge_filtered = {**bridge, "tools": {**(bridge.get("tools") or {}), "resources": False, "prompts": False}}
    try:
        names = register_mcp_servers({"notion": filtered, "jev-skill-advisor": bridge_filtered})
        native = [name for name in names if name.startswith("mcp__notion__")]
        selected = {name for name in names if name.startswith("mcp__jev_skill_advisor__")}
        native_definitions = registry.get_definitions(set(native), quiet=True)
        bridge_definitions = registry.get_definitions(selected, quiet=True)
        assembled = assemble_tool_defs(native_definitions + bridge_definitions, config=load_config_readonly())
        inspected = compute_prompt_breakdown("cli")
        expected_native = bool(native) if args.native_visible else not native
        outcome = {
            "status": "baseline_in_process" if args.native_visible and expected_native and selected == EXPECTED_BRIDGE_NAMES else (
                "partial_in_process" if expected_native and selected == EXPECTED_BRIDGE_NAMES else "fail"
            ),
            "model_registry_native_notion_count": len(native),
            "model_registry_bridge_count": len(selected),
            "native_registry_schema_bytes": len(json.dumps(native_definitions, separators=(",", ":")).encode()) if native_definitions else 0,
            "bridge_registry_schema_bytes": len(json.dumps(bridge_definitions, separators=(",", ":")).encode()) if bridge_definitions else 0,
            "mcp_only_assembled_schema_bytes": len(json.dumps(assembled.tool_defs, separators=(",", ":")).encode()),
            "mcp_only_assembled_tool_count": len(assembled.tool_defs),
            "mcp_only_deferred_count": assembled.deferred_count,
            "mcp_only_listing_form": assembled.listing_form,
            "offline_agent_system_prompt_bytes": inspected["system_prompt"]["bytes"],
            "offline_agent_tool_schema_bytes": inspected["tools"]["json_bytes"],
            "offline_agent_tool_count": inspected["tools"]["count"],
            "registered_bridge_names": sorted(selected),
            "agent_turn_tested": False,
            "provider_request_captured": False,
            "live_config_changed": False,
        }
        print(json.dumps(outcome, sort_keys=True))
        return 0 if outcome["status"] != "fail" else 1
    except Exception:
        print(json.dumps({"status": "fail", "reason": "probe_failed"}, sort_keys=True))
        return 1
    finally:
        shutdown_mcp_servers()


if __name__ == "__main__":
    raise SystemExit(main())
