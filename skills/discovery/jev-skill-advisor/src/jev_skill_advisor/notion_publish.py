"""Dormant, approval-gated publisher for a reviewed Notion Skills pilot."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import yaml

from .fidelity import FidelityError, compare_markdown
from .library_cache import LibraryCache, _archive_files, _skill_roots
from .notion_import import NotionExportClient


class PublishError(ValueError):
    pass


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _split_skill(data):
    text = data.decode("utf-8")
    if not text.startswith("---\n"):
        raise PublishError("missing_frontmatter")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise PublishError("invalid_frontmatter")
    lines = text[4:marker].splitlines()
    try:
        parsed = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as exc:
        raise PublishError("invalid_frontmatter") from exc
    if not isinstance(parsed, dict):
        raise PublishError("invalid_frontmatter")
    name, description = parsed.get("name"), parsed.get("description")
    if not isinstance(name, str) or not isinstance(description, str) or not name or not description:
        raise PublishError("missing_name_or_description")
    body = text[marker + 5 :].strip("\n")
    retained={key:value for key,value in parsed.items() if key not in {"name","description"}}
    metadata=yaml.safe_dump(retained,sort_keys=False,allow_unicode=True).strip() if retained else "{}"
    page = (
        body
        + "\n\n## Preserved source metadata\n\n"
        + "The following metadata is part of the canonical skill contract and remains authoritative for this pilot.\n\n"
        + "```yaml\n"
        + metadata
        + "\n```\n"
    )
    return name, description, metadata, page


def _exported_skill(data):
    text = data.decode("utf-8")
    if not text.startswith("---\n"):
        raise PublishError("export_missing_frontmatter")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise PublishError("export_invalid_frontmatter")
    try:
        metadata = yaml.safe_load(text[4:marker])
    except yaml.YAMLError as exc:
        raise PublishError("export_invalid_frontmatter") from exc
    if not isinstance(metadata, dict):
        raise PublishError("export_invalid_frontmatter")
    return metadata, text[marker + 5 :].strip("\n") + "\n"


def load_plan(manifest_path):
    path = Path(manifest_path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError("invalid_manifest") from exc
    destination = manifest.get("destination", {})
    contract = manifest.get("creation_contract", {})
    request = contract.get("database_request")
    if destination.get("status") not in {"proposed_pending_authorization", "created_and_verified"} or not isinstance(request, dict):
        raise PublishError("unreviewed_destination")
    if request.get("database_type") != "skills" or "initial_data_source" in request:
        raise PublishError("database_must_use_typed_skills_schema")
    if request.get("parent", {}).get("page_id") != destination.get("parent_page_id"):
        raise PublishError("parent_mismatch")
    root = Path(manifest.get("canonical_root", ""))
    if not root.is_absolute():
        raise PublishError("canonical_root_must_be_absolute")
    skills = manifest.get("skills")
    if not isinstance(skills, list) or len(skills) != 3:
        raise PublishError("pilot_requires_exactly_three_skills")
    tag = manifest.get("proposed_plugin_tag")
    if not isinstance(tag, str) or not tag:
        raise PublishError("invalid_plugin_tag")
    pages = []
    for row in skills:
        if row.get("proposed_tags") != [tag] or row.get("upload_inventory") != []:
            raise PublishError("invalid_page_scope")
        source = root / row.get("canonical_relative_path", "") / "SKILL.md"
        data = source.read_bytes()
        inventory = row.get("source_inventory", [])
        expected = next((item for item in inventory if item.get("path") == "SKILL.md"), None)
        if not expected or expected.get("sha256") != _sha(data) or expected.get("bytes") != len(data):
            raise PublishError("canonical_source_drift")
        name, description, metadata, page = _split_skill(data)
        mapping = row.get("notion_content_mapping", {})
        if name != row.get("proposed_page_title") or description != row.get("description"):
            raise PublishError("frontmatter_mapping_mismatch")
        if mapping.get("files_property") is not None:
            raise PublishError("files_must_be_empty")
        if mapping.get("page_content_sha256") != _sha(page.encode()) or mapping.get("page_content_bytes") != len(page.encode()):
            raise PublishError("mapped_body_drift")
        if mapping.get("retained_metadata_sha256") != _sha(metadata.encode()) or mapping.get("retained_metadata_bytes") != len(metadata.encode()):
            raise PublishError("retained_metadata_drift")
        pages.append({
            "stable_id": row["stable_id"],
            "source_sha256": expected["sha256"],
            "properties": {
                "Skill name": {"type": "title", "title": [{"type": "text", "text": {"content": name}}]},
                "Description": {"type": "rich_text", "rich_text": [{"type": "text", "text": {"content": description}}]},
                "Tags": {"type": "multi_select", "multi_select": [{"name": tag}]},
                "Files": {"type": "files", "files": []},
            },
            "markdown": page,
        })
    plan = {
        "schema_version": 1,
        "destination": {"parent_page_id": destination["parent_page_id"], "database_name": destination["database_name"]},
        "database_request": request,
        "plugin_tag": tag,
        "pages": pages,
    }
    plan["plan_hash"] = _sha(_canonical_json(plan))
    # Not part of the plan hash: this is only the local source used to revalidate
    # the reviewed packet immediately before a mutation.
    plan["manifest_path"] = str(path.resolve())
    return plan


def _atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".receipt-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def execution_lock(state_dir, timeout=5.0):
    root = Path(state_dir); root.mkdir(parents=True, exist_ok=True)
    with (root / "publish.lock").open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PublishError("publish_lock_timeout")
                time.sleep(0.025)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_authorization(path, plan):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublishError("invalid_authorization_record") from exc
    required = {
        "authorized": True,
        "plan_hash": plan["plan_hash"],
        "parent_page_id": plan["destination"]["parent_page_id"],
        "database_name": plan["destination"]["database_name"],
        "skill_ids": [row["stable_id"] for row in plan["pages"]],
        "plugin_tag": plan["plugin_tag"],
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        raise PublishError("authorization_scope_mismatch")
    if not isinstance(value.get("authorized_at"), str) or not isinstance(value.get("authorized_by"), str):
        raise PublishError("incomplete_authorization_record")
    return value


class NtnTransport:
    """Small JSON transport. It never retries mutation requests."""
    @staticmethod
    def request(path, *, method="GET", body=None):
        command = ["ntn", "api", path, "-X", method]
        payload = None
        if body is not None:
            command += ["--data", "@-"]
            payload = json.dumps(body).encode()
        process = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if process.returncode:
            raise PublishError("notion_request_failed_or_ambiguous")
        try:
            return json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise PublishError("invalid_notion_response") from exc

    def create_database(self, body): return self.request("v1/databases", method="POST", body=body)
    def get_database(self, identity): return self.request(f"v1/databases/{identity}")
    def get_data_source(self, identity): return self.request(f"v1/data_sources/{identity}")
    def create_page(self, body): return self.request("v1/pages", method="POST", body=body)
    def get_page(self, identity): return self.request(f"v1/pages/{identity}")
    def get_page_markdown(self, identity):
        value = self.request(f"v1/pages/{identity}/markdown")
        markdown = value.get("markdown") if isinstance(value, dict) else None
        if not isinstance(markdown, str): raise PublishError("page_readback_failed")
        return markdown
    def list_plugins(self): return self.request("v1/ai/plugins?page_size=100")


def _schema_names(data_source):
    properties = data_source.get("properties", {})
    return {value.get("name", key): value.get("type") for key, value in properties.items() if isinstance(value, dict)}


def _data_source_id(database):
    sources = database.get("data_sources")
    if not isinstance(sources, list) or len(sources) != 1 or not sources[0].get("id"):
        raise PublishError("database_data_source_missing")
    return sources[0]["id"]


def _database_title(database):
    return _plain_text(database.get("title", []))


def _validate_database(database, plan):
    if database.get("id") != plan.get("database_id", database.get("id")):
        raise PublishError("database_identity_mismatch")
    if database.get("database_type") != "skills":
        raise PublishError("database_type_mismatch")
    if database.get("parent", {}).get("page_id") != plan["destination"]["parent_page_id"]:
        raise PublishError("database_parent_mismatch")
    if _database_title(database) != plan["destination"]["database_name"]:
        raise PublishError("database_title_mismatch")


def _validate_data_source(source, *, identity, database_id):
    expected = {"Skill name": "title", "Description": "rich_text", "Files": "files", "Tags": "multi_select", "Created by": "created_by"}
    if source.get("id") != identity:
        raise PublishError("data_source_identity_mismatch")
    parent = source.get("parent", {})
    if parent.get("database_id") != database_id:
        raise PublishError("data_source_parent_mismatch")
    if source.get("database_type") != "skills" or _schema_names(source) != expected:
        raise PublishError("skills_schema_mismatch")


def _validate_export(export, plan, page_ids):
    files = _archive_files(export)
    roots = _skill_roots(files)
    expected = {row["properties"]["Skill name"]["title"][0]["text"]["content"]: row for row in plan["pages"]}
    actual = {}
    plugin_files = [name for name in files if PurePosixPath(name).name == "plugin.json"]
    if len(plugin_files) != 1:
        raise PublishError("export_plugin_manifest_mismatch")
    prefix = PurePosixPath(plugin_files[0]).parent
    try:
        plugin = json.loads(files[plugin_files[0]])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublishError("export_plugin_manifest_mismatch") from exc
    if plugin.get("name") != plan["plugin_tag"]:
        raise PublishError("export_plugin_manifest_mismatch")
    allowed = {plugin_files[0], (prefix / "mcp.json").as_posix()}
    for root in roots:
        if root.parent != prefix / "skills":
            raise PublishError("export_skill_membership_mismatch")
        skill_path = (root / "SKILL.md").as_posix()
        allowed.add(skill_path)
        metadata, body = _exported_skill(files[skill_path])
        name, description = metadata.get("name"), metadata.get("description")
        if not isinstance(name, str) or name in actual:
            raise PublishError("export_skill_membership_mismatch")
        actual[name] = (description, metadata.get("notion_page_id"), body)
    if set(files) - allowed or set(actual) != set(expected):
        raise PublishError("export_skill_membership_mismatch")
    evidence = []
    for name, row in expected.items():
        wanted_description = row["properties"]["Description"]["rich_text"][0]["text"]["content"]
        if actual[name][0] != wanted_description or actual[name][1] != page_ids[row["stable_id"]]:
            raise PublishError("export_skill_fidelity_mismatch")
        try:
            comparison = compare_markdown(row["markdown"], actual[name][2], title=name, profile="export")
        except FidelityError as exc:
            raise PublishError(f"export_skill_fidelity_mismatch:{exc}") from exc
        evidence.append({"stable_id": row["stable_id"], **comparison})
    return evidence


def apply_plan(plan, *, ack_hash, authorization_path, state_dir, transport=None):
    if ack_hash != plan["plan_hash"]:
        raise PublishError("plan_hash_acknowledgement_mismatch")
    authorization = validate_authorization(authorization_path, plan)
    if load_plan(plan.get("manifest_path", "")) != plan:
        raise PublishError("plan_changed_before_apply")
    transport = transport or NtnTransport()
    receipt_path = Path(state_dir) / f"{plan['plan_hash']}.json"
    with execution_lock(state_dir):
        if receipt_path.exists():
            raise PublishError("existing_receipt_requires_reconciliation")
        receipt = {"schema_version": 1, "plan_hash": plan["plan_hash"], "authorization": authorization, "status": "started", "pages": []}
        _atomic_json(receipt_path, receipt)
        try:
            database = transport.create_database(plan["database_request"])
            database_id = database.get("id")
            if not database_id:
                raise PublishError("database_identity_missing")
            receipt.update(database_id=database_id, status="database_created_identity_known")
            _atomic_json(receipt_path, receipt)
            data_source_id = _data_source_id(database)
            receipt.update(data_source_id=data_source_id, status="database_created")
            _atomic_json(receipt_path, receipt)
            read_database = transport.get_database(database_id)
            bound_plan = {**plan, "database_id": database_id}
            _validate_database(read_database, bound_plan)
            source = transport.get_data_source(data_source_id)
            _validate_data_source(source, identity=data_source_id, database_id=database_id)
            receipt["status"] = "schema_verified"; _atomic_json(receipt_path, receipt)
            for page in plan["pages"]:
                body = {"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "properties": page["properties"], "markdown": page["markdown"]}
                created = transport.create_page(body)
                if not created.get("id"):
                    raise PublishError("page_identity_missing")
                receipt["pages"].append({"stable_id": page["stable_id"], "page_id": created["id"]})
                receipt["status"] = "pages_partial"; _atomic_json(receipt_path, receipt)
            receipt["status"] = "created_pending_verification"; _atomic_json(receipt_path, receipt)
            return receipt
        except Exception:
            receipt["status"] = "stopped_requires_reconciliation"
            _atomic_json(receipt_path, receipt)
            raise


def receipt_status(state_dir, plan_hash):
    path = Path(state_dir) / f"{plan_hash}.json"
    if not path.is_file():
        return {"status": "not_started", "plan_hash": plan_hash}
    return json.loads(path.read_text(encoding="utf-8"))


def _plain_text(items):
    return "".join(item.get("plain_text", item.get("text", {}).get("content", "")) for item in items)


def _page_values(page):
    properties = page.get("properties", {})
    try:
        return {
            "Skill name": _plain_text(properties["Skill name"]["title"]),
            "Description": _plain_text(properties["Description"]["rich_text"]),
            "Tags": [item["name"] for item in properties["Tags"]["multi_select"]],
            "Files": properties["Files"]["files"],
        }
    except (KeyError, TypeError) as exc:
        raise PublishError("page_property_readback_invalid") from exc


def verify_plan(plan, *, state_dir, cache_root, transport=None, export_client=None):
    transport = transport or NtnTransport()
    receipt_path = Path(state_dir) / f"{plan['plan_hash']}.json"
    with execution_lock(state_dir):
        receipt = receipt_status(state_dir, plan["plan_hash"])
        if receipt.get("status") not in {"created_pending_verification", "verified"}:
            raise PublishError("receipt_not_ready_for_verification")
        source = transport.get_data_source(receipt["data_source_id"])
        bound_plan = {**plan, "database_id": receipt["database_id"]}
        _validate_database(transport.get_database(receipt["database_id"]), bound_plan)
        _validate_data_source(source, identity=receipt["data_source_id"], database_id=receipt["database_id"])
        ids = {row["stable_id"]: row["page_id"] for row in receipt.get("pages", [])}
        if set(ids) != {row["stable_id"] for row in plan["pages"]}:
            raise PublishError("page_receipt_mismatch")
        page_evidence = []
        for expected in plan["pages"]:
            page_id = ids[expected["stable_id"]]
            page = transport.get_page(page_id)
            if page.get("id") != page_id or page.get("parent", {}).get("data_source_id") != receipt["data_source_id"]:
                raise PublishError("page_parent_or_identity_mismatch")
            values = _page_values(page)
            wanted = {
                "Skill name": _plain_text(expected["properties"]["Skill name"]["title"]),
                "Description": _plain_text(expected["properties"]["Description"]["rich_text"]),
                "Tags": [plan["plugin_tag"]],
                "Files": [],
            }
            if values != wanted:
                raise PublishError("page_readback_mismatch")
            try:
                comparison = compare_markdown(expected["markdown"], transport.get_page_markdown(page_id), title=values["Skill name"])
            except FidelityError as exc:
                raise PublishError(f"page_readback_mismatch:{exc}") from exc
            page_evidence.append({"stable_id": expected["stable_id"], **comparison})
        plugins = transport.list_plugins().get("results", [])
        matches = [row for row in plugins if row.get("name") == plan["plugin_tag"]]
        if len(matches) != 1 or not matches[0].get("id"):
            raise PublishError("pilot_plugin_not_uniquely_visible")
        client = export_client or NotionExportClient(runner=lambda path: transport.request(path))
        export = client.fetch("plugin", matches[0]["id"])
        export_evidence = _validate_export(export, plan, ids)
        status = LibraryCache(cache_root).publish([export])
        receipt.update(status="verified", plugin_id=matches[0]["id"], snapshot_id=status["snapshot_id"],
                       fidelity={"page_readback": page_evidence, "export": export_evidence})
        _atomic_json(receipt_path, receipt)
        return receipt
