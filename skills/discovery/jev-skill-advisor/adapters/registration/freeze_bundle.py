"""Build or verify a read-only, host-local Jev MCP runtime from a pinned commit.

No checkout files or credentials enter the bundle. All dependency resolution is
offline and checked against the archived uv.lock hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

PACKAGE = Path("skills/discovery/jev-skill-advisor")
REVISION = "3fc6118"
RELEASE = "a528d001220eec155f1172f3b9b76793177ae2479dd5d054ae246c047e2e26bc"
BLOCKED_PATH_PARTS = ("CloudStorage", "Dropbox")
SECRET_NAMES = {"credential.env", "workspace.json", ".env"}


class BundleError(Exception):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str], *, cwd: Path | None = None, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            argv, cwd=cwd, input=input_bytes, capture_output=True, check=False, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BundleError("command_unavailable") from None
    if result.returncode:
        raise BundleError("offline_artifact_unavailable")
    return result.stdout


def validate_output_root(path: Path) -> Path:
    if not path.is_absolute() or len(path.parts) < 4 or any(part in path.parts for part in BLOCKED_PATH_PARTS):
        raise BundleError("unsafe_output_root")
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise BundleError("unsafe_output_root")
    if any(part in path.resolve().parts for part in BLOCKED_PATH_PARTS):
        raise BundleError("unsafe_output_root")
    return path


def export_source(repo: Path, revision: str, destination: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise BundleError("revision_invalid")
    archive = run(["git", "-C", str(repo), "archive", "--format=tar", revision, str(PACKAGE)])
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        members = tar.getmembers()
        prefix = str(PACKAGE) + "/"
        parents = {str(parent) for parent in PACKAGE.parents if str(parent) != "."}
        if not members or any(
            not (member.name == str(PACKAGE) or member.name in parents or member.name.startswith(prefix))
            or member.issym() or member.islnk() or member.name.startswith("/")
            or ".." in Path(member.name).parts
            for member in members
        ):
            raise BundleError("archive_invalid")
        tar.extractall(destination, filter="data")
    source = destination / PACKAGE
    if not (source / "uv.lock").is_file() or not (source / "pyproject.toml").is_file():
        raise BundleError("archive_invalid")
    return source


def entries(root: Path) -> dict[str, dict[str, str | int]]:
    out: dict[str, dict[str, str | int]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == "bundle-manifest.json":
            continue
        if path.name in SECRET_NAMES or path.suffix == ".pth":
            raise BundleError(f"unsafe_bundle_content:{relative}")
        if path.is_symlink():
            target = os.readlink(path)
            if any(part in target for part in BLOCKED_PATH_PARTS):
                raise BundleError(f"unsafe_bundle_content:{relative}")
            out[relative] = {"kind": "symlink", "target": target}
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            raise BundleError(f"unsafe_bundle_content:{relative}")
        data = path.read_bytes()
        if any(part.encode() in data for part in BLOCKED_PATH_PARTS):
            raise BundleError(f"unsafe_bundle_content:{relative}")
        if path.name == "direct_url.json":
            try:
                parsed = json.loads(data)
            except (ValueError, UnicodeError):
                raise BundleError(f"unsafe_bundle_content:{relative}") from None
            if parsed.get("dir_info", {}).get("editable"):
                raise BundleError(f"unsafe_bundle_content:{relative}")
        out[relative] = {"kind": "file", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return out


def verify(bundle: Path, *, require_readonly: bool = True) -> dict:
    if not bundle.is_dir() or bundle.is_symlink():
        raise BundleError("bundle_missing")
    manifest_path = bundle / "bundle-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise BundleError("manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError, UnicodeError):
        raise BundleError("manifest_invalid") from None
    if manifest.get("schema_version") != 1 or manifest.get("files") != entries(bundle):
        raise BundleError("manifest_mismatch")
    if not (bundle / "bin" / "python").is_file() or not (bundle / "bin" / "skill-advisor-mcp").is_file():
        raise BundleError("entrypoint_missing")
    if require_readonly:
        for path in (bundle, *bundle.rglob("*")):
            if not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) & 0o222:
                raise BundleError("bundle_writable")
    return manifest


def build(repo: Path, output_root: Path, *, revision: str, release: str, python: Path) -> tuple[Path, bool]:
    output_root = validate_output_root(output_root)
    if not re.fullmatch(r"[0-9a-f]{64}", release):
        raise BundleError("release_invalid")
    if not python.is_absolute() or not python.is_file() or any(
        part in python.resolve().parts for part in BLOCKED_PATH_PARTS
    ):
        raise BundleError("python_invalid")
    final = output_root / f"{release[:12]}-{revision}"
    if final.exists():
        manifest = verify(final)
        if manifest.get("source_revision") != revision or manifest.get("release_id") != release:
            raise BundleError("bundle_identity_mismatch")
        return final, False
    output_root.mkdir(parents=True, exist_ok=True)
    workspace = output_root / f".build-{os.getpid()}"
    if workspace.exists():
        raise BundleError("staging_exists")
    workspace.mkdir(mode=0o700)
    try:
        source = export_source(repo, revision, workspace)
        requirements = run(
            ["uv", "export", "--frozen", "--offline", "--no-dev", "--no-emit-project",
             "--extra", "mcp", "--format", "requirements.txt"], cwd=source,
        )
        if b"--hash=sha256:" not in requirements:
            raise BundleError("lock_hash_missing")
        requirements_path = workspace / "requirements.txt"
        requirements_path.write_bytes(requirements)
        wheels = workspace / "wheels"
        wheels.mkdir()
        run(["uv", "build", "--wheel", "--offline", str(source), "--out-dir", str(wheels)])
        built = list(wheels.glob("jev_skill_advisor-*.whl"))
        if len(built) != 1:
            raise BundleError("wheel_missing")
        bundle = workspace / "bundle"
        run(["uv", "venv", "--offline", "--python", str(python), str(bundle)])
        bundle_python = bundle / "bin" / "python"
        run(["uv", "pip", "install", "--offline", "--require-hashes", "--python",
             str(bundle_python), "-r", str(requirements_path)])
        run(["uv", "pip", "install", "--offline", "--no-deps", "--python",
             str(bundle_python), str(built[0])])
        for virtualenv_hook in bundle.glob("lib/python*/site-packages/_virtualenv.pth"):
            virtualenv_hook.unlink()
        shutil.copy2(source / "adapters" / "registration" / "run_bridge.py", bundle / "run_bridge.py")
        entrypoint = bundle / "bin" / "skill-advisor-mcp"
        entrypoint.write_text(
            '#!/bin/sh\nexec "$(dirname "$0")/python" -I -s -m jev_skill_advisor.mcp_server "$@"\n'
        )
        entrypoint.chmod(0o755)
        provenance = run(
            [str(bundle_python), "-I", "-s", "-c",
             "import jev_skill_advisor; print(jev_skill_advisor.__file__)"],
        ).decode().strip()
        if not provenance.startswith(str(bundle)):
            raise BundleError("import_provenance_invalid")
        manifest = {
            "schema_version": 1,
            "source_revision": revision,
            "release_id": release,
            "lock_sha256": digest(source / "uv.lock"),
            "wheel_sha256": digest(built[0]),
            "files": entries(bundle),
        }
        (bundle / "bundle-manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
        verify(bundle, require_readonly=False)
        os.replace(bundle, final)
        for path in sorted(final.rglob("*"), reverse=True):
            if path.is_symlink():
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(mode & ~0o222)
        final.chmod(0o555)
        verify(final)
        return final, True
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freeze-bundle")
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--revision", default=REVISION)
    parser.add_argument("--release", default=RELEASE)
    parser.add_argument("--python", type=Path, default=Path(sys._base_executable))
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            if args.bundle is None:
                raise BundleError("bundle_missing")
            manifest = verify(args.bundle)
            report = {"status": "verified", "bundle": str(args.bundle),
                      "release_id": manifest["release_id"], "files": len(manifest["files"])}
        else:
            if args.repo is None or args.output_root is None:
                raise BundleError("build_inputs_missing")
            bundle, changed = build(args.repo, args.output_root, revision=args.revision,
                                    release=args.release, python=args.python)
            report = {"status": "built" if changed else "unchanged", "bundle": str(bundle),
                      "release_id": args.release}
        print(json.dumps(report, sort_keys=True))
        return 0
    except BundleError as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
