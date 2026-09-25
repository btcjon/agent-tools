"""Fail closed at every bundle launch if Python or the active release drifted."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


class GuardError(Exception):
    pass


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def check_python(bundle: Path, manifest: dict) -> None:
    record = manifest.get("base_python")
    if not isinstance(record, dict):
        raise GuardError("python_unpinned")
    actual = (bundle / "bin" / "python").resolve()
    if str(actual) != record.get("path") or not actual.is_file() or _digest(actual) != record.get("sha256"):
        raise GuardError("python_mismatch")
    lib_path = record.get("libpython_path")
    lib_hash = record.get("libpython_sha256")
    if (lib_path is None) != (lib_hash is None):
        raise GuardError("python_unpinned")
    if lib_path is not None:
        library = Path(lib_path).resolve()
        if str(library) != lib_path or not library.is_file() or _digest(library) != lib_hash:
            raise GuardError("python_mismatch")


def bind_release(args: list[str], release_id: str) -> list[str]:
    if not isinstance(release_id, str) or len(release_id) != 64:
        raise GuardError("release_unpinned")
    if "--release-root" not in args:
        if "--help" in args or "-h" in args:
            return args
        raise GuardError("release_root_missing")
    index = args.index("--release-root")
    if index + 1 >= len(args):
        raise GuardError("release_root_missing")
    root = Path(args[index + 1])
    if not root.is_absolute() or not root.is_dir():
        raise GuardError("release_root_invalid")
    try:
        active = json.loads((root / "current-release.json").read_text())
    except (OSError, ValueError):
        raise GuardError("release_pointer_invalid") from None
    if active.get("release_id") != release_id:
        raise GuardError("release_mismatch")
    if "--expected-release" in args:
        expected = args.index("--expected-release")
        if expected + 1 >= len(args) or args[expected + 1] != release_id:
            raise GuardError("expected_release_mismatch")
        return args
    return [*args, "--expected-release", release_id]


def main(argv: list[str] | None = None) -> int:
    bundle = Path(__file__).resolve().parent
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        manifest = json.loads((bundle / "bundle-manifest.json").read_text())
        check_python(bundle, manifest)
        args = bind_release(args, manifest.get("release_id"))
    except (OSError, ValueError, GuardError) as exc:
        reason = str(exc) if isinstance(exc, GuardError) else "runtime_guard_invalid"
        print(f"jev-runtime-guard: {reason}", file=sys.stderr)
        return 1
    python = bundle / "bin" / "python"
    os.execv(str(python), [str(python), "-I", "-s", "-m", "jev_skill_advisor.mcp_server", *args])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
