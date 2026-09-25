import importlib.util
import json
from pathlib import Path

import pytest
from jev_skill_advisor.catalog_choice import MODEL

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "adapters" / "hermes" / "capability_hint.py"
OBSERVED_BRANCH = (
    "    result = _selected_context(selected)\n"
    "    _record_attempt(payload, session_id, delivered=bool(result and result.get(\"context\") != _MISS))\n"
    "    _TURN[key] = result\n"
    "    return result\n"
)
HASH = "ab" * 32


def _load():
    spec = importlib.util.spec_from_file_location("hermes_capability_hint", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hint = _load()


@pytest.fixture(autouse=True)
def _no_full_selector(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("full selector ran")

    monkeypatch.setattr("jev_skill_advisor.catalog_choice.catalog_choice_scan", boom)
    monkeypatch.setattr("jev_skill_advisor.select_cli.select_task", boom)
    monkeypatch.setattr("jev_skill_advisor.select_cli.select_named", boom)


def _entry(identifier, operation, summary):
    return {
        "id": identifier,
        "server": "notion",
        "operation": operation,
        "summary": summary,
        "writes": False,
        "schema_hash": HASH,
        "source": "adapter-test",
        "provenance": "fakeable hermes adapter test",
    }


def _manifest(tmp_path, entries):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "manifest_version": 1,
        "description": "Hermes adapter test manifest.",
        "entries": entries,
    }), encoding="utf-8")
    return path


def _evaluator(use):
    calls = []

    def evaluator(payload, timeout):
        calls.append(payload)
        answers = {}
        for index, card in enumerate(payload["state"]["candidates"]):
            answers[f"fit_{index}"] = {"type": "noul", "noul": 0.9 if card["id"] in use else 0.1}
        answers["write_intent"] = {"type": "noul", "noul": 0.1}
        return {"model": MODEL, "usage": {"input_tokens": 4}, "answers": answers}

    evaluator.calls = calls
    return evaluator


class _Service:
    def __init__(self, skill_id="warehouse:notion"):
        self.skill_id = skill_id
        self.suggests = []
        self.receipts = []
        self.runtime = self

    def suggest(self, value):
        self.suggests.append(value)
        if "available_ids" in value:
            raise AssertionError("catalog ids were supplied")
        return {
            "status": "explicit_selection",
            "receipt_id": "receipt123",
            "selected": [{"id": self.skill_id}],
        }

    def update_receipt(self, receipt_id, update):
        receipt = {"receipt_id": receipt_id, "session_id": "session-1", "expires_at": "2099-01-01T00:00:00Z"}
        assert update(receipt) is None
        self.receipts.append(receipt)


def _notion(body="NOTION_BODY_SENTINEL"):
    return {
        "selected": {
            "skill_id": "warehouse:notion",
            "name": "notion",
            "content": body,
        }
    }


def test_observed_plugin_branch_matches_patch_anchor():
    assert hint.SUCCESS_BRANCH == OBSERVED_BRANCH
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "skill-search" not in source
    assert "select_task" not in source
    assert "catalog_choice_scan" not in source


def test_non_notion_preserves_body_and_skips_jev(tmp_path):
    path = _manifest(tmp_path, [_entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id.")])
    evaluator = _evaluator(("notion.mcp.fetch",))
    service = _Service(skill_id="warehouse:tailscale")
    body = "TAILSCALE_BODY"
    result = hint.pre_model_context(
        {"selected": {"skill_id": "warehouse:tailscale", "name": "tailscale", "content": body}},
        "check the tailnet",
        manifest_path=path,
        evaluator=evaluator,
        session_id="session-1",
        service=service,
    )
    assert result["capability_calls"] == 0
    assert result["receipt_id"] is None
    assert service.suggests == []
    assert evaluator.calls == []
    assert body in result["context"]
    assert "capability-hint" not in result["context"]
    assert result["context"].startswith("Selected skill instructions follow.")


def test_notion_hint_and_service_receipt_skip_the_catalog(tmp_path):
    path = _manifest(tmp_path, [
        _entry("notion.mcp.search", "notion-search", "Search Notion by keyword."),
        _entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id."),
    ])
    evaluator = _evaluator(("notion.mcp.fetch",))
    service = _Service()
    body = "Use <skill> as written.\nLine 2"
    task = "read the operations page in Notion"
    result = hint.pre_model_context(
        _notion(body), task, manifest_path=path, evaluator=evaluator,
        session_id="session-1", service=service,
    )
    assert result["capability_calls"] == 1
    assert len(evaluator.calls) == 1
    assert set(evaluator.calls[0]["questions"]) == {"fit_0", "fit_1", "write_intent"}
    assert "inputSchema" not in json.dumps(evaluator.calls[0])
    assert result["receipt_id"] == "receipt123"
    assert service.suggests[0]["explicit_skills"] == ["warehouse:notion"]
    assert service.suggests[0]["context"] == ""
    assert body not in json.dumps(service.suggests[0])
    receipt = service.receipts[0]
    assert receipt["capability_ids"] == ["notion.mcp.fetch"]
    assert receipt["capability_manifest_hash"]
    assert "correlation" not in receipt
    assert result["capabilities"]["correlation"] not in json.dumps(receipt)
    assert result["capabilities"]["authorizes_calls"] is False
    context = result["context"]
    assert context.index(body) < context.index("</selected-skill>") < context.index("<capability-hint")
    assert context.index("<capability-hint") < context.index("<capability-receipt")
    assert "mcp__jev_skill_advisor__notion_fetch" in context
    assert "not mcp__notion__notion_fetch" in context
    assert 'authorizes-calls="false"' in context
    assert "notion.mcp.fetch: Read one Notion page by id." in context
    assert "notion.mcp.search" not in context.split("<capability-hint", 1)[1]
    assert "inputSchema" not in context
    assert result["event_fields"]["capability_surface"] == "service"
    assert result["event_fields"]["capability_ids"] == ["notion.mcp.fetch"]


def test_hint_caps_at_five_cards(tmp_path):
    entries = [
        _entry(f"notion.mcp.{name}", f"notion-{name}", f"Read card {name}.")
        for name in ("f", "a", "c", "e", "b", "d")
    ]
    path = _manifest(tmp_path, entries)
    evaluator = _evaluator(tuple(entry["id"] for entry in entries))
    result = hint.pre_model_context(
        _notion(), "work in Notion", manifest_path=path, evaluator=evaluator, session_id="session-1",
    )
    hint_body = result["context"].split("<capability-hint", 1)[1].split("</capability-hint>", 1)[0]
    lines = [line for line in hint_body.splitlines() if line.startswith("notion.mcp.")]
    assert lines == [
        "notion.mcp.a: Read card a.",
        "notion.mcp.b: Read card b.",
        "notion.mcp.c: Read card c.",
        "notion.mcp.d: Read card d.",
        "notion.mcp.e: Read card e.",
    ]
    assert "notion.mcp.f" not in result["context"]
    assert result["receipt_id"] is None
    assert "capability-receipt" not in result["context"]


def test_explicit_tool_name_does_not_call_jev(tmp_path):
    path = _manifest(tmp_path, [_entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id.")])
    evaluator = _evaluator(("notion.mcp.fetch",))
    service = _Service()
    result = hint.pre_model_context(
        _notion(), "please notion-fetch the operations page",
        manifest_path=path, evaluator=evaluator, session_id="session-1", service=service,
    )
    assert evaluator.calls == []
    assert result["capability_calls"] == 0
    assert "notion.mcp.fetch: Read one Notion page by id." in result["context"]
    assert service.receipts[0]["capability_ids"] == ["notion.mcp.fetch"]


def test_timeout_and_miss_preserve_fallback(tmp_path):
    path = _manifest(tmp_path, [_entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id.")])

    def fail(_payload, _timeout):
        raise TimeoutError("typesafe_timeout")

    service = _Service()
    body = "NOTION_BODY_SENTINEL"
    failed = hint.pre_model_context(
        _notion(body), "read a Notion page", manifest_path=path, evaluator=fail,
        session_id="session-1", service=service,
    )
    assert failed["capability_calls"] == 1
    assert body in failed["context"]
    assert "capability-hint" not in failed["context"]
    assert failed["receipt_id"] is None
    assert service.suggests == []

    missed = hint.pre_model_context(
        {"selected": None, "reason": "catalog_choice_none"}, "read a Notion page",
        manifest_path=path, evaluator=fail, service=service,
    )
    assert missed["context"] == hint.MISS
    assert missed["capability_calls"] == 0
    assert "capability-hint" not in missed["context"]


def test_no_manifest_and_tampered_correlation_do_not_authorize(tmp_path):
    path = _manifest(tmp_path, [_entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id.")])
    evaluator = _evaluator(("notion.mcp.fetch",))
    bare = hint.pre_model_context(_notion(), "read a Notion page", evaluator=evaluator, service=_Service())
    assert bare["capability_calls"] == 0
    assert "NOTION_BODY_SENTINEL" in bare["context"]
    assert "capability-hint" not in bare["context"]

    selected = hint.pre_model_context(
        _notion(), "read a Notion page", manifest_path=path, evaluator=evaluator,
    )
    public = dict(selected["capabilities"])
    public["correlation"] = "f" * 64
    assert hint.format_hint(public, "warehouse:notion") == ""
    forged = dict(public)
    forged["authorizes_calls"] = True
    assert hint.format_hint(forged, "warehouse:notion") == ""


def test_plugin_patch_keeps_selector_and_cache():
    fixture = (
        "from pathlib import Path\n\n"
        "HOME = Path(\"/home/dev\")\n"
        "NATIVE_SKILLS = HOME / \".hermes\" / \"skills\"\n"
        "_MISS = \"miss\"\n"
        "_TURN = {}\n\n"
        "def _pre_llm_call(**kwargs):\n"
        "    text = \"task\"\n"
        "    session_id = kwargs.get(\"session_id\")\n"
        "    key = (session_id if isinstance(session_id, str) else \"\", text)\n"
        "    if key in _TURN:\n"
        "        return _TURN[key]\n"
        "    payload = _select_result(text, timeout=22)\n"
        "    selected = payload.get(\"selected\")\n"
        "    if not isinstance(selected, dict):\n"
        "        _record_attempt(payload, session_id, delivered=False)\n"
        "        result = {\"context\": _MISS}\n"
        "        _TURN[key] = result\n"
        "        return result\n"
        + hint.SUCCESS_BRANCH
    )
    patched = hint.plugin_patch(fixture)
    compile(patched, "<plugin>", "exec")
    assert patched.count("pre_model_context(") == 1
    assert "_select_result(text, timeout=22)" in patched
    assert "if key in _TURN:" in patched
    assert "--capability-manifest" not in patched
    assert "CAPABILITY_ENABLED = False\n" in patched
    assert f'RELEASE_ROOT = "{hint.INSTALLED_RELEASE_ROOT}"\n' in patched
    assert "release_root=RELEASE_ROOT" in patched
    assert "enabled=CAPABILITY_ENABLED" in patched
    assert "events_path=CAPABILITY_EVENTS" in patched
    assert f'CAPABILITY_EVENTS = "{hint.INSTALLED_EVENTS_PATH}"\n' in patched
    assert "from capability_hint import live_pre_model_context\n" in patched
    assert "CAPABILITY_MANIFEST" not in patched
    assert "ADVISOR_SERVICE" not in patched
    with pytest.raises(ValueError):
        hint.plugin_patch(patched)


class _ReleaseStore:
    def __init__(self, snapshot_id="snap-live", path=None, release_id="release-1"):
        self.snapshot_id = snapshot_id
        self.path = path
        self.release_id = release_id
        self.profile = {"profile": "hermes"}
        self.resolves = []
        self.manifest_calls = []
        self.resolve_error = None
        self.manifest_error = None

    def resolve_profile(self, *, host, harness, session_id):
        self.resolves.append({"host": host, "harness": harness, "session_id": session_id})
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.release_id, {"snapshot_id": self.snapshot_id}, self.profile

    def capability_manifest_path(self, release_id):
        self.manifest_calls.append(release_id)
        if self.manifest_error is not None:
            raise self.manifest_error
        return self.path


def _live_service(evaluator, skill_id="warehouse:notion"):
    service = _Service(skill_id)
    service.evaluator = evaluator
    return service


def _notion_live(snapshot_id="snap-live", body="NOTION_BODY_SENTINEL"):
    payload = _notion(body)
    payload["selected"]["snapshot_id"] = snapshot_id
    return payload


def test_live_notion_uses_bound_manifest_once(tmp_path, monkeypatch):
    path = _manifest(tmp_path, [
        _entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id."),
    ])
    evaluator = _evaluator(("notion.mcp.fetch",))
    service = _live_service(evaluator)
    store = _ReleaseStore(path=path)
    built = []
    seen = []
    events = tmp_path / "capability-events.jsonl"

    def factory(profile):
        built.append(profile)
        return service

    original = hint.pre_model_context

    def once(*args, **kwargs):
        seen.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(hint, "pre_model_context", once)
    monkeypatch.setattr(hint.socket, "gethostname", lambda: "dest-host")
    result = hint.live_pre_model_context(
        _notion_live(), "read the operations page in Notion",
        session_id="session-1", enabled=True, store=store, service_factory=factory,
        events_path=events,
    )
    assert store.resolves == [{"host": "dest-host", "harness": "hermes", "session_id": "session-1"}]
    assert store.manifest_calls == ["release-1"]
    assert built == [store.profile]
    assert len(seen) == 1
    assert seen[0]["manifest_path"] == path
    assert seen[0]["evaluator"] is evaluator
    assert seen[0]["service"] is service
    assert result["capability_calls"] == 1
    assert len(evaluator.calls) == 1
    assert result["receipt_id"] == "receipt123"
    assert service.receipts[0]["capability_ids"] == ["notion.mcp.fetch"]
    assert "notion.mcp.fetch: Read one Notion page by id." in result["context"]
    logged = json.loads(events.read_text(encoding="utf-8"))
    assert logged["stage"] == "discovery"
    assert logged["capability_ids"] == ["notion.mcp.fetch"]
    assert logged["receipt_id"] == "receipt123"
    assert logged["status"] == "selected"
    assert "read the operations page" not in events.read_text(encoding="utf-8")
    assert "NOTION_BODY_SENTINEL" not in events.read_text(encoding="utf-8")


def test_live_event_failure_keeps_skill_body_without_hint(tmp_path):
    path = _manifest(tmp_path, [_entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id.")])
    service = _live_service(_evaluator(("notion.mcp.fetch",)))
    result = hint.live_pre_model_context(
        _notion_live(), "read a Notion page", session_id="session-1",
        enabled=True, host="dest-host", store=_ReleaseStore(path=path),
        service_factory=lambda _profile: service, events_path=tmp_path,
    )
    assert "NOTION_BODY_SENTINEL" in result["context"]
    assert "capability-hint" not in result["context"]
    assert "capability-receipt" not in result["context"]


def test_live_disabled_non_notion_and_bad_session_stay_on_skill_context(tmp_path):
    path = _manifest(tmp_path, [_entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id.")])
    store = _ReleaseStore(path=path)
    body = "NOTION_BODY_SENTINEL"
    disabled = hint.live_pre_model_context(
        _notion_live(body=body), "read a Notion page", session_id="session-1",
        enabled=False, store=store, host="dest-host",
    )
    assert disabled["context"] == hint.skill_context(_notion_live(body=body)["selected"])
    assert disabled["capability_calls"] == 0
    other = hint.live_pre_model_context(
        {"selected": {"skill_id": "warehouse:tailscale", "name": "tailscale", "snapshot_id": "snap-live", "content": "TAIL"}},
        "check the tailnet", session_id="session-1", enabled=True, store=store, host="dest-host",
    )
    assert "TAIL" in other["context"]
    assert "capability-hint" not in other["context"]
    for session_id in (None, "", "   ", 7):
        skipped = hint.live_pre_model_context(
            _notion_live(), "read a Notion page", session_id=session_id,
            enabled=True, store=store, host="dest-host",
        )
        assert "NOTION_BODY_SENTINEL" in skipped["context"]
        assert skipped["capability_calls"] == 0
    assert store.resolves == []
    assert store.manifest_calls == []


def test_live_mismatch_and_failures_do_not_call_jev(tmp_path, monkeypatch):
    path = _manifest(tmp_path, [_entry("notion.mcp.fetch", "notion-fetch", "Read one Notion page by id.")])
    evaluator = _evaluator(("notion.mcp.fetch",))
    service = _live_service(evaluator)
    calls = {"factory": 0}

    def factory(_profile):
        calls["factory"] += 1
        return service

    mismatch = _ReleaseStore(snapshot_id="release-snap", path=path)
    mismatched = hint.live_pre_model_context(
        _notion_live("other-snap"), "read a Notion page", session_id="session-1",
        enabled=True, host="dest-host", store=mismatch, service_factory=factory,
    )
    assert mismatch.resolves
    assert mismatch.manifest_calls == []
    assert "capability-hint" not in mismatched["context"]
    assert evaluator.calls == []

    missing = _ReleaseStore(path=None)
    bare = hint.live_pre_model_context(
        _notion_live(), "read a Notion page", session_id="session-1",
        enabled=True, host="dest-host", store=missing, service_factory=factory,
    )
    assert missing.manifest_calls == ["release-1"]
    assert bare["capability_calls"] == 0
    assert calls["factory"] == 0

    broken = _ReleaseStore(path=path)
    broken.resolve_error = RuntimeError("session pin missing")
    failed = hint.live_pre_model_context(
        _notion_live(), "read a Notion page", session_id="session-1",
        enabled=True, host="dest-host", store=broken, service_factory=factory,
    )
    assert "session pin missing" not in failed["context"]
    assert "NOTION_BODY_SENTINEL" in failed["context"]

    refused = _ReleaseStore(path=path)
    refused.manifest_error = RuntimeError("capability_manifest_invalid")
    rejected = hint.live_pre_model_context(
        _notion_live(), "read a Notion page", session_id="session-1",
        enabled=True, host="dest-host", store=refused, service_factory=factory,
    )
    assert refused.manifest_calls == ["release-1"]
    assert "capability-hint" not in rejected["context"]
    assert calls["factory"] == 0

    def explode(_profile):
        raise RuntimeError("credential material")

    down = hint.live_pre_model_context(
        _notion_live(), "read a Notion page", session_id="session-1",
        enabled=True, host="   ", store=_ReleaseStore(path=path), service_factory=explode,
    )
    assert "credential material" not in down["context"]
    assert down["capability_calls"] == 0

    def no_hostname():
        raise OSError("no host")

    monkeypatch.setattr(hint.socket, "gethostname", no_hostname)
    no_host = hint.live_pre_model_context(
        _notion_live(), "read a Notion page", session_id="session-1",
        enabled=True, store=_ReleaseStore(path=path), service_factory=explode,
    )
    assert no_host["capability_calls"] == 0
    assert evaluator.calls == []
