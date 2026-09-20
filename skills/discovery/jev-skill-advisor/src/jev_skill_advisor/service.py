"""Harness-neutral advisory service."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import multiprocessing
from pathlib import Path
import time

from .exposure import Registry, scan
from .profile import current_policy
from .protocol import validate_suggest, validate_read, validate_outcome
from .runtime import ServiceRuntime, iso

BODY_LIMIT = 32768
PROTECTED_MARKERS = ("typesafe_api_key=", "jev_api=", "authorization: bearer", "-----begin ")


def _protected(text):
    lowered = text.lower()
    return any(marker in lowered for marker in PROTECTED_MARKERS)


def _scan_process(profile, data, operation_id, queue):
    try:
        runtime = ServiceRuntime(profile, operation_id=operation_id)
        registry = profile.registry(data["available_ids"], implicit_only=True)
        queue.put(scan(registry, data["task"], data["context"], runtime.evaluator,
                       deadline_s=profile.deadline_s, max_calls=profile.max_calls,
                       max_tokens=profile.max_tokens, max_optional=3, detail_review=True))
    except BaseException as exc:
        queue.put({"status": "incomplete", "reason": "worker_failure", "selected": [],
                   "detail_reviewed": [], "attempts": 0, "provider_attempts": 0,
                   "cache_hits": 0, "input_tokens": 0, "unknown_usage": 1})


class SkillAdvisorService:
    def __init__(self, profile, runtime=None):
        self.profile = profile
        self.runtime = runtime or ServiceRuntime(profile)

    def _card(self, sid):
        entry = self.profile.entries[sid]
        name = sid.rsplit(":", 1)[-1]
        return {"id": sid, "name": name, "description": entry.description[:400],
                "content_hash": entry.source_hash, "policy_hash": entry.policy_hash}

    def _response(self, data, status, reason, selected=(), candidates=(), evidence="profile", telemetry=None):
        receipt_id = self.runtime.new_receipt_id()
        allowed = list(dict.fromkeys([*selected, *candidates]))
        now = time.time()
        receipt = {"receipt_id": receipt_id, "profile_id": self.profile.profile_id,
                   "session_id": data["session_id"], "request_id": data["request_id"],
                   "status": status, "reason": reason, "allowed_ids": allowed,
                   "implicit": status != "explicit_selection", "created_at": iso(now),
                   "expires_at": iso(now + self.profile.receipt_ttl_s), "reads": {}}
        receipt["bindings"] = {sid: {"content_hash": self.profile.entries[sid].source_hash,
                                     "policy_hash": self.profile.entries[sid].policy_hash}
                               for sid in allowed if sid in self.profile.entries}
        self.runtime.save_receipt(receipt)
        return {"protocol_version": 1, "receipt_id": receipt_id, "request_id": data["request_id"],
                "status": status, "reason": reason,
                "selected": [self._card(sid) for sid in selected],
                "candidates": [self._card(sid) for sid in candidates if sid not in selected][:3],
                "availability_evidence": evidence, "catalog_hash": self.profile.catalog_hash,
                "policy_hash": self.profile.policy_hash,
                "telemetry": telemetry or {"evaluations": 0, "provider_attempts": 0, "cache_hits": 0,
                                            "input_tokens": 0, "unknown_usage": 0, "elapsed_ms": 0.0},
                "advisory_only": True}

    def suggest(self, value):
        data = validate_suggest(value)
        started = time.monotonic()
        evidence = "session_verified" if data["available_ids"] is not None else "profile"
        registry = self.profile.registry(data["available_ids"], implicit_only=not bool(data["explicit_skills"]))
        if self.profile.mode == "off":
            return self._response(data, "off", "profile_off", evidence=evidence)
        if data["explicit_skills"]:
            resolved = []
            for requested in data["explicit_skills"]:
                ids = [requested] if requested in registry.entries else [sid for sid in self.profile.names.get(requested, []) if sid in registry.entries]
                if not ids:
                    return self._response(data, "unavailable", "explicit_skill_unavailable", candidates=resolved, evidence=evidence)
                if len(ids) != 1:
                    return self._response(data, "ambiguous", "explicit_skill_ambiguous", candidates=resolved, evidence=evidence)
                resolved.append(ids[0])
            return self._response(data, "explicit_selection", "explicit_skill_authoritative", selected=resolved, evidence=evidence)
        if _protected(data["task"]) or _protected(data["context"]):
            return self._response(data, "unavailable", "protected_input", evidence=evidence)
        if not self.profile.provider_enabled or not self.runtime.key:
            return self._response(data, "unavailable", "provider_unavailable", evidence=evidence)
        if not self.runtime.reserve_prompt():
            return self._response(data, "unavailable", "prompt_budget", evidence=evidence)
        if type(self.runtime) is ServiceRuntime:
            operation_id = self.runtime.new_receipt_id()
            ctx = multiprocessing.get_context("spawn")
            queue = ctx.Queue(maxsize=1)
            process = ctx.Process(target=_scan_process, args=(self.profile, data, operation_id, queue), daemon=True)
            process.start(); process.join(self.profile.deadline_s)
            if process.is_alive():
                process.terminate(); process.join(0.5)
                receipt = {"status": "incomplete", "reason": "absolute_deadline", "selected": [],
                           "detail_reviewed": [], "attempts": 0,
                           "provider_attempts": self.runtime.counts(operation_id).get("operation_attempts", 0),
                           "cache_hits": 0, "input_tokens": 0, "unknown_usage": 1}
            else:
                receipt = queue.get_nowait() if not queue.empty() else {"status": "incomplete", "reason": "worker_failure",
                    "selected": [], "detail_reviewed": [], "attempts": 0, "provider_attempts": 0,
                    "cache_hits": 0, "input_tokens": 0, "unknown_usage": 1}
            queue.close()
        else:
            receipt = scan(registry, data["task"], data["context"], self.runtime.evaluator,
                           deadline_s=self.profile.deadline_s, max_calls=self.profile.max_calls,
                           max_tokens=self.profile.max_tokens, max_optional=3, detail_review=True)
        reason = receipt.get("reason") or "unknown"
        if receipt["status"] == "complete" and receipt.get("selected"):
            status, selected, candidates = "suggested", receipt["selected"], []
        elif receipt["status"] == "complete":
            status, selected, candidates = "none", [], receipt.get("detail_reviewed", [])[:3]
        elif receipt["status"] == "uncertain":
            status, selected, candidates = "uncertain", [], receipt.get("detail_reviewed", [])[:3]
        else:
            status, selected, candidates = "incomplete", [], receipt.get("detail_reviewed", [])[:3]
        telemetry = {"evaluations": receipt.get("attempts", 0), "provider_attempts": receipt.get("provider_attempts", 0),
                     "cache_hits": receipt.get("cache_hits", 0), "input_tokens": receipt.get("input_tokens", 0),
                     "unknown_usage": receipt.get("unknown_usage", 0),
                     "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
        return self._response(data, status, reason, selected, candidates, evidence, telemetry)

    def _receipt(self, session_id, receipt_id):
        receipt = self.runtime.load_receipt(receipt_id)
        if receipt.get("profile_id") != self.profile.profile_id or receipt.get("session_id") != session_id:
            raise ValueError("foreign_receipt")
        expires = datetime.strptime(receipt["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expires:
            raise ValueError("expired_receipt")
        return receipt

    def read(self, value):
        data = validate_read(value)
        try:
            receipt = self._receipt(data["session_id"], data["receipt_id"])
        except ValueError as exc:
            status = "denied" if str(exc) == "foreign_receipt" else "expired"
            return {"protocol_version": 1, "status": status, "receipt_id": data["receipt_id"], "skill_id": data["skill_id"], "advisory_only": True}
        except (OSError, KeyError):
            return {"protocol_version": 1, "status": "expired", "receipt_id": data["receipt_id"], "skill_id": data["skill_id"], "advisory_only": True}
        sid = data["skill_id"]
        if not self.profile.read_enabled or sid not in receipt["allowed_ids"] or sid not in self.profile.read_allowlist:
            return {"protocol_version": 1, "status": "denied", "receipt_id": data["receipt_id"], "skill_id": sid, "advisory_only": True}
        entry = self.profile.entries.get(sid)
        if entry is None:
            return {"protocol_version": 1, "status": "unavailable", "receipt_id": data["receipt_id"], "skill_id": sid, "advisory_only": True}
        binding = receipt.get("bindings", {}).get(sid)
        if not binding or data["expected_content_hash"] != binding.get("content_hash"):
            return {"protocol_version": 1, "status": "stale", "receipt_id": data["receipt_id"], "skill_id": sid, "advisory_only": True}
        implicit, policy_hash = current_policy(Path(entry.source))
        if policy_hash != binding.get("policy_hash") or (receipt.get("implicit") and not implicit):
            return {"protocol_version": 1, "status": "stale", "receipt_id": data["receipt_id"], "skill_id": sid, "advisory_only": True}
        raw = Path(entry.source).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry.source_hash or digest != binding["content_hash"]:
            return {"protocol_version": 1, "status": "stale", "receipt_id": data["receipt_id"], "skill_id": sid, "advisory_only": True}
        if len(raw) > BODY_LIMIT:
            return {"protocol_version": 1, "status": "oversized", "receipt_id": data["receipt_id"], "skill_id": sid, "advisory_only": True}
        def account(current):
            reads = current.setdefault("reads", {})
            delivered = sum(item.get("body_bytes", 0) for key, item in reads.items() if key != sid)
            if delivered + len(raw) > BODY_LIMIT:
                return False
            reads[sid] = reads.get(sid) or {"content_hash": digest, "body_bytes": len(raw)}
            return None
        if self.runtime.update_receipt(data["receipt_id"], account) is False:
            return {"protocol_version": 1, "status": "oversized", "receipt_id": data["receipt_id"], "skill_id": sid, "advisory_only": True}
        self.runtime.record_outcome({"dedupe_key": f"{self.profile.profile_id}:{data['session_id']}:{data['receipt_id']}:read:{sid}",
            "profile_id": self.profile.profile_id, "session_id": data["session_id"], "receipt_id": data["receipt_id"],
            "event_id": f"read:{sid}", "skill_id": sid, "outcome": "read", "evidence": "host_observed", "reason_code": None})
        return {"protocol_version": 1, "status": "read", "receipt_id": data["receipt_id"], "skill_id": sid,
                "content_hash": digest, "body": raw.decode("utf-8"), "body_bytes": len(raw),
                "canonical_path": entry.source, "advisory_only": True}

    def report_outcome(self, value):
        data = validate_outcome(value)
        receipt = self._receipt(data["session_id"], data["receipt_id"])
        if data["skill_id"] not in receipt["allowed_ids"]:
            raise ValueError("unrelated_skill")
        record = {"dedupe_key": f"{self.profile.profile_id}:{data['session_id']}:{data['receipt_id']}:{data['event_id']}",
                  "profile_id": self.profile.profile_id, **{k: data[k] for k in ("session_id", "receipt_id", "event_id", "skill_id", "outcome", "evidence", "reason_code")}}
        created = self.runtime.record_outcome(record)
        return {"protocol_version": 1, "status": "recorded" if created else "duplicate", "event_id": data["event_id"]}
