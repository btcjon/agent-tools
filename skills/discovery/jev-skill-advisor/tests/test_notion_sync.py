import io,json,tarfile
from pathlib import Path
import pytest
from jev_skill_advisor.notion_import import Export
from jev_skill_advisor.notion_sync import NtnSyncTransport,SyncError,_bundle_bytes,_generated_manifest,_page_material,_pin_frozen_row,_property_fingerprint,discover_remote,execute_sync,freeze_inventory,managed_marker,parse_managed_marker,plan_sync


def skill(root,name,extra=None):
    folder=root/name; folder.mkdir(parents=True); (folder/"SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} helper.\n---\nBody\n")
    if extra: (folder/extra).write_text("resource")


def test_inventory_is_deterministic_sharded_and_complete(tmp_path):
    for name in ("charlie","alpha","bravo"): skill(tmp_path,name,"detail.md" if name=="alpha" else None)
    first=freeze_inventory(tmp_path,shard_size=2); second=freeze_inventory(tmp_path,shard_size=2)
    assert first==second and [row["stable_id"] for row in first["skills"]]==["warehouse:alpha","warehouse:bravo","warehouse:charlie"]
    assert [row["plugin_tag"] for row in first["skills"]]==["jev-catalog-001","jev-catalog-001","jev-catalog-002"]
    assert {file["path"] for file in first["skills"][0]["files"]}=={"SKILL.md","detail.md"}


def test_plan_classifies_without_automatic_deletion(tmp_path):
    skill(tmp_path,"alpha"); skill(tmp_path,"beta"); inventory=freeze_inventory(tmp_path)
    alpha=inventory["skills"][0]
    remote=[{"stable_id":alpha["stable_id"],"page_id":"page-a","verified_hash":alpha["desired_hash"],"last_synced_hash":"v1","remote_hash":"v1"},
            {"stable_id":"warehouse:gone","page_id":"page-g","verified_hash":"old"}]
    plan=plan_sync(inventory,remote,database_id="db",data_source_id="ds")
    assert plan["counts"]=={"create":1,"update":0,"unchanged":1,"conflict":0,"retirement_candidate":1}


def test_conflict_and_marker_roundtrip(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); row=inventory["skills"][0]
    plan=plan_sync(inventory,[{"stable_id":row["stable_id"],"page_id":"p","last_synced_hash":"a","remote_hash":"b"}],database_id="db",data_source_id="ds")
    assert plan["counts"]["conflict"]==1
    marker=managed_marker(row["stable_id"],row["desired_hash"],"op-1"); assert parse_managed_marker(marker)["operation_id"]=="op-1"


def test_bundle_inventory_accepts_package_over_attachment_limit(tmp_path):
    skill(tmp_path,"large"); folder=tmp_path/"large"
    for index in range(100): (folder/f"{index}.txt").write_text("x")
    result=freeze_inventory(tmp_path); assert len(result["skills"])==1 and len(result["skills"][0]["files"])==101


def test_nested_skills_preserve_parent_layout_and_only_skills_directory_is_independent(tmp_path):
    skill(tmp_path,"outer"); nested=tmp_path/"outer"/"skills"; skill(nested,"inner")
    skill(tmp_path/"outer"/"references","upstream")
    hidden=tmp_path/".archive"; hidden.mkdir(); skill(hidden,"old")
    result=freeze_inventory(tmp_path)
    assert {row["stable_id"] for row in result["skills"]}=={"warehouse:outer","warehouse:outer/skills/inner"}
    outer=next(row for row in result["skills"] if row["stable_id"]=="warehouse:outer")
    assert {item["path"] for item in outer["files"]}=={"SKILL.md","skills/inner/SKILL.md","references/upstream/SKILL.md"}
    assert any(row["reason"]=="embedded_resource_not_independent" for row in result["exclusions"])
    assert any(row["reason"]=="hidden_or_archived_path" for row in result["exclusions"])


def test_effective_policy_and_executable_paths_are_frozen(tmp_path):
    skill(tmp_path,"alpha"); source=tmp_path/"alpha"/"SKILL.md"; source.write_text("---\nname: alpha\ndescription: helper\ndisable-model-invocation: true\n---\nBody\n")
    script=tmp_path/"alpha"/"run.sh"; script.write_text("#!/bin/sh\n"); script.chmod(0o755)
    row=freeze_inventory(tmp_path)["skills"][0]
    assert row["invocation_policy"]=="explicit" and row["executable_paths"]==["run.sh"]


class FakeTransport:
    def __init__(self): self.calls=[]; self.count=0
    def upload(self,path,name): self.calls.append(("upload",name)); self.count+=1; return {"id":f"u{self.count}"}
    def upload_bytes(self,data,name): self.calls.append(("upload_bytes",name)); self.count+=1; return {"id":f"u{self.count}"}
    def create_page(self,body): self.calls.append(("create",body)); return {"id":"page-new"}
    def update_page(self,page_id,properties,markdown): self.calls.append(("update",page_id)); return {}
    def request(self,path,method="GET",body=None):
        if path.endswith("/query"): return {"results":[],"has_more":False}
        if "/ai/skills/" in path: return {"version_id":"v"*64}
        return {"markdown":""}
    def verify_skill(self,row,page_id,expected_markdown,*args): self.calls.append(("verify",page_id)); return "v"*64
    def validate_destination(self,database_id,data_source_id): self.calls.append(("destination",database_id,data_source_id))


def test_execute_sync_checkpoints_create_and_unchanged_is_mutation_free(tmp_path):
    skill(tmp_path,"alpha","detail.md"); inventory=freeze_inventory(tmp_path); transport=FakeTransport()
    plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    result=execute_sync(inventory,plan,ledger_path=tmp_path/"ledger.sqlite3",run_id="run-1",transport=transport)
    assert result["completed"]==[{"stable_id":"warehouse:alpha","page_id":"page-new","action":"create"}]
    assert [call[0] for call in transport.calls]==["destination","upload_bytes","upload_bytes","create","verify"]
    before=list(transport.calls); replay=execute_sync(inventory,plan,ledger_path=tmp_path/"ledger.sqlite3",run_id="run-1",transport=transport)
    assert replay["completed"]==result["completed"] and transport.calls==before+[("destination","db","ds")]
    unchanged=plan_sync(inventory,[{"stable_id":"warehouse:alpha","page_id":"page-new","verified_hash":inventory["skills"][0]["desired_hash"],"last_synced_hash":"v1","remote_hash":"v1"}],database_id="db",data_source_id="ds")
    quiet=FakeTransport(); result=execute_sync(inventory,unchanged,ledger_path=tmp_path/"quiet.sqlite3",run_id="run-2",transport=quiet)
    assert result["completed"]==[] and [call[0] for call in quiet.calls]==["destination"]


def test_remote_inventory_paginates_and_ignores_unmanaged():
    class Remote:
        def __init__(self): self.queries=0
        def request(self,path,method="GET",body=None):
            if path.endswith("/query"):
                self.queries+=1
                return {"results":[{"id":f"p{self.queries}"}],"has_more":self.queries==1,"next_cursor":"next" if self.queries==1 else None}
            if "/markdown" in path:
                return {"markdown":managed_marker("warehouse:alpha","wanted","op") if "p1" in path else "unmanaged"}
            return {"version_id":"a"*64}
    remote=Remote(); rows=discover_remote("ds",transport=remote)
    assert remote.queries==2 and rows==[{"stable_id":"warehouse:alpha","page_id":"p1","verified_hash":"wanted","remote_hash":"a"*64,"last_synced_hash":None,"verified_receipt":False}]


def test_frozen_source_drift_stops_before_upload(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    (tmp_path/"alpha"/"SKILL.md").write_text("changed"); transport=FakeTransport()
    with pytest.raises(SyncError,match="frozen_source_drift"): execute_sync(inventory,plan,ledger_path=tmp_path/"ledger.sqlite3",run_id="drift",transport=transport)
    assert transport.calls==[]


def test_accepted_but_response_lost_create_is_reconciled(tmp_path):
    skill(tmp_path,"outer"); skill(tmp_path/"outer"/"skills","inner"); inventory=freeze_inventory(tmp_path)
    inventory["skills"]=[row for row in inventory["skills"] if row["stable_id"]=="warehouse:outer/skills/inner"]
    from jev_skill_advisor.notion_sync import _hash
    inventory["inventory_hash"]=_hash({key:value for key,value in inventory.items() if key!="inventory_hash"}); plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    class Lost(FakeTransport):
        def __init__(self): super().__init__(); self.accepted=False
        def create_page(self,body): self.accepted=True; self.markdown=body["markdown"]; raise SyncError("notion_mutation_ambiguous")
        def request(self,path,method="GET",body=None):
            if path.endswith("/query"): return {"results":[{"id":"recovered"}] if self.accepted else [],"has_more":False}
            return {"markdown":self.markdown}
    transport=Lost(); result=execute_sync(inventory,plan,ledger_path=tmp_path/"ledger.sqlite3",run_id="lost",transport=transport)
    assert result["completed"][0]["page_id"]=="recovered"


def test_unverified_marker_is_conflict_not_unchanged(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); row=inventory["skills"][0]
    remote=[{"stable_id":row["stable_id"],"page_id":"p","verified_hash":row["desired_hash"],"remote_hash":"changed","last_synced_hash":None}]
    assert plan_sync(inventory,remote,database_id="db",data_source_id="ds")["counts"]["conflict"]==1


def test_bootstrap_observation_cannot_self_bless_on_second_discovery():
    class Remote:
        def request(self,path,method="GET",body=None):
            if path.endswith("/query"): return {"results":[{"id":"p"}],"has_more":False}
            if path.endswith("/markdown"): return {"markdown":managed_marker("warehouse:alpha","wanted","op")}
            return {"version_id":"a"*64}
    first=discover_remote("ds",transport=Remote())
    second=discover_remote("ds",transport=Remote(),bootstrap=first)
    assert second[0]["last_synced_hash"] is None and second[0]["verified_receipt"] is False


def test_added_file_and_plan_tampering_stop_before_mutation(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    (tmp_path/"alpha"/"new.txt").write_text("late")
    transport=FakeTransport()
    with pytest.raises(SyncError,match="frozen_source_drift"):
        execute_sync(inventory,plan,ledger_path=tmp_path/"drift.sqlite3",run_id="drift-added",transport=transport)
    assert transport.calls==[]
    plan["database_id"]="other"
    with pytest.raises(SyncError,match="plan_hash_mismatch"):
        execute_sync(inventory,plan,ledger_path=tmp_path/"tamper.sqlite3",run_id="tamper",transport=FakeTransport())


def test_source_change_after_preflight_is_not_uploaded(tmp_path):
    skill(tmp_path,"alpha","detail.md"); inventory=freeze_inventory(tmp_path); plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    class Mutating(FakeTransport):
        def validate_destination(self,database_id,data_source_id):
            super().validate_destination(database_id,data_source_id); (tmp_path/"alpha"/"SKILL.md").write_text("changed after preflight")
    transport=Mutating()
    with pytest.raises(SyncError,match="frozen_source_drift"):
        execute_sync(inventory,plan,ledger_path=tmp_path/"race.sqlite3",run_id="race",transport=transport)
    assert not any(call[0] in {"upload","upload_bytes","create"} for call in transport.calls)


def test_reused_run_is_bound_to_plan_and_destination(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    execute_sync(inventory,plan,ledger_path=tmp_path/"ledger.sqlite3",run_id="same",transport=FakeTransport())
    changed=plan_sync(inventory,[],database_id="other",data_source_id="ds")
    with pytest.raises(ValueError,match="run_identity_mismatch"):
        execute_sync(inventory,changed,ledger_path=tmp_path/"ledger.sqlite3",run_id="same",transport=FakeTransport())


def test_ambiguous_create_blocks_fresh_run_before_second_upload(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    class Ambiguous(FakeTransport):
        def create_page(self,body): raise SyncError("notion_mutation_ambiguous")
    first=Ambiguous()
    with pytest.raises(SyncError,match="notion_mutation_ambiguous"):
        execute_sync(inventory,plan,ledger_path=tmp_path/"ledger.sqlite3",run_id="first",transport=first)
    skill(tmp_path,"beta"); changed_inventory=freeze_inventory(tmp_path); changed_plan=plan_sync(changed_inventory,[],database_id="db",data_source_id="ds")
    second=FakeTransport()
    with pytest.raises(SyncError,match="ambiguous_prior_create_requires_reconciliation"):
        execute_sync(changed_inventory,changed_plan,ledger_path=tmp_path/"ledger.sqlite3",run_id="second",transport=second)
    assert [call[0] for call in second.calls]==["destination"]


def test_bulk_create_builds_remote_identity_map_once(tmp_path):
    for name in ("alpha","beta","gamma"): skill(tmp_path,name)
    inventory=freeze_inventory(tmp_path); plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    class Counting(FakeTransport):
        def __init__(self): super().__init__(); self.queries=0
        def request(self,path,method="GET",body=None):
            if path.endswith("/query"): self.queries+=1
            return super().request(path,method,body)
        def create_page(self,body): self.count+=1; self.calls.append(("create",body)); return {"id":f"page-{self.count}"}
    transport=Counting(); execute_sync(inventory,plan,ledger_path=tmp_path/"ledger.sqlite3",run_id="bulk",transport=transport)
    assert transport.queries==1


def test_partial_update_resumes_without_reuploading_or_rechecking_old_version(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); row=inventory["skills"][0]
    plan=plan_sync(inventory,[{"stable_id":row["stable_id"],"page_id":"page-a","last_synced_hash":"old","remote_hash":"old"}],database_id="db",data_source_id="ds")
    operation_id="resume:warehouse:alpha"
    class Partial(FakeTransport):
        def __init__(self): super().__init__(); self.markdown=_page_material(row,operation_id,"pending")[2]
        def request(self,path,method="GET",body=None):
            if path.endswith("/query"): return {"results":[{"id":"page-a"}],"has_more":False}
            if path.endswith("/markdown"): return {"markdown":self.markdown}
            if "/ai/skills/" in path: return {"version_id":"changed-during-owned-update"}
            return {}
        def update_markdown(self,page_id,markdown): self.calls.append(("markdown",page_id)); self.markdown=markdown
        def update_properties(self,page_id,properties): self.calls.append(("properties",page_id))
    from jev_skill_advisor.sync_ledger import SyncLedger
    ledger_path=tmp_path/"ledger.sqlite3"; ledger=SyncLedger(ledger_path)
    binding=__import__("jev_skill_advisor.notion_sync",fromlist=["_hash"])._hash({"inventory_hash":inventory["inventory_hash"],"plan_hash":plan["plan_hash"],"database_id":"db","data_source_id":"ds"})
    destination=__import__("jev_skill_advisor.notion_sync",fromlist=["_hash"])._hash({"database_id":"db","data_source_id":"ds"})
    ledger.create_run("resume",source_snapshot_id=binding,destination_id=destination)
    ledger.checkpoint_intent("resume",row["stable_id"],"update",row["desired_hash"],page_id="page-a"); ledger.record_attempt("resume",row["stable_id"],"update")
    baseline_hash=_property_fingerprint({}); ledger.checkpoint_intent("resume",row["stable_id"],"baseline:properties",baseline_hash); ledger.record_attempt("resume",row["stable_id"],"baseline:properties"); ledger.record_result("resume",row["stable_id"],"baseline:properties",result_id=baseline_hash)
    for kind in ("upload:@bundle","upload:skill-package.json"):
        ledger.checkpoint_intent("resume",row["stable_id"],kind,"saved"); ledger.record_attempt("resume",row["stable_id"],kind); ledger.record_result("resume",row["stable_id"],kind,upload_id=f"id-{kind}")
    transport=Partial(); result=execute_sync(inventory,plan,ledger_path=ledger_path,run_id="resume",transport=transport)
    assert result["completed"][0]["page_id"]=="page-a"
    assert not any(call[0]=="upload_bytes" for call in transport.calls)
    assert [call[0] for call in transport.calls if call[0] in {"markdown","properties"}]==["markdown","properties","markdown"]


def test_partial_update_rejects_intervening_human_content_edit(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); row=inventory["skills"][0]
    plan=plan_sync(inventory,[{"stable_id":row["stable_id"],"page_id":"page-a","last_synced_hash":"old","remote_hash":"old"}],database_id="db",data_source_id="ds")
    operation_id="resume:warehouse:alpha"
    class Edited(FakeTransport):
        def request(self,path,method="GET",body=None):
            if path.endswith("/query"): return {"results":[{"id":"page-a"}],"has_more":False}
            if path.endswith("/markdown"): return {"markdown":_page_material(row,operation_id,"pending")[2]+"\nHuman edit\n"}
            return {"version_id":"changed"}
    from jev_skill_advisor.sync_ledger import SyncLedger
    from jev_skill_advisor.notion_sync import _hash
    ledger_path=tmp_path/"ledger.sqlite3"; ledger=SyncLedger(ledger_path)
    binding=_hash({"inventory_hash":inventory["inventory_hash"],"plan_hash":plan["plan_hash"],"database_id":"db","data_source_id":"ds"})
    destination=_hash({"database_id":"db","data_source_id":"ds"})
    ledger.create_run("resume",source_snapshot_id=binding,destination_id=destination)
    ledger.checkpoint_intent("resume",row["stable_id"],"update",row["desired_hash"],page_id="page-a"); ledger.record_attempt("resume",row["stable_id"],"update")
    transport=Edited()
    with pytest.raises(SyncError,match="partial_update_content_changed"):
        execute_sync(inventory,plan,ledger_path=ledger_path,run_id="resume",transport=transport)
    assert not any(call[0] in {"upload_bytes","markdown","properties"} for call in transport.calls)


def test_preflight_failure_marks_run_failed(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); plan=plan_sync(inventory,[],database_id="db",data_source_id="ds")
    (tmp_path/"alpha"/"SKILL.md").write_text("changed")
    ledger_path=tmp_path/"ledger.sqlite3"
    with pytest.raises(SyncError,match="frozen_source_drift"):
        execute_sync(inventory,plan,ledger_path=ledger_path,run_id="preflight",transport=FakeTransport())
    from jev_skill_advisor.sync_ledger import SyncLedger
    assert SyncLedger(ledger_path).run("preflight")["status"]=="failed"


def test_partial_update_rejects_intervening_property_or_attachment_edit(tmp_path):
    skill(tmp_path,"alpha"); inventory=freeze_inventory(tmp_path); row=inventory["skills"][0]
    plan=plan_sync(inventory,[{"stable_id":row["stable_id"],"page_id":"page-a","last_synced_hash":"old","remote_hash":"old"}],database_id="db",data_source_id="ds")
    operation_id="resume:warehouse:alpha"
    class Edited(FakeTransport):
        def request(self,path,method="GET",body=None):
            if path.endswith("/query"): return {"results":[{"id":"page-a"}],"has_more":False}
            if path.endswith("/markdown"): return {"markdown":_page_material(row,operation_id,"pending")[2]}
            if path=="v1/pages/page-a": return {"properties":{"Description":{"rich_text":[{"plain_text":"human edit"}]},"Files":{"files":[{"name":"human.txt","type":"file"}]}}}
            return {"version_id":"changed"}
    from jev_skill_advisor.sync_ledger import SyncLedger
    from jev_skill_advisor.notion_sync import _hash
    ledger_path=tmp_path/"ledger.sqlite3"; ledger=SyncLedger(ledger_path)
    binding=_hash({"inventory_hash":inventory["inventory_hash"],"plan_hash":plan["plan_hash"],"database_id":"db","data_source_id":"ds"})
    destination=_hash({"database_id":"db","data_source_id":"ds"})
    ledger.create_run("resume",source_snapshot_id=binding,destination_id=destination)
    ledger.checkpoint_intent("resume",row["stable_id"],"update",row["desired_hash"],page_id="page-a"); ledger.record_attempt("resume",row["stable_id"],"update")
    ledger.checkpoint_intent("resume",row["stable_id"],"baseline:properties","baseline")
    ledger.record_attempt("resume",row["stable_id"],"baseline:properties"); ledger.record_result("resume",row["stable_id"],"baseline:properties",result_id="baseline")
    transport=Edited()
    with pytest.raises(SyncError,match="partial_update_properties_changed"):
        execute_sync(inventory,plan,ledger_path=ledger_path,run_id="resume",transport=transport)
    assert not any(call[0] in {"markdown","properties"} for call in transport.calls)


def test_custom_entrypoint_is_explicitly_excluded(tmp_path):
    skill(tmp_path,"alpha"); folder=tmp_path/"alpha"
    (folder/"ALT.md").write_text((folder/"SKILL.md").read_text())
    (folder/"skill-package.json").write_text(json.dumps({"schema_version":1,"stable_id":"custom:alpha","entrypoint":"ALT.md","invocation_policy":"source"}))
    result=freeze_inventory(tmp_path)
    assert result["skills"]==[] and result["exclusions"][0]["reason"]=="unsupported_custom_entrypoint"


def test_real_export_verifier_accepts_exact_body_and_rejects_wrong_body(tmp_path,monkeypatch):
    skill(tmp_path,"alpha","detail.md"); row=freeze_inventory(tmp_path)["skills"][0]; expected=_page_material(row,"op")[2]
    pinned=_pin_frozen_row(row); bundle_data=_bundle_bytes(row,pinned); manifest_data=_generated_manifest(row,bundle_data)
    def payload(body,bundle=None):
        files={"bundle/alpha/SKILL.md":f"---\nname: alpha\ndescription: alpha helper.\n---\n{body}".encode(),
               "bundle/alpha/package-bundle.txt":bundle or bundle_data,
               "bundle/alpha/skill-package.json":manifest_data}
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode="w:gz") as bundle:
            for name,data in files.items():
                info=tarfile.TarInfo(name); info.size=len(data); bundle.addfile(info,io.BytesIO(data))
        return Export("skill","page","a"*64,output.getvalue())
    monkeypatch.setattr("jev_skill_advisor.notion_sync.NotionExportClient.fetch",lambda self,kind,page_id:payload(expected))
    assert NtnSyncTransport(sleeper=lambda _:None,min_interval=0).verify_skill(row,"page",expected,bundle_data,manifest_data)=="a"*64
    monkeypatch.setattr("jev_skill_advisor.notion_sync.NotionExportClient.fetch",lambda self,kind,page_id:payload(expected,bundle=b"wrong"))
    with pytest.raises(SyncError,match="export_resource_mismatch"):
        NtnSyncTransport(sleeper=lambda _:None,min_interval=0).verify_skill(row,"page",expected)
