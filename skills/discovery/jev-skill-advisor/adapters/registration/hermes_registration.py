"""CAS-replace only the Hermes Jev MCP command, preserving all other YAML bytes.

Cooperating installers share a sidecar advisory lock and re-check the source
bytes immediately before the atomic replace. A writer that ignores the lock is
not excluded; a moved snapshot is refused instead.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

import yaml


class RegistrationError(Exception):
    pass


class _Snapshot:
    def __init__(self, data: bytes, mode: int, device: int, inode: int) -> None:
        self.data = data
        self.mode = mode
        self.device = device
        self.inode = inode

    def matches(self, other: "_Snapshot") -> bool:
        return (
            self.data == other.data
            and self.mode == other.mode
            and self.device == other.device
            and self.inode == other.inode
        )


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".jev-registration.lock")


def replace_command(data: bytes, old: str, new: str) -> bytes:
    if not old.startswith("/") or not new.startswith("/") or "\n" in old + new:
        raise RegistrationError("command_invalid")
    parsed = yaml.safe_load(data)
    servers = parsed.get("mcp_servers") if isinstance(parsed, dict) else None
    item = servers.get("jev-skill-advisor") if isinstance(servers, dict) else None
    if not isinstance(item, dict) or item.get("command") != old:
        raise RegistrationError("current_mismatch")
    start = data.find(b"  jev-skill-advisor:\n")
    if start < 0 or data.find(b"  jev-skill-advisor:\n", start + 1) >= 0:
        raise RegistrationError("section_invalid")
    line = b"    command: " + old.encode() + b"\n"
    marker_end = start + len(b"  jev-skill-advisor:\n")
    boundary = re.search(rb"\n  [^ \t\r\n]", data[marker_end:])
    end = len(data) if boundary is None else marker_end + boundary.start() + 1
    section = data[start:end]
    if section.count(line) != 1:
        raise RegistrationError("command_line_invalid")
    revised = data[:start] + section.replace(line, b"    command: " + new.encode() + b"\n") + data[end:]
    after = yaml.safe_load(revised)
    parsed["mcp_servers"]["jev-skill-advisor"]["command"] = new
    if after != parsed:
        raise RegistrationError("unrelated_change")
    return revised


def _read_snapshot(path: Path) -> _Snapshot:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RegistrationError("config_invalid") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RegistrationError("config_invalid")
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return _Snapshot(b"".join(chunks), stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino)
    finally:
        os.close(fd)


def _acquire_lock(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(_lock_path(path), flags, 0o600)
    except OSError as exc:
        raise RegistrationError("lock_invalid") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RegistrationError("lock_invalid")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short_write")
        view = view[written:]


def _commit_replace(path: Path, payload: bytes, before: _Snapshot, expected_sha: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".jev-mcp-", dir=path.parent)
    open_fd = fd
    replaced = False
    try:
        os.fchmod(fd, before.mode)
        _write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        open_fd = -1
        # Re-read under the advisory lock and refuse if the snapshot moved.
        # This does not stop a writer that ignores the lock.
        current = _read_snapshot(path)
        if digest(current.data) != expected_sha or not current.matches(before):
            raise RegistrationError("config_changed")
        os.replace(temporary, path)
        replaced = True
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if open_fd >= 0:
            os.close(open_fd)
        if not replaced:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def install(path: Path, old: str, new: str, expected_sha: str) -> dict:
    lock_fd = _acquire_lock(path)
    try:
        before = _read_snapshot(path)
        if digest(before.data) != expected_sha:
            raise RegistrationError("config_changed")
        after = replace_command(before.data, old, new)
        status = "unchanged" if after == before.data else "installed"
        if after == before.data:
            current = _read_snapshot(path)
            if digest(current.data) != expected_sha or not current.matches(before):
                raise RegistrationError("config_changed")
        else:
            _commit_replace(path, after, before, expected_sha)
            written = _read_snapshot(path)
            if written.data != after or written.mode != before.mode:
                raise RegistrationError("post_write_mismatch")
        return {
            "status": status,
            "before_sha256": digest(before.data),
            "after_sha256": digest(after),
        }
    finally:
        _release_lock(lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "install"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--old-command", required=True)
    parser.add_argument("--new-command", required=True)
    parser.add_argument("--expect-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.action == "install":
            result = install(args.config, args.old_command, args.new_command, args.expect_sha256)
        else:
            snapshot = _read_snapshot(args.config)
            if digest(snapshot.data) != args.expect_sha256:
                raise RegistrationError("config_changed")
            updated = replace_command(snapshot.data, args.old_command, args.new_command)
            result = {
                "status": "ready",
                "before_sha256": digest(snapshot.data),
                "after_sha256": digest(updated),
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RegistrationError) as exc:
        reason = str(exc) if isinstance(exc, RegistrationError) else "registration_invalid"
        print(json.dumps({"status": "refused", "reason": reason}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
