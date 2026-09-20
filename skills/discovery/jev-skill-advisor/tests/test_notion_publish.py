import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from jev_skill_advisor.notion_import import Export
from jev_skill_advisor.notion_publish import PublishError, apply_plan, load_plan, receipt_status, verify_plan


def sha(data): return hashlib.sha256(data).hexdigest()


def fixture(tmp_path):
    root = tmp_path / "canonical"; rows = []
    for index, name in enumerate(("alpha", "beta", "gamma")):
        directory = root / name; directory.mkdir(parents=True)
        source = f"---\nname: {name}\ndescription: \"Description {index}\"\nversion: 1.0.{index}\nlicense: MIT\n---\n\n# {name.title()}\n\nBody {index}.\n"
        path = directory / "SKILL.md"; path.write_text(source)
        metadata = f"version: 1.0.{index}\nlicense: MIT"
        page = f"# {name.title()}\n\nBody {index}.\n\n## Preserved source metadata\n\nThe following metadata is part of the canonical skill contract and remains authoritative for this pilot.\n\n```yaml\n{metadata}\n```\n"
        rows.append({
            "stable_id": f"warehouse:{name}", "canonical_relative_path": name,
            "proposed_page_title": name, "proposed_tags": ["pilot"], "description": f"Description {index}",
            "source_inventory": [{"path": "SKILL.md", "bytes": len(source.encode()), "sha256": sha(source.encode())}],
            "upload_inventory": [],
            "notion_content_mapping": {"page_content_bytes": len(page.encode()), "page_content_sha256": sha(page.encode()),
                "retained_metadata_bytes": len(metadata.encode()), "retained_metadata_sha256": sha(metadata.encode()), "files_property": None},
        })
    manifest = {
        "canonical_root": str(root), "proposed_plugin_tag": "pilot",
        "destination": {"status": "proposed_pending_authorization", "parent_page_id": "parent", "database_name": "Pilot"},
        "creation_contract": {"database_request": {"parent": {"type": "page_id", "page_id": "parent"}, "title": [], "database_type": "skills"}},
        "skills": rows,
    }
    manifest_path = tmp_path / "manifest.json"; manifest_path.write_text(json.dumps(manifest))
    return manifest_path, root


def authorization(tmp_path, plan, **changes):
    value = {"authorized": True, "authorized_at": "2026-09-20T12:00:00Z", "authorized_by": "user",
        "plan_hash": plan["plan_hash"], "parent_page_id": "parent", "database_name": "Pilot",
        "skill_ids": [row["stable_id"] for row in plan["pages"]], "plugin_tag": "pilot"}
    value.update(changes)
    path = tmp_path / "authorization.json"; path.write_text(json.dumps(value)); return path


class FakeTransport:
    def __init__(self): self.calls = []; self.pages = {}
    def create_database(self, body): self.calls.append(("create_database", body)); return {"id": "db", "data_sources": [{"id": "ds"}]}
    def get_database(self, identity):
        return {"id": identity, "database_type": "skills", "parent": {"page_id": "parent"}, "title": [{"plain_text": "Pilot"}]}
    def get_data_source(self, identity):
        self.calls.append(("get_data_source", identity))
        return {"id": identity, "parent": {"database_id": "db"}, "database_type": "skills", "properties": {"Skill name": {"type": "title"}, "Description": {"type": "rich_text"}, "Files": {"type": "files"}, "Tags": {"type": "multi_select"}, "Created by": {"type": "created_by"}}}
    def create_page(self, body):
        identity = f"page-{len(self.pages)}"; self.calls.append(("create_page", body)); self.pages[identity] = body; return {"id": identity}
    def get_page(self, identity):
        properties = json.loads(json.dumps(self.pages[identity]["properties"]))
        for item in properties["Skill name"]["title"] + properties["Description"]["rich_text"]:
            item["plain_text"] = item["text"]["content"]
        return {"id": identity, "parent": {"data_source_id": "ds"}, "properties": properties}
    def get_page_markdown(self, identity): return self.pages[identity]["markdown"]
    def list_plugins(self): return {"results": [{"name": "pilot", "id": "plugin", "version_id": "a" * 64}]}


def archive(plan, *, extra=False, corrupt=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for row in plan["pages"]:
            name = row["properties"]["Skill name"]["title"][0]["text"]["content"]
            description = row["properties"]["Description"]["rich_text"][0]["text"]["content"]
            body = "Wrong\n" if corrupt and name == "alpha" else row["markdown"]
            data = f"---\nname: {name}\ndescription: {description}\n---\n{body}".encode()
            info = tarfile.TarInfo(f"skills/{name}/SKILL.md"); info.size = len(data); tar.addfile(info, io.BytesIO(data))
        if extra:
            data = b"---\nname: delta\ndescription: Extra\n---\nBody\n"
            info = tarfile.TarInfo("skills/delta/SKILL.md"); info.size = len(data); tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeExportClient:
    def __init__(self, plan, **options): self.plan = plan; self.options = options
    def fetch(self, kind, identity): return Export(kind, identity, "a" * 64, archive(self.plan, **self.options))


def test_plan_is_deterministic_and_local(tmp_path):
    manifest, _ = fixture(tmp_path)
    first = load_plan(manifest); second = load_plan(manifest)
    assert first == second
    assert len(first["pages"]) == 3


def test_plan_rejects_source_drift(tmp_path):
    manifest, root = fixture(tmp_path); (root / "alpha" / "SKILL.md").write_text("changed")
    with pytest.raises(PublishError, match="canonical_source_drift"):
        load_plan(manifest)


@pytest.mark.parametrize("change", [{"authorized": False}, {"plan_hash": "wrong"}, {"plugin_tag": "other"}])
def test_apply_rejects_invalid_authorization_before_calls(tmp_path, change):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport()
    with pytest.raises(PublishError, match="authorization_scope_mismatch"):
        apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan, **change), state_dir=tmp_path / "state", transport=transport)
    assert transport.calls == []


def test_apply_rejects_wrong_ack_before_calls(tmp_path):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport()
    with pytest.raises(PublishError, match="plan_hash_acknowledgement_mismatch"):
        apply_plan(plan, ack_hash="wrong", authorization_path=authorization(tmp_path, plan), state_dir=tmp_path / "state", transport=transport)
    assert transport.calls == []


def test_apply_revalidates_source_immediately_before_write(tmp_path):
    manifest, root = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport()
    (root / "alpha" / "SKILL.md").write_text("changed")
    with pytest.raises(PublishError, match="canonical_source_drift"):
        apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=tmp_path / "state", transport=transport)
    assert transport.calls == []


def test_apply_checkpoints_and_prevents_duplicate(tmp_path):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport(); state = tmp_path / "state"
    result = apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=state, transport=transport)
    assert result["status"] == "created_pending_verification"
    assert len(result["pages"]) == 3
    with pytest.raises(PublishError, match="existing_receipt_requires_reconciliation"):
        apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=state, transport=transport)


def test_schema_mismatch_stops_before_pages(tmp_path):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport(); transport.get_data_source = lambda identity: {"id": identity, "parent": {"database_id": "db"}, "database_type": "other", "properties": {}}
    with pytest.raises(PublishError, match="skills_schema_mismatch"):
        apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=tmp_path / "state", transport=transport)
    assert receipt_status(tmp_path / "state", plan["plan_hash"])["status"] == "stopped_requires_reconciliation"


def test_verify_publishes_cache_only_after_readback(tmp_path):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport(); state = tmp_path / "state"
    apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=state, transport=transport)
    result = verify_plan(plan, state_dir=state, cache_root=tmp_path / "cache", transport=transport, export_client=FakeExportClient(plan))
    assert result["status"] == "verified"
    assert (tmp_path / "cache" / "current.json").is_file()


def test_verify_failure_preserves_prior_cache(tmp_path):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport(); state = tmp_path / "state"; cache = tmp_path / "cache"
    apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=state, transport=transport)
    verify_plan(plan, state_dir=state, cache_root=cache, transport=transport, export_client=FakeExportClient(plan))
    prior = (cache / "current.json").read_bytes(); transport.pages["page-0"]["markdown"] = "tampered"
    with pytest.raises(PublishError, match="page_readback_mismatch"):
        verify_plan(plan, state_dir=state, cache_root=cache, transport=transport, export_client=FakeExportClient(plan))
    assert (cache / "current.json").read_bytes() == prior


@pytest.mark.parametrize("options,error", [({"corrupt": True}, "export_skill_fidelity_mismatch"), ({"extra": True}, "export_skill_membership_mismatch")])
def test_export_mismatch_never_promotes_cache(tmp_path, options, error):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport(); state = tmp_path / "state"; cache = tmp_path / "cache"
    apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=state, transport=transport)
    with pytest.raises(PublishError, match=error):
        verify_plan(plan, state_dir=state, cache_root=cache, transport=transport, export_client=FakeExportClient(plan, **options))
    assert not (cache / "current.json").exists()


def test_wrong_object_bindings_are_rejected(tmp_path):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport(); state = tmp_path / "state"
    transport.get_database = lambda identity: {"id": identity, "database_type": "skills", "parent": {"page_id": "wrong"}, "title": [{"plain_text": "Pilot"}]}
    with pytest.raises(PublishError, match="database_parent_mismatch"):
        apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=state, transport=transport)


def test_database_id_checkpointed_before_data_source_parse(tmp_path):
    manifest, _ = fixture(tmp_path); plan = load_plan(manifest); transport = FakeTransport(); state = tmp_path / "state"
    transport.create_database = lambda body: {"id": "known-db", "data_sources": []}
    with pytest.raises(PublishError, match="database_data_source_missing"):
        apply_plan(plan, ack_hash=plan["plan_hash"], authorization_path=authorization(tmp_path, plan), state_dir=state, transport=transport)
    receipt = receipt_status(state, plan["plan_hash"])
    assert receipt["database_id"] == "known-db"
    assert receipt["status"] == "stopped_requires_reconciliation"
