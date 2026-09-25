"""Pinned curated-summary overlay for a capability manifest.

Replaces one-line summaries only. Ids, operations, writes flags, schema
hashes, and source strings stay as captured. Application refuses a source
content hash other than the overlay pin, and refuses an id that is not in
the source. It does not select tools or perform writes.
"""
from __future__ import annotations

import hmac
import json
from pathlib import Path
import re
import sys

from .capability_core import SUMMARY_MAX_BYTES, CapabilityError, CapabilityManifest, load_manifest

_OVERLAY_KEYS = frozenset({"expected_source_hash", "description", "provenance", "cards"})
_CARD_KEYS = frozenset({"id", "summary"})
_HASH = re.compile(r"^[0-9a-f]{64}$")
_TEXT_LIMIT = 240
_PROTECTED = ("typesafe_api_key", "jev_api", "authorization: bearer", "bearer ", "-----begin ")
_LEAKS = ("inputSchema", "schema_hash", "additionalProperties", "Operation is required.", "Not required or only topical.")


def _line(value, limit):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CapabilityError("invalid_overlay")
    if "\n" in value or "\r" in value or "{" in value or "}" in value:
        raise CapabilityError("invalid_overlay")
    if len(value.encode("utf-8")) > limit:
        raise CapabilityError("invalid_overlay")
    if any(marker in value for marker in _LEAKS):
        raise CapabilityError("invalid_overlay")
    lowered = value.lower()
    if any(marker in lowered for marker in _PROTECTED):
        raise CapabilityError("invalid_overlay")
    return value


def load_overlay(path):
    """Read one overlay file. The file is data, not a procedure."""
    file = Path(path)
    if not file.is_file() or file.is_symlink():
        raise CapabilityError("invalid_overlay")
    try:
        loaded = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CapabilityError("invalid_overlay") from None
    if not isinstance(loaded, dict) or set(loaded) != _OVERLAY_KEYS:
        raise CapabilityError("invalid_overlay")
    if not isinstance(loaded["expected_source_hash"], str) or not _HASH.fullmatch(loaded["expected_source_hash"]):
        raise CapabilityError("invalid_overlay")
    if not isinstance(loaded["cards"], list):
        raise CapabilityError("invalid_overlay")
    _line(loaded["description"], _TEXT_LIMIT)
    _line(loaded["provenance"], _TEXT_LIMIT)
    return loaded


def apply_cards(manifest, overlay):
    """Return a manifest document whose summaries come from the overlay.

    Unlisted ids keep their captured summary and provenance. Listed ids keep
    every field except summary and provenance.
    """
    if not isinstance(manifest, CapabilityManifest) or not isinstance(overlay, dict):
        raise CapabilityError("invalid_overlay")
    if set(overlay) != _OVERLAY_KEYS:
        raise CapabilityError("invalid_overlay")
    expected = overlay["expected_source_hash"]
    if not isinstance(expected, str) or not _HASH.fullmatch(expected):
        raise CapabilityError("invalid_overlay")
    if not hmac.compare_digest(expected, manifest.content_hash):
        raise CapabilityError("source_hash_mismatch")
    description = _line(overlay["description"], _TEXT_LIMIT)
    provenance = _line(overlay["provenance"], _TEXT_LIMIT)
    cards = overlay["cards"]
    if not isinstance(cards, list):
        raise CapabilityError("invalid_overlay")
    summaries = {}
    for card in cards:
        if not isinstance(card, dict) or set(card) != _CARD_KEYS:
            raise CapabilityError("invalid_overlay")
        identifier = card["id"]
        if not isinstance(identifier, str) or identifier in summaries:
            raise CapabilityError("invalid_overlay")
        summaries[identifier] = _line(card["summary"], SUMMARY_MAX_BYTES)
    unknown = [identifier for identifier in summaries if identifier not in manifest.entries]
    if unknown:
        raise CapabilityError("unknown_id")
    entries = []
    for key in sorted(manifest.entries):
        record = manifest.entries[key].record()
        if key in summaries:
            record["summary"] = summaries[key]
            record["provenance"] = provenance
        entries.append(record)
    return {"manifest_version": 1, "description": description, "entries": entries}


def document_text(document):
    return json.dumps(document, indent=2) + "\n"


def write_curated(path, document, *, source=None):
    """Write a curated manifest. Refuses the captured source path and symlinks."""
    target = Path(path)
    if source is not None and target.resolve() == Path(source).resolve():
        raise CapabilityError("invalid_overlay")
    if target.is_symlink():
        raise CapabilityError("invalid_overlay")
    text = document_text(document)
    if "inputSchema" in text or "additionalProperties" in text:
        raise CapabilityError("invalid_overlay")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def curated_document(manifest_path, overlay_path):
    return apply_cards(load_manifest(manifest_path), load_overlay(overlay_path))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        print("usage: python -m jev_skill_advisor.capability_cards MANIFEST OVERLAY DEST", file=sys.stderr)
        return 2
    document = curated_document(argv[0], argv[1])
    write_curated(argv[2], document, source=argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
