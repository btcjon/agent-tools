"""Resume read-only Notion skill version inspection and prepare a pinned stage.

This does not activate a release or change a cache pointer. Run
``skill-library --config <printed config> stage`` after it completes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


class PreparationError(ValueError):
    pass


def _atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".stage-", delete=False) as handle:
        temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        json.dump(value, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _token(path: Path) -> str:
    if path.is_symlink():
        raise PreparationError("credential_file_invalid")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PreparationError("credential_file_invalid")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise PreparationError("credential_file_invalid") from None
    values = [line.partition("=")[2].strip().strip('"\'') for line in lines if line.startswith("NOTION_PAT=")]
    if len(values) != 1 or not values[0] or any(char.isspace() for char in values[0]):
        raise PreparationError("credential_invalid")
    return values[0]


def _inspect_version(identity: str, env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            ["ntn", "api", f"v1/ai/skills/{identity}"],
            env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=20,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise PreparationError("inspect_failed") from None
    if not isinstance(payload, dict) or payload.get("id") != identity or not isinstance(payload.get("version_id"), str) or not _HASH.fullmatch(payload["version_id"]):
        raise PreparationError("inspect_failed")
    return payload["version_id"]


def _ids(path: Path) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PreparationError("live_ids_invalid") from None
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) or not _UUID.fullmatch(item) for item in raw):
        raise PreparationError("live_ids_invalid")
    if len(raw) != len(set(raw)):
        raise PreparationError("live_ids_duplicate")
    return sorted(raw)


def _versions(path: Path, identities: set[str]) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PreparationError("checkpoint_invalid") from None
    if not isinstance(raw, dict) or not set(raw) <= identities or any(not isinstance(value, str) or not _HASH.fullmatch(value) for value in raw.values()):
        raise PreparationError("checkpoint_invalid")
    return raw


def _cache_key(identity: str, version: str) -> str:
    return hashlib.sha256(f"skill\0{identity}\0{version}".encode()).hexdigest()


def _reuse_cache(rows, prior: Path, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    reused = 0
    for row in rows:
        key = _cache_key(row["id"], row["version_id"])
        receipt = prior / f"{key}.json"
        archive = prior / f"{key}.tar.gz"
        if not receipt.is_file() or not archive.is_file() or receipt.is_symlink() or archive.is_symlink():
            continue
        try:
            metadata = json.loads(receipt.read_text(encoding="utf-8"))
            data = archive.read_bytes()
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PreparationError("prior_cache_invalid") from None
        if (metadata.get("kind"), metadata.get("id"), metadata.get("version_id"), metadata.get("bytes"), metadata.get("sha256")) != (
            "skill", row["id"], row["version_id"], len(data), hashlib.sha256(data).hexdigest(),
        ):
            raise PreparationError("prior_cache_invalid")
        for source in (receipt, archive):
            destination = target / source.name
            if destination.exists():
                if destination.read_bytes() != source.read_bytes():
                    raise PreparationError("target_cache_conflict")
            else:
                os.link(source, destination)
        reused += 1
    return reused


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="prepare-live-snapshot")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--shared-env", type=Path, required=True)
    parser.add_argument("--prior-cache", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.8)
    args = parser.parse_args(argv)
    if not 0.4 <= args.delay <= 10:
        raise PreparationError("invalid_delay")
    state = args.state_dir.resolve()
    identities = _ids(state / "live-ids.json")
    versions_path = state / "versions.json"
    versions = _versions(versions_path, set(identities))
    env = {**os.environ, "NOTION_API_TOKEN": _token(args.shared_env)}
    remaining = [identity for identity in identities if identity not in versions]
    if args.limit > 0:
        remaining = remaining[:args.limit]
    for identity in remaining:
        try:
            versions[identity] = _inspect_version(identity, env)
        except PreparationError as exc:
            print(json.dumps({"status": "incomplete", "reason": str(exc), "inspected": len(versions), "expected": len(identities)}))
            return 1
        _atomic_json(versions_path, versions)
        time.sleep(args.delay)
    if len(versions) != len(identities):
        print(json.dumps({"status": "incomplete", "inspected": len(versions), "expected": len(identities)}))
        return 0
    rows = [{"kind": "skill", "id": identity, "version_id": versions[identity]} for identity in identities]
    library = state / "library"
    config = state / "stage-config.json"
    _atomic_json(config, {"allowlist": rows, "state_dir": str(library)})
    reused = _reuse_cache(rows, args.prior_cache, library / "export-cache") if args.prior_cache else 0
    print(json.dumps({"status": "ready_to_stage", "count": len(rows), "reused_archives": reused, "config": str(config)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreparationError as exc:
        raise SystemExit(str(exc)) from None
