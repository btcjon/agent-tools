#!/usr/bin/env python3
"""Offline package completeness and privacy scans. Not a behavior proof."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "SOURCE-REVISION.md",
    "SKILL.md",
    "CONTRACT-SNIPPET.md",
    "references/mode.md",
    "references/profiles.md",
    "references/packets.md",
    "references/claims.md",
    "references/runbooks.md",
    "references/adapters/herdr.md",
    "references/install.md",
    "scripts/claims.py",
    "scripts/check_package.py",
    "examples/config.example.json",
    "examples/early-packet.md",
    "examples/task-packet.md",
    "examples/closing-packet.json",
    "examples/receipts.json",
    "tests/test_claims.py",
    "tests/policy-scenarios.md",
    "tests/traceability.md",
]

PRIVATE_PATTERNS = [
    re.compile(r"notion\.so|notion\.com", re.I),
    re.compile(r"vmi\d+", re.I),
    re.compile(r"Dropbox/Projects"),
    re.compile(r"contabo", re.I),
    re.compile(r"--dangerously-bypass"),
    re.compile(r"op://"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
]


def main() -> int:
    errors: list[str] = []
    for rel in REQUIRED:
        if not (ROOT / rel).is_file():
            errors.append(f"missing:{rel}")

    skill_md = ROOT / "SKILL.md"
    if skill_md.is_file():
        text = skill_md.read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1] if text.startswith("---") else ""
        if not frontmatter:
            errors.append("frontmatter:missing")
        elif "name: herdr-orchestration" not in frontmatter:
            errors.append("frontmatter:name")
        if "description:" not in frontmatter:
            errors.append("frontmatter:description")

    skip_scan = {Path("scripts/check_package.py")}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if rel in skip_scan or "__pycache__" in path.parts:
            continue
        if path.suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        for pattern in PRIVATE_PATTERNS:
            if pattern.search(body):
                errors.append(f"private:{rel}:{pattern.pattern}")

    if errors:
        print("\n".join(errors))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
