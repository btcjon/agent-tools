import hashlib, json
from pathlib import Path

from jev_skill_advisor.codex_hook import handle_event
from jev_skill_advisor.select_cli import capability_correlation


def event(prompt="Use the alpha workflow",turn="turn-1"):
    return {"hook_event_name":"UserPromptSubmit","session_id":"session-1","turn_id":turn,"prompt":prompt}


def test_emits_complete_package_and_revalidates_replay(tmp_path):
    root=tmp_path/"package"; root.mkdir(); source=root/"SKILL.md"; body="# Alpha\nBODY_SENTINEL\nSee references/detail.md\n"; source.write_text(body)
    (root/"references").mkdir(); (root/"references"/"detail.md").write_text("RESOURCE_SENTINEL")
    catalog=tmp_path/"catalog.json"; catalog.write_text(json.dumps({"entries":[{"stable_id":"shared:alpha","relative_path":"package","content_hash":hashlib.sha256(body.encode()).hexdigest()}]}))
    profile=tmp_path/"profile.json"; profile.write_text(json.dumps({"warehouse_root":str(tmp_path),"catalog_path":str(catalog)}))
    calls=[]
    def runner(request,path,timeout):
        calls.append((request,path)); return {"selected_ids":["shared:alpha"],"status":"explicit_selection","receipt_id":"r1","catalog_hash":"c","policy_hash":"p","telemetry":{"provider_attempts":0},"skills":[{"id":"shared:alpha","body":body,"content_hash":hashlib.sha256(body.encode()).hexdigest(),"canonical_path":str(source),"package_root":str(root)}]}
    first=handle_event(event("Use $shared:alpha"),profile=profile,state=tmp_path/"state",runner=runner)
    second=handle_event(event("Use $shared:alpha"),profile=profile,state=tmp_path/"state",runner=runner)
    assert first==second and len(calls)==2
    assert all(path == profile.resolve() for _, path in calls)
    context=first["hookSpecificOutput"]["additionalContext"]
    assert "BODY_SENTINEL" in context and str(root) in context and "RESOURCE_SENTINEL" not in context


def test_failures_are_empty_and_content_free(tmp_path):
    catalog=tmp_path/"catalog.json"; catalog.write_text('{"entries":[]}'); profile=tmp_path/"profile.json"; profile.write_text(json.dumps({"warehouse_root":str(tmp_path),"catalog_path":str(catalog)}))
    assert handle_event({"bad":True},profile=profile,state=tmp_path/"state")=={}
    assert handle_event(event("TYPESAFE_API_KEY=secret"),profile=profile,state=tmp_path/"state")=={}
    assert "secret" not in (tmp_path/"state"/"events.jsonl").read_text()
    assert handle_event(event(),profile=profile,state=tmp_path/"state",runner=lambda *_: {"skills":[]})=={}
    rows=[json.loads(line) for line in (tmp_path/"state"/"events.jsonl").read_text().splitlines()]
    assert len(rows)==2  # A non-UserPromptSubmit event is not a selection attempt.
    assert all(row["timestamp"].endswith("Z") for row in rows)


def test_rejects_stale_or_oversize_body(tmp_path):
    root=tmp_path/"p"; root.mkdir(); source=root/"SKILL.md"; source.write_text("actual")
    catalog=tmp_path/"catalog.json"; catalog.write_text(json.dumps({"entries":[{"stable_id":"x","relative_path":"p","content_hash":hashlib.sha256(source.read_bytes()).hexdigest()}]})); profile=tmp_path/"profile.json"; profile.write_text(json.dumps({"warehouse_root":str(tmp_path),"catalog_path":str(catalog)}))
    bad={"status":"suggested","receipt_id":"r","selected_ids":["x"],"skills":[{"id":"x","body":"wrong","content_hash":"0"*64,"canonical_path":str(source),"package_root":str(root)}]}
    assert handle_event(event(),profile=profile,state=tmp_path/"state",runner=lambda *_: bad)=={}
    huge="x"*40000; source.write_text(huge); over={"status":"suggested","receipt_id":"r","selected_ids":["x"],"skills":[{"id":"x","body":huge,"content_hash":hashlib.sha256(huge.encode()).hexdigest(),"canonical_path":str(source),"package_root":str(root)}]}
    assert handle_event(event(turn="turn-2"),profile=profile,state=tmp_path/"state",runner=lambda *_: over)=={}


def test_rejects_unlinked_or_unsuccessful_result(tmp_path):
    catalog=tmp_path/"catalog.json"; catalog.write_text('{"entries":[]}'); profile=tmp_path/"profile.json"; profile.write_text(json.dumps({"warehouse_root":str(tmp_path),"catalog_path":str(catalog)}))
    assert handle_event(event(),profile=profile,state=tmp_path/"state",runner=lambda *_:{"status":"none","skills":[]})=={}
    record=json.loads((tmp_path/"state"/"events.jsonl").read_text().splitlines()[-1])
    assert record["selection_mode"]=="none" and record["fallback_reason"]=="unspecified"
    assert record["status"]=="fallback"


def capabilities(cards, skill_id="shared:alpha", status="selected"):
    ids = [card["id"] for card in cards]
    return {
        "advisory": True, "authorizes_calls": False, "status": status, "reason": "capability_choice_selected",
        "manifest_hash": "ab" * 32, "skill_id": skill_id, "cards": cards,
        "correlation": capability_correlation("ab" * 32, skill_id, ids, status),
    }


def skill_result(source, root, body, caps=None):
    result = {"selected_ids": ["shared:alpha"], "status": "suggested", "receipt_id": "r1", "telemetry": {"provider_attempts": 1},
              "skills": [{"id": "shared:alpha", "body": body, "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                          "canonical_path": str(source), "package_root": str(root)}]}
    if caps is not None:
        result["capabilities"] = caps
    return result


def test_capability_hint_is_advisory_and_content_free(tmp_path):
    root = tmp_path / "package"; root.mkdir(); source = root / "SKILL.md"
    body = "# Alpha\nBODY_SENTINEL\n"
    source.write_text(body)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"entries": [{"stable_id": "shared:alpha", "relative_path": "package", "content_hash": hashlib.sha256(body.encode()).hexdigest()}]}))
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"warehouse_root": str(tmp_path), "catalog_path": str(catalog)}))
    card = {"id": "notion.mcp.fetch", "description": "CAPABILITY_SENTINEL", "inputSchema": {"type": "object", "properties": {"page_id": {}}}}
    emitted = handle_event(event(), profile=profile, state=tmp_path / "state", runner=lambda *_: skill_result(source, root, body, capabilities([card])))
    context = emitted["hookSpecificOutput"]["additionalContext"]
    assert "BODY_SENTINEL" in context and "<capability-hint " in context and "CAPABILITY_SENTINEL" in context
    assert "inputSchema" not in context and "page_id" not in context and 'authorizes-calls="false"' in context
    assert context.count("notion.mcp.fetch") == 1
    record = json.loads((tmp_path / "state" / "events.jsonl").read_text().splitlines()[-1])
    assert record["capability_ids"] == ["notion.mcp.fetch"]
    assert record["capability_surface"] == "cli"
    assert "CAPABILITY_SENTINEL" not in json.dumps(record) and "BODY_SENTINEL" not in json.dumps(record)
    many = [{"id": f"notion.mcp.tool{index}", "description": f"CARD_{index}"} for index in range(45)]
    hidden = handle_event(event(turn="turn-2"), profile=profile, state=tmp_path / "state", runner=lambda *_: skill_result(source, root, body, capabilities(many)))
    hidden_context = hidden["hookSpecificOutput"]["additionalContext"]
    assert "BODY_SENTINEL" in hidden_context and "<capability-hint " not in hidden_context and "CARD_44" not in hidden_context
    tampered = capabilities([{"id": "notion.mcp.fetch", "description": "CAPABILITY_SENTINEL"}])
    tampered["correlation"] = "0" * 64
    plain = handle_event(event(turn="turn-3"), profile=profile, state=tmp_path / "state", runner=lambda *_: skill_result(source, root, body, tampered))
    assert "BODY_SENTINEL" in plain["hookSpecificOutput"]["additionalContext"]
    assert "<capability-hint " not in plain["hookSpecificOutput"]["additionalContext"]


def test_emergency_stop_prevents_runner_and_context(tmp_path):
    stop=tmp_path/"EMERGENCY_STOP"; stop.touch()
    catalog=tmp_path/"catalog.json"; catalog.write_text('{"entries":[]}')
    profile=tmp_path/"profile.json"; profile.write_text(json.dumps({"warehouse_root":str(tmp_path),"catalog_path":str(catalog),"emergency_stop_file":str(stop)}))
    called=[]
    assert handle_event(event(),profile=profile,state=tmp_path/"state",runner=lambda *_: called.append(True))=={}
    assert called==[]
    record=json.loads((tmp_path/"state"/"events.jsonl").read_text().splitlines()[-1])
    assert record["fallback_reason"]=="emergency_stop"
