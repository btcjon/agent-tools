#!/usr/bin/env bash
# Install Grok shunt PreToolUse hook:
#  1) ~/.grok/hooks/shunt-pretooluse.json  (global hooks dir)
#  2) [[hooks.PreToolUse]] into ~/.grok/config.toml (idempotent; survives JSON-only misses)
# Long-lived Grok panes must restart to pick up new hooks.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
chmod +x "$ROOT/adapters/grok/pretooluse.sh" "$ROOT/adapters/_common/shunt-hook.sh"
GROK_HOME_DIR="${GROK_HOME:-$HOME/.grok}"
DEST_DIR="$GROK_HOME_DIR/hooks"
mkdir -p "$DEST_DIR"
DEST="$DEST_DIR/shunt-pretooluse.json"
HOOK_CMD="$ROOT/adapters/grok/pretooluse.sh"
sed "s|__SHUNT_ROOT__|$ROOT|g" "$ROOT/adapters/grok/hooks.json" > "$DEST"
echo "Installed Grok shunt gate (HARD PreToolUse): $DEST"

CFG="$GROK_HOME_DIR/config.toml"
MARKER="# --- shunt PreToolUse (WP-REPAIR) ---"
if [[ -f "$CFG" ]] && grep -qF "$HOOK_CMD" "$CFG"; then
  echo "config.toml already references shunt pretooluse"
elif [[ -f "$CFG" ]] && grep -qF "$MARKER" "$CFG"; then
  echo "config.toml already has shunt marker (left intact)"
else
  mkdir -p "$(dirname "$CFG")"
  {
    echo ""
    echo "$MARKER"
    echo "[[hooks.PreToolUse]]"
    echo 'matcher = "Read|read_file|Bash|run_terminal_command|Shell|run_terminal_cmd"'
    echo "hooks = ["
    echo "  { type = \"command\", command = \"$HOOK_CMD\", timeout = 10 },"
    echo "]"
  } >> "$CFG"
  echo "Appended shunt [[hooks.PreToolUse]] to $CFG"
fi
echo "IMPORTANT: restart Grok sessions (or new -p) so PreToolUse loads. /hooks to confirm."
echo "Unrelated hooks untouched."
