import io
import json
import tarfile

import pytest

from jev_skill_advisor.library_cache import LibraryCache
from jev_skill_advisor.notion_import import Export
from jev_skill_advisor.shadow_eval import ShadowEvalError, run_shadow, write_profile
from jev_skill_advisor.exposure import detail_selection_audit


SKILLS = {
    "plan": "Write a plan when the user asks for one.",
    "session-librarian": "Organize Hermes sessions by prompt.",
    "workspace-product-improvement": "Design Hermes Workspace improvements.",
}


def snapshot(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        plugin = b'{"name":"pilot"}'
        info = tarfile.TarInfo("pilot/plugin.json"); info.size = len(plugin); archive.addfile(info, io.BytesIO(plugin))
        for index, (name, description) in enumerate(SKILLS.items()):
            data = f"---\nname: {name}\ndescription: {description}\nnotion_page_id: page-{index}\n---\n# {name}\n".encode()
            info = tarfile.TarInfo(f"pilot/skills/{name}/SKILL.md"); info.size = len(data); archive.addfile(info, io.BytesIO(data))
    cache = tmp_path / "cache"
    status = LibraryCache(cache).publish([Export("plugin", "plugin", "a" * 64, buffer.getvalue())])
    return cache, status


def case_file(tmp_path, snapshot_id):
    base = {"context": "safe synthetic context", "harness": "codex"}
    rows = [
        {**base, "id": "plan", "task": "make a plan", "acceptable_ids": ["warehouse:plan"], "expect": "selection"},
        {**base, "id": "sessions", "task": "organize Hermes sessions", "harness": "hermes", "acceptable_ids": ["warehouse:session-librarian"], "expect": "selection"},
        {**base, "id": "workspace", "task": "design Hermes Workspace", "harness": "hermes", "acceptable_ids": ["warehouse:workspace-product-improvement"], "expect": "selection"},
        {**base, "id": "none", "task": "translate hello", "acceptable_ids": [], "expect": "abstain"},
        {**base, "id": "explicit", "task": "explicit", "explicit_skills": ["plan"], "acceptable_ids": ["warehouse:plan"], "expect": "selection"},
        {**base, "id": "wrong", "task": "archive Codex tasks", "acceptable_ids": [], "forbidden_ids": ["warehouse:session-librarian"], "expect": "abstain"},
        {**base, "id": "replay", "task": "make a plan", "acceptable_ids": ["warehouse:plan"], "expect": "selection", "replay_of": "plan"},
    ]
    path = tmp_path / "cases.json"; path.write_text(json.dumps({"schema_version": 1, "snapshot_id": snapshot_id, "cases": rows})); return path


class FakeService:
    calls = 0
    seen = {}

    def __init__(self, profile):
        assert profile.mode == "advisory" and profile.read_enabled is False and not profile.read_allowlist
        self.profile = profile
    def read(self, value): raise AssertionError("shadow evaluator must not read skill bodies")
    def suggest(self, request):
        type(self).calls += 1
        task = request["task"]
        if request["explicit_skills"]: selected, status, attempts, hits = ["warehouse:plan"], "explicit_selection", 0, 0
        elif task == "make a plan":
            hits = int(task in type(self).seen); type(self).seen[task] = True
            selected, status, attempts = ["warehouse:plan"], "suggested", 0 if hits else 1
        elif "Hermes sessions" in task: selected, status, attempts, hits = ["warehouse:session-librarian"], "suggested", 1, 0
        elif "Workspace" in task: selected, status, attempts, hits = ["warehouse:workspace-product-improvement"], "suggested", 1, 0
        else: selected, status, attempts, hits = [], "none", 1, 0
        return {"status": status, "reason": "fake", "selected": [{"id": sid} for sid in selected],
                "telemetry": {"provider_attempts": attempts, "cache_hits": hits, "input_tokens": 10 if attempts else 0, "unknown_usage": 0, "elapsed_ms": 1.5},
                "raw_provider_payload": "must-not-enter-report"}


def prepared(tmp_path):
    cache, status = snapshot(tmp_path); profile = tmp_path / "profile.json"; state = tmp_path / "state"
    write_profile(cache_root=cache, state_dir=state, profile_path=profile, harness="pilot")
    return cache, status, profile, case_file(tmp_path, status["snapshot_id"]), state


def test_prepare_accepts_selected_credential_file(tmp_path):
    cache, _ = snapshot(tmp_path)
    credential_file = tmp_path / "selected.env"
    credential_file.write_text("JEV_API=placeholder\n", encoding="utf-8")
    profile_path = tmp_path / "profile.json"
    write_profile(
        cache_root=cache,
        state_dir=tmp_path / "state",
        profile_path=profile_path,
        credential_file=credential_file,
    )
    assert json.loads(profile_path.read_text())["credential_file"] == str(credential_file.resolve())


def test_shadow_report_is_bounded_and_omits_raw_payloads(tmp_path):
    cache, status, profile, cases, state = prepared(tmp_path); FakeService.calls = 0; FakeService.seen = {}
    report_path = tmp_path / "report.json"
    report = run_shadow(cache_root=cache, profile_path=profile, cases_path=cases, report_path=report_path, service_factory=FakeService)
    assert report["summary"]["correct_selections"] == 5
    assert report["summary"]["abstentions"] == 2
    assert report["summary"]["explicit_zero_call"] is True
    assert report["summary"]["replay_cache_hit"] is True
    assert report["skill_bodies_delivered"] == 0
    assert set(report["notion_page_mapping"]) == {f"warehouse:{name}" for name in SKILLS}
    assert "raw_provider_payload" not in report_path.read_text()
    details = list((state / "runtime" / "shadow-details").glob("*.json"))
    assert len(details) == 1 and "raw_provider_payload" in details[0].read_text()


def test_release_bound_eval_does_not_depend_on_current_pointer(tmp_path, monkeypatch):
    cache,status,profile,cases,_=prepared(tmp_path); FakeService.calls=0; FakeService.seen={}
    manifest={"snapshot_id":status["snapshot_id"],"snapshot_root":status["catalog_root"],"skill_count":3,
              "profiles":["codex","generic"],"files":{"profile:codex":{"path":str(profile)}}}
    from jev_skill_advisor.release import ReleaseStore
    monkeypatch.setattr(ReleaseStore,"validate",lambda self,release_id:manifest)
    report=run_shadow(release_root=tmp_path/"releases",release_id="a"*64,harness="codex",
                      cases_path=cases,report_path=tmp_path/"eval"/"report.json",service_factory=FakeService)
    assert report["release_id"]=="a"*64 and report["skill_bodies_delivered"]==0
    assert (tmp_path/"eval"/"runtime"/"advisor.sqlite3").is_file()


def test_invalid_snapshot_blocks_service_calls(tmp_path):
    cache, status, profile, cases, _ = prepared(tmp_path); FakeService.calls = 0
    root = cache / "snapshots" / status["snapshot_id"] / "skills" / "plan" / "SKILL.md"
    root.write_text("tampered")
    with pytest.raises(Exception):
        run_shadow(cache_root=cache, profile_path=profile, cases_path=cases, report_path=tmp_path / "report.json", service_factory=FakeService)
    assert FakeService.calls == 0


def test_tampered_catalog_routing_text_blocks_service_calls(tmp_path):
    cache, _, profile, cases, state = prepared(tmp_path); FakeService.calls = 0
    catalog_path = state / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["entries"][0]["description"] = "tampered routing text"
    catalog_path.write_text(json.dumps(catalog))
    with pytest.raises(ShadowEvalError, match="catalog_provenance_mismatch"):
        run_shadow(cache_root=cache, profile_path=profile, cases_path=cases, report_path=tmp_path / "report.json", service_factory=FakeService)
    assert FakeService.calls == 0


def test_wrong_profile_blocks_calls(tmp_path):
    cache, _, profile, cases, _ = prepared(tmp_path); value = json.loads(profile.read_text()); value["read_enabled"] = True; profile.write_text(json.dumps(value)); FakeService.calls = 0
    with pytest.raises(ShadowEvalError, match="profile_not_strict_shadow"):
        run_shadow(cache_root=cache, profile_path=profile, cases_path=cases, report_path=tmp_path / "report.json", service_factory=FakeService)
    assert FakeService.calls == 0


def test_budget_larger_than_twenty_blocks_calls(tmp_path):
    cache, _, profile, cases, _ = prepared(tmp_path); value = json.loads(profile.read_text()); value["provider_attempt_limit"] = 21; profile.write_text(json.dumps(value)); FakeService.calls = 0
    with pytest.raises(ShadowEvalError, match="shadow_budget_too_large"):
        run_shadow(cache_root=cache, profile_path=profile, cases_path=cases, report_path=tmp_path / "report.json", service_factory=FakeService)
    assert FakeService.calls == 0


def test_disagreement_stays_visible(tmp_path):
    cache, _, profile, cases, _ = prepared(tmp_path)
    class Wrong(FakeService):
        def suggest(self, request):
            value = super().suggest(request)
            if request["task"] == "translate hello": value["selected"] = [{"id": "warehouse:plan"}]; value["status"] = "suggested"
            return value
    report = run_shadow(cache_root=cache, profile_path=profile, cases_path=cases, report_path=tmp_path / "report.json", service_factory=Wrong)
    assert report["summary"]["disagreements"] == 1


def test_provider_failure_stops_and_is_not_scored_as_abstention(tmp_path):
    cache, _, profile, cases, _ = prepared(tmp_path)
    class Failed(FakeService):
        def suggest(self, request):
            return {"status": "uncertain", "reason": "detail_provider_failure", "selected": [],
                    "telemetry": {"provider_attempts": 1, "cache_hits": 0, "input_tokens": 0,
                                  "unknown_usage": 1, "elapsed_ms": 1.0}}
    report_path = tmp_path / "failed-report.json"
    report = run_shadow(cache_root=cache, profile_path=profile, cases_path=cases, report_path=report_path, service_factory=Failed)
    assert report["run_status"] == "failed"
    assert report["summary"]["provider_failures"] == 1
    assert report["summary"]["abstentions"] == 0
    assert report["summary"]["case_count"] == 1
    assert report_path.is_file()


def test_detail_decision_audit_reuses_inclusive_thresholds():
    at_floor = detail_selection_audit(0.65, 0.8)
    assert at_floor["passed"] is True
    below = detail_selection_audit(0.62, 0.91)
    assert below["passed"] is False
    assert below["failed_predicates"] == ["winner_confidence:0.62<0.65"]
    with pytest.raises(ValueError, match="invalid_detail_decision_input"):
        detail_selection_audit(float("nan"), 0.91)
    assert detail_selection_audit(0.65, 0.79, decision="none")["passed"] is True
    assert detail_selection_audit(0.64, 0.79, decision="none")["passed"] is False
    assert detail_selection_audit(0.9, 0.8, decision="none")["passed"] is False
