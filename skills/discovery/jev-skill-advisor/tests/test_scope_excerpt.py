import json
from jev_skill_advisor.exposure import Capability, envelope, fits, scope_excerpt


def test_scope_excerpt_selects_late_applicability_exclusions_and_procedure():
    body="---\nname: demo\n---\n# Intro\n"+("preface\n"*400)+"## Use When\nportable packages\n## Do Not Use\nnot BB local\n## Procedure\n1. inspect\n2. build\n"
    value=scope_excerpt(body)
    assert "portable packages" in value["text"]
    assert "not BB local" in value["text"]
    assert "1. inspect" in value["text"]
    assert [item["heading"] for item in value["sections"]]==["Use When","Do Not Use","Procedure"]
    assert len(value["text"].encode())<=1200


def test_scope_excerpt_missing_or_malformed_headings_falls_back_safely():
    value=scope_excerpt("---\nname: demo\n---\nplain scope\n"+("x"*2000))
    assert value["fallback"] is True and "plain scope" in value["text"]
    assert value["truncated"] is True and len(value["text"].encode())<=1200


def test_scope_excerpt_focuses_long_procedure_on_request_terms():
    body="# Demo\n## When to Use\nUse for databases.\n## Procedure\n1. Authenticate.\n"+("unrelated setup\n"*100)+"3. Update typed database properties with the API.\n4. Verify.\n"
    value=scope_excerpt(body,query="update database property through API")
    assert "typed database properties" in value["text"]
    procedure=next(item for item in value["sections"] if item["heading"]=="Procedure")
    assert procedure["selected_lines"] is not None
    assert procedure["selected_lines"]==sorted(procedure["selected_lines"])
    assert procedure["omitted_content"] is True


def test_detail_excerpt_rejects_protected_markers_outside_file_prefix(tmp_path):
    import hashlib
    from jev_skill_advisor.exposure import Capability,detail_envelope
    source=tmp_path/"SKILL.md"; source.write_text("# Intro\n"+("safe\n"*2000)+"## Procedure\nAuthorization: Bearer secret\n")
    entry=Capability("x","skill","demo",source=str(source),source_hash=hashlib.sha256(source.read_bytes()).hexdigest(),policy_hash="p",disclose=True)
    try: detail_envelope("authorization procedure","",[entry])
    except ValueError as exc: assert str(exc)=="protected_skill_excerpt"
    else: raise AssertionError("protected excerpt was not rejected")


def test_detail_excerpt_rejects_marker_hidden_by_relevant_line_clipping(tmp_path):
    import hashlib
    from jev_skill_advisor.exposure import Capability,detail_envelope
    source=tmp_path/"SKILL.md"
    source.write_text("## Procedure\nAuthorization: Bearer SECRET_SENTINEL "+("padding "*300)+"database update\n")
    entry=Capability("x","skill","demo",source=str(source),source_hash=hashlib.sha256(source.read_bytes()).hexdigest(),policy_hash="p",disclose=True)
    try: detail_envelope("database update","",[entry])
    except ValueError as exc: assert str(exc)=="protected_skill_excerpt"
    else: raise AssertionError("clipped protected source was not rejected")


def test_detail_excerpt_rejects_marker_in_long_heading_metadata(tmp_path):
    import hashlib
    from jev_skill_advisor.exposure import Capability,detail_envelope
    source=tmp_path/"SKILL.md"
    source.write_text("## Procedure "+("padding "*300)+"Authorization: Bearer SECRET_SENTINEL\nSafe body.\n")
    entry=Capability("x","skill","demo",source=str(source),source_hash=hashlib.sha256(source.read_bytes()).hexdigest(),policy_hash="p",disclose=True)
    try: detail_envelope("procedure","",[entry])
    except ValueError as exc: assert str(exc)=="protected_skill_excerpt"
    else: raise AssertionError("protected heading metadata was not rejected")


def test_screening_cards_include_bounded_source_verbatim_evidence(tmp_path):
    import hashlib
    source=tmp_path/"SKILL.md"
    source.write_text("## Use When\nUpdate database properties through the API.\n## Do Not Use When\nA browser-only workflow was requested.\n## Procedure\nDo the work.\n")
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    entry=Capability("warehouse:notion","skill","Manage Notion",source=str(source),source_hash=digest,policy_hash="p",disclose=True)
    payload=envelope("update database property","",[entry])
    card=payload["state"]["capabilities"][0]
    assert "Update database properties" in card["applicability_evidence"]
    assert "browser-only" in card["applicability_evidence"]
    assert len(card["applicability_evidence"].encode()) <= 240
    assert payload["_cache_identity"]["capabilities"][0]["source_hash"] == digest
    assert fits(payload)


def test_screening_cards_reject_protected_content_before_clipping(tmp_path):
    import hashlib
    source=tmp_path/"SKILL.md"
    source.write_text("## Use When\nSafe.\n"+("padding\n"*500)+"Authorization: Bearer SECRET_SENTINEL\n")
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    entry=Capability("x","skill","demo",source=str(source),source_hash=digest,policy_hash="p",disclose=True)
    try: envelope("safe","",[entry])
    except ValueError as exc: assert str(exc)=="protected_skill_excerpt"
    else: raise AssertionError("protected screening source was not rejected")


def test_selection_contract_is_generic_and_labels_truncated_evidence(tmp_path):
    import hashlib
    from jev_skill_advisor.exposure import CRITERIA, FIT, SELECTION_EVIDENCE_ROLE, detail_envelope, detail_selection_audit
    source=tmp_path/"SKILL.md"
    source.write_text("## Use When\nUpdate database properties through the API.\n" + ("padding\n"*400) + "## Procedure\nDo the work.\n")
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    entry=Capability("warehouse:notion","skill","Manage Notion",source=str(source),source_hash=digest,policy_hash="p",disclose=True)
    screened=envelope("update database property","",[entry])
    detailed=detail_envelope("update database property","",[entry])
    screened_text=json.dumps(screened)
    detailed_text=json.dumps(detailed)
    for payload,text in ((screened,screened_text),(detailed,detailed_text)):
        assert payload["_cache_identity"]["selection_contract"]["version"] == 1
        assert payload["_cache_identity"]["selection_contract"]["fit"] == FIT
        assert payload["_cache_identity"]["selection_contract"]["criteria"] == CRITERIA
        assert payload["_cache_identity"]["selection_contract"]["evidence_role"] == SELECTION_EVIDENCE_ROLE
        assert "complete skill" in FIT
        assert "complete executable procedure" not in FIT
        assert "complete skill" in text
        assert "complete executable procedure" in payload["state"]["data_handling"]
        assert "truncated" in payload["state"]["data_handling"]
        assert "documented procedure" not in text
        assert fits(payload)
    assert "applicability_evidence_role" not in screened["state"]["capabilities"][0]
    assert "scope_excerpt_role" not in detailed["state"]["candidates"][0]
    assert set({key for key in screened if key != "_cache_identity"}) == {"model","state","questions"}
    assert set({key for key in detailed if key != "_cache_identity"}) == {"model","state","questions"}
    assert "best next skill to load" in detailed["questions"]["winner"]["instructions"]
    isolated=dict(screened)
    isolated["_cache_identity"]={**screened["_cache_identity"],"selection_contract":{**screened["_cache_identity"]["selection_contract"],"version":2}}
    assert isolated["_cache_identity"] != screened["_cache_identity"]
    assert "complete executable procedure" in detailed["questions"]["winner"]["instructions"]
    assert detail_selection_audit(0.9, 0.65)["passed"] is False
    assert detail_selection_audit(0.9, 0.65)["failed_predicates"] == ["finalist_fit:0.65<0.8"]
    assert detail_selection_audit(0.9, 0.8)["passed"] is True
