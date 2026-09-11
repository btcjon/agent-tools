#!/usr/bin/env bash
# Thin wrapper — dry-run list of panes that may need a fresh session after shunt install.
# Never interrupts panes. See docs/RELOAD-POLICY.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/herdr_shunt_reload_hint.py" "$@"
