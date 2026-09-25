import json
from types import SimpleNamespace

from jev_skill_advisor.selector_eval import _outcome, evaluate


def test_eval_uses_live_selector_callable_and_separates_provider_failure(tmp_path):
    entries = {sid: SimpleNamespace(description=description) for sid, description in {
        "warehouse:tailscale": "Troubleshoot Tailscale networks",
        "warehouse:plan": "Write implementation plans",
    }.items()}
    profile = SimpleNamespace(entries=entries, eligible_ids=frozenset(entries),
                              registry=lambda *_args, **_kwargs: SimpleNamespace(eligible=lambda: [SimpleNamespace(id=sid) for sid in entries]))
    cases = {"cases": [
        {"id": "good", "task": "Troubleshoot Tailscale", "expect": "selection", "acceptable_ids": ["warehouse:tailscale"]},
        {"id": "bad", "task": "Write implementation plan", "expect": "selection", "acceptable_ids": ["warehouse:plan"]},
        {"id": "none", "task": "Say hello", "expect": "abstain", "acceptable_ids": []},
        {"id": "failure", "task": "SECRET PROMPT Tailscale is broken", "expect": "selection", "acceptable_ids": ["warehouse:tailscale"]},
    ]}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases))
    seen = []

    def selector(task, **_kwargs):
        seen.append(task)
        if task == "SECRET PROMPT Tailscale is broken":
            return {"selected": None, "status": "incomplete", "reason": "catalog_choice_provider_failure", "eligible_count": 2}
        if task == "Write implementation plan":
            return {"selected": {"skill_id": "warehouse:tailscale"}, "status": "complete", "eligible_count": 2}
        if task == "Say hello":
            return {"selected": None, "status": "complete", "reason": "catalog_choice_none", "eligible_count": 2}
        return {"selected": {"skill_id": "warehouse:tailscale", "content": "SECRET BODY"}, "status": "complete", "raw_provider_payload": "SECRET PROVIDER", "eligible_count": 2}

    report = evaluate(path, profile=profile, selector=selector)
    assert len(seen) == 4
    assert report["metrics"]["jev"]["outcomes"] == {"correct_selection": 1, "wrong_selection": 1, "correct_abstention": 1, "provider_failure": 1}
    assert report["metrics"]["jev"]["top1_precision"] == 0.5
    assert report["metrics"]["jev"]["correct_abstention_rate"] == 1.0
    assert report["metrics"]["jev"]["provider_failure_rate"] == 0.25
    assert report["lexical_eligible_count"] == 2
    assert "SECRET PROMPT" not in json.dumps(report)
    assert "SECRET BODY" not in json.dumps(report)
    assert "SECRET PROVIDER" not in json.dumps(report)


def test_candidate_universe_mismatch_fails_closed(tmp_path):
    entries = {"warehouse:one": SimpleNamespace(description="One skill")}
    profile = SimpleNamespace(entries=entries, eligible_ids=frozenset(entries),
                              registry=lambda *_args, **_kwargs: SimpleNamespace(eligible=lambda: [SimpleNamespace(id="warehouse:one")]))
    cases = {"cases": [{"id": str(i), "task": "one skill", "expect": "selection", "acceptable_ids": ["warehouse:one"]} for i in range(4)]}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases))
    from pytest import raises
    with raises(ValueError, match="candidate_universe_mismatch"):
        evaluate(path, profile=profile, selector=lambda *_args, **_kwargs: {"selected": None, "eligible_count": 2})


def test_deadline_is_not_a_provider_failure_or_clean_abstention():
    assert _outcome(None, {"expect": "abstain", "acceptable_ids": []}, "absolute_deadline", "incomplete") == "deadline"
