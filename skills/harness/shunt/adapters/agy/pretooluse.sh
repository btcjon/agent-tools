#!/usr/bin/env bash
# AGY (Antigravity / ~/.gemini) PreToolUse — HARD (decision: deny).
exec "$(cd "$(dirname "$0")/../.." && pwd)/adapters/_common/shunt-hook.sh" agy
