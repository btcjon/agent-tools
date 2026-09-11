#!/usr/bin/env bash
# Merge-safe AGY install: add "shunt" key into ~/.gemini/config/hooks.json without removing herdr/etc.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
chmod +x "$ROOT/adapters/agy/pretooluse.sh" "$ROOT/adapters/_common/shunt-hook.sh"
HOOKS_JSON="${AGY_HOOKS_JSON:-$HOME/.gemini/config/hooks.json}"
mkdir -p "$(dirname "$HOOKS_JSON")"
export AGY_HOOKS_JSON="$HOOKS_JSON"
export SHUNT_SNIP
SHUNT_SNIP="$(sed "s|__SHUNT_ROOT__|$ROOT|g" "$ROOT/adapters/agy/hooks.snippet.json")"
python3 <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ["AGY_HOOKS_JSON"])
snip = json.loads(os.environ["SHUNT_SNIP"])
existing: dict = {}
if path.is_file():
    existing = json.loads(path.read_text(encoding="utf-8") or "{}")
if not isinstance(existing, dict):
    raise SystemExit(f"hooks.json is not an object: {path}")
existing["shunt"] = snip["shunt"]
path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
others = [k for k in existing if k != "shunt"]
print(f"Installed AGY shunt gate (HARD PreToolUse) into {path}")
print("Preserved other top-level hook groups:", ", ".join(others) or "(none)")
PY
