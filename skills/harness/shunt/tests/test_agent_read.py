from __future__ import annotations

import json
from pathlib import Path

from shunt.agent_read import decide_agent_read, evaluate_shell_command, parse_shell_read


def _lines(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(n)) + "\n"


def test_agent_read_allows_small(tmp_path: Path):
    p = tmp_path / "small.py"
    p.write_text(_lines(40), encoding="utf-8")
    r = decide_agent_read(p)
    assert r.allow is True
    assert r.reason == "below_min_lines"


def test_agent_read_denies_oversized(tmp_path: Path):
    p = tmp_path / "big.py"
    p.write_text(_lines(400), encoding="utf-8")
    r = decide_agent_read(p, min_lines=350)
    assert r.allow is False
    assert r.reason == "oversized_full_read"
    assert r.message and "shunt bulk-read" in r.message
    assert str(p) in r.message


def test_agent_read_allows_window(tmp_path: Path):
    p = tmp_path / "big.py"
    p.write_text(_lines(400), encoding="utf-8")
    r = decide_agent_read(p, offset=1, limit=50, min_lines=350)
    assert r.allow is True
    assert r.reason == "windowed_read"


def test_parse_cat_and_head():
    assert parse_shell_read("cat /tmp/x.py") == [("/tmp/x.py", None, None)]
    assert parse_shell_read("head -n 20 /tmp/x.py") == [("/tmp/x.py", None, 20)]
    assert parse_shell_read("tail -5 /tmp/x.py") == [("/tmp/x.py", None, 5)]
    assert parse_shell_read("ls -la") == []


def test_shell_cat_denies_oversized(tmp_path: Path):
    p = tmp_path / "big.py"
    p.write_text(_lines(400), encoding="utf-8")
    r = evaluate_shell_command(f"cat {p}", min_lines=350)
    assert r is not None
    assert r.allow is False
    assert "shunt bulk-read" in (r.message or "")


def test_shell_head_allows_window(tmp_path: Path):
    p = tmp_path / "big.py"
    p.write_text(_lines(400), encoding="utf-8")
    r = evaluate_shell_command(f"head -n 20 {p}", min_lines=350)
    assert r is not None
    assert r.allow is True
