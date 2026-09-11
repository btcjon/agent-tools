#!/usr/bin/env bash
# Grok PreToolUse hook — HARD (decision: deny).
exec "$(cd "$(dirname "$0")/../.." && pwd)/adapters/_common/shunt-hook.sh" grok
