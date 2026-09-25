"""Host-local registration for the five-tool Jev capability bridge.

Install and disable edit only the jev-capability-bridge entry in an explicit
config file. They refuse a release that has no validated capability manifest.
Pi has no MCP client here, so Pi never writes an MCP config.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

SERVER_NAME = "jev-capability-bridge"
PINNED_NTN_VERSION = "0.23.2"
BRIDGE_TOOLS = (
    "skill_suggest",
    "skill_read",
    "skill_report_outcome",
    "capability_describe",
    "notion-fetch",
)
HARNESSES = ("codex", "cursor", "pi")
PROFILE_HARNESSES = {"codex": "codex", "cursor": "generic", "pi": "generic"}
BEGIN = "# BEGIN jev-capability-bridge"
END = "# END jev-capability-bridge"
_HEADER = re.compile(r"^\s*\[(?!\[)(.+?)\]\s*(?:#.*)?$")
_TRANSPORT_CHOICES = re.compile(r"--notion-transport\s+\{([^}]+)\}")
_SECRET_MARKERS = ("bearer", "token", "secret", "api_key", "authorization")


class RegistrationError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def default_release_root():
    return Path.home() / ".local" / "state" / "jev-skill-advisor" / "releases"


def default_config(harness):
    home = Path.home()
    if harness == "codex":
        return home / ".codex" / "config.toml"
    if harness == "cursor":
        return home / ".cursor" / "mcp.json"
    if harness == "pi":
        return home / ".pi" / "agent" / "mcp.json"
    raise RegistrationError("unsupported_harness")


def default_executable():
    found = shutil.which("skill-advisor-mcp")
    if found:
        return Path(found)
    return Path(__file__).resolve().parents[2] / ".venv" / "bin" / "skill-advisor-mcp"


def default_ntn_path():
    found = shutil.which("ntn")
    return Path(found).resolve() if found else Path("/nonexistent/ntn")


def default_state_file(harness, name):
    return Path.home() / ".local" / "state" / "jev-skill-advisor" / f"{harness}-adapter" / name


def default_identity_file(name):
    return Path.home() / ".local" / "state" / "jev-skill-advisor" / "notion-cli" / name


def local_host():
    return socket.gethostname()


def pi_client_state():
    adapter = Path.home() / ".pi" / "agent" / "npm" / "node_modules" / "pi-mcp-adapter"
    if adapter.exists():
        return "present_unwired"
    return "absent"


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def schema_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compare_schema_digest(expected, live):
    """Mismatch stays fail-open: native servers are left in place."""

    if not expected and not live:
        return "skipped"
    if not isinstance(expected, str) or not isinstance(live, str):
        return "fail_open"
    if len(expected) == 64 and expected == live:
        return "match"
    return "fail_open"


def _sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_capability(path: Path):
    src = Path(__file__).resolve().parents[2] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from jev_skill_advisor.capability_core import CapabilityError, load_manifest
    try:
        return load_manifest(path)
    except CapabilityError:
        return None


def _toml_string(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_array(items):
    return "[" + ", ".join(_toml_string(item) for item in items) + "]"


def bridge_args(release_root, release_id, host, harness, events_path, route_log, ntn_path, ntn_version):
    return [
        "--release-root", str(release_root),
        "--expected-release", release_id,
        "--host", host,
        "--harness", harness,
        "--capability-events", str(events_path),
        "--notion-transport", "cli",
        "--notion-cli-path", str(ntn_path),
        "--notion-cli-version", ntn_version,
        "--route-log", str(route_log),
    ]


def _secret_in(values):
    for value in values:
        lowered = value.lower()
        if any(marker in lowered for marker in _SECRET_MARKERS):
            return True
    return False


def codex_block(command, args):
    body = "\n".join([
        BEGIN,
        f"[mcp_servers.{SERVER_NAME}]",
        f"command = {_toml_string(command)}",
        f"args = {_toml_array(args)}",
        "enabled = true",
        f"enabled_tools = {_toml_array(BRIDGE_TOOLS)}",
        END,
        "",
    ])
    return body


def _strip_codex_block(text):
    lines = text.splitlines(keepends=True)
    kept = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == BEGIN:
            end = index + 1
            while end < len(lines) and lines[end].strip() != END:
                end += 1
            if end >= len(lines):
                raise RegistrationError("malformed_config")
            if kept and kept[-1].strip() == "":
                kept.pop()
            index = end + 1
            continue
        kept.append(lines[index])
        index += 1
    return "".join(kept)


def _parse_toml(text):
    try:
        loaded = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise RegistrationError("malformed_config") from None
    if not isinstance(loaded, dict):
        raise RegistrationError("malformed_config")
    return loaded


def _server_snapshot(loaded):
    servers = loaded.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        raise RegistrationError("malformed_config")
    snapshot = {}
    for name, item in servers.items():
        if name == SERVER_NAME:
            continue
        if not isinstance(item, dict):
            raise RegistrationError("malformed_config")
        snapshot[name] = {
            "command": item.get("command"),
            "args": item.get("args"),
            "url": item.get("url"),
            "enabled": item.get("enabled"),
        }
    return snapshot


def _our_codex_server(loaded):
    servers = loaded.get("mcp_servers") or {}
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return None
    item = servers[SERVER_NAME]
    if not isinstance(item, dict):
        raise RegistrationError("malformed_config")
    return item


def _same_server(item, command, args):
    if not isinstance(item, dict):
        return False
    tools = item.get("enabled_tools")
    return (
        item.get("command") == command
        and list(item.get("args") or []) == list(args)
        and item.get("enabled") is True
        and list(tools or []) == list(BRIDGE_TOOLS)
    )


def render_codex(text, command, args):
    if _secret_in([command, *args]):
        raise RegistrationError("secret_argument")
    original = _parse_toml(text) if text.strip() else {}
    before = _server_snapshot(original) if text.strip() else {}
    current = _our_codex_server(original) if text.strip() else None
    if current is not None and _same_server(current, command, args):
        return text, False
    base = _strip_codex_block(text)
    if base and not base.endswith("\n"):
        base += "\n"
    if base and not base.endswith("\n\n"):
        base += "\n"
    updated = base + codex_block(command, args)
    loaded = _parse_toml(updated)
    if _server_snapshot(loaded) != before:
        raise RegistrationError("unrelated_server_changed")
    item = _our_codex_server(loaded)
    if not _same_server(item, command, args):
        raise RegistrationError("malformed_config")
    return updated, True


def disable_codex(text):
    original = _parse_toml(text) if text.strip() else {}
    before = _server_snapshot(original) if text.strip() else {}
    if text.strip() and _our_codex_server(original) is None and BEGIN not in text:
        return text, False
    updated = _strip_codex_block(text)
    loaded = _parse_toml(updated) if updated.strip() else {}
    if updated.strip() and _server_snapshot(loaded) != before:
        raise RegistrationError("unrelated_server_changed")
    if _our_codex_server(loaded) is not None:
        raise RegistrationError("malformed_config")
    return updated, updated != text


def _parse_cursor(text):
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        raise RegistrationError("malformed_config") from None
    if not isinstance(loaded, dict) or not isinstance(loaded.get("mcpServers"), dict):
        raise RegistrationError("malformed_config")
    return loaded


def _cursor_snapshot(loaded):
    snapshot = {}
    for name, item in loaded["mcpServers"].items():
        if name == SERVER_NAME:
            continue
        if not isinstance(item, dict):
            raise RegistrationError("malformed_config")
        snapshot[name] = dict(item)
    return snapshot


def render_cursor(text, command, args):
    if _secret_in([command, *args]):
        raise RegistrationError("secret_argument")
    loaded = _parse_cursor(text)
    before = _cursor_snapshot(loaded)
    current = loaded["mcpServers"].get(SERVER_NAME)
    desired = {"command": command, "args": list(args)}
    if current == desired:
        return text, False
    loaded["mcpServers"][SERVER_NAME] = desired
    if _cursor_snapshot(loaded) != before:
        raise RegistrationError("unrelated_server_changed")
    updated = json.dumps(loaded, indent=2) + "\n"
    return updated, True


def disable_cursor(text):
    loaded = _parse_cursor(text)
    before = _cursor_snapshot(loaded)
    if SERVER_NAME not in loaded["mcpServers"]:
        return text, False
    del loaded["mcpServers"][SERVER_NAME]
    if _cursor_snapshot(loaded) != before:
        raise RegistrationError("unrelated_server_changed")
    return json.dumps(loaded, indent=2) + "\n", True


def _read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise RegistrationError("malformed_config") from None


def _atomic_write(path: Path, text):
    if not path.parent.is_dir():
        raise RegistrationError("config_parent_missing")
    temporary = path.with_name(path.name + ".jev-registration-tmp")
    mode = path.stat().st_mode if path.exists() else 0o600
    data = text.encode("utf-8")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode & 0o777)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise RegistrationError("config_unwritable") from None


def inspect_release(release_root: Path, harness: str, expect_release: str | None):
    """Read-only gate. Does not construct ReleaseStore (that chmod's the root)."""

    root = Path(release_root).expanduser()
    pointer = root / "current-release.json"
    if not pointer.is_file() or pointer.is_symlink():
        raise RegistrationError("no_current_release")
    try:
        current = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RegistrationError("invalid_current_pointer") from None
    if not isinstance(current, dict) or current.get("schema_version") != 1:
        raise RegistrationError("invalid_current_pointer")
    release_id = current.get("release_id")
    if not isinstance(release_id, str) or len(release_id) != 64 or any(c not in "0123456789abcdef" for c in release_id):
        raise RegistrationError("invalid_current_pointer")
    if expect_release:
        if expect_release != release_id and not release_id.startswith(expect_release):
            raise RegistrationError("wrong_release")
        if len(expect_release) < 8:
            raise RegistrationError("wrong_release")
    manifest_path = root / "releases" / release_id / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise RegistrationError("missing_or_invalid_release")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RegistrationError("missing_or_invalid_release") from None
    if not isinstance(manifest, dict) or hashlib.sha256(canonical_bytes(manifest)).hexdigest() != release_id:
        raise RegistrationError("release_manifest_tampered")
    if manifest.get("schema_version") not in {1, 2}:
        raise RegistrationError("invalid_release_schema")
    if manifest.get("schema_version") == 2 and manifest.get("activation") not in {"shadow", "selection", "delivery"}:
        raise RegistrationError("invalid_release_schema")
    profiles = manifest.get("profiles")
    if not isinstance(profiles, list) or not all(isinstance(item, str) for item in profiles):
        raise RegistrationError("release_profile_missing")
    selected = harness if harness in profiles else PROFILE_HARNESSES.get(harness)
    if selected not in profiles:
        raise RegistrationError("release_profile_missing")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise RegistrationError("missing_or_invalid_release")
    profile_item = files.get(f"profile:{selected}")
    if not isinstance(profile_item, dict):
        raise RegistrationError("release_profile_missing")
    _bound_file(root, profile_item, kind="profile")
    try:
        profile = json.loads(Path(profile_item["path"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RegistrationError("release_profile_harness_mismatch") from None
    if not isinstance(profile, dict) or profile.get("harness") != selected:
        raise RegistrationError("release_profile_harness_mismatch")
    bound = files.get("capability_manifest")
    if bound is None:
        raise RegistrationError("capability_manifest_missing")
    if not isinstance(bound, dict):
        raise RegistrationError("capability_manifest_invalid")
    capability_path = _bound_file(root, bound, kind="capability")
    expected_hash = bound.get("content_hash")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise RegistrationError("capability_manifest_invalid")
    loaded = _load_capability(capability_path)
    if loaded is None or loaded.content_hash != expected_hash:
        raise RegistrationError("capability_manifest_invalid")
    from jev_skill_advisor.notion_host_transport import CLI_CAPABILITY_ID, CLI_SERVER, CLI_FETCH_SCHEMA
    if set(loaded.entries) != {CLI_CAPABILITY_ID}:
        raise RegistrationError("wrong_capability_route")
    card = loaded.entries[CLI_CAPABILITY_ID]
    if (card.writes or card.server != CLI_SERVER or card.operation != "notion-fetch"
            or card.schema_hash != schema_digest(CLI_FETCH_SCHEMA)):
        raise RegistrationError("wrong_capability_route")
    return {"release_id": release_id, "profile_harness": selected, "release_prefix": release_id[:12]}


def _bound_file(root: Path, item, *, kind):
    path_text = item.get("path")
    digest = item.get("sha256")
    code = "capability_manifest_invalid" if kind == "capability" else "release_file_tampered"
    if not isinstance(path_text, str) or not isinstance(digest, str):
        raise RegistrationError(code)
    path = Path(path_text)
    if path.is_symlink() or not path.is_file():
        raise RegistrationError(code)
    inputs = (root / "release-inputs").resolve()
    resolved = path.resolve()
    if inputs not in resolved.parents:
        raise RegistrationError(code)
    try:
        actual = _sha256(path)
    except OSError:
        raise RegistrationError(code) from None
    if actual != digest:
        raise RegistrationError(code)
    return path


def inspect_executable(executable: Path):
    path = Path(executable).expanduser()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RegistrationError("executable_missing")
    try:
        completed = subprocess.run(
            [str(path), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RegistrationError("executable_rejected") from None
    help_text = (completed.stdout or "") + (completed.stderr or "")
    if any(flag not in help_text for flag in (
        "--release-root", "--harness", "--expected-release", "--notion-cli-path", "--notion-cli-version",
    )):
        raise RegistrationError("executable_rejected")
    match = _TRANSPORT_CHOICES.search(help_text)
    choices = tuple(part.strip() for part in match.group(1).split(",")) if match else ()
    if "cli" not in choices:
        raise RegistrationError("portable_transport_unavailable")
    return {"executable": str(path), "portable_transport": "cli"}


def inspect_ntn(path: Path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise RegistrationError("ntn_executable_missing")
    try:
        result = subprocess.run([str(path), "--version"], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        raise RegistrationError("ntn_version_unverified") from None
    if result.returncode != 0 or result.stdout.strip() != f"ntn {PINNED_NTN_VERSION}":
        raise RegistrationError("ntn_version_mismatch")
    return str(path)


def workspace_matches(payload, expected):
    if not isinstance(payload, dict) or payload.get("object") != "user" or payload.get("type") != "bot":
        return False
    bot = payload.get("bot")
    if not isinstance(bot, dict):
        return False
    found = bot.get("workspace_id")
    return isinstance(found, str) and isinstance(expected, str) and found.lower() == expected.lower()


def inspect_workspace(runner, expected):
    try:
        result = runner(["ntn", "whoami", "--json"])
    except Exception:
        raise RegistrationError("workspace_unverified") from None
    raw = getattr(result, "stdout", b"")
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > 65536:
        raise RegistrationError("workspace_unverified")
    if getattr(result, "returncode", 1) != 0:
        raise RegistrationError("workspace_unverified")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise RegistrationError("workspace_unverified") from None
    if not workspace_matches(payload, expected):
        raise RegistrationError("workspace_mismatch")
    return "match"


def default_whoami(argv):
    if argv != ["ntn", "whoami", "--json"]:
        raise RegistrationError("workspace_unverified")
    try:
        return subprocess.run(argv, check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise RegistrationError("workspace_unverified") from None


def declared_whoami(token):
    def run(argv):
        if argv != ["ntn", "whoami", "--json"]:
            raise RegistrationError("workspace_unverified")
        try:
            return subprocess.run(
                argv, check=False, capture_output=True, timeout=10,
                env={**os.environ, "NOTION_API_TOKEN": token},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RegistrationError("workspace_unverified") from None
    return run


def discover_tools(argv, timeout=5):
    if not argv or _secret_in(argv):
        raise RegistrationError("tool_discovery_failed")
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "jev-registration", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    payload = "".join(json.dumps(item) + "\n" for item in messages).encode("utf-8")
    try:
        completed = subprocess.run(argv, input=payload, check=False, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise RegistrationError("tool_discovery_failed") from None
    tools = []
    fetch_schema = None
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            raise RegistrationError("tool_discovery_failed") from None
        result = message.get("result") if isinstance(message, dict) else None
        listed = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(listed, list):
            continue
        for tool in listed:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                raise RegistrationError("tool_discovery_failed")
            tools.append(tool["name"])
            if tool["name"] == "notion-fetch":
                fetch_schema = tool.get("inputSchema")
    if set(tools) != set(BRIDGE_TOOLS) or len(tools) != len(BRIDGE_TOOLS):
        raise RegistrationError("tool_set_mismatch")
    live = schema_digest(fetch_schema) if isinstance(fetch_schema, dict) else ""
    return {"tools": list(BRIDGE_TOOLS), "notion_fetch_schema_digest": live}


def _base_report(harness, **fields):
    report = {
        "status": "refused",
        "reason": "refused",
        "harness": harness,
        "server": SERVER_NAME,
        "transport": "stdio",
        "tools": list(BRIDGE_TOOLS),
        "applied": False,
        "installed": False,
        "workspace": "skipped",
        "schema_digest": "skipped",
        "pi_mcp_client": None,
        "profile_harness": None,
        "release_prefix": None,
        "portable_transport": None,
        "config": None,
        "executable": None,
        "command": None,
        "args": None,
    }
    report.update(fields)
    return report


def _config_text(path: Path):
    if not path.exists():
        return ""
    if path.is_symlink() or not path.is_file():
        raise RegistrationError("malformed_config")
    return _read_text(path)


def assess(harness, *, release_root, config_path, executable, host, actual_host, events_path,
           route_log, expect_release=None, whoami_runner=None, discover_argv=None,
           expect_schema_digest=None, pi_client=None, expected_workspace_id=None,
           credential_file=None, workspace_file=None, ntn_path=None, apply=False, disable=False):
    if harness not in HARNESSES:
        return _base_report(harness, reason="unsupported_harness")
    report = _base_report(harness, config=None if config_path is None else str(config_path))
    if harness == "pi":
        state = pi_client if pi_client is not None else pi_client_state()
        report["pi_mcp_client"] = state
        report["status"] = "blocked"
        report["reason"] = "pi_mcp_client_missing" if state == "absent" else "pi_adapter_present_unwired"
        try:
            release = inspect_release(release_root, "pi", expect_release)
            report["profile_harness"] = release["profile_harness"]
            report["release_prefix"] = release["release_prefix"]
            report["release_reason"] = "ok"
        except RegistrationError as exc:
            report["release_reason"] = exc.code
        if discover_argv:
            try:
                discovered = discover_tools(discover_argv)
            except RegistrationError as exc:
                report["discovery"] = exc.code
                return report
            report["discovery"] = "match"
            report["tools"] = discovered["tools"]
            report["schema_digest"] = compare_schema_digest(
                expect_schema_digest, discovered["notion_fetch_schema_digest"],
            )
        return report
    if disable:
        if config_path is None:
            report["status"] = "absent"
            report["reason"] = "ok"
            return report
        path = Path(config_path)
        try:
            if not path.exists():
                report["status"] = "absent"
                report["reason"] = "ok"
                return report
            text = _config_text(path)
            updated, changed = (disable_codex if harness == "codex" else disable_cursor)(text)
            if not changed:
                report["status"] = "absent"
                report["reason"] = "ok"
                return report
            if apply:
                _atomic_write(path, updated)
                report["applied"] = True
            report["status"] = "disabled"
            report["reason"] = "ok"
        except RegistrationError as exc:
            report["reason"] = exc.code
        return report
    if host != actual_host or not host:
        report["reason"] = "host_mismatch"
        return report
    try:
        if config_path is not None and Path(config_path).exists():
            text = _config_text(Path(config_path))
            if harness == "codex":
                _parse_toml(text)
            else:
                _parse_cursor(text)
        release = inspect_release(release_root, harness, expect_release)
        binary = inspect_executable(executable)
        checked_ntn = inspect_ntn(ntn_path or default_ntn_path())
    except RegistrationError as exc:
        report["reason"] = exc.code
        return report
    report["profile_harness"] = release["profile_harness"]
    report["release_prefix"] = release["release_prefix"]
    report["portable_transport"] = binary["portable_transport"]
    report["executable"] = binary["executable"]
    args = bridge_args(Path(release_root).expanduser().resolve(), release["release_id"], host, harness,
                       events_path, route_log, checked_ntn, PINNED_NTN_VERSION)
    try:
        if credential_file is None or workspace_file is None:
            raise RegistrationError("identity_files_missing")
        from run_bridge import LauncherError, load_identity
        token, pinned_workspace_id = load_identity(Path(credential_file), Path(workspace_file))
        if expected_workspace_id is not None and expected_workspace_id != pinned_workspace_id:
            raise RegistrationError("workspace_mismatch")
        report["workspace"] = inspect_workspace(whoami_runner or declared_whoami(token), pinned_workspace_id)
    except RegistrationError as exc:
        report["reason"] = exc.code
        report["workspace"] = "mismatch" if exc.code == "workspace_mismatch" else "unverified"
        return report
    except LauncherError:
        report["reason"] = "identity_files_invalid"
        report["workspace"] = "unverified"
        return report
    launcher = Path(__file__).with_name("run_bridge.py")
    command = sys.executable
    args = [
        str(launcher), "--credential-file", str(credential_file),
        "--workspace-file", str(workspace_file),
        "--executable", binary["executable"], "--", *args,
    ]
    report["command"] = command
    report["args"] = args
    if discover_argv:
        try:
            discovered = discover_tools(discover_argv)
        except RegistrationError as exc:
            report["reason"] = exc.code
            return report
        report["schema_digest"] = compare_schema_digest(expect_schema_digest, discovered["notion_fetch_schema_digest"])
        report["tools"] = discovered["tools"]
    if config_path is None:
        report["status"] = "ready"
        report["reason"] = "ok"
        return report
    path = Path(config_path)
    try:
        text = _config_text(path) if path.exists() else ""
        if harness == "codex":
            if not text.strip():
                text = ""
            updated, changed = render_codex(text, command, args)
        else:
            updated, changed = render_cursor(text or '{"mcpServers":{}}\n', command, args)
        if not changed:
            report["status"] = "installed"
            report["reason"] = "ok"
            report["installed"] = True
            return report
        if not apply:
            report["status"] = "drift" if path.exists() and _entry_present(harness, text) else "ready"
            report["reason"] = "ok" if report["status"] == "ready" else "drift"
            report["installed"] = report["status"] == "drift"
            return report
        if not path.exists():
            if not path.parent.is_dir():
                raise RegistrationError("config_parent_missing")
        _atomic_write(path, updated)
    except RegistrationError as exc:
        report["status"] = "refused"
        report["reason"] = exc.code
        report["applied"] = False
        return report
    report["status"] = "installed"
    report["reason"] = "ok"
    report["applied"] = True
    report["installed"] = True
    return report


def _entry_present(harness, text):
    if not text.strip():
        return False
    if harness == "codex":
        loaded = _parse_toml(text)
        return _our_codex_server(loaded) is not None
    loaded = _parse_cursor(text)
    return SERVER_NAME in loaded["mcpServers"]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jev-bridge-registration")
    parser.add_argument("action", choices=("check", "install", "disable"))
    parser.add_argument("--harness", choices=HARNESSES, required=True)
    parser.add_argument("--release-root", type=Path, default=default_release_root())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--executable", type=Path, default=default_executable())
    parser.add_argument("--ntn-path", type=Path, default=default_ntn_path())
    parser.add_argument("--host", default=local_host())
    parser.add_argument("--expect-release")
    parser.add_argument("--credential-file", type=Path, default=default_identity_file("credential.env"))
    parser.add_argument("--workspace-file", type=Path, default=default_identity_file("workspace.json"))
    args = parser.parse_args(argv)
    config = args.config or default_config(args.harness)
    report = assess(
        args.harness,
        release_root=args.release_root,
        config_path=config,
        executable=args.executable,
        ntn_path=args.ntn_path,
        host=args.host,
        actual_host=local_host(),
        events_path=default_state_file(args.harness, "capability-events.jsonl"),
        route_log=default_state_file(args.harness, "notion-routes.jsonl"),
        expect_release=args.expect_release,
        credential_file=args.credential_file,
        workspace_file=args.workspace_file,
        apply=args.action in {"install", "disable"},
        disable=args.action == "disable",
    )
    # The diagnostic record is configuration-only. No token, page content, or
    # workspace identity is included, even when the environment supplied one.
    print(json.dumps(report, sort_keys=True))
    return 0 if report["reason"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
