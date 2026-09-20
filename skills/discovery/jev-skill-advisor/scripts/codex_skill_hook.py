#!/usr/bin/env python3
from pathlib import Path
import os
import sys

project=Path(__file__).resolve().parents[1]
try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    os.execv("/Users/jonbennett/.local/bin/uv",["uv","run","--project",str(project),"python",str(Path(__file__).resolve())])
sys.path.insert(0,str(project/"src"))
from jev_skill_advisor.codex_hook import main

raise SystemExit(main())
