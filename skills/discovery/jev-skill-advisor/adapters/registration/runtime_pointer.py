"""Atomic, compare-and-swap activation of one host-local bridge runtime."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path


class PointerError(Exception):
    pass


def default_runtime_root() -> Path:
    return Path.home() / ".local" / "state" / "jev-skill-advisor" / "runtime"


def _safe_root(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise PointerError("runtime_root_invalid")
    resolved = root.resolve()
    if any(part in {"Dropbox", "CloudStorage"} for part in resolved.parts):
        raise PointerError("runtime_root_invalid")
    return resolved


def _safe_target(root: Path, target: Path) -> Path:
    target = Path(target)
    if not target.is_absolute() or target.is_symlink() or not target.is_dir():
        raise PointerError("runtime_target_invalid")
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise PointerError("runtime_target_outside_root") from None
    return target


def current_target(root: Path) -> Path | None:
    pointer = root / "current"
    if not pointer.exists() and not pointer.is_symlink():
        return None
    if not pointer.is_symlink():
        raise PointerError("current_not_symlink")
    raw = Path(os.readlink(pointer))
    if not raw.is_absolute():
        raw = pointer.parent / raw
    target = raw.resolve()
    if not target.is_dir():
        raise PointerError("current_broken")
    return target


def switch(root: Path, target: Path, expected: Path | None) -> dict:
    root = _safe_root(root)
    target = _safe_target(root, target)
    expected = _safe_target(root, expected) if expected is not None else None
    lock = root / ".pointer.lock"
    with lock.open("a+b") as handle:
        os.chmod(lock, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        before = current_target(root)
        if before != expected:
            raise PointerError("current_mismatch")
        if before == target:
            return {"status": "unchanged", "before": str(before), "after": str(target)}
        temporary = root / f".current-{os.getpid()}.tmp"
        if temporary.exists() or temporary.is_symlink():
            raise PointerError("temporary_exists")
        try:
            os.symlink(str(target), temporary)
            os.replace(temporary, root / "current")
            dirfd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if temporary.is_symlink():
                temporary.unlink()
        if current_target(root) != target:
            raise PointerError("post_switch_mismatch")
        return {"status": "switched", "before": None if before is None else str(before),
                "after": str(target)}


def remove(root: Path, expected: Path) -> dict:
    root = _safe_root(root)
    expected = _safe_target(root, expected)
    lock = root / ".pointer.lock"
    with lock.open("a+b") as handle:
        os.chmod(lock, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        before = current_target(root)
        if before != expected:
            raise PointerError("current_mismatch")
        (root / "current").unlink()
        dirfd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        if current_target(root) is not None:
            raise PointerError("post_remove_mismatch")
        return {"status": "removed", "before": str(before), "after": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-runtime-pointer")
    parser.add_argument("action", choices=("check", "switch", "remove"))
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    parser.add_argument("--target", type=Path)
    parser.add_argument("--expect-current", required=False)
    args = parser.parse_args(argv)
    try:
        root = _safe_root(args.runtime_root)
        if args.action == "check":
            target = current_target(root)
            result = {"status": "present" if target else "absent",
                      "target": None if target is None else str(target)}
        elif args.action == "remove":
            if args.expect_current is None or args.expect_current == "absent":
                raise PointerError("remove_inputs_missing")
            result = remove(root, Path(args.expect_current))
        else:
            if args.target is None or args.expect_current is None:
                raise PointerError("switch_inputs_missing")
            expected = None if args.expect_current == "absent" else Path(args.expect_current)
            result = switch(root, args.target, expected)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, PointerError) as exc:
        reason = str(exc) if isinstance(exc, PointerError) else "pointer_io_error"
        print(json.dumps({"status": "refused", "reason": reason}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
