"""Launch the host-local stdio bridge with one explicit Notion CLI identity.

Credential and workspace files are host-local, not synchronized or committed.
Only the named token crosses into the child environment; file contents are
never written to stdout or stderr.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class LauncherError(ValueError):
    pass


def _private_file(path: Path) -> bytes:
    if path.is_symlink():
        raise LauncherError("private_file_invalid")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise LauncherError("private_file_invalid")
        return path.read_bytes()
    except OSError:
        raise LauncherError("private_file_invalid") from None


def load_identity(credential_file: Path, workspace_file: Path) -> tuple[str, str]:
    raw = _private_file(credential_file)
    if len(raw) > 4096:
        raise LauncherError("credential_invalid")
    try:
        line = raw.decode("utf-8").strip()
    except UnicodeError:
        raise LauncherError("credential_invalid") from None
    if not line.startswith("NOTION_API_TOKEN=") or "\n" in line or "\r" in line:
        raise LauncherError("credential_invalid")
    token = line.partition("=")[2]
    if not token or token != token.strip() or any(char.isspace() for char in token):
        raise LauncherError("credential_invalid")
    raw_workspace = _private_file(workspace_file)
    if len(raw_workspace) > 4096:
        raise LauncherError("workspace_invalid")
    try:
        item = json.loads(raw_workspace)
    except (UnicodeError, json.JSONDecodeError):
        raise LauncherError("workspace_invalid") from None
    if not isinstance(item, dict) or set(item) != {"workspace_id"} or not isinstance(item["workspace_id"], str):
        raise LauncherError("workspace_invalid")
    workspace_id = item["workspace_id"]
    if not _UUID.fullmatch(workspace_id):
        raise LauncherError("workspace_invalid")
    return token, workspace_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jev-bridge-launcher")
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--workspace-file", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("server_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.executable.is_file() or not os.access(args.executable, os.X_OK):
        raise LauncherError("executable_invalid")
    token, workspace_id = load_identity(args.credential_file, args.workspace_file)
    env = os.environ.copy()
    env["NOTION_API_TOKEN"] = token
    env["NOTION_EXPECTED_WORKSPACE_ID"] = workspace_id
    env["NOTION_CREDENTIAL_SOURCE"] = "env"
    server_args = args.server_args[1:] if args.server_args[:1] == ["--"] else args.server_args
    child_args = [str(args.executable), *server_args]
    os.execve(str(args.executable), child_args, env)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LauncherError as exc:
        # Stable reason code only; never emit a token, file contents, or ID.
        raise SystemExit(exc.args[0]) from None
