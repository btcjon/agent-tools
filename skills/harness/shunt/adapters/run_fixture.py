#!/usr/bin/env python3
"""Substitute __SAMPLE_OVERSIZED__ in a fixture and pipe to a hook script."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <hook-script> <fixture.json>", file=sys.stderr)
        return 2
    script = Path(sys.argv[1]).resolve()
    fixture = Path(sys.argv[2]).resolve()
    sample = fixture.parent / "sample_oversized.txt"
    if not sample.is_file():
        # fall back to cursor sample
        sample = ROOT / "adapters" / "cursor" / "tests" / "fixtures" / "sample_oversized.txt"
    text = fixture.read_text(encoding="utf-8").replace("__SAMPLE_OVERSIZED__", str(sample))
    # validate JSON
    json.loads(text)
    env = os.environ.copy()
    env["SHUNT_ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["SHUNT_INTERNAL"] = "1"
    if script.suffix in {".sh", ".bash"}:
        cmd = ["bash", str(script)]
    else:
        cmd = [sys.executable, str(script)]
    proc = subprocess.run(
        cmd,
        input=text,
        text=True,
        env=env,
        check=False,
    )
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
