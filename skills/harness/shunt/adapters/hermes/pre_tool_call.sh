#!/usr/bin/env bash
# Hermes pre_tool_call shell hook — HARD (can block).
exec "$(cd "$(dirname "$0")/../.." && pwd)/adapters/_common/shunt-hook.sh" hermes
