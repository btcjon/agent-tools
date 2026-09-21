from __future__ import annotations
import argparse, json
from pathlib import Path

from .release import ReleaseStore, build_release


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-advisor-release")
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    builds = {}
    for activation in ("shadow", "selection", "delivery"):
        build = commands.add_parser(f"build-{activation}")
        build.add_argument("--snapshot-id", required=True); build.add_argument("--snapshot-root", type=Path, required=True)
        build.add_argument("--revision", required=True); build.add_argument("--evidence", action="append", default=[])
        builds[f"build-{activation}"] = activation
    activate = commands.add_parser("activate")
    activate.add_argument("release_id"); activate.add_argument("--expected-previous", default="")
    validate = commands.add_parser("validate"); validate.add_argument("release_id")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--host", required=True); resolve.add_argument("--harness", required=True); resolve.add_argument("--session", required=True)
    args = parser.parse_args(argv); store = ReleaseStore(args.root)
    if args.command in builds:
        evidence = {}
        for item in args.evidence:
            name, separator, value = item.partition("=")
            if not separator or not name or not value: raise ValueError("evidence_must_be_name_path")
            evidence[name] = Path(value)
        release_id, manifest = build_release(root=args.root, snapshot_id=args.snapshot_id,
            snapshot_root=args.snapshot_root, evidence=evidence, revision=args.revision,
            activation=builds[args.command])
        result = {"release_id": release_id, "manifest": manifest}
    elif args.command == "activate":
        store.activate(args.release_id, expected_previous=args.expected_previous or None)
        result = {"status": "activated", "release_id": args.release_id}
    elif args.command == "validate":
        result = {"release_id": args.release_id, "manifest": store.validate(args.release_id)}
    else:
        release_id, manifest = store.resolve(host=args.host, harness=args.harness, session_id=args.session)
        result = {"release_id": release_id, "manifest": manifest}
    print(json.dumps(result, sort_keys=True, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
