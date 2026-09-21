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
