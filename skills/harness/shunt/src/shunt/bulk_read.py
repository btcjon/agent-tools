"""OpenRouter live bulk-read — points only. Never CAPI/gflash/Gemini OAuth."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from shunt.gate import DEFAULT_MAX_BYTES, GateResult, decide

# LOCKED — never substitute CAPI Gemini OAuth / gflash* / Google account Flash.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE_URL}/chat/completions"
OPENROUTER_MODEL = "google/gemini-3.8-flash"

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_PAYLOAD_BYTES = 400_000  # request body ceiling from config
BOUNDED_READ_GUIDANCE = (
    "Use a normal Read with offset/limit (e.g. ~200-line windows) instead of bulk-read. "
    "Do not fall back to CAPI Gemini OAuth, gflash*, or a Google-account Flash path."
)

SYSTEM_PROMPT = """You are a bulk file scanner. Return POINTS ONLY for the main model.
Rules:
- Output bullets only (lines starting with - or *).
- Include: symbol/names, paths, line ranges (e.g. L120-L180), short quotes (<=120 chars), coverage/truncation notes.
- Mention the provided content_hash exactly once.
- Do NOT rewrite the file, decide edits, or invent APIs.
- Do NOT use tools. Text only."""


@dataclass
class BulkReadResult:
    ok: bool
    gate: GateResult
    points: list[str] = field(default_factory=list)
    model: str = OPENROUTER_MODEL
    stub: bool = False
    detail: str = ""
    content_hash: str | None = None
    guidance: str | None = None
    http_status: int | None = None


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def load_openrouter_settings() -> dict[str, Any]:
    """Read timeout / max payload / model from config/default.toml when present."""
    settings = {
        "base_url": OPENROUTER_BASE_URL,
        "model": OPENROUTER_MODEL,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_payload_bytes": DEFAULT_MAX_PAYLOAD_BYTES,
        "max_bytes": DEFAULT_MAX_BYTES,
    }
    cfg = Path(__file__).resolve().parents[2] / "config" / "default.toml"
    if not cfg.is_file():
        return settings
    text = cfg.read_text(encoding="utf-8")
    # Minimal TOML subset (no dependency): key = value lines
    section = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, val = [x.strip() for x in line.split("=", 1)]
        val = val.strip().strip('"').strip("'")
        if section == "openrouter":
            if key == "base_url":
                settings["base_url"] = val.rstrip("/")
            elif key == "model":
                settings["model"] = val
            elif key == "timeout_seconds":
                settings["timeout_seconds"] = int(val)
            elif key == "max_payload_bytes":
                settings["max_payload_bytes"] = int(val)
        elif section == "gate" and key == "max_bytes":
            settings["max_bytes"] = int(val)
    # Env overrides
    if os.environ.get("SHUNT_TIMEOUT"):
        settings["timeout_seconds"] = int(os.environ["SHUNT_TIMEOUT"])
    if os.environ.get("SHUNT_MAX_PAYLOAD_BYTES"):
        settings["max_payload_bytes"] = int(os.environ["SHUNT_MAX_PAYLOAD_BYTES"])
    return settings


def build_messages(path: str, body: str, digest: str) -> list[dict[str, str]]:
    numbered = "\n".join(f"{i:6d}|{line}" for i, line in enumerate(body.splitlines(), 1))
    user = (
        f"path: {path}\n"
        f"content_hash: {digest}\n"
        f"lines: {body.count(chr(10)) + (0 if body.endswith(chr(10)) or not body else 1)}\n"
        f"bytes: {len(body.encode('utf-8', errors='replace'))}\n\n"
        f"FILE:\n{numbered}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_points(assistant_text: str) -> list[str]:
    points: list[str] = []
    for line in (assistant_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("-", "*", "•")):
            points.append(re.sub(r"^[-*•]\s*", "", s).strip())
        else:
            points.append(s)
    return [p for p in points if p]


def _fail(
    gate: GateResult,
    detail: str,
    *,
    digest: str | None = None,
    model: str = OPENROUTER_MODEL,
    http_status: int | None = None,
    points: list[str] | None = None,
) -> BulkReadResult:
    return BulkReadResult(
        ok=False,
        gate=gate,
        points=points or [],
        model=model,
        stub=False,
        detail=detail,
        content_hash=digest,
        guidance=BOUNDED_READ_GUIDANCE,
        http_status=http_status,
    )


HttpPoster = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, Any]]]


def default_http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", 200) or 200
            data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            return int(status), data
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {"error": {"message": raw[:500] or e.reason}}
        return int(e.code), data
    except urllib.error.URLError as e:
        raise TimeoutError(str(e.reason if hasattr(e, "reason") else e)) from e


def call_openrouter(
    *,
    api_key: str,
    path: str,
    body: str,
    digest: str,
    settings: dict[str, Any],
    http_post: HttpPoster | None = None,
) -> BulkReadResult:
    model = settings["model"]
    if model != OPENROUTER_MODEL:
        # Hard lock even if config is tampered
        model = OPENROUTER_MODEL
    messages = build_messages(path, body, digest)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    raw = json.dumps(payload).encode("utf-8")
    if len(raw) > int(settings["max_payload_bytes"]):
        return _fail(
            GateResult(True, "ok", path, bytes=len(body.encode("utf-8", errors="replace"))),
            f"payload_too_large:{len(raw)}>max_payload_bytes:{settings['max_payload_bytes']}",
            digest=digest,
            model=model,
        )

    url = f"{str(settings['base_url']).rstrip('/')}/chat/completions"
    # Force canonical OpenRouter chat completions host path — no alternate providers
    if "openrouter.ai" not in url:
        return _fail(
            GateResult(True, "ok", path),
            "refused_non_openrouter_base_url",
            digest=digest,
            model=model,
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/btcjon/custom-skills",
        "X-Title": "shunt-bulk-read",
    }
    poster = http_post or default_http_post
    try:
        status, data = poster(url, headers, raw, float(settings["timeout_seconds"]))
    except TimeoutError:
        return _fail(GateResult(True, "ok", path), "openrouter_timeout", digest=digest, model=model)
    except Exception as e:  # network / URL errors
        return _fail(
            GateResult(True, "ok", path),
            f"openrouter_network_error:{type(e).__name__}:{e}",
            digest=digest,
            model=model,
        )

    if status >= 400:
        err = data.get("error") if isinstance(data, dict) else None
        msg = ""
        if isinstance(err, dict):
            msg = str(err.get("message") or err)
        elif err:
            msg = str(err)
        return _fail(
            GateResult(True, "ok", path),
            f"openrouter_http_{status}:{msg[:300]}",
            digest=digest,
            model=model,
            http_status=status,
        )

    try:
        choice = (data.get("choices") or [])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        if isinstance(text, list):
            text = "\n".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in text
            )
    except (IndexError, AttributeError, TypeError):
        return _fail(
            GateResult(True, "ok", path),
            "openrouter_bad_response_shape",
            digest=digest,
            model=model,
            http_status=status,
        )

    points = parse_points(str(text))
    if not points:
        return _fail(
            GateResult(True, "ok", path),
            "openrouter_empty_points",
            digest=digest,
            model=model,
            http_status=status,
            points=[],
        )

    # Ensure hash appears in points for traceability
    if not any(digest[:12] in p for p in points):
        points.append(f"content_hash: {digest}")

    return BulkReadResult(
        ok=True,
        gate=GateResult(True, "ok", path),
        points=points,
        model=model,
        stub=False,
        detail="openrouter_ok",
        content_hash=digest,
        guidance=None,
        http_status=status,
    )


def bulk_read(
    path: str,
    *,
    content: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    api_key: str | None = None,
    http_post: HttpPoster | None = None,
) -> BulkReadResult:
    """
    Gate the path, then POST OpenRouter google/gemini-3.8-flash for POINTS ONLY.

    Never falls back to CAPI / gflash / Google OAuth on failure.
    SHUNT_INTERNAL=1 skips the network (recursion / nested bulk-read guard).
    """
    settings = load_openrouter_settings()
    model = OPENROUTER_MODEL

    if content is None and Path(path).is_file():
        content = Path(path).read_text(encoding="utf-8", errors="replace")

    gate = decide(
        path,
        content=content,
        offset=offset,
        limit=limit,
        max_bytes=int(settings.get("max_bytes", DEFAULT_MAX_BYTES)),
    )
    if not gate.allow:
        return _fail(gate, gate.reason, model=model)

    if content is None:
        return _fail(gate, "missing_file_content", model=model)

    digest = content_sha256(content)

    if os.environ.get("SHUNT_INTERNAL", "").strip() == "1":
        return BulkReadResult(
            ok=False,
            gate=gate,
            points=[],
            model=model,
            stub=False,
            detail="recursion_guard_SHUNT_INTERNAL",
            content_hash=digest,
            guidance=BOUNDED_READ_GUIDANCE,
        )

    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return _fail(
            gate,
            "missing_OPENROUTER_API_KEY",
            digest=digest,
            model=model,
            points=["Set OPENROUTER_API_KEY to enable live bulk-read."],
        )

    prev = os.environ.get("SHUNT_INTERNAL")
    os.environ["SHUNT_INTERNAL"] = "1"
    try:
        result = call_openrouter(
            api_key=key,
            path=gate.path,
            body=content,
            digest=digest,
            settings=settings,
            http_post=http_post,
        )
        # Attach accurate gate metadata from outer decide
        result.gate = gate
        return result
    finally:
        if prev is None:
            os.environ.pop("SHUNT_INTERNAL", None)
        else:
            os.environ["SHUNT_INTERNAL"] = prev


def openrouter_request_skeleton(path: str, excerpt: str) -> dict[str, Any]:
    digest = content_sha256(excerpt)
    return {
        "model": OPENROUTER_MODEL,
        "messages": build_messages(path, excerpt, digest),
    }
