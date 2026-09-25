#!/usr/bin/env python3
"""Print the content-free capability route aggregate. This command only reads."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jev_skill_advisor.capability_observability import summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capability-route-report")
    parser.add_argument("events", nargs="+", type=Path)
    parser.add_argument("--since-hours", type=float, default=24)
    parser.add_argument("--exclude-session-id", action="append", default=[])
    parser.add_argument("--expected", action="append", default=None)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print the whole summary. The default prints the window and invocation_routes only.",
    )
    args = parser.parse_args(argv)
    try:
        report = summary(
            args.events,
            since_hours=args.since_hours,
            expected=args.expected,
            exclude_session_ids=args.exclude_session_id,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.full:
        payload = report
    else:
        payload = {
            "window_start": report["window_start"],
            "window_end": report["window_end"],
            "invocation_routes": report["invocation_routes"],
        }
    json.dump(payload, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
