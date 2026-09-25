"""Project an explicitly named shared Notion token to private host-local bridge files."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class ProvisionError(ValueError):
    pass


def _private_source(path: Path) -> str:
    if path.is_symlink():
        raise ProvisionError("source_invalid")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ProvisionError("source_invalid")
        if info.st_size > 1_000_000:
            raise ProvisionError("source_invalid")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ProvisionError("source_invalid") from None


def extract_token(source: str, name="NOTION_PAT") -> str:
    lines = [line.partition("=")[2].strip() for line in source.splitlines() if line.startswith(name + "=")]
    if len(lines) != 1:
        raise ProvisionError("credential_missing_or_duplicate")
    token = lines[0].strip('"\'')
    if not token or token != token.strip() or any(char.isspace() for char in token):
        raise ProvisionError("credential_invalid")
    return token


def _workspace_from_token(token: str) -> str:
    try:
        result = subprocess.run(
            ["ntn", "whoami", "--json"],
            env={**os.environ, "NOTION_API_TOKEN": token},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=15,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise ProvisionError("workspace_unverified") from None
    bot = payload.get("bot") if isinstance(payload, dict) else None
    identity = bot.get("workspace_id") if isinstance(bot, dict) else None
    if not isinstance(payload, dict) or payload.get("object") != "user" or payload.get("type") != "bot" or not isinstance(identity, str) or not _UUID.fullmatch(identity):
        raise ProvisionError("workspace_unverified")
    return identity


def _atomic_private(path: Path, data: bytes, *, replace: bool):
    if path.is_symlink() or (path.exists() and not replace):
        raise ProvisionError("target_exists")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise ProvisionError("target_parent_not_private")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".identity-", delete=False) as handle:
        temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jev-bridge-provision-identity")
    parser.add_argument("--shared-env", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--workspace-file", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    if args.credential_file == args.workspace_file:
        raise ProvisionError("targets_overlap")
    if not args.replace and (args.credential_file.exists() or args.workspace_file.exists()):
        raise ProvisionError("target_exists")
    token = extract_token(_private_source(args.shared_env))
    workspace = _workspace_from_token(token)
    _atomic_private(args.credential_file, f"NOTION_API_TOKEN={token}\n".encode(), replace=args.replace)
    _atomic_private(args.workspace_file, (json.dumps({"workspace_id": workspace}) + "\n").encode(), replace=args.replace)
    print(json.dumps({"status": "provisioned", "credential_mode": "0600", "workspace_mode": "0600", "credential_source": "shared_env"}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisionError as exc:
        raise SystemExit(exc.args[0]) from None
