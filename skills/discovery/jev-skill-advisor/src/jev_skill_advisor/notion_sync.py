"""Deterministic, resumable planning core for Notion Agent Skills synchronization."""
from __future__ import annotations

import hashlib
import gzip
import io
import json
import mimetypes
import subprocess
from pathlib import Path, PurePosixPath
import re
import time
import tarfile
import uuid

import yaml

from .catalog_cli import frontmatter
from .notion_publish import _split_skill
from .notion_publish import _exported_skill
from .notion_import import NotionExportClient,NotionImportError
from .library_cache import _archive_entries,_package_roots
from .fidelity import FidelityError,compare_markdown
from .profile import current_policy
from .sync_ledger import SyncLedger


class SyncError(ValueError):
    pass


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def _file(path,root):
    data=path.read_bytes()
    return {"path":path.relative_to(root).as_posix(),"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data),
            "mode":0o755 if path.stat().st_mode & 0o111 else 0o644,
            "content_type":mimetypes.guess_type(path.name)[0] or "application/octet-stream"}


def _package_manifest(root,relative):
    path=root/relative/"skill-package.json"
    if not path.is_file():
        return {"stable_id":f"warehouse:{relative.as_posix()}","aliases":[],"entrypoint":"SKILL.md",
                "invocation_policy":"source","required_runtimes":[],"executable_paths":[]}
    try: value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise SyncError("invalid_package_manifest") from exc
    required={"schema_version","stable_id","entrypoint","invocation_policy"}
    if not isinstance(value,dict) or not required <= set(value) or value["schema_version"]!=1: raise SyncError("invalid_package_manifest")
    return {**value,"aliases":value.get("aliases",[]),"required_runtimes":value.get("required_runtimes",[]),
            "executable_paths":value.get("executable_paths",[])}


def _package_files(root):
    files=[]
    for path in sorted(root.rglob("*")):
        if path.is_symlink(): raise SyncError("package_contains_link")
        if path.is_file(): files.append(_file(path,root))
    return files


def freeze_inventory(warehouse, *, shard_size=80):
    warehouse=Path(warehouse).resolve()
    if not warehouse.is_dir() or not 1<=shard_size<=90: raise SyncError("invalid_inventory_configuration")
    rows=[]; exclusions=[]
    candidates=sorted(warehouse.glob("**/SKILL.md"))
    candidate_parents={path.parent for path in candidates}
    for source in candidates:
        relative=source.parent.relative_to(warehouse)
        if any(part.startswith(".") for part in relative.parts):
            exclusions.append({"path":relative.as_posix(),"reason":"hidden_or_archived_path"}); continue
        ancestors=[parent for parent in source.parents if parent in candidate_parents and parent!=source.parent]
        if ancestors:
            nearest=max(ancestors,key=lambda path:len(path.parts))
            nested=source.parent.relative_to(nearest)
            if not nested.parts or nested.parts[0]!="skills":
                exclusions.append({"path":relative.as_posix(),"reason":"embedded_resource_not_independent"}); continue
        try:
            package=_package_manifest(warehouse,relative); entry=(source.parent/Path(package["entrypoint"])).resolve()
            if package["entrypoint"]!="SKILL.md" or entry!=source.resolve(): raise SyncError("unsupported_custom_entrypoint")
            meta=frontmatter(source.read_text(encoding="utf-8",errors="strict"))
            name,description=meta.get("name"),meta.get("description")
            if not isinstance(name,str) or not name.strip() or not isinstance(description,str) or not description.strip(): raise SyncError("invalid_skill_metadata")
            files=_package_files(source.parent)
            effective_implicit,_policy_hash=current_policy(source,source.parent)
            if package["invocation_policy"]=="source" and not effective_implicit: package["invocation_policy"]="explicit"
            if not package["executable_paths"]: package["executable_paths"]=[item["path"] for item in files if item["mode"]==0o755]
            row={"stable_id":package["stable_id"],"aliases":package["aliases"],"name":name.strip(),"description":description.strip(),
                 "source_root":str(source.parent),"entrypoint":source.relative_to(source.parent).as_posix(),
                 "invocation_policy":package["invocation_policy"],"required_runtimes":package["required_runtimes"],
                 "executable_paths":package["executable_paths"],"transport_schema_version":5,"files":files}
            rows.append(row)
        except (OSError,UnicodeDecodeError,ValueError,SyncError) as exc:
            exclusions.append({"path":relative.as_posix(),"reason":str(exc)[:200]})
    ids=[row["stable_id"] for row in rows]
    if len(ids)!=len(set(ids)): raise SyncError("duplicate_stable_id")
    rows.sort(key=lambda row:row["stable_id"])
    for index,row in enumerate(rows):
        row["plugin_tag"]=f"jev-catalog-{index//shard_size+1:03d}"
        row["desired_hash"]=_hash({key:value for key,value in row.items() if key not in {"source_root","desired_hash"}})
    inventory={"schema_version":1,"warehouse_root":str(warehouse),"shard_size":shard_size,"skills":rows,"exclusions":exclusions}
    inventory["inventory_hash"]=_hash(inventory)
    return inventory


def plan_sync(inventory, remote_rows, *, database_id, data_source_id):
    if not all(isinstance(item,list) for item in (inventory.get("skills"),remote_rows)): raise SyncError("invalid_sync_inputs")
    remote={row.get("stable_id"):row for row in remote_rows if isinstance(row,dict) and isinstance(row.get("stable_id"),str)}
    if len(remote)!=len(remote_rows): raise SyncError("ambiguous_remote_inventory")
    actions=[]; local_ids=set()
    for row in inventory["skills"]:
        sid=row["stable_id"]; local_ids.add(sid); found=remote.get(sid)
        if found is None: action="create"
        elif found.get("last_synced_hash") and found.get("remote_hash")!=found.get("last_synced_hash"): action="conflict"
        elif not found.get("last_synced_hash"): action="conflict"
        elif found.get("verified_hash")==row["desired_hash"]: action="unchanged"
        else: action="update"
        actions.append({"stable_id":sid,"action":action,"desired_hash":row["desired_hash"],"page_id":found.get("page_id") if found else None,
                        "plugin_tag":row["plugin_tag"],"expected_remote_hash":found.get("remote_hash") if found else None})
    for sid,row in sorted(remote.items()):
        if sid not in local_ids: actions.append({"stable_id":sid,"action":"retirement_candidate","page_id":row.get("page_id")})
    plan={"schema_version":1,"inventory_hash":inventory["inventory_hash"],"database_id":database_id,"data_source_id":data_source_id,
          "actions":actions,"counts":{kind:sum(item["action"]==kind for item in actions) for kind in ("create","update","unchanged","conflict","retirement_candidate")}}
    plan["plan_hash"]=_hash(plan)
    return plan


def managed_marker(stable_id,desired_hash,operation_id,phase="complete"):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}",stable_id): raise SyncError("invalid_stable_id")
    if phase not in {"pending","complete"}: raise SyncError("invalid_marker_phase")
    return "\n\n## Managed skill identity\n\n```json\n"+json.dumps({"stable_id":stable_id,"desired_hash":desired_hash,"operation_id":operation_id,"phase":phase},sort_keys=True,separators=(",",":"))+"\n```\n"


def parse_managed_marker(markdown):
    matches=re.findall(r"## Managed skill identity\s*```json\s*(\{[^`]+\})\s*```",markdown,re.S)
    if len(matches)!=1: return None
    try: value=json.loads(matches[0])
    except json.JSONDecodeError: return None
    return value if isinstance(value,dict) and isinstance(value.get("stable_id"),str) else None


class NtnSyncTransport:
    """Mutation transport with bounded retries; signed URLs never enter the ledger."""
    def __init__(self, *, attempts=4, sleeper=time.sleep, timeout=30, min_interval=.75): self.attempts=attempts; self.sleeper=sleeper; self.timeout=timeout; self.min_interval=min_interval; self.last_request=0.0
    def _run(self,command,*,input=None,mutation=False,timeout=None):
        for attempt in range(self.attempts):
            wait=self.min_interval-(time.monotonic()-self.last_request)
            if wait>0: self.sleeper(wait)
            try: process=subprocess.run(command,input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=timeout or self.timeout)
            except subprocess.TimeoutExpired as exc: raise SyncError("notion_request_timeout") from exc
            self.last_request=time.monotonic()
            if process.returncode==0:
                try: return json.loads(process.stdout)
                except json.JSONDecodeError as exc: raise SyncError("invalid_notion_response") from exc
            error=process.stderr.decode("utf-8",errors="replace")
            match=re.search(r"\b(429|529)\b",error)
            if match and attempt+1<self.attempts:
                retry=re.search(r"Retry-After:?\s*(\d+)",error,re.I); delay=int(retry.group(1)) if retry else min(2**attempt,30)
                self.sleeper(delay); continue
            if "extension that is not supported" in error: raise SyncError("unsupported_upload_extension")
            raise SyncError("notion_mutation_ambiguous" if mutation else "notion_request_failed")
        raise SyncError("notion_request_failed")
    def request(self,path,*,method="GET",body=None,mutation=False):
        command=["ntn","api",path,"-X",method]; payload=None
        if body is not None: command += ["--data","@-"]; payload=json.dumps(body).encode()
        return self._run(command,input=payload,mutation=mutation)
    def upload(self,path,name):
        return self.upload_bytes(Path(path).read_bytes(),name)
    def upload_bytes(self,data,name):
        upload_name=f"payload-{hashlib.sha256(data).hexdigest()[:20]}.txt"
        command=["ntn","files","create","--json","--filename",upload_name,"--content-type","text/plain"]
        upload_timeout=max(self.timeout,min(600,30+len(data)//1_000_000*10))
        return self._run(command,input=data,mutation=True,timeout=upload_timeout)
    def create_page(self,body): return self.request("v1/pages",method="POST",body=body,mutation=True)
    def update_page(self,page_id,properties,markdown):
        self.update_markdown(page_id,markdown)
        self.update_properties(page_id,properties)
    def update_properties(self,page_id,properties): return self.request(f"v1/pages/{page_id}",method="PATCH",body={"properties":properties},mutation=True)
    def update_markdown(self,page_id,markdown): return self.request(f"v1/pages/{page_id}/markdown",method="PATCH",body={"type":"replace_content","replace_content":{"new_str":markdown}},mutation=True)
    def verify_skill(self,row,page_id,expected_markdown,expected_bundle=None,expected_manifest=None):
        archive_limit=max(25_000_000,len(expected_bundle or b"")+10_000_000)
        client=NotionExportClient(runner=lambda path:self.request(path),max_archive_bytes=archive_limit)
        for attempt in range(3):
            try: export=client.fetch("skill",page_id); break
            except NotionImportError as exc:
                if str(exc)!="archive_download_failure" or attempt==2: raise SyncError("export_download_failed") from exc
                self.sleeper(2**attempt)
        files=_archive_entries(export); roots=_package_roots(files)
        if len(roots)!=1: raise SyncError("export_membership_mismatch")
        root=roots[0]; relative={PurePosixPath(name).relative_to(root).as_posix():entry for name,entry in files.items() if root in PurePosixPath(name).parents}
        if expected_bundle is None: expected_bundle=_bundle_bytes(row,_pin_frozen_row(row))
        if expected_manifest is None: expected_manifest=_generated_manifest(row,expected_bundle)
        expected={"package-bundle.txt":hashlib.sha256(expected_bundle).hexdigest(),
                  "skill-package.json":hashlib.sha256(expected_manifest).hexdigest()}
        if set(relative)!={"SKILL.md",*expected}: raise SyncError("export_membership_mismatch")
        for name,digest in expected.items():
            if hashlib.sha256(relative[name]["data"]).hexdigest()!=digest: raise SyncError("export_resource_mismatch")
        try: package=json.loads(relative["skill-package.json"]["data"])
        except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise SyncError("export_package_manifest_invalid") from exc
        if package.get("stable_id")!=row["stable_id"] or package.get("executable_paths")!=row["executable_paths"] or package.get("bundle_sha256")!=hashlib.sha256(expected_bundle).hexdigest(): raise SyncError("export_package_manifest_mismatch")
        _metadata,body=_exported_skill(relative["SKILL.md"]["data"])
        expected_marker=parse_managed_marker(expected_markdown); actual_marker=parse_managed_marker(body)
        if not expected_marker or actual_marker!=expected_marker: raise SyncError("export_skill_marker_mismatch")
        return export.version_id

    def validate_destination(self,database_id,data_source_id):
        data=self.request(f"v1/data_sources/{data_source_id}")
        parent=data.get("parent",{}) if isinstance(data,dict) else {}
        observed=parent.get("database_id") or data.get("database_id") if isinstance(data,dict) else None
        if observed!=database_id: raise SyncError("destination_relationship_mismatch")


def discover_remote(data_source_id, *, transport=None, bootstrap=None):
    transport=transport or NtnSyncTransport(); cursor=None; pages=[]
    while True:
        body={"page_size":100}
        if cursor: body["start_cursor"]=cursor
        response=transport.request(f"v1/data_sources/{data_source_id}/query",method="POST",body=body)
        results=response.get("results")
        if not isinstance(results,list): raise SyncError("invalid_remote_inventory")
        pages.extend(results)
        if not response.get("has_more"): break
        cursor=response.get("next_cursor")
        if not isinstance(cursor,str) or not cursor: raise SyncError("invalid_remote_pagination")
    boot={row["page_id"]:row for row in (bootstrap or [])}; remote=[]; seen=set()
    for page in pages:
        page_id=page.get("id")
        if not isinstance(page_id,str): raise SyncError("invalid_remote_page")
        markdown_response=transport.request(f"v1/pages/{page_id}/markdown")
        marker=parse_managed_marker(markdown_response.get("markdown","") if isinstance(markdown_response,dict) else "")
        seed=boot.get(page_id)
        if marker is None and seed is None: continue
        sid=(marker or seed)["stable_id"]
        if sid in seen: raise SyncError("ambiguous_remote_inventory")
        seen.add(sid); export=transport.request(f"v1/ai/skills/{page_id}")
        version=export.get("version_id") if isinstance(export,dict) else None
        if not isinstance(version,str): raise SyncError("invalid_remote_export")
        verified=marker.get("desired_hash") if marker and marker.get("phase","complete")=="complete" else seed.get("verified_hash") if seed else None
        baseline=seed.get("last_synced_hash") if seed and seed.get("verified_receipt") is True else None
        if seed and seed.get("verified_receipt") is True and marker and marker.get("phase","complete")=="complete" and seed.get("verified_hash")==marker.get("desired_hash"):
            baseline=seed.get("remote_hash")
        remote.append({"stable_id":sid,"page_id":page_id,"verified_hash":verified,"remote_hash":version,"last_synced_hash":baseline,
                       "verified_receipt":bool(seed and seed.get("verified_receipt") is True and baseline==version)})
    return sorted(remote,key=lambda row:row["stable_id"])


def verify_remote_receipts(inventory,remote_rows,*,transport=None):
    transport=transport or NtnSyncTransport(); rows={row["stable_id"]:row for row in inventory.get("skills",[])}; verified=[]
    for remote in remote_rows:
        result=dict(remote); row=rows.get(remote.get("stable_id"))
        if (row and remote.get("verified_receipt") is True and remote.get("last_synced_hash")==remote.get("remote_hash")
                and remote.get("verified_hash")==row["desired_hash"]):
            markdown=transport.request(f"v1/pages/{remote['page_id']}/markdown").get("markdown","")
            marker=parse_managed_marker(markdown)
            if marker and marker.get("desired_hash")==row["desired_hash"] and marker.get("phase","complete")=="complete":
                try:
                    version=transport.verify_skill(row,remote["page_id"],_page_material(row,marker.get("operation_id"))[2])
                except SyncError:
                    pass
                else:
                    if version==remote.get("remote_hash"):
                        result.update({"last_synced_hash":version,"verified_receipt":True})
        verified.append(result)
    return verified


def fidelity_preflight(inventory):
    passed=[]; failed=[]
    for row in inventory.get("skills",[]):
        try:
            pinned=_pin_frozen_row(row); _page_material(row,"preflight",source_data=pinned[row["entrypoint"]])
            bundle=_bundle_bytes(row,pinned)
            if len(bundle)>500_000_000: raise SyncError("bundle_exceeds_upload_limit")
            json.loads(_generated_manifest(row,bundle))
        except (SyncError,FidelityError,ValueError) as exc:
            failed.append({"stable_id":row.get("stable_id"),"error":str(exc)[:160]})
        else: passed.append(row["stable_id"])
    return {"inventory_hash":inventory.get("inventory_hash"),"passed":passed,"failed":failed,
            "counts":{"passed":len(passed),"failed":len(failed)}}


def _generated_manifest(row,bundle_data=None):
    if bundle_data is None: bundle_data=_bundle_bytes(row,_pin_frozen_row(row))
    bundled=[item for item in row["files"] if item["path"]!="skill-package.json"]
    return json.dumps({"schema_version":1,"stable_id":row["stable_id"],"aliases":row["aliases"],"entrypoint":"SKILL.md",
        "invocation_policy":row["invocation_policy"],"required_runtimes":row["required_runtimes"],
        "executable_paths":row["executable_paths"],"bundle_attachment":"package-bundle.txt",
        "bundle_sha256":hashlib.sha256(bundle_data).hexdigest(),"bundle_bytes":len(bundle_data),
        "bundle_files":[{key:item[key] for key in ("path","sha256","bytes","mode")} for item in bundled]},sort_keys=True,indent=2).encode()+b"\n"


def _bundle_bytes(row,pinned):
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode="w") as archive:
        for item in sorted(row["files"],key=lambda value:value["path"]):
            if item["path"]=="skill-package.json": continue
            data=pinned[item["path"]]; info=tarfile.TarInfo(item["path"]); info.size=len(data); info.mode=item["mode"]
            info.mtime=0; info.uid=0; info.gid=0; info.uname=""; info.gname=""; archive.addfile(info,io.BytesIO(data))
    output=io.BytesIO()
    with gzip.GzipFile(fileobj=output,mode="wb",mtime=0,filename="") as compressed: compressed.write(raw.getvalue())
    return output.getvalue()


def _attachment_name(path):
    if "/" not in path and len(path.encode("utf-8"))<=100: return path
    suffix=PurePosixPath(path).name; digest=hashlib.sha256(path.encode()).hexdigest()[:16]
    while len(f"resource-{digest}-{suffix}".encode("utf-8"))>100: suffix=suffix[1:]
    return f"resource-{digest}-{suffix}"


def _canonical_attachment_name(row):
    source=next(item for item in row["files"] if item["path"]==row["entrypoint"])
    return f"canonical-{source['sha256'][:20]}.txt"


def _page_material(row,operation_id,phase="complete",source_data=None):
    root=Path(row["source_root"]); source=root/row["entrypoint"]
    name,description,_metadata,markdown=_split_skill(source.read_bytes() if source_data is None else source_data)
    identity=managed_marker(row["stable_id"],row["desired_hash"],operation_id,phase)
    heading="## Preserved source metadata"
    if heading in markdown: markdown=markdown.replace(heading,identity+"\n"+heading,1)
    else: markdown+=identity
    return name,description,markdown


def _verify_frozen_row(row):
    root=Path(row["source_root"])
    if row.get("entrypoint")!="SKILL.md": raise SyncError("unsupported_custom_entrypoint")
    expected_paths=[item["path"] for item in row["files"]]
    if any(PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts for path in expected_paths): raise SyncError("invalid_package_path")
    actual=_package_files(root)
    if [item["path"] for item in actual]!=expected_paths: raise SyncError("frozen_source_drift")
    for item in row["files"]:
        path=root/item["path"]
        if not path.is_file() or path.is_symlink(): raise SyncError("frozen_source_drift")
        current=_file(path,root)
        if any(current[key]!=item[key] for key in ("path","sha256","bytes","mode")): raise SyncError("frozen_source_drift")


def _pin_frozen_row(row):
    _verify_frozen_row(row); root=Path(row["source_root"]); pinned={}
    for item in row["files"]:
        data=(root/item["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest()!=item["sha256"] or len(data)!=item["bytes"]: raise SyncError("frozen_source_drift")
        pinned[item["path"]]=data
    return pinned


def _validate_frozen_plan(inventory,plan):
    if inventory.get("inventory_hash")!=_hash({key:value for key,value in inventory.items() if key!="inventory_hash"}): raise SyncError("inventory_hash_mismatch")
    if plan.get("plan_hash")!=_hash({key:value for key,value in plan.items() if key!="plan_hash"}): raise SyncError("plan_hash_mismatch")
    if inventory["inventory_hash"]!=plan.get("inventory_hash"): raise SyncError("plan_inventory_mismatch")
    rows={row["stable_id"]:row for row in inventory["skills"]}; local=set(rows)
    actions=plan.get("actions",[]); seen=set()
    for action in actions:
        sid=action.get("stable_id")
        if sid in seen: raise SyncError("duplicate_plan_action")
        seen.add(sid)
        if action.get("action") in {"create","update","unchanged","conflict"}:
            if sid not in rows or action.get("desired_hash")!=rows[sid]["desired_hash"]: raise SyncError("plan_membership_mismatch")
    if not local <= seen: raise SyncError("plan_membership_mismatch")
    counts={kind:sum(item.get("action")==kind for item in actions) for kind in ("create","update","unchanged","conflict","retirement_candidate")}
    if counts!=plan.get("counts"): raise SyncError("plan_counts_mismatch")


def _find_operation(transport,data_source_id,operation_id):
    cursor=None; matches=[]
    while True:
        body={"page_size":100};
        if cursor: body["start_cursor"]=cursor
        response=transport.request(f"v1/data_sources/{data_source_id}/query",method="POST",body=body)
        for page in response.get("results",[]):
            identity=page.get("id"); markdown=transport.request(f"v1/pages/{identity}/markdown").get("markdown","")
            marker=parse_managed_marker(markdown)
            if marker and marker.get("operation_id")==operation_id: matches.append(identity)
        if not response.get("has_more"): break
        cursor=response.get("next_cursor")
    if len(matches)>1: raise SyncError("ambiguous_operation_reconciliation")
    return matches[0] if matches else None


def _find_stable_id(transport,data_source_id,stable_id):
    cursor=None; matches=[]
    while True:
        body={"page_size":100}
        if cursor: body["start_cursor"]=cursor
        response=transport.request(f"v1/data_sources/{data_source_id}/query",method="POST",body=body)
        for page in response.get("results",[]):
            identity=page.get("id"); marker=parse_managed_marker(transport.request(f"v1/pages/{identity}/markdown").get("markdown",""))
            if marker and marker.get("stable_id")==stable_id: matches.append(identity)
        if not response.get("has_more"): break
        cursor=response.get("next_cursor")
    if len(matches)>1: raise SyncError("ambiguous_stable_id_reconciliation")
    return matches[0] if matches else None


def _remote_identity_map(transport,data_source_id):
    cursor=None; identities={}
    while True:
        body={"page_size":100}
        if cursor: body["start_cursor"]=cursor
        response=transport.request(f"v1/data_sources/{data_source_id}/query",method="POST",body=body)
        for page in response.get("results",[]):
            page_id=page.get("id"); marker=parse_managed_marker(transport.request(f"v1/pages/{page_id}/markdown").get("markdown",""))
            if not marker: continue
            stable_id=marker.get("stable_id")
            if stable_id in identities: raise SyncError("ambiguous_stable_id_reconciliation")
            identities[stable_id]=page_id
        if not response.get("has_more"): break
        cursor=response.get("next_cursor")
    return identities


def _verify_existing(transport,row,page_id,source_data=None,bundle_data=None,manifest_data=None):
    remote=transport.request(f"v1/pages/{page_id}/markdown").get("markdown","")
    marker=parse_managed_marker(remote)
    if not marker or marker.get("desired_hash")!=row["desired_hash"] or marker.get("phase","complete")!="complete":
        raise SyncError("existing_skill_requires_reconciliation")
    expected=_page_material(row,marker.get("operation_id"),source_data=source_data)[2]
    return transport.verify_skill(row,page_id,expected,bundle_data,manifest_data)


def _property_fingerprint(properties):
    properties=properties if isinstance(properties,dict) else {}
    def text(name,kind):
        value=properties.get(name,{})
        items=value.get(kind,[]) if isinstance(value,dict) else []
        return "".join((item.get("plain_text") or item.get("text",{}).get("content") or "") for item in items if isinstance(item,dict))
    tags=properties.get("Tags",{}); tags=tags.get("multi_select",[]) if isinstance(tags,dict) else []
    files=properties.get("Files",{}); files=files.get("files",[]) if isinstance(files,dict) else []
    normalized={"name":text("Skill name","title"),"description":text("Description","rich_text"),
        "tags":sorted(item.get("name","") for item in tags if isinstance(item,dict)),
        "files":sorted((item.get("name",""),item.get("type",""),
            (item.get("file_upload") or {}).get("id","") if isinstance(item.get("file_upload"),dict) else "") for item in files if isinstance(item,dict))}
    return _hash(normalized)


def execute_sync(inventory,plan,*,ledger_path,run_id=None,transport=None,limit=None,only_ids=None):
    ledger=SyncLedger(ledger_path)
    run_id=run_id or f"sync-{uuid.uuid4().hex}"
    with ledger.run_lock():
        try:
            return _execute_sync(inventory,plan,ledger=ledger,run_id=run_id,transport=transport,limit=limit,only_ids=only_ids)
        except Exception:
            current=ledger.run(run_id)
            if current and current["status"]=="running": ledger.finish_run(run_id,"failed")
            raise


def _execute_sync(inventory,plan,*,ledger,run_id=None,transport=None,limit=None,only_ids=None):
    _validate_frozen_plan(inventory,plan)
    transport=transport or NtnSyncTransport(); run_id=run_id or f"sync-{uuid.uuid4().hex}"
    binding=_hash({"inventory_hash":inventory["inventory_hash"],"plan_hash":plan["plan_hash"],"database_id":plan["database_id"],"data_source_id":plan["data_source_id"]})
    destination_id=_hash({"database_id":plan["database_id"],"data_source_id":plan["data_source_id"]})
    ledger.create_run(run_id,source_snapshot_id=binding,destination_id=destination_id); rows={row["stable_id"]:row for row in inventory["skills"]}
    mutations=[item for item in plan["actions"] if item["action"] in {"create","update"}]
    if only_ids is not None:
        wanted=set(only_ids); mutations=[item for item in mutations if item["stable_id"] in wanted]
        if {item["stable_id"] for item in mutations}!=wanted: raise SyncError("requested_sync_ids_unavailable")
    elif plan["counts"].get("conflict"):
        raise SyncError("plan_contains_conflicts")
    if limit is not None: mutations=mutations[:limit]
    for action in mutations: _verify_frozen_row(rows[action["stable_id"]])
    transport.validate_destination(plan["database_id"],plan["data_source_id"])
    remote_identities=_remote_identity_map(transport,plan["data_source_id"])
    completed=[]
    try:
        for action in mutations:
            row=rows[action["stable_id"]]; kind=action["action"]; operation_id=f"{run_id}:{row['stable_id']}"
            prior=ledger.operation(run_id,row["stable_id"],kind)
            if prior and prior["status"]=="complete":
                completed.append({"stable_id":row["stable_id"],"page_id":prior["page_id"],"action":kind}); continue
            pinned=_pin_frozen_row(row); source_data=pinned[row["entrypoint"]]; bundle_data=_bundle_bytes(row,pinned); manifest_data=_generated_manifest(row,bundle_data)
            if prior and prior["status"]=="in_progress":
                page_id=action.get("page_id") if kind=="update" else remote_identities.get(row["stable_id"])
                if not page_id: raise SyncError("ambiguous_mutation_requires_reconciliation")
                remote=transport.request(f"v1/pages/{page_id}/markdown").get("markdown","")
                marker=parse_managed_marker(remote)
                if marker and marker.get("desired_hash")==row["desired_hash"] and marker.get("phase","complete")=="complete":
                    version=_verify_existing(transport,row,page_id,source_data,bundle_data,manifest_data)
                    ledger.record_result(run_id,row["stable_id"],kind,page_id=page_id,remote_hash=version,result_id=version)
                    completed.append({"stable_id":row["stable_id"],"page_id":page_id,"action":kind}); continue
                if kind!="update" or not marker or marker.get("operation_id")!=operation_id or marker.get("phase")!="pending":
                    raise SyncError("ambiguous_mutation_requires_reconciliation")
                pending_expected=_page_material(row,operation_id,"pending",source_data=source_data)[2]
                try: compare_markdown(pending_expected,remote,title=row["name"],profile="page")
                except FidelityError as exc: raise SyncError("partial_update_content_changed") from exc
            else:
                ledger.checkpoint_intent(run_id,row["stable_id"],kind,row["desired_hash"],page_id=action.get("page_id")); ledger.record_attempt(run_id,row["stable_id"],kind)
            name,description,markdown=_page_material(row,operation_id,source_data=source_data); uploads=[]
            if kind=="update":
                live=transport.request(f"v1/ai/skills/{action['page_id']}")
                marker=parse_managed_marker(transport.request(f"v1/pages/{action['page_id']}/markdown").get("markdown","")) if prior else None
                owned_partial=bool(marker and marker.get("operation_id")==operation_id)
                if not owned_partial and live.get("version_id")!=action.get("expected_remote_hash"): raise SyncError("remote_changed_before_update")
                baseline_kind="baseline:properties"; baseline=ledger.operation(run_id,row["stable_id"],baseline_kind)
                if baseline is None:
                    if prior: raise SyncError("missing_property_baseline")
                    page=transport.request(f"v1/pages/{action['page_id']}")
                    baseline_hash=_property_fingerprint(page.get("properties",{}))
                    ledger.checkpoint_intent(run_id,row["stable_id"],baseline_kind,baseline_hash)
                    ledger.record_attempt(run_id,row["stable_id"],baseline_kind)
                    baseline=ledger.record_result(run_id,row["stable_id"],baseline_kind,result_id=baseline_hash)
                elif baseline["status"]!="complete": raise SyncError("ambiguous_property_baseline")
            else:
                existing_page=remote_identities.get(row["stable_id"])
                if existing_page:
                    version=_verify_existing(transport,row,existing_page,source_data,bundle_data,manifest_data)
                    ledger.record_result(run_id,row["stable_id"],kind,page_id=existing_page,remote_hash=version,result_id=version)
                    completed.append({"stable_id":row["stable_id"],"page_id":existing_page,"action":kind}); continue
                incomplete=ledger.incomplete_skill_operations(row["stable_id"],kind,destination_id)
                if any(item["run_id"]!=run_id for item in incomplete): raise SyncError("ambiguous_prior_create_requires_reconciliation")
            resources=[{"path":"@bundle","upload_name":"package-bundle.txt","sha256":hashlib.sha256(bundle_data).hexdigest(),"data":bundle_data}]
            try:
                for item in resources + [{"path":"skill-package.json"}]:
                    upload_kind=f"upload:{item['path']}"; upload_hash=hashlib.sha256(manifest_data).hexdigest() if item["path"]=="skill-package.json" else item["sha256"]
                    saved=ledger.operation(run_id,row["stable_id"],upload_kind)
                    if saved and saved["status"]=="complete": identity=saved["upload_id"]
                    else:
                        if saved and saved["status"]=="in_progress": raise SyncError("ambiguous_upload_requires_reconciliation")
                        ledger.checkpoint_intent(run_id,row["stable_id"],upload_kind,upload_hash); ledger.record_attempt(run_id,row["stable_id"],upload_kind)
                        if item["path"]=="skill-package.json": uploaded=transport.upload_bytes(manifest_data,item["path"])
                        else: uploaded=transport.upload_bytes(item["data"],item["upload_name"])
                        identity=uploaded.get("id")
                        if not identity: raise SyncError("upload_identity_missing")
                        ledger.record_result(run_id,row["stable_id"],upload_kind,upload_id=identity)
                    if not identity: raise SyncError("upload_identity_missing")
                    display_name=item.get("upload_name",_attachment_name(item["path"]))
                    uploads.append({"type":"file_upload","file_upload":{"id":identity},"name":display_name})
                properties={"Skill name":{"type":"title","title":[{"type":"text","text":{"content":name}}]},
                    "Description":{"type":"rich_text","rich_text":[{"type":"text","text":{"content":description}}]},
                    "Tags":{"type":"multi_select","multi_select":[{"name":row["plugin_tag"]}]},"Files":{"type":"files","files":uploads}}
                if kind=="update":
                    observed=transport.request(f"v1/pages/{action['page_id']}")
                    observed_hash=_property_fingerprint(observed.get("properties",{})); intended_hash=_property_fingerprint(properties)
                    if observed_hash not in {baseline["desired_hash"],intended_hash}: raise SyncError("partial_update_properties_changed")
                if kind=="create":
                    try: page_id=transport.create_page({"parent":{"type":"data_source_id","data_source_id":plan["data_source_id"]},"properties":properties,"markdown":markdown}).get("id")
                    except SyncError:
                        page_id=_find_operation(transport,plan["data_source_id"],operation_id)
                        if page_id is None: raise
                    remote_identities[row["stable_id"]]=page_id
                else:
                    page_id=action["page_id"]
                    pending=_page_material(row,operation_id,"pending")[2]
                    transport.update_markdown(page_id,pending); transport.update_properties(page_id,properties); transport.update_markdown(page_id,markdown)
                if not page_id: raise SyncError("page_identity_missing")
                version=transport.verify_skill(row,page_id,markdown,bundle_data,manifest_data)
                ledger.record_result(run_id,row["stable_id"],kind,page_id=page_id,remote_hash=version,result_id=version); completed.append({"stable_id":row["stable_id"],"page_id":page_id,"action":kind})
            finally: pass
        ledger.finish_run(run_id,"complete")
    except Exception as exc:
        ledger.finish_run(run_id,"failed"); raise
    return {"run_id":run_id,"completed":completed,"limited":limit is not None or only_ids is not None}
