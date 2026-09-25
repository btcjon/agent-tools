"""Import selection receipts and publish totals onto Global Skills.

This command never calls Jev and never rebuilds a snapshot.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .usage import default_ledger, ingest, load_ledger, reclassify, summarize
from .observability import append_label, summary


def _default_events() -> list[Path]:
    root = Path.home() / ".local" / "state" / "jev-skill-advisor"
    return [root / f"{name}-adapter" / "events.jsonl" for name in ("codex", "hermes", "cursor", "pi")]


def _hours(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)h?", value)
    if not match:
        raise argparse.ArgumentTypeError("use hours such as 24 or 24h")
    return float(match.group(1))


def _title(properties: object) -> str:
    if not isinstance(properties, dict):
        return ""
    value = properties.get("Skill name")
    if not isinstance(value, dict):
        return ""
    title = value.get("title")
    if not isinstance(title, list):
        return ""
    parts = []
    for item in title:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("plain_text"), str):
            parts.append(item["plain_text"])
        elif isinstance(item.get("text"), dict) and isinstance(item["text"].get("content"), str):
            parts.append(item["text"]["content"])
    return "".join(parts).strip()


def page_index(pages: list[dict]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Map a skill title to one page id. Duplicate titles stay grouped for a stable-id check."""
    found: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}
    for page in pages:
        if not isinstance(page, dict) or page.get("archived") is True:
            continue
        page_id = page.get("id")
        name = _title(page.get("properties"))
        if not isinstance(page_id, str) or not name:
            continue
        if name in ambiguous:
            ambiguous[name].append(page_id)
            continue
        if name in found:
            ambiguous[name] = [found.pop(name), page_id]
            continue
        found[name] = page_id
    return found, ambiguous


def stable_id_from_markdown(markdown: str) -> str | None:
    match = re.search(r"## Managed skill identity\s+```json\s+(\{.*?\})\s+```", markdown, re.S)
    if not match:
        return None
    try:
        marker = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    stable_id = marker.get("stable_id") if isinstance(marker, dict) else None
    return stable_id if isinstance(stable_id, str) and stable_id else None


def skill_pages(catalog: dict, pages: dict[str, str], by_stable_id: dict[str, str] | None = None) -> dict[str, str]:
    """Join stable ids to Notion pages through the catalog skill name."""
    names: dict[str, str] = {}
    ambiguous = set()
    for row in catalog.get("entries") or []:
        if not isinstance(row, dict):
            continue
        stable_id = row.get("stable_id")
        name = row.get("name")
        if not isinstance(stable_id, str) or not isinstance(name, str) or not name:
            continue
        if name in names or name in ambiguous:
            names.pop(name, None)
            ambiguous.add(name)
            continue
        names[name] = stable_id
    joined = {}
    for name, stable_id in names.items():
        page_id = pages.get(name) or (by_stable_id or {}).get(stable_id)
        if page_id:
            joined[stable_id] = page_id
    return joined


def usage_properties(count: int, last_selected: str | None) -> dict:
    properties = {"Selection count": {"type": "number", "number": count}}
    if last_selected:
        properties["Last selected"] = {"type": "date", "date": {"start": last_selected}}
    return properties


def _query_pages(transport, data_source_id: str) -> list[dict]:
    pages = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        response = transport.request(f"v1/data_sources/{data_source_id}/query", method="POST", body=body)
        results = response.get("results")
        if not isinstance(results, list):
            raise ValueError("invalid_remote_inventory")
        pages.extend(results)
        if not response.get("has_more"):
            return pages
        cursor = response.get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise ValueError("invalid_remote_pagination")


def _catalog_from_release() -> dict:
    root = Path.home() / ".local" / "state" / "jev-skill-advisor" / "releases"
    current = json.loads((root / "current-release.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "releases" / current["release_id"] / "manifest.json").read_text(encoding="utf-8"))
    profile = json.loads(Path(manifest["files"]["profile:generic"]["path"]).read_text(encoding="utf-8"))
    return json.loads(Path(profile["catalog_path"]).read_text(encoding="utf-8"))


def publish(ledger: dict, pages: dict[str, str], transport) -> dict:
    summary = summarize(ledger)
    updated = []
    missing = []
    for skill_id, slot in sorted(summary["skills"].items()):
        page_id = pages.get(skill_id)
        if not page_id:
            missing.append(skill_id)
            continue
        transport.update_properties(page_id, usage_properties(slot["count"], slot["last_selected"]))
        updated.append({"skill_id": skill_id, "page_id": page_id, "count": slot["count"], "last_selected": slot["last_selected"]})
    return {"updated": updated, "missing": missing}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="skill-advisor-usage")
    parser.add_argument("--ledger", type=Path, default=default_ledger())
    commands = parser.add_subparsers(dest="command", required=True)
    import_command = commands.add_parser("import")
    import_command.add_argument("paths", nargs="+", type=Path)
    commands.add_parser("report")
    summary_command = commands.add_parser("summary", help="Read content-free attempt logs; does not mutate the ledger")
    summary_command.add_argument("--since", type=_hours, default=24, metavar="HOURS")
    summary_command.add_argument("--events", action="append", type=Path, default=[])
    summary_command.add_argument("--remote-host", action="append", default=[], metavar="SSH_ALIAS",
                                 help="Read the remote Hermes attempt log over BatchMode SSH for this report only")
    summary_command.add_argument("--labels", action="append", type=Path, default=[])
    summary_command.add_argument("--expect", action="append", default=[], metavar="HOST:HARNESS")
    label_command = commands.add_parser("label", help="Record a bounded self-report or human review")
    label_command.add_argument("receipt_id")
    label_command.add_argument("label", choices=["followed", "partial", "ignored", "not_applicable", "better", "same", "worse", "wrong_skill"])
    label_command.add_argument("--evidence", choices=["self_report", "human_review"], required=True)
    label_command.add_argument("--output", type=Path, default=Path.home()/".local/state/jev-skill-advisor/usage/labels.jsonl")
    commands.add_parser("reclassify")
    publish_command = commands.add_parser("publish")
    publish_command.add_argument("--data-source", required=True)
    publish_command.add_argument("--catalog", type=Path)
    args = parser.parse_args(argv)
    if args.command == "import":
        result = ingest(args.paths, args.ledger)
    elif args.command == "summary":
        if args.since <= 0 or args.since > 24 * 365:
            parser.error("--since must be between 0 and 8760 hours")
        paths = args.events or _default_events()
        labels = args.labels or [Path.home()/".local/state/jev-skill-advisor/usage/labels.jsonl"]
        remote_errors = []
        with tempfile.TemporaryDirectory(prefix="jev-attempts-") as temporary:
            remote_paths = []
            for alias in args.remote_host:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias):
                    parser.error("invalid SSH alias")
                proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", alias,
                                       'cat "$HOME/.local/state/jev-skill-advisor/hermes-adapter/events.jsonl"'],
                                      capture_output=True, text=True, timeout=30, check=False)
                if proc.returncode:
                    remote_errors.append({"host": alias, "error": "ssh_or_log_unavailable"})
                    continue
                remote_path = Path(temporary) / f"{alias}-events.jsonl"
                remote_path.write_text(proc.stdout, encoding="utf-8")
                remote_paths.append(remote_path)
            result = summary([*paths, *remote_paths], since_hours=args.since, label_paths=labels, expected=args.expect)
        result["event_sources"] = [str(path) for path in paths] + [f"ssh:{host}:hermes-adapter" for host in args.remote_host]
        result["remote_source_errors"] = remote_errors
    elif args.command == "label":
        if not append_label(args.output, receipt_id=args.receipt_id, label=args.label, evidence=args.evidence):
            parser.error("could not write label")
        result = {"recorded": True, "evidence": args.evidence, "label": args.label, "receipt_id": args.receipt_id}
    elif args.command == "reclassify":
        result = reclassify(args.ledger)
    elif args.command == "report":
        result = summarize(load_ledger(args.ledger))
        notes = []
        if result["adapters"].get("cursor", 0) == 0:
            notes.append("Cursor selections are absent until skill-search is called with --receipt.")
        if result["adapters"].get("unlabeled"):
            notes.append("Receipts written before adapter labels are counted, and they do not set Last selected.")
        if notes:
            result["note"] = " ".join(notes)
    else:
        from .notion_sync import NtnSyncTransport
        catalog = json.loads(args.catalog.read_text(encoding="utf-8")) if args.catalog else _catalog_from_release()
        transport = NtnSyncTransport()
        unique, ambiguous = page_index(_query_pages(transport, args.data_source))
        by_stable_id = {}
        for page_ids in ambiguous.values():
            claimed = {}
            conflicts = set()
            for page_id in page_ids:
                markdown = transport.request(f"v1/pages/{page_id}/markdown").get("markdown", "")
                stable_id = stable_id_from_markdown(markdown if isinstance(markdown, str) else "")
                if not stable_id or stable_id in conflicts:
                    continue
                if stable_id in claimed:
                    claimed.pop(stable_id)
                    conflicts.add(stable_id)
                    continue
                claimed[stable_id] = page_id
            by_stable_id.update(claimed)
        pages = skill_pages(catalog, unique, by_stable_id)
        result = publish(load_ledger(args.ledger), pages, transport)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
