from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import time
import urllib.error
import urllib.request

API_URL = "https://api.typesafe.ai/v1/systemone"


class AdvisorError(RuntimeError):
    pass


def load_api_key(env_file: Path | None = None) -> str | None:
    for name in ("TYPESAFE_API_KEY", "JEV_API"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    if env_file is None:
        return None
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AdvisorError(f"cannot read selected env file: {exc}") from exc
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"TYPESAFE_API_KEY", "JEV_API"}:
            values[key.strip()] = value.strip().strip('"\'')
    return values.get("TYPESAFE_API_KEY") or values.get("JEV_API")


def validate_response(payload: dict, questions: dict, requested_model: str) -> dict:
    if not isinstance(payload, dict):
        raise AdvisorError("response is not an object")
    if payload.get("model") != requested_model:
        raise AdvisorError("returned model does not match pinned model")
    answers = payload.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise AdvisorError("response answer keys do not match request")
    for key, question in questions.items():
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise AdvisorError(f"invalid answer type for {key}")
        if question["type"] == "noul":
            _probability(answer.get("noul"), f"{key}.noul")
        elif question["type"] == "choice":
            options = set(question["criteria"])
            if answer.get("choice") not in options:
                raise AdvisorError(f"invalid choice for {key}")
            _distribution(answer.get("probabilities"), options, key)
            _probability(answer.get("confidence"), f"{key}.confidence")
        elif question["type"] == "score":
            value = answer.get("score")
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= len(question["criteria"]) - 1:
                raise AdvisorError(f"invalid score for {key}")
            levels = {str(i) for i in range(len(question["criteria"]))}
            legend = answer.get("legend")
            if not isinstance(legend, dict) or set(map(str, legend)) != levels:
                raise AdvisorError(f"invalid legend for {key}")
            _distribution(answer.get("probabilities"), levels, key, numeric_keys=True)
            _probability(answer.get("confidence"), f"{key}.confidence")
    usage = payload.get("usage")
    if not isinstance(usage, dict) or isinstance(usage.get("input_tokens"), bool) or not isinstance(usage.get("input_tokens"), int) or usage["input_tokens"] < 0:
        raise AdvisorError("missing input token usage")
    return payload


def _probability(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise AdvisorError(f"invalid probability at {label}")
    return float(value)


def _distribution(value: object, options: set[str], label: str, numeric_keys: bool = False) -> None:
    if not isinstance(value, dict):
        raise AdvisorError(f"missing probability distribution for {label}")
    normalized = {str(k) if numeric_keys else k: v for k, v in value.items()}
    if set(normalized) != options:
        raise AdvisorError(f"probability options mismatch for {label}")
    total = sum(_probability(v, f"{label}.probabilities") for v in normalized.values())
    if abs(total - 1.0) > 0.02:
        raise AdvisorError(f"probabilities do not sum to one for {label}")


def evaluate(request_payload: dict, api_key: str, timeout: float) -> tuple[dict, dict]:
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or not math.isfinite(timeout):
        raise AdvisorError("typesafe_timeout")
    data = json.dumps(request_payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "jev-skill-advisor/0.1.0"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise AdvisorError(f"typesafe_http_{exc.code}") from exc
    except TimeoutError as exc:
        raise AdvisorError("typesafe_timeout") from exc
    except socket.timeout as exc:
        raise AdvisorError("typesafe_timeout") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
            raise AdvisorError("typesafe_timeout") from exc
        raise AdvisorError("typesafe_transport_failure") from exc
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    if status != 200:
        raise AdvisorError(f"typesafe_http_{status}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AdvisorError("typesafe_invalid_json") from exc
    return validate_response(payload, request_payload["questions"], request_payload["model"]), {"latency_ms": elapsed_ms, "http_status": status}
