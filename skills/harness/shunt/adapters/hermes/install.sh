#!/usr/bin/env bash
# Install Hermes shunt pre_tool_call into ~/.hermes/config.yaml (idempotent append).
# Does not remove unrelated hooks. Prefer fail_closed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOOK="$ROOT/adapters/hermes/pre_tool_call.sh"
chmod +x "$HOOK" "$ROOT/adapters/_common/shunt-hook.sh"
CFG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"
mkdir -p "$(dirname "$CFG")"
export HERMES_CONFIG="$CFG"
export SHUNT_HOOK="$HOOK"
python3 <<'PY'
import os, re
from pathlib import Path
cfg = Path(os.environ["HERMES_CONFIG"])
hook = os.environ["SHUNT_HOOK"]
marker = "shunt WP"
block = (
    f"\n# --- {marker} (do not remove unrelated hooks) ---\n"
    f"hooks:\n"
    f"  pre_tool_call:\n"
    f"    - command: \"{hook}\"\n"
    f"      timeout: 10\n"
    f"      fail_closed: true\n"
)
text = cfg.read_text(encoding="utf-8") if cfg.is_file() else ""
if hook in text and "pre_tool_call" in text:
    print(f"Hermes shunt pre_tool_call already present in {cfg}")
else:
    # If hooks.pre_tool_call exists without our command, append under a second hooks key
    # is invalid YAML merge — prefer appending a clearly marked block Hermes accepts as
    # last-wins for duplicate keys in some loaders; safer: inject into existing list.
    if re.search(r"(?m)^hooks:\s*$", text) and "pre_tool_call" in text:
        # Append command entry after first pre_tool_call: list if not present
        if hook not in text:
            text = text.rstrip() + (
                f"\n    - command: \"{hook}\"\n"
                f"      timeout: 10\n"
                f"      fail_closed: true\n"
            )
            cfg.write_text(text + "\n", encoding="utf-8")
            print(f"Appended shunt command under existing hooks.pre_tool_call in {cfg}")
        else:
            print(f"Hermes config already references shunt hook: {cfg}")
    else:
        cfg.write_text(text.rstrip() + block, encoding="utf-8")
        print(f"Installed Hermes shunt gate (HARD pre_tool_call) into {cfg}")
print("Approve on first run via `hermes hooks` or HERMES_ACCEPT_HOOKS=1 for CI.")
PY
