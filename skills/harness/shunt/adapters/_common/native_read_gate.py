"""Decide whether a native Read/cat should be blocked and redirected to shunt.

This is the inverse of `shunt.gate.decide` (bulk-read allowlist):
- Scoped reads (offset/limit or head/tail -n) → allow native.
- Oversized full-file reads → block native; tell agent to run `shunt bulk-read`.
- Small files → allow native.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_MIN_LINES = 350
DEFAULT_MAX_BYTES = 524288  # soft ceiling for "large"; still shunt if over min_lines

SHUNT_MSG = (
    "Oversized full-file read blocked by shunt. "
    "Run: shunt bulk-read {path}  "
    "(OpenRouter google/gemini-3.8-flash points-only; never CAPI/gflash OAuth.)"
)


@dataclass(frozen=True)
class NativeGateResult:
    block: bool
    reason: str
    path: str | None = None
    lines: int | None = None
    bytes: int | None = None
    message: str | None = None


def _count_lines_file(path: Path) -> tuple[int, int]:
    """Return (line_count, byte_count). Uses st_size; counts newlines with early exit when clearly oversized."""
    n_bytes = path.stat().st_size
    if n_bytes == 0:
        return 0, 0
    n_lines = 0
    last_nl = True
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            n_lines += chunk.count(b"\n")
            last_nl = chunk.endswith(b"\n")
            # Early exit once we know it's oversized by lines.
            if n_lines >= DEFAULT_MIN_LINES and not last_nl:
                n_lines += 1  # incomplete last line still counts
                return n_lines, n_bytes
            if n_lines >= DEFAULT_MIN_LINES and last_nl:
                return n_lines, n_bytes
    if not last_nl:
        n_lines += 1
    return n_lines, n_bytes

def evaluate_native_read(
    path: str | Path | None,
    *,
    offset: int | None = None,
    limit: int | None = None,
    min_lines: int = DEFAULT_MIN_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> NativeGateResult:
    if os.environ.get("SHUNT_INTERNAL", "").strip() in ("1", "true", "yes"):
        return NativeGateResult(False, "shunt_internal")

    if not path:
        return NativeGateResult(False, "no_path")

    p = Path(str(path)).expanduser()
    # Scoped window → native read is correct; bulk-read is for whole oversized files.
    if offset is not None or limit is not None:
        return NativeGateResult(False, "scoped_window", str(p), lines=limit)

    if not p.is_file():
        return NativeGateResult(False, "missing_file", str(p))

    try:
        n_lines, n_bytes = _count_lines_file(p)
    except OSError as e:
        return NativeGateResult(False, f"stat_error:{e}", str(p))

    if n_lines >= min_lines or n_bytes >= max_bytes:
        msg = SHUNT_MSG.format(path=str(p))
        return NativeGateResult(True, "oversized_full_read", str(p), n_lines, n_bytes, msg)

    return NativeGateResult(False, "small_file", str(p), n_lines, n_bytes)


_CAT_RE = re.compile(
    r"""(?:^|[;&|]\s*|&&\s*|\|\|\s*)(?:sudo\s+)?(?:/bin/)?cat(?:\s+(-[A-Za-z]+|\s))*\s+(['\"]?)(?P<path>[^\s'\";&|]+)\2""",
    re.IGNORECASE,
)
_HEAD_TAIL_RE = re.compile(
    r"""(?:^|[;&|]\s*)(?:sudo\s+)?(?:/usr/bin/)?(?P<cmd>head|tail)\b(?P<args>[^;&|]*)""",
    re.IGNORECASE,
)
_N_FLAG = re.compile(r"(?:^|\s)-(?:n\s*|n=)(?P<n>\d+)")
_PATH_TOKEN = re.compile(r"""(?:^|\s)(['\"]?)(?P<path>(?:/|\./|\.\./|~/)[^\s'\"|&;]+|(?:[A-Za-z0-9_./~-]+\.[A-Za-z0-9]+))\1""")


def parse_shell_read(command: str) -> tuple[str | None, int | None, int | None]:
    """Extract (path, offset, limit) from cat/head/tail. Unknown → (None, None, None)."""
    if not command or not command.strip():
        return None, None, None
    cmd = command.strip()

    m = _HEAD_TAIL_RE.search(cmd)
    if m:
        args = m.group("args") or ""
        n_m = _N_FLAG.search(args)
        limit = int(n_m.group("n")) if n_m else 10  # POSIX default
        paths = list(_PATH_TOKEN.finditer(args))
        path = paths[-1].group("path") if paths else None
        if path and path.startswith("-"):
            path = None
        return path, None, limit

    m = _CAT_RE.search(cmd)
    if m:
        return m.group("path"), None, None

    return None, None, None


def extract_read_from_payload(data: dict) -> tuple[str | None, int | None, int | None]:
    """Normalize Cursor/Claude/Grok/Hermes/AGY tool payloads to (path, offset, limit)."""
    # Common nestings
    tool = (
        data.get("toolName")
        or data.get("tool_name")
        or data.get("tool")
        or (data.get("toolCall") or {}).get("name")
        or ""
    )
    tool = str(tool)
    inp = (
        data.get("toolInput")
        or data.get("tool_input")
        or data.get("arguments")
        or data.get("args")
        or (data.get("toolCall") or {}).get("args")
        or (data.get("toolCall") or {}).get("input")
        or {}
    )
    if not isinstance(inp, dict):
        inp = {}

    # AGY / Jetski style CommandLine
    command = (
        inp.get("command")
        or inp.get("CommandLine")
        or inp.get("cmd")
        or data.get("command")
    )

    read_names = {
        "read",
        "Read",
        "read_file",
        "ReadFile",
        "view_file",
        "ViewFile",
        "read_file_v2",
    }
    bash_names = {
        "bash",
        "Bash",
        "shell",
        "Shell",
        "run_terminal_command",
        "run_command",
        "RunCommand",
        "terminal",
    }

    if tool in read_names or (not tool and ("path" in inp or "file_path" in inp or "filePath" in inp)):
        path = inp.get("path") or inp.get("file_path") or inp.get("filePath") or inp.get("target_file")
        offset = inp.get("offset") if "offset" in inp else inp.get("Offset")
        limit = inp.get("limit") if "limit" in inp else inp.get("Limit")
        if offset is not None:
            try:
                offset = int(offset)
            except (TypeError, ValueError):
                offset = None
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = None
        return (str(path) if path else None), offset, limit

    if tool in bash_names or command:
        return parse_shell_read(str(command or ""))

    # Hermes sometimes puts tool_name + args at top level already handled.
    return None, None, None


def main(argv: list[str] | None = None) -> int:
    """CLI: path [--offset N] [--limit N]  OR  --json-stdin (raw evaluate)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--json-stdin" in args:
        raw = sys.stdin.read()
        data = json.loads(raw or "{}")
        path, offset, limit = extract_read_from_payload(data)
        result = evaluate_native_read(path, offset=offset, limit=limit)
        print(json.dumps(asdict(result)))
        # Always exit 0 for JSON protocol — callers read `block` from stdout.
        # Non-zero exits break Node execFile and cause fail-open in Pi.
        return 0

    path = None
    offset = None
    limit = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--offset" and i + 1 < len(args):
            offset = int(args[i + 1])
            i += 2
        elif a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            path = a
            i += 1
    result = evaluate_native_read(path, offset=offset, limit=limit)
    print(json.dumps(asdict(result)))
    return 2 if result.block else 0


if __name__ == "__main__":
    raise SystemExit(main())
