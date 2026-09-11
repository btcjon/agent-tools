"""shunt CLI — bulk-read / doctor / install / uninstall."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shunt import __version__
from shunt.agent_read import decide_agent_read
from shunt.bulk_read import bulk_read
from shunt.install import apply_install, apply_uninstall, doctor_report


def cmd_doctor(_: argparse.Namespace) -> int:
    return doctor_report()


def cmd_bulk_read(args: argparse.Namespace) -> int:
    path = args.path
    content = None
    if Path(path).is_file():
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    result = bulk_read(path, content=content, offset=args.offset, limit=args.limit)
    payload = {
        "ok": result.ok,
        "stub": result.stub,
        "model": result.model,
        "detail": result.detail,
        "content_hash": result.content_hash,
        "guidance": result.guidance,
        "http_status": result.http_status,
        "gate": {
            "allow": result.gate.allow,
            "reason": result.gate.reason,
            "path": result.gate.path,
            "lines": result.gate.lines,
            "bytes": result.gate.bytes,
        },
        "points": result.points,
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.ok else 2


def cmd_check_read(args: argparse.Namespace) -> int:
    """Gate an agent full-read; exit 0 allow, 2 deny. JSON on stdout."""
    result = decide_agent_read(args.path, offset=args.offset, limit=args.limit)
    payload = {
        "allow": result.allow,
        "reason": result.reason,
        "path": result.path,
        "lines": result.lines,
        "bytes": result.bytes,
        "message": result.message,
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.allow else 2


def cmd_install(args: argparse.Namespace) -> int:
    return apply_install(dry_run=bool(args.dry_run))


def cmd_uninstall(args: argparse.Namespace) -> int:
    return apply_uninstall(dry_run=bool(args.dry_run))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shunt",
        description="Cheap bulk-read gate (OpenRouter gemini-3.8-flash)",
    )
    p.add_argument("--version", action="version", version=f"shunt {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="Capability report (config, adapters, registrations)")
    d.set_defaults(func=cmd_doctor)

    b = sub.add_parser("bulk-read", help="Gate + OpenRouter bulk-read (points only)")
    b.add_argument("path", help="File path to evaluate")
    b.add_argument("--offset", type=int, default=None)
    b.add_argument("--limit", type=int, default=None)
    b.set_defaults(func=cmd_bulk_read)

    c = sub.add_parser("check-read", help="Allow/deny agent full-read (hooks use this)")
    c.add_argument("path", help="File path the agent wants to read")
    c.add_argument("--offset", type=int, default=None)
    c.add_argument("--limit", type=int, default=None)
    c.set_defaults(func=cmd_check_read)

    i = sub.add_parser("install", help="Backup + merge shunt adapter hooks into harness configs")
    i.add_argument("--dry-run", action="store_true", help="List targets only; do not mutate")
    i.set_defaults(func=cmd_install)

    u = sub.add_parser("uninstall", help="Remove shunt-managed hooks; preserve unrelated hooks")
    u.add_argument("--dry-run", action="store_true", help="List removals only; do not mutate")
    u.set_defaults(func=cmd_uninstall)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
