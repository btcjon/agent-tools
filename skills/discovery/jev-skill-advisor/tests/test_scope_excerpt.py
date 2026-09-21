from jev_skill_advisor.exposure import scope_excerpt


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
