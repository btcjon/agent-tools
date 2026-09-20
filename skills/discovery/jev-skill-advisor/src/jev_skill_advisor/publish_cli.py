from __future__ import annotations

import argparse
import json
from pathlib import Path

from .notion_publish import apply_plan, load_plan, receipt_status, verify_plan


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-library-publish")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    status = commands.add_parser("status"); status.add_argument("--plan-hash")
    verify = commands.add_parser("verify"); verify.add_argument("--cache-root", type=Path, required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--apply", action="store_true", required=True)
    apply.add_argument("--ack-plan-hash", required=True)
    apply.add_argument("--authorization-record", type=Path, required=True)
    args = parser.parse_args(argv)
    plan = load_plan(args.manifest)
    if args.command == "plan":
        result = plan
    elif args.command == "status":
        result = receipt_status(args.state_dir, args.plan_hash or plan["plan_hash"])
    elif args.command == "verify":
        result = verify_plan(plan, state_dir=args.state_dir, cache_root=args.cache_root)
    else:
        result = apply_plan(plan, ack_hash=args.ack_plan_hash, authorization_path=args.authorization_record, state_dir=args.state_dir)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
