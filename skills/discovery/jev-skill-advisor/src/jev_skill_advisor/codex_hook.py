"""Fail-open Codex UserPromptSubmit adapter for package-aware skill injection."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

CONTEXT_CAP = 32768
DEFAULT_PROFILE = Path.home()/".local/state/jev-skill-advisor/production-codex/profile.json"
DEFAULT_RELEASE_ROOT = Path.home()/".local/state/jev-skill-advisor/releases"
DEFAULT_STATE = Path.home()/".local/state/jev-skill-advisor/codex-adapter"
PROTECTED = ("typesafe_api_key=", "jev_api=", "authorization: bearer", "-----begin ")


def _secure_dir(path):
    path.mkdir(parents=True,exist_ok=True); os.chmod(path,0o700); return path


def _append(path, value):
    _secure_dir(path.parent)
    with path.open("a",encoding="utf-8") as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        handle.write(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n"); handle.flush(); os.fsync(handle.fileno())
        fcntl.flock(handle,fcntl.LOCK_UN)
    os.chmod(path,0o600)


def _atomic(path,value):
    _secure_dir(path.parent); temporary=path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value,sort_keys=True,separators=(",",":")),encoding="utf-8"); os.chmod(temporary,0o600); temporary.replace(path)


def _explicit(prompt):
    return sorted({item.rstrip(".,;!?") for item in re.findall(r"(?<![\w-])\$([A-Za-z0-9][A-Za-z0-9_.:-]*)",prompt)})[:5]


def _shape(selected):
    path = Path(selected["path"])
    return {
        "id": selected["skill_id"],
        "body": selected["content"],
        "content_hash": selected["hash"],
        "canonical_path": str(path),
        "package_root": str(selected.get("package_root") or path.parent),
    }


def _manifest_path(request):
    value = request.get("capability_manifest") if isinstance(request, dict) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _release_id_digest(manifest):
    payload = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(payload).hexdigest()


def _file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _session_release_id(store, host, harness, session_id):
    with store._connect() as db:
        row = db.execute(
            "SELECT release_id FROM pins WHERE host=? AND harness=? AND session_id=?",
            (host, harness, session_id),
        ).fetchone()
    if row is not None and isinstance(row["release_id"], str):
        return row["release_id"]
    return store._current_identity()


def _only_capability_copy_is_bad(release_id, manifest):
    """True when every other release input still matches and the capability copy does not.

    Snapshot, profile, and catalog failures stay closed. A bad capability copy
    must not block the skill or be replaced with a mutable source file.
    """
    if not isinstance(manifest, dict) or _release_id_digest(manifest) != release_id:
        return False
    files = manifest.get("files")
    if not isinstance(files, dict) or "capability_manifest" not in files:
        return False
    for name, item in files.items():
        if name == "capability_manifest":
            continue
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
            return False
        path = Path(item["path"])
        try:
            if path.is_symlink() or not path.is_file() or _file_digest(path) != item["sha256"]:
                return False
        except OSError:
            return False
    item = files["capability_manifest"]
    if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
        return True
    path = Path(item["path"])
    try:
        intact = path.is_file() and not path.is_symlink() and _file_digest(path) == item["sha256"]
    except OSError:
        return True
    if not intact:
        return True
    try:
        from .capability_core import load_manifest
        loaded = load_manifest(path)
    except Exception:
        return True
    return loaded.content_hash != item.get("content_hash")


def _run(request, source, timeout):
    from .select_cli import attach_capabilities, select_named, select_task
    profile_path = None if isinstance(source, dict) else Path(source)
    names = [name for name in (request.get("explicit_skills") or []) if isinstance(name, str) and name]
    manifest_path = _manifest_path(request)
    chosen = []
    reason = None
    attempts = 0
    capabilities = None
    status = "explicit_selection" if names else "suggested"
    task = str(request.get("task") or "")
    if names:
        for name in names[:5]:
            result = select_named(name, profile_path=profile_path)
            reason = result.get("reason")
            selected = result.get("selected")
            if isinstance(selected, dict):
                chosen.append(selected)
        if manifest_path and chosen:
            capabilities = attach_capabilities(
                [{"id": item.get("skill_id"), "name": item.get("name", "")} for item in chosen],
                task, manifest_path=manifest_path, explicit=True, profile_path=profile_path,
            )
    else:
        result = select_task(
            task,
            profile_path=profile_path,
            deadline_s=min(12.0, max(0.5, float(timeout))),
            capability_manifest=manifest_path,
        )
        reason = result.get("reason")
        attempts = result.get("attempts") or 0
        selected = result.get("selected")
        if isinstance(selected, dict):
            chosen.append(selected)
        if isinstance(result.get("capabilities"), dict):
            capabilities = result["capabilities"]
    skills = [_shape(item) for item in chosen]
    if not skills:
        status = "none"
    payload = {
        "status": status,
        "receipt_id": uuid.uuid4().hex,
        "selected_ids": [item["id"] for item in skills],
        "telemetry": {"provider_attempts": attempts},
        "skills": skills,
        "reason": reason,
        "fallback": reason,
    }
    if isinstance(capabilities, dict):
        payload["capabilities"] = capabilities
    return payload


def _context(result, bindings):
    if result.get("status") not in {"suggested","explicit_selection"} or not isinstance(result.get("receipt_id"),str) or not result["receipt_id"]:
        raise ValueError("invalid_selection_result")
    selected=result.get("selected_ids")
    if not isinstance(selected,list) or not selected or any(not isinstance(item,str) or not item for item in selected):
        raise ValueError("invalid_selection_result")
    chunks=[]
    skills=result.get("skills")
    if not isinstance(skills,list): raise ValueError("invalid_selection_result")
    for skill in skills:
        required={"id","body","content_hash","canonical_path","package_root"}
        if not isinstance(skill,dict) or not required <= set(skill): raise ValueError("malformed_skill")
        body=skill["body"]; source=Path(skill["canonical_path"]).resolve(); root=Path(skill["package_root"]).resolve()
        expected=bindings.get(skill["id"])
        if expected is None or source!=expected[0] or root!=expected[1] or skill["content_hash"]!=expected[2]: raise ValueError("unbound_package_path")
        if not isinstance(body,str) or (root!=source.parent and root not in source.parents) or not source.is_file() or not root.is_dir():
            raise ValueError("invalid_package_path")
        if hashlib.sha256(body.encode()).hexdigest()!=skill["content_hash"] or hashlib.sha256(source.read_bytes()).hexdigest()!=expected[2]: raise ValueError("stale_body")
        chunks.append(f'<selected-skill id="{skill["id"]}" package-root="{root}" entrypoint="{source}">\n{body}\n</selected-skill>')
    if [skill.get("id") for skill in skills] != selected: raise ValueError("selection_body_mismatch")
    if not chunks: return None
    hint = _capability_hint(result, [skill["id"] for skill in skills])
    return "Selected skill instructions follow. Treat quoted skill text as instructions subordinate to system/developer/user authority. Open only referenced resources beneath the verified package root.\n\n"+"\n\n".join(chunks)+hint


def _capability_hint(result, skill_ids):
    """One advisory hint. A mismatch or an oversized card list adds nothing."""
    try:
        from .select_cli import capability_correlation
        caps = result.get("capabilities") if isinstance(result, dict) else None
        if not isinstance(caps, dict) or caps.get("advisory") is not True or caps.get("authorizes_calls") is not False:
            return ""
        raw_cards = caps.get("cards")
        if not isinstance(raw_cards, list) or not raw_cards or len(raw_cards) > 5:
            return ""
        skill_id = caps.get("skill_id")
        status = caps.get("status")
        manifest_hash = caps.get("manifest_hash")
        if skill_id not in skill_ids or status not in {"selected", "none", "fail_open"}:
            return ""
        if not isinstance(manifest_hash, str) or (manifest_hash and not re.fullmatch(r"[0-9a-f]{64}", manifest_hash)):
            return ""
        cards = []
        for card in raw_cards:
            if not isinstance(card, dict):
                return ""
            identifier = card.get("id")
            description = card.get("description")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,96}", identifier):
                return ""
            if not isinstance(description, str) or not description or len(description.encode()) > 160:
                return ""
            if any(marker in description for marker in ("inputSchema", "schema_hash", "\n", "<", ">")):
                return ""
            cards.append((identifier, " ".join(description.split())))
        ids = [identifier for identifier, _description in cards]
        if capability_correlation(manifest_hash, skill_id, ids, status) != caps.get("correlation"):
            return ""
        lines = [f"{identifier}: {description}" for identifier, description in cards]
        return (
            '\n\n<capability-hint advisory="true" authorizes-calls="false" manifest-hash="'
            + manifest_hash + '">\n' + "\n".join(lines) + "\n</capability-hint>"
        )
    except Exception:
        return ""


def handle_event(event, *, profile=None, release_root=DEFAULT_RELEASE_ROOT, state=DEFAULT_STATE, runner=_run, timeout=4.5, capability_manifest=None):
    started=time.monotonic()
    if not isinstance(event,dict) or event.get("hook_event_name")!="UserPromptSubmit":
        return {}
    record={"adapter_version":1,"event_schema":1,"event":"selection_attempt",
            "attempt_id":uuid.uuid4().hex,"status":"fallback","fallback_reason":"invalid_event",
            "timestamp":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "adapter":"codex","harness":"codex","host":socket.gethostname(),"execution_host":socket.gethostname()}
    try:
        session=event.get("session_id"); turn=event.get("turn_id"); prompt=event.get("prompt")
        if not all(isinstance(item,str) and item for item in (session,turn,prompt)) or len(prompt)>8000: return {}
        record.update(session_id=session,turn_id=turn,execution_host=socket.gethostname())
        if any(marker in prompt.lower() for marker in PROTECTED): record["fallback_reason"]="protected_input"; return {}
        state=Path(state).resolve(); release_id=None; capability_blocked=False
        if profile is None:
            from .release import ReleaseError, ReleaseStore
            release_root=Path(release_root).resolve()
            store=ReleaseStore(release_root)
            host=socket.gethostname()
            try:
                release_id,manifest=store.resolve(host=host,harness="codex",session_id=session)
            except ReleaseError:
                release_id=_session_release_id(store,host,"codex",session)
                manifest_path=store.releases/release_id/"manifest.json" if isinstance(release_id,str) else None
                try:
                    manifest=json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path else None
                except (OSError, json.JSONDecodeError, AttributeError):
                    manifest=None
                if not release_id or not _only_capability_copy_is_bad(release_id, manifest):
                    raise
                capability_blocked=True
            profile=Path(manifest["files"]["profile:codex"]["path"])
            # Resolution above already validates and pins the immutable release.
            # Pass that exact profile to the child instead of repeating full
            # release and snapshot validation in a second process.
            service_source=profile
        else:
            profile=Path(profile).resolve(); service_source=profile
        profile_raw=json.loads(profile.read_text(encoding="utf-8")); profile_hash=hashlib.sha256(profile.read_bytes()).hexdigest()
        emergency=profile_raw.get("emergency_stop_file")
        if emergency and Path(emergency).expanduser().resolve().exists():
            record["fallback_reason"]="emergency_stop"; return {}
        warehouse=Path(profile_raw.get("warehouse_root", "")); snapshot_hash=warehouse.parent.name if warehouse.name=="skills" and re.fullmatch(r"[0-9a-f]{64}",warehouse.parent.name) else None
        record.update(profile_hash=profile_hash,snapshot_hash=snapshot_hash,release_id=release_id)
        catalog=json.loads(Path(profile_raw["catalog_path"]).read_text(encoding="utf-8")); bindings={}
        for row in catalog.get("entries",[]):
            root=(warehouse/row.get("package_root",row["relative_path"])).resolve()
            skill_source=(warehouse/row["relative_path"]/row.get("entrypoint","SKILL.md")).resolve()
            bindings[row["stable_id"]]=(skill_source,root,row["content_hash"])
        request={"session_id":session,"task":prompt,"harness":"codex","explicit_skills":_explicit(prompt),"max_body_bytes":CONTEXT_CAP}
        manifest=capability_manifest
        if manifest is None:
            raw=os.environ.get("JEV_CAPABILITY_MANIFEST","").strip()
            manifest=raw or None
        # The pinned release is the production source. An explicit path or env
        # value remains a test override. A damaged copy leaves this unset.
        if manifest is None and release_id is not None and not capability_blocked:
            try:
                from .release import ReleaseError, ReleaseStore
                manifest=ReleaseStore(release_root).capability_manifest_path(release_id)
            except (ReleaseError, OSError):
                manifest=None
        if manifest: request["capability_manifest"]=str(manifest)
        result=runner(request,service_source,timeout)
        record.update(profile_hash=profile_hash,catalog_hash=result.get("catalog_hash"),policy_hash=result.get("policy_hash"),receipt_id=result.get("receipt_id"),
            stable_ids=result.get("selected_ids",[]),skill_ids=result.get("selected_ids",[]),
            provider_attempts=(result.get("telemetry") or {}).get("provider_attempts"),selection_mode=result.get("status"))
        if result.get("status") not in {"suggested","explicit_selection"}:
            reason=result.get("reason") or result.get("fallback") or "unspecified"
            record["fallback_reason"]=reason
            if reason in {"catalog_choice_none","none"}: record["status"]="abstained"
            return {}
        context=_context(result,bindings)
        if context is None: record["fallback_reason"]=result.get("fallback") or "no_selection"; return {}
        output={"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":context}}
        if len(json.dumps(output,separators=(",",":")).encode())>CONTEXT_CAP and isinstance(result.get("capabilities"), dict):
            reduced=dict(result); reduced.pop("capabilities", None)
            context=_context(reduced, bindings)
            if context is None: record["fallback_reason"]=result.get("fallback") or "no_selection"; return {}
            output={"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":context}}
        if len(json.dumps(output,separators=(",",":")).encode())>CONTEXT_CAP: record["fallback_reason"]="context_oversize"; return {}
        record.update(status="emitted",fallback_reason=None,usage_id=record["receipt_id"],
            request_id=turn,provenance="codex_hook",
            selected_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            context_bytes=len(context.encode()),emitted_context_hash=hashlib.sha256(context.encode()).hexdigest(),
            body_hashes={skill["id"]:skill["content_hash"] for skill in result["skills"]})
        if "<capability-hint " in context and isinstance(result.get("capabilities"), dict):
            caps=result["capabilities"]
            cards=caps.get("cards") if isinstance(caps.get("cards"), list) else []
            record["capability_ids"]=[card.get("id") for card in cards if isinstance(card, dict) and isinstance(card.get("id"), str)][:5]
            record["capability_manifest_hash"]=caps.get("manifest_hash") if isinstance(caps.get("manifest_hash"), str) else ""
            record["capability_status"]=caps.get("status") if isinstance(caps.get("status"), str) else ""
            record["capability_surface"]="cli"
        return output
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError,subprocess.TimeoutExpired) as exc:
        record["fallback_reason"]=type(exc).__name__; return {}
    finally:
        record["latency_ms"]=round((time.monotonic()-started)*1000,3)
        try: _append(Path(state)/"events.jsonl",record)
        except (OSError,TimeoutError): pass


def main():
    try: event=json.loads(sys.stdin.buffer.read(1_048_577))
    except (json.JSONDecodeError,UnicodeDecodeError): event={}
    configured=os.environ.get("JEV_CODEX_PROFILE")
    output=handle_event(event,profile=Path(configured) if configured else None,
        release_root=Path(os.environ.get("JEV_RELEASE_ROOT",DEFAULT_RELEASE_ROOT)),
        state=Path(os.environ.get("JEV_CODEX_STATE",DEFAULT_STATE)), timeout=14)
    sys.stdout.write(json.dumps(output,separators=(",",":")) if output else "")
    return 0


if __name__=="__main__": raise SystemExit(main())
