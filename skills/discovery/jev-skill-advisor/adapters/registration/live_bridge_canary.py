"""Read-only, content-free parity canary for one registered Mac bridge."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import tomllib
import uuid
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from run_bridge import load_identity

TOOLS = {"skill_suggest", "skill_read", "skill_report_outcome", "capability_describe", "notion-fetch"}
CAPABILITY = "notion.cli.page_read"


def _import_provenance(harness: str, *, bundle: Path | None = None) -> bool:
    command, _ = _registered(harness, bundle=bundle)
    probe = subprocess.run(
        [command, "-I", "-s", "-c",
         "import jev_skill_advisor,json,sys; print(json.dumps({'module':jev_skill_advisor.__file__,'path':sys.path}))"],
        env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        capture_output=True, check=False, timeout=15,
    )
    if probe.returncode:
        return False
    payload = json.loads(probe.stdout)
    module = Path(payload["module"]).resolve()
    expected = (bundle or (Path.home() / ".local" / "state" / "jev-skill-advisor" / "runtime" / "current")).resolve()
    paths = [str(module), *(str(Path(path).resolve()) for path in payload["path"] if path)]
    return module.is_relative_to(expected) and not any(
        "CloudStorage" in path or "Dropbox" in path for path in paths
    )


def _body(result):
    if result.is_error:
        raise ValueError("bridge_tool_error")
    value = result.structured_content or json.loads(result.content[0].text)
    return value.get("result", value)


def _registered(harness: str, *, bundle: Path | None = None, sandbox: bool = False) -> tuple[str, list[str]]:
    if harness == "codex":
        config = tomllib.loads((Path.home() / ".codex" / "config.toml").read_text())
        entry = config["mcp_servers"]["jev-capability-bridge"]
    elif harness == "cursor":
        config = json.loads((Path.home() / ".cursor" / "mcp.json").read_text())
        entry = config["mcpServers"]["jev-capability-bridge"]
    else:
        raise ValueError("unsupported_harness")
    command, args = entry["command"], entry["args"]
    if not isinstance(command, str) or not isinstance(args, list) or not all(
        isinstance(item, str) for item in args
    ):
        raise ValueError("registration_invalid")
    if bundle is not None:
        current = str(Path.home() / ".local" / "state" / "jev-skill-advisor" / "runtime" / "current")
        if not bundle.is_absolute() or not bundle.is_dir() or not command.startswith(current + "/"):
            raise ValueError("bundle_override_invalid")
        command = str(bundle) + command[len(current):]
        args = [str(bundle) + item[len(current):] if item.startswith(current + "/") else item for item in args]
    if sandbox:
        profile = '(version 1) (allow default) (deny file-read* (subpath "' + str(Path.home() / "Library" / "CloudStorage" / "Dropbox") + '"))'
        args = ["-p", profile, command, *args]
        command = "/usr/bin/sandbox-exec"
    return command, args


def _page_id(snapshot_manifest: Path, stable_id: str) -> str:
    payload = json.loads(snapshot_manifest.read_text())
    matches = [item.get("notion_id") for item in payload["packages"] if item.get("stable_id") == stable_id]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise ValueError("canary_page_missing")
    return str(uuid.UUID(matches[0]))


def _native_hash(page_id: str) -> tuple[str, int]:
    state = Path.home() / ".local" / "state" / "jev-skill-advisor"
    token, workspace_id = load_identity(
        state / "notion-cli" / "credential.env", state / "notion-cli" / "workspace.json",
    )
    env = {
        "HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
        "NOTION_API_TOKEN": token, "NOTION_EXPECTED_WORKSPACE_ID": workspace_id,
        "NOTION_CREDENTIAL_SOURCE": "env",
    }
    result = subprocess.run(
        [str(Path.home() / ".local" / "bin" / "ntn"), "api", f"v1/pages/{page_id}/markdown", "-X", "GET"],
        env=env, capture_output=True, check=False, timeout=30,
    )
    if result.returncode:
        raise ValueError("native_read_failed")
    payload = json.loads(result.stdout)
    if payload.get("object") != "page_markdown" or payload.get("truncated") is not False:
        raise ValueError("native_read_incomplete")
    markdown = payload.get("markdown")
    if not isinstance(markdown, str):
        raise ValueError("native_read_invalid")
    data = markdown.encode("utf-8")
    return hashlib.sha256(data).hexdigest(), len(data)


async def _bridge(harness: str, page_id: str, *, bundle: Path | None = None,
                  sandbox: bool = False, hold_seconds: int = 0) -> dict:
    command, args = _registered(harness, bundle=bundle, sandbox=sandbox)
    params = StdioServerParameters(
        command=command, args=args,
        env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/opt/homebrew/bin", "LANG": "C.UTF-8"},
        cwd=str(Path.home() / ".local" / "state" / "jev-skill-advisor" / "runtime") if sandbox else None,
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            if {item.name for item in listed.tools} != TOOLS or len(listed.tools) != 5:
                raise ValueError("tool_set_mismatch")
            schemas = {tool.name: tool.input_schema for tool in listed.tools}
            schema_hash = hashlib.sha256(
                json.dumps(schemas, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            session_id = "canary-" + uuid.uuid4().hex
            suggested = _body(await session.call_tool("skill_suggest", {
                "protocol_version": 1, "request_id": "canary-" + uuid.uuid4().hex,
                "session_id": session_id, "task": "Read the complete contents of a Notion page by its UUID.",
            }))
            cards = suggested.get("capabilities", [])
            if [card.get("id") for card in cards] != [CAPABILITY]:
                raise ValueError("capability_not_selected")
            receipt = suggested.get("receipt_id")
            if not isinstance(receipt, str):
                raise ValueError("receipt_missing")
            fetched = _body(await session.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": session_id,
                "receipt_id": receipt, "page_id": page_id,
            }))
            content = fetched.get("result", fetched)
            markdown = content.get("markdown")
            if not isinstance(markdown, str) or content.get("truncated") is not False:
                raise ValueError("bridge_read_incomplete")
            data = markdown.encode("utf-8")
            denied = _body(await session.call_tool("notion-fetch", {
                "protocol_version": 1, "session_id": "forged-" + uuid.uuid4().hex,
                "receipt_id": "forged", "page_id": page_id,
            }))
            if hold_seconds:
                await asyncio.sleep(hold_seconds)
            return {
                "tool_count": len(listed.tools), "schema_sha256": schema_hash,
                "capability_id": CAPABILITY, "body_sha256": hashlib.sha256(data).hexdigest(),
                "body_bytes": len(data), "denial": denied.get("reason"),
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", choices=("codex", "cursor"), required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--skill-id", default="warehouse:approval-gated-migrations")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--sandbox-deny-dropbox", action="store_true")
    parser.add_argument("--hold-seconds", type=int, default=0)
    parser.add_argument("--require-host-local-imports", action="store_true")
    args = parser.parse_args()
    try:
        if not 0 <= args.hold_seconds <= 30:
            raise ValueError("hold_seconds_invalid")
        page_id = _page_id(args.snapshot_manifest, args.skill_id)
        native_hash, native_bytes = _native_hash(page_id)
        report = asyncio.run(_bridge(args.harness, page_id, bundle=args.bundle,
                                     sandbox=args.sandbox_deny_dropbox,
                                     hold_seconds=args.hold_seconds))
        report["harness"] = args.harness
        report["native_match"] = (
            report["body_sha256"] == native_hash and report["body_bytes"] == native_bytes
        )
        if args.require_host_local_imports:
            report["host_local_imports"] = _import_provenance(args.harness, bundle=args.bundle)
        report["status"] = (
            "pass" if report["native_match"] and report["denial"] == "unauthorized_capability"
            and report.get("host_local_imports", True) else "fail"
        )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "pass" else 1
    except Exception:
        print(json.dumps({"harness": args.harness, "status": "fail", "reason": "canary_failed"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
