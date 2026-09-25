#!/usr/bin/env bash
set -euo pipefail

package=/Users/jonbennett/Library/CloudStorage/Dropbox/Projects/agent-tools/skills/discovery/jev-skill-advisor
extension=/Users/jonbennett/.pi/agent/extensions
backup=/Users/jonbennett/.local/state/jev-skill-advisor/registration-backups/pi-bridge-20260925-before

if [[ ! -f "$backup/skill-select.ts" || -e "$backup/bridge-protocol.ts" ]]; then
  echo 'Pi rollback unavailable: backup is missing or already used.' >&2
  exit 1
fi
if ! cmp -s "$package/adapters/pi/skill-select.ts" "$extension/skill-select.ts" ||
   ! cmp -s "$package/adapters/pi/bridge-protocol.ts" "$extension/bridge-protocol.ts"; then
  echo 'Pi rollback refused: installed extension changed since deployment.' >&2
  exit 1
fi
if [[ "${1:-}" == "--check" ]]; then
  echo 'Pi rollback ready; no files changed.'
  exit 0
fi
if [[ $# -gt 0 ]]; then
  echo 'Usage: rollback-bridge.sh [--check]' >&2
  exit 1
fi

install -m 0644 "$backup/skill-select.ts" "$extension/skill-select.ts"
mv "$extension/bridge-protocol.ts" "$backup/bridge-protocol.ts"
cmp -s "$backup/skill-select.ts" "$extension/skill-select.ts"
echo 'Previous Pi extension restored; bridge helper moved into the backup directory.'
