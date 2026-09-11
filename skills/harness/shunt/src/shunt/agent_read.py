"""Allow/deny decisions for agent Read / shell cat|head|tail of files.

Inverse of bulk-read `decide`: block oversized *full* agent reads; allow
offset/limit windows and small files. On deny, tell the agent to run
`shunt bulk-read <path>`.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from shunt.gate import DEFAULT_MAX_BYTES, DEFAULT_MIN_LINES, count_lines

DENY_MESSAGE_TMPL = (
    "File is oversized for a full agent read ({lines} lines). "
    "Do not cat/Read the whole file. Run: shunt bulk-read {path}"
)


@dataclass(frozen=True)
class AgentReadResult:
    allow: bool
    reason: str
    path: str
    lines: int | None = None
    bytes: int | None = None
    message: str | None = None


def _file_stats(path: str | Path) -> tuple[int | None, int | None]:
    """Return (lines, bytes). Honors SHUNT_INTERNAL for recursion safety."""
    p = Path(path)
    if not p.is_file():
        return None, None
    # Caller should set SHUNT_INTERNAL=1; we reinforce here while reading.
    prev = os.environ.get("SHUNT_INTERNAL")
    os.environ["SHUNT_INTERNAL"] = "1"
    try:
        raw = p.read_bytes()
    finally:
        if prev is None:
            os.environ.pop("SHUNT_INTERNAL", None)
        else:
            os.environ["SHUNT_INTERNAL"] = prev
    text = raw.decode("utf-8", errors="replace")
    return count_lines(text), len(raw)


def decide_agent_read(
    path: str | Path,
    *,
    offset: int | None = None,
    limit: int | None = None,
    min_lines: int = DEFAULT_MIN_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    content: str | None = None,
    size_bytes: int | None = None,
) -> AgentReadResult:
    """
    Decide whether an agent may fully read ``path``.

    - Windowed reads (offset and/or limit set) → allow.
    - Missing / non-file → allow (fail open; tool will error normally).
    - Below min_lines → allow.
    - At/above min_lines (or over max_bytes with unknown lines) → deny; message
      instructs ``shunt bulk-read <path>``.
    """
    p = str(Path(path).expanduser())
    if offset is not None or limit is not None:
        return AgentReadResult(True, "windowed_read", p, lines=limit)

    n_lines = None
    n_bytes = size_bytes
    if content is not None:
        n_bytes = len(content.encode("utf-8", errors="replace"))
        n_lines = count_lines(content)
    else:
        n_lines, n_bytes = _file_stats(p)

    if n_lines is None and n_bytes is None:
        return AgentReadResult(True, "missing_or_unreadable", p)

    if n_bytes is not None and n_bytes > max_bytes and (n_lines is None or n_lines >= min_lines):
        msg = DENY_MESSAGE_TMPL.format(lines=n_lines if n_lines is not None else "?", path=p)
        return AgentReadResult(False, "oversized_bytes", p, lines=n_lines, bytes=n_bytes, message=msg)

    if n_lines is not None and n_lines >= min_lines:
        msg = DENY_MESSAGE_TMPL.format(lines=n_lines, path=p)
        return AgentReadResult(False, "oversized_full_read", p, lines=n_lines, bytes=n_bytes, message=msg)

    return AgentReadResult(True, "below_min_lines", p, lines=n_lines, bytes=n_bytes)


_HEAD_TAIL = re.compile(
    r"^(?:sudo\s+)?(?P<cmd>head|tail)\b",
    re.IGNORECASE,
)
_CAT = re.compile(
    r"^(?:sudo\s+)?cat\b",
    re.IGNORECASE,
)


def _strip_shell_wrappers(command: str) -> str:
    """Take the leftmost simple pipeline segment / ignore env assignments lightly."""
    cmd = command.strip()
    # Drop leading env VAR=value
    while True:
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\S+\s+", cmd)
        if not m:
            break
        cmd = cmd[m.end() :]
    # First pipeline stage only
    if "|" in cmd:
        cmd = cmd.split("|", 1)[0].strip()
    # Drop redirections for path extraction simplicity
    cmd = re.split(r"[<>]", cmd, maxsplit=1)[0].strip()
    return cmd


def parse_shell_read(command: str) -> list[tuple[str, int | None, int | None]]:
    """
    Extract (path, offset, limit) targets from cat/head/tail commands.

    head/tail with an explicit line/byte count → limit set (windowed → allow).
    bare cat → full read (offset/limit None).
    Returns empty list when the command is not a gated read.
    """
    raw = _strip_shell_wrappers(command)
    if not raw:
        return []
    try:
        parts = shlex.split(raw)
    except ValueError:
        return []
    if not parts:
        return []

    joined = " ".join(parts)
    if _CAT.match(joined):
        paths = [a for a in parts[1:] if not a.startswith("-")]
        return [(p, None, None) for p in paths]

    m = _HEAD_TAIL.match(joined)
    if not m:
        return []

    cmd = parts[0].lower()
    args = parts[1:]
    limit: int | None = 10  # POSIX default for head/tail
    paths: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-n", "-c", "-q", "-v") and i + 1 < len(args):
            if a in ("-n", "-c"):
                try:
                    limit = abs(int(args[i + 1].lstrip("+")))
                except ValueError:
                    limit = 10
            i += 2
            continue
        if re.fullmatch(r"-n\d+", a) or re.fullmatch(r"-\d+", a):
            try:
                limit = abs(int(a.lstrip("-n")))
            except ValueError:
                pass
            i += 1
            continue
        if a.startswith("-"):
            i += 1
            continue
        paths.append(a)
        i += 1

    # head/tail without file → stdin; nothing to gate
    if not paths:
        return []
    # offset unused for head/tail; limit marks windowed
    _ = cmd
    return [(p, None, limit) for p in paths]


def evaluate_shell_command(command: str, **kwargs) -> AgentReadResult | None:
    """
    Gate cat/head/tail in a shell command.

    Returns None when the command is not a gated read (caller should allow).
    Returns the first deny, else the last allow among gated targets.
    """
    targets = parse_shell_read(command)
    if not targets:
        return None
    last: AgentReadResult | None = None
    for path, offset, limit in targets:
        last = decide_agent_read(path, offset=offset, limit=limit, **kwargs)
        if not last.allow:
            return last
    return last
