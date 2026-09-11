"""Mocked HTTP tests for live OpenRouter bulk_read."""

from __future__ import annotations

import json
import os

import pytest

from shunt.bulk_read import (
    BOUNDED_READ_GUIDANCE,
    OPENROUTER_MODEL,
    bulk_read,
    content_sha256,
    parse_points,
)


def _large_body(n: int = 400) -> str:
    return "\n".join(f"def fn_{i}():\n    return {i}" for i in range(n // 2))


def test_parse_points_bullets():
    text = "- alpha\n* beta\nplain\n"
    assert parse_points(text) == ["alpha", "beta", "plain"]


def test_missing_key_guidance(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SHUNT_INTERNAL", raising=False)
    r = bulk_read("src/big.py", content=_large_body(), api_key="")
    assert r.ok is False
    assert r.stub is False
    assert r.detail == "missing_OPENROUTER_API_KEY"
    assert r.guidance == BOUNDED_READ_GUIDANCE
    assert r.content_hash == content_sha256(_large_body())


def test_recursion_guard(monkeypatch):
    monkeypatch.setenv("SHUNT_INTERNAL", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    r = bulk_read("src/big.py", content=_large_body(), api_key="sk-test")
    assert r.ok is False
    assert r.detail == "recursion_guard_SHUNT_INTERNAL"
    assert r.guidance == BOUNDED_READ_GUIDANCE
    assert r.stub is False


def test_gate_still_blocks_small(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("SHUNT_INTERNAL", raising=False)
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not HTTP")

    r = bulk_read("tiny.py", content="a\nb\n", api_key="sk-test", http_post=boom)
    assert r.ok is False
    assert r.detail == "below_min_lines"
    assert called["n"] == 0


def test_mocked_success(monkeypatch):
    monkeypatch.delenv("SHUNT_INTERNAL", raising=False)
    body = _large_body()
    digest = content_sha256(body)

    def fake_post(url, headers, raw, timeout):
        assert "openrouter.ai" in url
        assert url.endswith("/chat/completions")
        assert headers["Authorization"] == "Bearer sk-test"
        payload = json.loads(raw.decode())
        assert payload["model"] == OPENROUTER_MODEL
        assert "POINTS ONLY" in payload["messages"][0]["content"] or "POINTS" in payload["messages"][0]["content"]
        assert digest in payload["messages"][1]["content"]
        return 200, {
            "choices": [
                {
                    "message": {
                        "content": (
                            f"- symbols: fn_0..fn_N\n"
                            f"- range L1-L40\n"
                            f"- content_hash {digest}\n"
                            f"- coverage: full file scanned\n"
                        )
                    }
                }
            ]
        }

    r = bulk_read("src/big.py", content=body, api_key="sk-test", http_post=fake_post)
    assert r.ok is True
    assert r.stub is False
    assert r.detail == "openrouter_ok"
    assert r.model == OPENROUTER_MODEL
    assert r.content_hash == digest
    assert r.guidance is None
    assert any("fn_0" in p for p in r.points)
    assert any(digest[:12] in p or digest in p for p in r.points)


def test_mocked_http_fail(monkeypatch):
    monkeypatch.delenv("SHUNT_INTERNAL", raising=False)

    def fake_post(url, headers, raw, timeout):
        return 429, {"error": {"message": "Rate limit"}}

    r = bulk_read("src/big.py", content=_large_body(), api_key="sk-test", http_post=fake_post)
    assert r.ok is False
    assert r.stub is False
    assert r.http_status == 429
    assert "openrouter_http_429" in r.detail
    assert r.guidance == BOUNDED_READ_GUIDANCE


def test_mocked_network_fail(monkeypatch):
    monkeypatch.delenv("SHUNT_INTERNAL", raising=False)

    def fake_post(url, headers, raw, timeout):
        raise TimeoutError("timed out")

    r = bulk_read("src/big.py", content=_large_body(), api_key="sk-test", http_post=fake_post)
    assert r.ok is False
    assert r.detail == "openrouter_timeout"
    assert r.guidance == BOUNDED_READ_GUIDANCE


@pytest.mark.skipif(os.environ.get("SHUNT_LIVE_TEST") != "1", reason="set SHUNT_LIVE_TEST=1 for live smoke")
def test_live_openrouter_smoke():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        pytest.skip("OPENROUTER_API_KEY missing")
    # Build >350 lines of structured content
    lines = []
    for i in range(120):
        lines.append(f"class Widget{i}:")
        lines.append(f"    def run(self):")
        lines.append(f"        return {i}")
    body = "\n".join(lines)
    assert body.count("\n") + 1 >= 350
    r = bulk_read("fixtures/live_smoke.py", content=body, api_key=key)
    assert r.stub is False
    if not r.ok:
        # Clear blocker acceptable if OpenRouter rejects
        assert r.guidance == BOUNDED_READ_GUIDANCE
        assert "openrouter" in r.detail or "missing" in r.detail
        pytest.skip(f"OpenRouter blocked live smoke: {r.detail}")
    assert len(r.points) >= 1
    assert r.model == OPENROUTER_MODEL
    assert r.content_hash == content_sha256(body)
