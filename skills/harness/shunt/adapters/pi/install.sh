#!/usr/bin/env bash
# Pi adapter install — register HARD tool_call extension into ~/.pi/agent/extensions.
# Prefer a real file copy over symlink: jiti module resolution breaks for some
# Dropbox-symlink layouts. Write shunt-root.txt so the copy can find the package.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXT_DIR="${PI_AGENT_DIR:-$HOME/.pi/agent}/extensions"
mkdir -p "$EXT_DIR"
TARGET="$EXT_DIR/shunt-gate.ts"
SOURCE="$ROOT/adapters/pi/shunt-gate.ts"
rm -f "$TARGET"
cp "$SOURCE" "$TARGET"
printf '%s\n' "$ROOT" > "$EXT_DIR/shunt-root.txt"
echo "Installed Pi shunt gate (HARD): $TARGET (copied from $SOURCE)"
echo "Wrote $EXT_DIR/shunt-root.txt → $ROOT"
echo "IMPORTANT: long-lived Pi panes must /reload or restart to load the extension."
echo "Bulk tool: shunt_bulk_read"
