#!/usr/bin/env bash
# Shared launcher: run native_read_gate against stdin JSON; emit format-specific deny.
# Usage: shunt-hook.sh <format>
# Formats: grok | hermes | agy | pi-json
set -euo pipefail

FORMAT="${1:-grok}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$ROOT/adapters/_common/native_read_gate.py"
export SHUNT_ROOT="$ROOT"
# Never inherit SHUNT_INTERNAL into the gate decision (that flag is for bulk-read CLI only).
unset SHUNT_INTERNAL || true

INPUT="$(cat)"
if [[ -z "${INPUT//[[:space:]]/}" ]]; then
  case "$FORMAT" in
    hermes) printf '%s\n' '{}' ;;
    pi-json) printf '%s\n' '{"block":false}' ;;
    *) printf '%s\n' '{"decision":"allow"}' ;;
  esac
  exit 0
fi

# Gate uses Python open() (not harness tools) — do not set SHUNT_INTERNAL here
# or the gate short-circuits. SHUNT_INTERNAL is for bulk-read CLI recursion only.
RESULT="$(printf '%s' "$INPUT" | python3 "$GATE" --json-stdin || true)"
BLOCK="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("1" if d.get("block") else "0")' 2>/dev/null || echo 0)"
REASON="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("message") or d.get("reason") or "")' 2>/dev/null || true)"

if [[ "$BLOCK" != "1" ]]; then
  case "$FORMAT" in
    hermes) printf '%s\n' '{}' ;;
    pi-json) printf '%s\n' '{"block":false}' ;;
    *) printf '%s\n' '{"decision":"allow"}' ;;
  esac
  exit 0
fi

export SHUNT_BLOCK_MSG="${REASON:-Oversized full-file read blocked. Run: shunt bulk-read <path>}"

case "$FORMAT" in
  hermes)
    python3 -c 'import json,os; print(json.dumps({"action":"block","message":os.environ["SHUNT_BLOCK_MSG"]}))'
    ;;
  grok)
    # JSON deny is authoritative; exit 2 is Grok's explicit-deny code (belt + suspenders).
    python3 -c 'import json,os; print(json.dumps({"decision":"deny","reason":os.environ["SHUNT_BLOCK_MSG"]}))'
    exit 2
    ;;
  agy)
    python3 -c 'import json,os; print(json.dumps({"decision":"deny","reason":os.environ["SHUNT_BLOCK_MSG"]}))'
    ;;
  pi-json)
    python3 -c 'import json,os; print(json.dumps({"block":True,"reason":os.environ["SHUNT_BLOCK_MSG"]}))'
    ;;
  *)
    python3 -c 'import json,os; print(json.dumps({"decision":"deny","reason":os.environ["SHUNT_BLOCK_MSG"]}))'
    ;;
esac
