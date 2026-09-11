"""Shared native-read gate tests (used by all WP4 hard adapters)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters" / "_common"))

from native_read_gate import (  # noqa: E402
    evaluate_native_read,
    extract_read_from_payload,
    parse_shell_read,
)


@pytest.fixture
def large_file(tmp_path: Path) -> Path:
    p = tmp_path / "big.txt"
    p.write_text("\n".join(f"line {i}" for i in range(400)) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def small_file(tmp_path: Path) -> Path:
    p = tmp_path / "small.txt"
    p.write_text("\n".join(f"line {i}" for i in range(20)) + "\n", encoding="utf-8")
    return p


def test_block_oversized_full_read(large_file: Path):
    r = evaluate_native_read(large_file)
    assert r.block is True
    assert r.reason == "oversized_full_read"
    assert "shunt bulk-read" in (r.message or "")


def test_allow_scoped_offset(large_file: Path):
    r = evaluate_native_read(large_file, offset=10, limit=20)
    assert r.block is False
    assert r.reason == "scoped_window"


def test_allow_small(small_file: Path):
    r = evaluate_native_read(small_file)
    assert r.block is False
    assert r.reason == "small_file"


def test_parse_cat(large_file: Path):
    path, off, lim = parse_shell_read(f"cat {large_file}")
    assert path == str(large_file)
    assert off is None and lim is None


def test_parse_head_scoped():
    path, off, lim = parse_shell_read("head -n 20 /tmp/x.txt")
    assert path == "/tmp/x.txt"
    assert lim == 20


def test_extract_grok_read(large_file: Path):
    path, off, lim = extract_read_from_payload(
        {"toolName": "read_file", "toolInput": {"path": str(large_file)}}
    )
    assert path == str(large_file)


def test_extract_agy_command(large_file: Path):
    path, off, lim = extract_read_from_payload(
        {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": f"cat {large_file}"},
            }
        }
    )
    assert path == str(large_file)
