#!/usr/bin/env python3
"""Search only the host's active verified skill distribution."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path.home() / ".agents" / "exported-skills"
STOP = frozenset("a an and for from how i in is of on the to use when with".split())
ALIASES = {
    "xlsx": {"spreadsheet", "excel", "workbook"},
    "docx": {"document", "word"},
    "slides": {"presentation", "deck", "powerpoint"},
}


def tokens(text: str) -> set[str]:
    values = set()
    for token in re.findall(r"[a-z0-9-]+", text.lower()):
        if token in STOP or len(token) < 2:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            token = token[:-1]
        values.add(token)
    for key, synonyms in ALIASES.items():
        if key in values or values.intersection(synonyms):
            values.add(key)
            values.update(synonyms)
    return values


def description(text: str) -> str:
    match = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", text, re.M)
    if match:
        return match.group(1).strip().strip("\"").strip("'")
    heading = re.search(r"^#\s+(.+)$", text, re.M)
    return heading.group(1).strip() if heading else ""


def main() -> int:
    parser = argparse.ArgumentParser(prog="skill-search")
    parser.add_argument("query", nargs="+")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not ROOT.is_dir():
        raise SystemExit(f"missing active skill distribution: {ROOT}")
    query = " ".join(args.query).strip()
    wanted = tokens(query)
    hits = []
    for skill in ROOT.glob("*/SKILL.md"):
        body = skill.read_text(encoding="utf-8", errors="replace")[:4000]
        name = skill.parent.name
        desc = description(body)
        name_tokens, desc_tokens, body_tokens = tokens(name.replace("-", " ")), tokens(desc), tokens(body[:1200])
        matched = {item for item in wanted if item in name_tokens or item in desc_tokens or item in body_tokens}
        coverage = len(matched) / max(1, len(wanted))
        score = 8 * len(wanted & name_tokens) + 4 * len(wanted & desc_tokens) + len(wanted & body_tokens)
        if query.lower() == name.lower():
            score += 100
        if score and coverage >= 0.34:
            hits.append((score, coverage, name, desc, str(skill.resolve())))
    hits.sort(key=lambda row: (-row[0], -row[1], row[2]))
    selected = hits[: max(1, min(args.limit, 20))]
    values = [
        {"name": name, "description": desc, "path": path, "score": score, "coverage": coverage}
        for score, coverage, name, desc, path in selected
    ]
    if args.json:
        print(json.dumps(values, indent=2))
    elif not values:
        print("no matches")
    else:
        for value in values:
            print(f"{value['name']}\t{value['description']}\t{value['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
