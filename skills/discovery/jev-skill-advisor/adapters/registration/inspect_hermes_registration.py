"""Print only safe Hermes Jev MCP registration fields; never env values."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


def main() -> int:
    path = Path.home() / ".hermes" / "config.yaml"
    raw = path.read_bytes()
    config = yaml.safe_load(raw)
    servers = config.get("mcp_servers") if isinstance(config, dict) else None
    item = servers.get("jev-skill-advisor") if isinstance(servers, dict) else None
    if not isinstance(item, dict):
        print(json.dumps({"status": "missing", "config_sha256": hashlib.sha256(raw).hexdigest()}))
        return 1
    command = item.get("command")
    args = item.get("args")
    if not isinstance(command, str) or not isinstance(args, list) or not all(isinstance(x, str) for x in args):
        print(json.dumps({"status": "shape_unexpected", "keys": sorted(item),
                          "config_sha256": hashlib.sha256(raw).hexdigest()}))
        return 1
    safe_flags = {"--release-root", "--expected-release", "--host", "--harness", "--notion-transport",
                  "--notion-server", "--capability-events", "--route-log"}
    flags = {}
    for index, arg in enumerate(args[:-1]):
        if arg in safe_flags:
            flags[arg] = args[index + 1]
    print(json.dumps({
        "status": "present", "config_sha256": hashlib.sha256(raw).hexdigest(),
        "command": command, "args_count": len(args), "flags": flags,
        "env_keys": sorted(item.get("env", {})) if isinstance(item.get("env", {}), dict) else [],
        "keys": sorted(item),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
