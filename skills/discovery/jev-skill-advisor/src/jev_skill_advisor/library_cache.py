"""Validated, immutable local snapshots of portable skill exports."""
from __future__ import annotations

import hashlib
import gzip
import io
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
from contextlib import contextmanager
import fcntl

from .notion_import import Export


class LibraryCacheError(ValueError):
    pass


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _LimitedReader:
    def __init__(self, stream, limit): self.stream=stream; self.remaining=limit
    def read(self, size=-1):
        if self.remaining <= 0: raise LibraryCacheError("expanded_archive_too_large")
        requested=self.remaining+1 if size is None or size < 0 else min(size,self.remaining+1)
        data=self.stream.read(requested)
        if len(data)>self.remaining: raise LibraryCacheError("expanded_archive_too_large")
        self.remaining-=len(data); return data


def _archive_entries(export, *, max_files=1000, max_expanded_bytes=100_000_000):
    try:
        expanded=_LimitedReader(gzip.GzipFile(fileobj=io.BytesIO(export.archive)),max_expanded_bytes)
        archive = tarfile.open(fileobj=expanded, mode="r|")
    except (OSError,EOFError,tarfile.TarError) as exc:
        raise LibraryCacheError("invalid_archive") from exc
    files = {}; folded = set(); component_spellings={}; count=0; payload_bytes=0
    try:
      with archive:
        for member in archive:
            count+=1
            if count>max_files: raise LibraryCacheError("too_many_archive_entries")
            path = PurePosixPath(member.name)
            if not member.name or path.is_absolute() or ".." in path.parts or "\x00" in member.name:
                raise LibraryCacheError("unsafe_archive_path")
            if member.isdir():
                continue
            if not member.isfile():
                raise LibraryCacheError("unsupported_archive_entry")
            normalized = path.as_posix()
            if normalized in files or normalized.casefold() in folded:
                raise LibraryCacheError("duplicate_archive_path")
            for index in range(1,len(path.parts)):
                component="/".join(path.parts[:index]); key=component.casefold()
                if key in component_spellings and component_spellings[key]!=component:
                    raise LibraryCacheError("case_colliding_directory")
                component_spellings[key]=component
            folded.add(normalized.casefold())
            payload_bytes += member.size
            if payload_bytes > max_expanded_bytes:
                raise LibraryCacheError("expanded_archive_too_large")
            handle = archive.extractfile(member)
            if handle is None:
                raise LibraryCacheError("unreadable_archive_entry")
            data = handle.read(member.size + 1)
            if len(data) != member.size:
                raise LibraryCacheError("archive_size_mismatch")
            files[normalized] = {"data": data, "mode": 0o755 if member.mode & 0o111 else 0o644}
    except LibraryCacheError: raise
    except (OSError,EOFError,tarfile.TarError) as exc: raise LibraryCacheError("invalid_archive") from exc
    return files


def _archive_files(export, **kwargs):
    return {name: entry["data"] for name, entry in _archive_entries(export, **kwargs).items()}


_PAGE_MARKER = re.compile(r"\n*## Managed skill identity\s*```json\s*\{[^`]+\}\s*```\s*", re.S)
_PAGE_APPENDIX = "\n## Preserved source metadata"


def skill_from_page(data):
    """Canonical SKILL.md from a Notion page export.

    The export adds a managed marker, a metadata appendix, and Notion frontmatter.
    Supporting files stay in the package bundle. This keeps the page body.
    """
    # Imported here so the selector can load this module on a Python without PyYAML.
    import yaml
    # The selector imports this module on a Python that does not have PyYAML.
    import yaml
    text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        raise LibraryCacheError("page_skill_missing_frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise LibraryCacheError("page_skill_missing_frontmatter")
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise LibraryCacheError("page_skill_invalid_frontmatter") from exc
    if not isinstance(meta, dict):
        raise LibraryCacheError("page_skill_invalid_frontmatter")
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not isinstance(description, str) or not name.strip() or not description.strip():
        raise LibraryCacheError("page_skill_missing_name")
    body = _PAGE_MARKER.sub("\n\n", text[end + 5 :], count=1)
    extra = {}
    if _PAGE_APPENDIX in body:
        body, _, tail = body.partition(_PAGE_APPENDIX)
        match = re.search(r"```yaml\n(.*?)\n```", tail, re.S)
        if match:
            try:
                loaded = yaml.safe_load(match.group(1)) or {}
            except yaml.YAMLError as exc:
                raise LibraryCacheError("page_skill_invalid_metadata") from exc
            if not isinstance(loaded, dict):
                raise LibraryCacheError("page_skill_invalid_metadata")
            extra = loaded
    body = body.replace("<p>", "").replace("</p>", "")
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    front = {key: value for key, value in extra.items() if key not in {"name", "description"}}
    ordered = {"name": name.strip(), "description": description.strip(), **{key: front[key] for key in sorted(front)}}
    dumped = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n\n{body}".encode()


def reseal_bundle(bundle, manifest, skill_markdown):
    """Replace the bundle skill body and record that the page is the source."""
    entries = _archive_entries(Export("skill", "bundle", "0" * 64, bundle), max_files=10_000, max_expanded_bytes=600_000_000)
    skill_paths = [name for name in entries if PurePosixPath(name).name == "SKILL.md"]
    if not skill_paths:
        raise LibraryCacheError("missing_skill_md")
    target = min(skill_paths, key=lambda name: name.count("/"))
    entries[target] = {"data": skill_markdown, "mode": entries[target]["mode"]}
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name in sorted(entries):
            data = entries[name]["data"]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = entries[name]["mode"]
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed:
        compressed.write(raw.getvalue())
    sealed = output.getvalue()
    try:
        package = json.loads(manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LibraryCacheError("invalid_package_manifest") from exc
    if not isinstance(package, dict):
        raise LibraryCacheError("invalid_package_manifest")
    package["bundle_files"] = [
        {"bytes": len(entries[name]["data"]), "mode": entries[name]["mode"], "path": name,
         "sha256": hashlib.sha256(entries[name]["data"]).hexdigest()}
        for name in sorted(entries)
    ]
    package["bundle_bytes"] = len(sealed)
    package["bundle_sha256"] = hashlib.sha256(sealed).hexdigest()
    package["body_source"] = "page"
    return sealed, json.dumps(package, sort_keys=True, indent=2).encode() + b"\n"



def _skill_roots(files):
    roots = sorted(PurePosixPath(name).parent for name in files if PurePosixPath(name).name == "SKILL.md")
    if not roots:
        raise LibraryCacheError("missing_skill_md")
    return roots


def _package_roots(files):
    roots=_skill_roots(files)
    return [root for root in roots if not any(parent!=root and parent in root.parents for parent in roots)]


def _package_metadata(root, selected, export):
    raw = selected.get("skill-package.json")
    if raw is None:
        return {"schema_version": 1, "stable_id": f"warehouse:{root.name}", "aliases": [],
                "entrypoint": "SKILL.md", "invocation_policy": "source",
                "required_runtimes": [], "executable_paths": [], "attachment_paths": {},
                "canonical_skill_attachment": None,"bundle_attachment":None,"bundle_files":[],
                "notion_id": export.id, "notion_version_id": export.version_id}
    try:
        value = json.loads(raw["data"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LibraryCacheError("invalid_package_manifest") from exc
    required = {"schema_version", "stable_id", "entrypoint", "invocation_policy"}
    allowed = required | {"aliases", "required_runtimes", "executable_paths", "attachment_paths", "canonical_skill_attachment",
                          "bundle_attachment","bundle_sha256","bundle_bytes","bundle_files","body_source"}
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value) or value["schema_version"] != 1:
        raise LibraryCacheError("invalid_package_manifest")
    stable_id = value["stable_id"]
    if not isinstance(stable_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", stable_id):
        raise LibraryCacheError("invalid_package_identity")
    entrypoint = PurePosixPath(value["entrypoint"] if isinstance(value["entrypoint"], str) else "")
    if entrypoint.is_absolute() or ".." in entrypoint.parts or entrypoint.as_posix() not in selected:
        raise LibraryCacheError("invalid_package_entrypoint")
    aliases = value.get("aliases", [])
    runtimes = value.get("required_runtimes", [])
    executables = value.get("executable_paths", [])
    if any(not isinstance(items, list) or any(not isinstance(item, str) or not item for item in items)
           for items in (aliases, runtimes, executables)):
        raise LibraryCacheError("invalid_package_manifest")
    attachment_paths=value.get("attachment_paths",{})
    if not isinstance(attachment_paths,dict): raise LibraryCacheError("invalid_attachment_paths")
    bundle_files=value.get("bundle_files",[])
    bundle_paths={item.get("path") for item in bundle_files if isinstance(item,dict)}
    executable_paths=[]
    for item in executables:
        path=PurePosixPath(item)
        if path.is_absolute() or ".." in path.parts or (path.as_posix() not in selected and path.as_posix() not in attachment_paths.values() and path.as_posix() not in bundle_paths): raise LibraryCacheError("invalid_executable_path")
        executable_paths.append(path.as_posix())
    if value["invocation_policy"] not in {"implicit", "explicit", "source"}:
        raise LibraryCacheError("invalid_invocation_policy")
    for exported,original in attachment_paths.items():
        source=PurePosixPath(exported) if isinstance(exported,str) else PurePosixPath("")
        target=PurePosixPath(original) if isinstance(original,str) else PurePosixPath("")
        if (not exported or source.is_absolute() or len(source.parts)!=1 or exported not in selected or
                not original or target.is_absolute() or ".." in target.parts or target.as_posix() in {"SKILL.md","skill-package.json"}):
            raise LibraryCacheError("invalid_attachment_paths")
    canonical=value.get("canonical_skill_attachment")
    if canonical is not None and (not isinstance(canonical,str) or PurePosixPath(canonical).name!=canonical or canonical not in selected):
        raise LibraryCacheError("invalid_canonical_skill_attachment")
    body_source=value.get("body_source", "bundle")
    if body_source not in {"bundle", "page"}: raise LibraryCacheError("invalid_package_manifest")
    bundle=value.get("bundle_attachment")
    if bundle is not None:
        if (not isinstance(bundle,str) or PurePosixPath(bundle).name!=bundle or bundle not in selected or
                not isinstance(value.get("bundle_sha256"),str) or not isinstance(value.get("bundle_bytes"),int) or
                not isinstance(bundle_files,list)): raise LibraryCacheError("invalid_bundle_manifest")
    value.pop("body_source", None)
    returned = {**value, "aliases": aliases, "required_runtimes": runtimes, "executable_paths": executable_paths,
            "attachment_paths":attachment_paths,"canonical_skill_attachment":canonical,
            "bundle_attachment":bundle,"bundle_files":bundle_files,
            "entrypoint": entrypoint.as_posix(), "notion_id": export.id, "notion_version_id": export.version_id}
    if body_source == "page": returned["body_source"] = "page"
    return returned


class LibraryCache:
    def __init__(self, root, *, max_exports=1024, max_skills=2048):
        self.root = Path(root)
        self.snapshots = self.root / "snapshots"
        self.current_file = self.root / "current.json"
        if not 1 <= max_exports <= 4096 or not 1 <= max_skills <= 8192:
            raise LibraryCacheError("invalid_cache_bounds")
        self.max_exports, self.max_skills = max_exports, max_skills

    @contextmanager
    def _lock(self, timeout=5.0):
        self.root.mkdir(parents=True,exist_ok=True); lock_path=self.root/"library.lock"
        with lock_path.open("a+b") as handle:
            deadline=time.monotonic()+timeout
            while True:
                try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB); break
                except BlockingIOError:
                    if time.monotonic()>=deadline: raise LibraryCacheError("cache_lock_timeout")
                    time.sleep(.025)
            try: yield
            finally: fcntl.flock(handle,fcntl.LOCK_UN)

    def _snapshot_path(self,identity):
        if not isinstance(identity,str) or not re.fullmatch(r"[0-9a-f]{64}",identity): raise LibraryCacheError("invalid_snapshot_id")
        path=self.snapshots/identity
        if path.resolve().parent!=self.snapshots.resolve(): raise LibraryCacheError("invalid_snapshot_id")
        return path

    def _verify_path(self,path,identity):
        manifest_path=path/"manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink(): raise LibraryCacheError("invalid_snapshot")
        try: manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError) as exc: raise LibraryCacheError("invalid_snapshot") from exc
        if manifest.get("snapshot_id")!=identity: raise LibraryCacheError("snapshot_identity_mismatch")
        durable={key:value for key,value in manifest.items() if key!="snapshot_id"}
        semantic={**durable,"exports":[{key:value for key,value in row.items() if key!="archive_hash"} for row in durable.get("exports",[])]}
        if _json_hash(semantic)!=identity: raise LibraryCacheError("snapshot_identity_mismatch")
        expected={row["path"]:(row["sha256"],row["bytes"],row.get("mode",0o644)) for row in manifest.get("files",[]) if isinstance(row,dict) and set(row)>={"path","sha256","bytes"}}
        if len(expected)!=len(manifest.get("files",[])): raise LibraryCacheError("invalid_snapshot_manifest")
        actual={}
        skills=path/"skills"
        if not skills.is_dir() or skills.is_symlink(): raise LibraryCacheError("invalid_snapshot")
        for item in skills.rglob("*"):
            if item.is_symlink(): raise LibraryCacheError("snapshot_contains_link")
            if item.is_dir(): continue
            if not item.is_file(): raise LibraryCacheError("snapshot_contains_special_file")
            relative=item.relative_to(skills).as_posix(); data=item.read_bytes()
            actual[relative]=(hashlib.sha256(data).hexdigest(),len(data),0o755 if item.stat().st_mode & 0o111 else 0o644)
        if actual!=expected: raise LibraryCacheError("snapshot_content_mismatch")
        return manifest

    def _verify(self,identity):
        return self._verify_path(self._snapshot_path(identity),identity)

    def _current(self):
        if not self.current_file.is_file():
            return None
        value = json.loads(self.current_file.read_text(encoding="utf-8"))
        identity = value.get("snapshot_id")
        self._verify(identity)
        return identity

    def status(self):
      with self._lock():
        return self._status_unlocked()

    def _status_unlocked(self):
        identity = self._current()
        if identity is None:
            return {"status": "empty", "snapshot_id": None}
        manifest = json.loads((self.snapshots / identity / "manifest.json").read_text(encoding="utf-8"))
        return {"status": "ready", "snapshot_id": identity, "skill_count": manifest["skill_count"],
                "export_count": len(manifest["exports"]), "catalog_root": str(self.snapshots / identity / "skills")}

    def publish(self, exports, *, activate=True):
        exports = list(exports)
        if not 1 <= len(exports) <= self.max_exports:
            raise LibraryCacheError("export_count_out_of_bounds")
        keys = [(item.kind, item.id) for item in exports]
        if len(keys) != len(set(keys)):
            raise LibraryCacheError("duplicate_export_identity")
        assembled = {}; assembled_folded=set(); packages=[]; package_ids=set(); alias_ids=set()
        export_rows = []
        for export in sorted(exports, key=lambda item: (item.kind, item.id)):
            files = _archive_entries(export,max_expanded_bytes=600_000_000)
            roots = _package_roots(files)
            archive_hash = hashlib.sha256(export.archive).hexdigest()
            export_file_rows=[]
            for name,data in sorted(files.items()):
                export_file_rows.append({"path":name,"sha256":hashlib.sha256(data["data"]).hexdigest(),"bytes":len(data["data"]),"mode":data["mode"]})
            content_hash=_json_hash(export_file_rows)
            for source_root in roots:
                selected = {PurePosixPath(name).relative_to(source_root).as_posix(): data for name, data in files.items()
                            if source_root in PurePosixPath(name).parents}
                if "SKILL.md" not in selected:
                    raise LibraryCacheError("missing_skill_md")
                page_skill = selected["SKILL.md"]
                package = _package_metadata(source_root, selected, export)
                bundle=package["bundle_attachment"]
                if bundle is not None:
                    payload=selected[bundle]["data"]
                    if len(payload)!=package["bundle_bytes"] or hashlib.sha256(payload).hexdigest()!=package["bundle_sha256"]: raise LibraryCacheError("bundle_hash_mismatch")
                    expanded=sum(item.get("bytes",0) for item in package["bundle_files"] if isinstance(item,dict))
                    overhead=max(10_000_000,len(package["bundle_files"])*4096)
                    entries=_archive_entries(Export("skill","bundle","0"*64,payload),max_files=len(package["bundle_files"])+10,
                                             max_expanded_bytes=min(1_000_000_000,expanded+overhead))
                    expected={item["path"]:(item["sha256"],item["bytes"],item["mode"]) for item in package["bundle_files"]}
                    actual={name:(hashlib.sha256(item["data"]).hexdigest(),len(item["data"]),item["mode"]) for name,item in entries.items()}
                    if actual!=expected or "SKILL.md" not in entries: raise LibraryCacheError("bundle_content_mismatch")
                    transport_manifest=selected["skill-package.json"]
                    selected={**entries,"skill-package.json":transport_manifest}
                if package.get("body_source") == "page":
                    selected["SKILL.md"]={"data":skill_from_page(page_skill["data"]),"mode":selected["SKILL.md"]["mode"]}
                canonical=package["canonical_skill_attachment"]
                if canonical is not None: selected["SKILL.md"]=selected.pop(canonical)
                for exported,original in package["attachment_paths"].items():
                    if original in selected and original!=exported: raise LibraryCacheError("duplicate_attachment_target")
                    selected[original]=selected.pop(exported)
                for executable in package["executable_paths"]: selected[executable]["mode"]=0o755
                if "skill-package.json" in selected:
                    slug=re.sub(r"[^A-Za-z0-9._-]+","-",package["stable_id"]).strip("-._") or "skill"
                    destination_name=f"{slug[:80]}-{hashlib.sha256(package['stable_id'].encode()).hexdigest()[:8]}"
                else:
                    destination_name = source_root.name
                if not destination_name or destination_name in assembled or destination_name.casefold() in assembled_folded:
                    raise LibraryCacheError("duplicate_skill_destination")
                assembled_folded.add(destination_name.casefold())
                identifiers = {package["stable_id"], *package["aliases"]}
                if package["stable_id"] in package_ids or identifiers & (package_ids | alias_ids):
                    raise LibraryCacheError("duplicate_package_identity")
                package_ids.add(package["stable_id"]); alias_ids.update(package["aliases"])
                package["destination"] = destination_name
                package["package_root"] = destination_name
                packages.append(package)
                assembled[destination_name] = selected
            export_rows.append({"kind": export.kind, "id": export.id, "version_id": export.version_id,
                                "archive_hash": archive_hash, "content_hash":content_hash,
                                "skill_roots": [root.as_posix() for root in roots]})
        if not 1 <= len(assembled) <= self.max_skills:
            raise LibraryCacheError("skill_count_out_of_bounds")
        file_rows = []
        for skill, files in sorted(assembled.items()):
            for relative, entry in sorted(files.items()):
                data=entry["data"]
                file_rows.append({"path": f"{skill}/{relative}", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "mode": entry["mode"]})
        durable = {"schema_version": 2, "source": "notion-agent-skills", "exports": export_rows,
                   "skill_count": len(assembled), "packages": sorted(packages,key=lambda row:row["stable_id"]), "files": file_rows}
        identity={**durable,"exports":[{key:value for key,value in row.items() if key!="archive_hash"} for row in export_rows]}
        snapshot_id = _json_hash(identity)
        with self._lock():
            destination = self._snapshot_path(snapshot_id)
            self.snapshots.mkdir(parents=True, exist_ok=True)
            for manifest_path in self.snapshots.glob("*/manifest.json"):
                prior=self._verify(manifest_path.parent.name)
                prior_versions={(row["kind"],row["id"],row["version_id"]):row.get("content_hash") for row in prior.get("exports",[])}
                for row in export_rows:
                    key=(row["kind"],row["id"],row["version_id"])
                    if key in prior_versions and prior_versions[key]!=row["content_hash"]:
                        raise LibraryCacheError("content_changed_without_version_change")
            if destination.exists(): self._verify(snapshot_id)
            else:
                staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.root))
                published=False
                try:
                    skills_dir = staging / "skills"
                    for skill, files in assembled.items():
                        for relative, entry in files.items():
                            data=entry["data"]
                            target = skills_dir / skill / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(data)
                            target.chmod(entry["mode"])
                    (staging / "manifest.json").write_text(json.dumps({**durable, "snapshot_id": snapshot_id}, sort_keys=True, indent=2) + "\n")
                    self._verify_path(staging,snapshot_id)
                    os.replace(staging, destination)
                    published=True
                    self._verify(snapshot_id)
                except Exception:
                    shutil.rmtree(staging, ignore_errors=True)
                    if published: shutil.rmtree(destination,ignore_errors=True)
                    raise
            if activate:
                self._point_to(snapshot_id)
                return self._status_unlocked()
            manifest=self._verify(snapshot_id)
            return {"status":"staged","snapshot_id":snapshot_id,"skill_count":manifest["skill_count"],
                    "export_count":len(manifest["exports"]),"catalog_root":str(destination/"skills")}

    def stage(self,exports):
        return self.publish(exports,activate=False)

    def promote(self,snapshot_id):
        with self._lock():
            self._point_to(snapshot_id)
            return self._status_unlocked()

    def _point_to(self, snapshot_id):
        self._verify(snapshot_id)
        self.root.mkdir(parents=True, exist_ok=True)
        handle=tempfile.NamedTemporaryFile("w",encoding="utf-8",prefix=".current-",suffix=".json",dir=self.root,delete=False)
        temporary=Path(handle.name)
        try:
            with handle: handle.write(json.dumps({"snapshot_id": snapshot_id}, sort_keys=True) + "\n")
            os.replace(temporary, self.current_file)
        finally:
            temporary.unlink(missing_ok=True)

    def rollback(self, snapshot_id):
        with self._lock():
            self._point_to(snapshot_id)
            return self._status_unlocked()
