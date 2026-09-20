from __future__ import annotations
import argparse, json, os, tempfile
from pathlib import Path
from .catalog_cli import build_catalog


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, sort_keys=True, indent=2); handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(*, warehouse, state_dir, profile_path, harness, credential_file=None):
    warehouse = Path(warehouse).resolve(); state = Path(state_dir).resolve()
    catalog = build_catalog(warehouse)
    catalog_path = state / "catalog.json"; atomic_json(catalog_path, catalog)
    eligible = [row["stable_id"] for row in catalog["entries"] if row["provider_disclosure_eligible"]]
    readable = [row["stable_id"] for row in catalog["entries"]]
    profile = {"config_version": 1, "profile_id": f"production-{catalog['catalog_hash'][:12]}", "harness": harness,
        "warehouse_root": str(warehouse), "catalog_path": str(catalog_path), "state_dir": str(state / "runtime"),
        "mode": "advisory", "provider_enabled": True, "read_enabled": True, "eligible_ids": readable,
        "read_allowlist": readable, "credential_env": "TYPESAFE_API_KEY", "deadline_s": 5,
        "max_calls": 32, "max_tokens": 200000, "receipt_ttl_s": 86400,
        "prompt_limit": 20, "provider_attempt_limit": 160}
    if credential_file:
        target = Path(credential_file).resolve()
        if not target.is_file(): raise FileNotFoundError("credential_file_not_found")
        profile["credential_file"] = str(target)
    atomic_json(profile_path, profile)
    return {"catalog_hash": catalog["catalog_hash"], "included": catalog["included_count"],
            "excluded": catalog["excluded_count"], "implicit_provider_eligible": len(eligible),
            "profile_path": str(Path(profile_path).resolve())}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-advisor-production")
    parser.add_argument("--warehouse", type=Path, required=True); parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True); parser.add_argument("--harness", required=True)
    parser.add_argument("--credential-file", type=Path)
    args = parser.parse_args(argv); result = prepare(warehouse=args.warehouse, state_dir=args.state_dir,
        profile_path=args.profile, harness=args.harness, credential_file=args.credential_file)
    print(json.dumps(result, sort_keys=True, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
