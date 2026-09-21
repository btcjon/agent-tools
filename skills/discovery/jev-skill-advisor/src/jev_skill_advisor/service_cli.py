from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .profile import load_profile, ProfileError
from .protocol import ProtocolError, error
from .service import SkillAdvisorService
from .context import native_fallback, prepare_context, validate_prepare_request
from .release import ReleaseStore
import sqlite3


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-advisor-service")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--release-root", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--harness")
    parser.add_argument("command", choices=("suggest", "read", "report-outcome", "prepare-context"))
    args = parser.parse_args(argv)
    try:
        value = json.load(sys.stdin)
        if args.command == "prepare-context":
            value = validate_prepare_request(value)
        try:
            if args.release_root:
                harness = value.get("harness") if args.command == "prepare-context" else args.harness
                if not args.host or not harness:
                    raise ValueError("release_resolution_requires_host_and_harness")
                session_id = value.get("session_id")
                _, _, profile = ReleaseStore(args.release_root).resolve_profile(
                    host=args.host, harness=harness, session_id=session_id)
                service = SkillAdvisorService(profile)
            else:
                service = SkillAdvisorService(load_profile(args.config))
        except (OSError, RuntimeError, sqlite3.Error):
            if args.command == "prepare-context":
                result = native_fallback("advisor_initialization_failure")
                service = None
            else:
                raise
        if args.command == "prepare-context":
            if service is not None:
                result = prepare_context(service, **value)
        else:
            result = getattr(service, args.command.replace("-", "_"))(value)
    except (json.JSONDecodeError, ProtocolError, ProfileError, ValueError, OSError) as exc:
        result = error(type(exc).__name__.lower(), str(exc))
        code = 2
    else:
        code = 3 if result.get("status") in {"unavailable", "incomplete"} else 0
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return code

if __name__ == "__main__":
    raise SystemExit(main())
