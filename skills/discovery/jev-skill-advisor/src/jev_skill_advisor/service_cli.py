from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .profile import load_profile, ProfileError
from .protocol import ProtocolError, error
from .service import SkillAdvisorService
from .context import native_fallback, prepare_context, validate_prepare_request
import sqlite3


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-advisor-service")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("command", choices=("suggest", "read", "report-outcome", "prepare-context"))
    args = parser.parse_args(argv)
    try:
        value = json.load(sys.stdin)
        if args.command == "prepare-context":
            value = validate_prepare_request(value)
        try:
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
