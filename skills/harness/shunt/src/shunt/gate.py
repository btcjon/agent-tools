"""Allow/block decisions for shunt bulk-read from path + size + offset/limit."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_MIN_LINES = 350
DEFAULT_MAX_BYTES = 524288
DEFAULT_BLOCK_GLOBS = (
    "**/.env",
    "**/.env.*",
    "**/secrets/**",
    "**/*credential*",
    "**/*.pem",
    "**/*.key",
)


@dataclass(frozen=True)
class GateResult:
    allow: bool
    reason: str
    path: str
    lines: int | None = None
    bytes: int | None = None


def _posix(path: str | Path) -> str:
    return PurePosixPath(Path(path).as_posix()).as_posix()


def matches_block_glob(path: str | Path, globs: tuple[str, ...] | list[str] = DEFAULT_BLOCK_GLOBS) -> bool:
    p = _posix(path)
    name = Path(p).name
    parts = PurePosixPath(p).parts
    for g in globs:
        g = g.replace("\\", "/")
        if fnmatch.fnmatch(p, g) or fnmatch.fnmatch(name, g.lstrip("*/")):
            return True
        # directory prefix: **/secrets/** or secrets/** matches secrets/token.txt
        if g.endswith("/**"):
            root = g[:-3].rstrip("/")
            while root.startswith("**/"):
                root = root[3:]
            if root and (p == root or p.startswith(root + "/") or f"/{root}/" in f"/{p}/"):
                return True
        # *credential* style against any path segment
        if "*" in g and "/" not in g.strip("*"):
            if any(fnmatch.fnmatch(seg, g) for seg in parts):
                return True
    return False


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def decide(
    path: str | Path,
    *,
    content: str | None = None,
    size_bytes: int | None = None,
    offset: int | None = None,
    limit: int | None = None,
    min_lines: int = DEFAULT_MIN_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    block_globs: tuple[str, ...] | list[str] = DEFAULT_BLOCK_GLOBS,
) -> GateResult:
    """
    Return allow/block for bulk-read.

    Heuristics (Phase 0 stub, tested):
    - Block secret-like paths.
    - Block when offset/limit already scopes a small window (caller should normal-read).
    - Block when under min_lines (not worth bulk-read).
    - Block when over max_bytes hard ceiling (stub: refuse until chunking exists).
    - Else allow.
    """
    p = _posix(path)
    if matches_block_glob(p, block_globs):
        return GateResult(False, "blocked_secret_path", p)

    if offset is not None or limit is not None:
        # Explicit window → prefer normal read tools; bulk-read is for whole oversized files.
        window = limit if limit is not None else 0
        if limit is not None and limit < min_lines:
            return GateResult(False, "window_smaller_than_min_lines", p, lines=limit)
        if offset is not None and limit is None:
            return GateResult(False, "offset_without_full_scan", p)

    data = content
    n_bytes = size_bytes
    n_lines = None
    if data is not None:
        n_bytes = len(data.encode("utf-8", errors="replace"))
        n_lines = count_lines(data)
    elif Path(path).is_file():
        raw = Path(path).read_bytes()
        n_bytes = len(raw)
        n_lines = count_lines(raw.decode("utf-8", errors="replace"))

    if n_bytes is not None and n_bytes > max_bytes:
        return GateResult(False, "exceeds_max_bytes", p, lines=n_lines, bytes=n_bytes)

    if n_lines is not None and n_lines < min_lines:
        return GateResult(False, "below_min_lines", p, lines=n_lines, bytes=n_bytes)

    if n_lines is None and n_bytes is None:
        return GateResult(False, "unknown_size", p)

    return GateResult(True, "ok", p, lines=n_lines, bytes=n_bytes)
