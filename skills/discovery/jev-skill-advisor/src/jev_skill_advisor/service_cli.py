from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .profile import load_profile, ProfileError
from .protocol import ProtocolError, error
from .service import SkillAdvisorService


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-advisor-service")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("command", choices=("suggest", "read", "report-outcome"))
    args = parser.parse_args(argv)
    try:
        value = json.load(sys.stdin)
        service = SkillAdvisorService(load_profile(args.config))
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
