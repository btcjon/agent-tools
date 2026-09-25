"""Hermes pre-model capability hint.

The warehouse-skills plugin has already chosen one skill. This module reads
that result, and it asks Jev only for capability cards when that skill is
Notion and a manifest path was passed in. It does not scan the skill catalog.

The live wrapper is opt-in. When enabled, a Notion turn uses the session pin,
the release's immutable capability manifest, and that profile's advisor
service. Every other turn returns the skill context unchanged.

A verified hint is remembered for that session. Hermes ``post_tool_call`` can
then append one content-free native route event. The observer never reads tool
arguments, results, or errors, and a telemetry failure does not change the tool
result.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import uuid
from pathlib import Path

from jev_skill_advisor.capability_observability import append_capability_event
from jev_skill_advisor.capability_choice import notion_skill_selected, resolve_capabilities
from jev_skill_advisor.capability_core import load_manifest
from jev_skill_advisor.release import ReleaseStore
from jev_skill_advisor.select_cli import capability_correlation, public_capabilities
from jev_skill_advisor.service import SkillAdvisorService

MISS = "Selection returned nothing. Continue with tools already available."
MAX_CARDS = 5
INSTALLED_RELEASE_ROOT = "/home/dev/.local/state/jev-skill-advisor/releases"
INSTALLED_EVENTS_PATH = "/home/dev/.local/state/jev-skill-advisor/capability-events/hermes.jsonl"
HARNESS = "hermes"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITY_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,96}$")
_SKILL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_HANDLE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_PROTECTED = ("typesafe_api_key", "jev_api", "authorization: bearer", "bearer ", "-----begin ")

# Exact success branch of dest /home/dev/.hermes/plugins/warehouse-skills/__init__.py
# as read on 2026-09-24. plugin_patch() swaps this and does not write the remote file.
SUCCESS_BRANCH = (
    "    result = _selected_context(selected)\n"
    "    _record_attempt(payload, session_id, delivered=bool(result and result.get(\"context\") != _MISS))\n"
    "    _TURN[key] = result\n"
    "    return result\n"
)
_REPLACEMENT = (
    "    augmented = live_pre_model_context(\n"
    "        payload,\n"
    "        text,\n"
    "        session_id=session_id if isinstance(session_id, str) else None,\n"
    "        release_root=RELEASE_ROOT,\n"
    "        enabled=CAPABILITY_ENABLED,\n"
    "        events_path=CAPABILITY_EVENTS,\n"
    "    )\n"
    "    result = {\"context\": augmented[\"context\"]}\n"
    "    _record_attempt(payload, session_id, delivered=bool(result and result.get(\"context\") != _MISS))\n"
    "    _TURN[key] = result\n"
    "    return result\n"
)
_CONSTANTS = (
    "CAPABILITY_ENABLED = False\n"
    f"RELEASE_ROOT = \"{INSTALLED_RELEASE_ROOT}\"\n"
    f"CAPABILITY_EVENTS = \"{INSTALLED_EVENTS_PATH}\"\n"
)
_IMPORT = "from .capability_hint import live_pre_model_context, observe_post_tool_call\n"
_OBSERVER_FN = (
    "def _post_tool_call(**kwargs):\n"
    "    observe_post_tool_call(kwargs, events_path=CAPABILITY_EVENTS, enabled=CAPABILITY_ENABLED)\n"
    "    return None\n\n\n"
)
_REGISTER_DEF = "def register(ctx):\n"
_REGISTER_HOOK = '    ctx.register_hook("pre_llm_call", _pre_llm_call)\n'
_REGISTER_HOOKS = (
    '    ctx.register_hook("pre_llm_call", _pre_llm_call)\n'
    '    ctx.register_hook("post_tool_call", _post_tool_call)\n'
)
_NATIVE_TOOL = re.compile(r"^mcp__notion__notion_([A-Za-z0-9_]{1,80})$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_NOTION_OPERATION = re.compile(r"^notion-[A-Za-z0-9_-]{1,44}$")
_NATIVE_NAME = re.compile(r"^mcp__notion__notion_[A-Za-z0-9_]{1,44}$")
_SECRET_MARKERS = ("bearer", "oauth", "sk-", "secret", "password", "authorization", "api_key", "apikey")
_HINTS = {}
_HINTS_LOCK = threading.Lock()
_HINT_TTL_S = 3600
_HINT_LIMIT = 512


def skill_context(selected):
    """Same body wrapper the installed plugin uses."""
    if not isinstance(selected, dict):
        return MISS
    content = selected.get("content")
    if not isinstance(content, str) or not content:
        return MISS
    return (
        "Selected skill instructions follow. Use them for this task.\n\n"
        f"<selected-skill id=\"{selected.get('skill_id')}\">\n{content}\n</selected-skill>"
    )


def _selected(payload):
    if not isinstance(payload, dict):
        return None
    if "selected" in payload:
        selected = payload.get("selected")
        return selected if isinstance(selected, dict) else None
    if isinstance(payload.get("skill_id"), str) and "content" in payload:
        return payload
    return None


def _cards(selected):
    skill_id = selected.get("skill_id")
    if not isinstance(skill_id, str) or not skill_id.strip():
        return []
    name = selected.get("name")
    if not isinstance(name, str) or not name.strip():
        name = skill_id.rsplit(":", 1)[-1]
    return [{"id": skill_id, "name": name}]


def _manifest_path(value):
    if isinstance(value, Path):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if not text:
        return None
    return Path(text)


def _protected(task):
    if not isinstance(task, str):
        return False
    lowered = task.lower()
    return any(marker in lowered for marker in _PROTECTED)


def format_hint(public, skill_id, *, native_tools=None):
    """One advisory block. Empty when the object is not a verified five-card hint."""
    if not isinstance(public, dict) or not isinstance(skill_id, str):
        return ""
    if public.get("advisory") is not True or public.get("authorizes_calls") is not False:
        return ""
    if public.get("status") != "selected" or public.get("skill_id") != skill_id:
        return ""
    manifest_hash = public.get("manifest_hash")
    if not isinstance(manifest_hash, str) or not _HASH.fullmatch(manifest_hash):
        return ""
    cards = public.get("cards")
    if not isinstance(cards, list) or not cards or len(cards) > MAX_CARDS:
        return ""
    lines = []
    ids = []
    for card in cards:
        if not isinstance(card, dict):
            return ""
        identifier = card.get("id")
        description = card.get("description")
        if not isinstance(identifier, str) or not _CAPABILITY_ID.fullmatch(identifier):
            return ""
        if not isinstance(description, str) or not description.strip():
            return ""
        if len(description.encode("utf-8")) > 160:
            return ""
        if any(token in description for token in ("inputSchema", "schema_hash", "\n", "<", ">", "{")):
            return ""
        if native_tools is not None:
            native_name = native_tools.get(identifier) if isinstance(native_tools, dict) else None
            if native_name is not None:
                if not isinstance(native_name, str) or not _NATIVE_NAME.fullmatch(native_name):
                    return ""
                lines.append(f"{identifier}: {description} [tool: {native_name}]")
        else:
            lines.append(f"{identifier}: {description}")
        ids.append(identifier)
    expected = capability_correlation(manifest_hash, skill_id, ids, "selected")
    if public.get("correlation") != expected:
        return ""
    if not lines:
        return ""
    body = "\n".join(lines)
    if native_tools is not None:
        body += "\nFor a selected read, load its native schema with tool_describe, then use tool_call. This hint grants no write permission."
    rendered = (
        f'\n\n<capability-hint advisory="true" authorizes-calls="false" manifest-hash="{manifest_hash}">\n'
        f"{body}\n</capability-hint>"
    )
    return rendered if len(rendered.encode("utf-8")) <= 2048 else ""


def _receipt_block(session_id, receipt_id):
    if not isinstance(session_id, str) or not isinstance(receipt_id, str):
        return ""
    if not _HANDLE.fullmatch(session_id) or not _HANDLE.fullmatch(receipt_id):
        return ""
    return (
        f'\n<capability-receipt session="{session_id}" receipt="{receipt_id}">'
        "For a selected read, use mcp__jev_skill_advisor__notion_fetch with this session and receipt, "
        "not mcp__notion__notion_fetch. If the bridge tool is absent, follow the selected skill's fallback. "
        "The advisory correlation is not authorization."
        "</capability-receipt>"
    )


def _native_tools(manifest, cards):
    """Map selected pinned operations to Hermes's exact MCP registry names."""
    mapped = {}
    for card in cards:
        identifier = card.get("id") if isinstance(card, dict) else None
        entry = manifest.entries.get(identifier) if isinstance(identifier, str) else None
        if entry is None or entry.server != "notion":
            return None
        if entry.writes:
            continue
        if not _NOTION_OPERATION.fullmatch(entry.operation):
            return None
        name = "mcp__notion__" + re.sub(r"[^A-Za-z0-9_]", "_", entry.operation)
        if len(name) > 64 or not _NATIVE_NAME.fullmatch(name) or name in mapped.values():
            return None
        mapped[identifier] = name
    return mapped


def bind_service_receipt(service, *, session_id, task, skill_id, capability_ids, manifest_hash, reason):
    """Mint a receipt for the skill already selected, then store capability ids.

    explicit_skills skips catalog retrieval and the rank scan. The advisory
    correlation is not written onto the receipt.
    """
    if service is None or not isinstance(session_id, str) or not _HANDLE.fullmatch(session_id):
        return None
    if not isinstance(skill_id, str) or not _SKILL_ID.fullmatch(skill_id):
        return None
    if not isinstance(task, str) or not task.strip() or len(task.encode("utf-8")) > 8000:
        return None
    if not isinstance(capability_ids, list) or not capability_ids or len(capability_ids) > MAX_CARDS:
        return None
    if any(not isinstance(item, str) or not _CAPABILITY_ID.fullmatch(item) for item in capability_ids):
        return None
    if not isinstance(manifest_hash, str) or not _HASH.fullmatch(manifest_hash):
        return None
    if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,80}", reason):
        reason = "capability_choice_selected"
    try:
        response = service.suggest({
            "protocol_version": 1,
            "request_id": uuid.uuid4().hex,
            "session_id": session_id,
            "task": task,
            "context": "",
            "explicit_skills": [skill_id],
        })
    except Exception:
        return None
    if not isinstance(response, dict) or response.get("status") != "explicit_selection":
        return None
    receipt_id = response.get("receipt_id")
    if not isinstance(receipt_id, str) or not _HANDLE.fullmatch(receipt_id):
        return None
    selected_ids = [
        row.get("id") for row in response.get("selected") or []
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    if skill_id not in selected_ids:
        return None
    evidence = {
        "manifest_hash": manifest_hash,
        "ids": list(capability_ids),
        "status": "selected",
        "reason": reason,
    }

    def update(receipt):
        if not isinstance(receipt, dict):
            return False
        receipt["capability_ids"] = list(capability_ids)
        receipt["capability_manifest_hash"] = manifest_hash
        receipt["capability_selection"] = evidence
        return None

    try:
        service.runtime.update_receipt(receipt_id, update)
    except Exception:
        return None
    return receipt_id


def _base(context, calls=0):
    return {
        "context": context,
        "capability_calls": calls,
        "receipt_id": None,
        "capabilities": None,
        "event_fields": None,
    }


def pre_model_context(payload, task, *, manifest_path=None, evaluator=None, session_id=None, service=None, deadline_s=2.0, route_mode="bridge"):
    """Return plugin context. The skill body stays. A hint is added only for Notion."""
    selected = _selected(payload)
    context = skill_context(selected)
    path = _manifest_path(manifest_path)
    cards = _cards(selected) if isinstance(selected, dict) else []
    if context == MISS or path is None or not cards or not notion_skill_selected(cards) or route_mode not in {"bridge", "native"}:
        return _base(context)
    skill_id = cards[0]["id"]
    if not isinstance(task, str) or _protected(task):
        return _base(context)
    calls = {"n": 0}

    def wrapped(envelope, timeout):
        try:
            blob = json.dumps(envelope)
        except (TypeError, ValueError):
            raise RuntimeError("schema_in_capability_request") from None
        if "inputSchema" in blob or "schema_hash" in blob:
            raise RuntimeError("schema_in_capability_request")
        calls["n"] += 1
        if not callable(evaluator):
            raise RuntimeError("capability_evaluator_unconfigured")
        return evaluator(envelope, timeout)

    try:
        manifest = load_manifest(path)
        decision = resolve_capabilities(
            manifest,
            task,
            context="",
            selected_skills=cards,
            evaluator=wrapped,
            deadline_s=deadline_s,
        )
        public = public_capabilities(decision, skill_id)
    except Exception:
        return _base(context, calls["n"])
    if route_mode == "native":
        native_tools = _native_tools(manifest, public.get("cards") or []) if isinstance(public, dict) else None
        hint = format_hint(public, skill_id, native_tools=native_tools) if native_tools else ""
    else:
        hint = format_hint(public, skill_id)
    if route_mode == "native" and isinstance(public, dict) and public.get("status") == "selected" and not hint:
        return _base(context, calls["n"])
    visible_ids = list(native_tools) if route_mode == "native" and hint else (
        [card["id"] for card in public["cards"]] if hint else []
    )
    receipt_id = None
    if hint:
        receipt_id = bind_service_receipt(
            service,
            session_id=session_id,
            task=task,
            skill_id=skill_id,
            capability_ids=visible_ids,
            manifest_hash=public["manifest_hash"],
            reason=public.get("reason") if isinstance(public.get("reason"), str) else "capability_choice_selected",
        )
    rendered = context + hint
    if receipt_id and route_mode == "bridge":
        rendered += _receipt_block(session_id, receipt_id)
    event_fields = None
    if isinstance(public, dict) and public.get("status") in {"selected", "none", "fail_open"}:
        event_fields = {
            "capability_ids": visible_ids,
            "capability_manifest_hash": public.get("manifest_hash") if isinstance(public.get("manifest_hash"), str) else "",
            "capability_status": public.get("status"),
            "capability_surface": "service" if receipt_id else "advisory",
            "route_mode": route_mode,
        }
    return {
        "context": rendered,
        "capability_calls": calls["n"],
        "receipt_id": receipt_id,
        "capabilities": public,
        "event_fields": event_fields,
    }


def _identity(value):
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _session_host(host):
    if host is None:
        try:
            host = socket.gethostname()
        except OSError:
            return None
    return _identity(host)


def live_pre_model_context(
    payload,
    task,
    *,
    session_id=None,
    release_root=INSTALLED_RELEASE_ROOT,
    enabled=False,
    host=None,
    store=None,
    service_factory=None,
    events_path=None,
    route_mode=None,
):
    """Return today's skill context unless the Notion release path is enabled.

    The enabled path resolves the Hermes session pin, requires the selected
    snapshot id to match that release, and reads only
    ``capability_manifest_path``. The same service supplies the evaluator and
    the receipt binding. Failures, a missing manifest, and every non-Notion
    turn return the skill context with no further lookup.
    """
    forget_verified_hint(session_id)
    selected = _selected(payload)
    base = _base(skill_context(selected))
    if enabled is not True:
        return base
    if route_mode is None:
        route_mode = os.environ.get("JEV_HERMES_CAPABILITY_MODE", "bridge")
    if route_mode == "off" or route_mode not in {"bridge", "native"}:
        return base
    cards = _cards(selected) if isinstance(selected, dict) else []
    if base["context"] == MISS or not cards or not notion_skill_selected(cards):
        return base
    if _identity(session_id) is None:
        return base
    started = time.perf_counter()
    try:
        augmented = _notion_release_context(
            payload,
            task,
            selected=selected,
            session_id=session_id,
            release_root=release_root,
            host=host,
            store=store,
            service_factory=service_factory,
            base=base,
            route_mode=route_mode,
        )
    except Exception:
        return base
    public = augmented.get("capabilities") if isinstance(augmented, dict) else None
    if events_path is not None and isinstance(public, dict):
        status = public.get("status")
        ids = [card.get("id") for card in public.get("cards", []) if isinstance(card, dict)] if status == "selected" else []
        try:
            recorded = append_capability_event(
                Path(events_path), harness="hermes", stage="discovery",
                outcome="selected" if status == "selected" else "absent",
                latency_ms=(time.perf_counter() - started) * 1000,
                capability_ids=ids, host=_session_host(host),
                receipt_id=augmented.get("receipt_id"), session_id=session_id,
                context_bytes=len(augmented["context"].encode("utf-8")),
                manifest_hash=public.get("manifest_hash"),
                status=status, reason=public.get("reason"),
                route_mode=route_mode,
            )
        except (OSError, TypeError, ValueError):
            return base
        if not recorded:
            return base
    try:
        remember_verified_hint(
            session_id,
            receipt_id=augmented.get("receipt_id") if isinstance(augmented, dict) else None,
            manifest_hash=public.get("manifest_hash") if isinstance(public, dict) else None,
            capability_ids=augmented.get("event_fields", {}).get("capability_ids") if isinstance(augmented, dict) and isinstance(augmented.get("event_fields"), dict) else None,
            route_mode=route_mode,
        )
    except Exception:
        pass
    return augmented


def _notion_release_context(
    payload,
    task,
    *,
    selected,
    session_id,
    release_root,
    host,
    store,
    service_factory,
    base,
    route_mode,
):
    host = _session_host(host)
    if host is None:
        return base
    if store is None:
        store = ReleaseStore(Path(release_root))
    release_id, manifest, profile = store.resolve_profile(
        host=host, harness=HARNESS, session_id=session_id,
    )
    if not isinstance(manifest, dict):
        return base
    selected_snapshot = selected.get("snapshot_id")
    release_snapshot = manifest.get("snapshot_id")
    if (not isinstance(selected_snapshot, str) or not selected_snapshot
            or selected_snapshot != release_snapshot):
        return base
    manifest_path = store.capability_manifest_path(release_id)
    if manifest_path is None:
        return base
    factory = service_factory or SkillAdvisorService
    service = factory(profile)
    runtime = getattr(service, "runtime", None)
    evaluator = getattr(runtime, "evaluator", None)
    if not callable(evaluator):
        return base
    return pre_model_context(
        payload,
        task,
        manifest_path=manifest_path,
        evaluator=evaluator,
        session_id=session_id,
        service=service,
        route_mode=route_mode,
    )


def _safe_token(value):
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        return None
    if any(marker in value.lower() for marker in _SECRET_MARKERS):
        return None
    return value


def remember_verified_hint(session_id, *, receipt_id, manifest_hash, capability_ids=None, route_mode="bridge"):
    """Keep one same-process hint. Invalid tokens are ignored."""
    session_id = _safe_token(session_id)
    receipt_id = _safe_token(receipt_id)
    if session_id is None or receipt_id is None:
        return False
    if not isinstance(manifest_hash, str) or not _HASH.fullmatch(manifest_hash):
        return False
    if (not isinstance(capability_ids, list) or not capability_ids or len(capability_ids) > MAX_CARDS
            or any(not isinstance(item, str) or not _CAPABILITY_ID.fullmatch(item) for item in capability_ids)
            or len(set(capability_ids)) != len(capability_ids) or route_mode not in {"bridge", "native"}):
        return False
    with _HINTS_LOCK:
        now = time.monotonic()
        for key, item in list(_HINTS.items()):
            if now - item["seen_at"] > _HINT_TTL_S:
                _HINTS.pop(key, None)
        _HINTS[session_id] = {"receipt_id": receipt_id, "manifest_hash": manifest_hash,
                              "capability_ids": frozenset(capability_ids), "route_mode": route_mode, "seen_at": now}
        while len(_HINTS) > _HINT_LIMIT:
            oldest = next(iter(_HINTS))
            _HINTS.pop(oldest, None)
    return True


def forget_verified_hint(session_id):
    if not isinstance(session_id, str):
        return
    with _HINTS_LOCK:
        _HINTS.pop(session_id, None)


def clear_verified_hints():
    with _HINTS_LOCK:
        _HINTS.clear()


def _tool_id(tool_name):
    if not isinstance(tool_name, str):
        return None
    match = _NATIVE_TOOL.fullmatch(tool_name)
    if match is None:
        return None
    identifier = "notion.mcp." + match.group(1).lower().replace("_", "-")
    if not _CAPABILITY_ID.fullmatch(identifier):
        return None
    if any(marker in identifier for marker in _SECRET_MARKERS):
        return None
    return identifier


def observe_post_tool_call(payload, *, events_path=None, enabled=False):
    """Append one native Notion route event. Telemetry failures return False."""
    try:
        if enabled is not True or not isinstance(payload, dict):
            return False
        tool_id = _tool_id(payload.get("tool_name"))
        session_id = _safe_token(payload.get("session_id"))
        if tool_id is None or session_id is None:
            return False
        with _HINTS_LOCK:
            stored = _HINTS.get(session_id)
            if stored is None:
                return False
            if time.monotonic() - stored["seen_at"] > _HINT_TTL_S:
                _HINTS.pop(session_id, None)
                return False
            receipt_id = stored["receipt_id"]
            manifest_hash = stored["manifest_hash"]
            if tool_id not in stored["capability_ids"]:
                return False
            route_mode = stored["route_mode"]
        status = payload.get("status")
        if status in (None, "", "ok", "success"):
            outcome, event_status, reason = "success", "success", "native"
        else:
            outcome, event_status, reason = "failed", "denied", "other"
        duration = payload.get("duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            latency = 0
        elif 0 <= float(duration) <= 1_000_000_000:
            latency = duration
        else:
            latency = 0
        path = Path(events_path) if isinstance(events_path, str) else events_path
        host = _session_host(None)
        if not isinstance(host, str) or not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$", host):
            host = None
        return bool(append_capability_event(
            path,
            harness=HARNESS,
            stage="invoke",
            outcome=outcome,
            latency_ms=latency,
            capability_ids=[tool_id],
            host=host,
            receipt_id=receipt_id,
            session_id=session_id,
            manifest_hash=manifest_hash,
            status=event_status,
            reason=reason,
            route="native",
            route_mode=route_mode,
        ))
    except Exception:
        return False


def plugin_patch(source):
    """Return plugin source with pre-turn hint and post-tool observer.

    Accept the original source or the already-installed pre-turn patch. This
    function never writes to disk or to dest.
    """
    if not isinstance(source, str):
        raise ValueError("plugin_success_branch_missing")
    if "--capability-manifest" in source or "JEV_CAPABILITY_MANIFEST" in source:
        raise ValueError("selector_already_runs_capability_choice")
    if source.count(SUCCESS_BRANCH) == 1:
        patched = source.replace(SUCCESS_BRANCH, _REPLACEMENT, 1)
    elif source.count(_REPLACEMENT) == 1:
        patched = source
    else:
        raise ValueError("plugin_success_branch_missing")
    if _IMPORT not in patched:
        old_import = "from .capability_hint import live_pre_model_context\n"
        if old_import in patched:
            patched = patched.replace(old_import, _IMPORT, 1)
        else:
            anchor = "from pathlib import Path\n"
            if anchor not in patched:
                raise ValueError("plugin_import_anchor_missing")
            patched = patched.replace(anchor, anchor + "\n" + _IMPORT, 1)
    constants_anchor = 'NATIVE_SKILLS = HOME / ".hermes" / "skills"\n'
    if not re.search(r"^CAPABILITY_ENABLED = (?:True|False)$", patched, re.MULTILINE):
        if constants_anchor not in patched:
            raise ValueError("plugin_constants_anchor_missing")
        patched = patched.replace(constants_anchor, constants_anchor + _CONSTANTS, 1)
    if "def _post_tool_call(" not in patched:
        if patched.count(_REGISTER_DEF) != 1:
            raise ValueError("plugin_post_tool_anchor_missing")
        patched = patched.replace(_REGISTER_DEF, _OBSERVER_FN + _REGISTER_DEF, 1)
    if patched.count(_REGISTER_HOOK) != 1:
        raise ValueError("plugin_post_tool_anchor_missing")
    if 'ctx.register_hook("post_tool_call", _post_tool_call)' not in patched:
        patched = patched.replace(_REGISTER_HOOK, _REGISTER_HOOKS, 1)
    return patched
