"""Progressive disclosure for executable capabilities.

Startup and selection expose ids and one-line summaries only. A caller-supplied
MCP list-tools client is consulted later, and only for a receipt-authorized id.
This module never opens a network connection, reads secrets, or calls Notion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
from types import MappingProxyType

SELECTION_MAX_CAPABILITIES = 5
SUMMARY_MAX_BYTES = 160
STARTUP_CAPABILITY_BUDGET_BYTES = 2048
DESCRIBE_SCHEMA_MAX_BYTES = 16384
CALL_ARGUMENT_MAX_BYTES = 16384
CALL_RESULT_MAX_BYTES = 65536
MANIFEST_MAX_ENTRIES = 64
WRITE_APPROVAL_KIND = "per_invocation_write_approval"
_ENTRY_KEYS = frozenset({"id", "server", "operation", "summary", "writes", "schema_hash", "source", "provenance"})
_TOP_KEYS = frozenset({"manifest_version", "description", "entries"})
_APPROVAL_KEYS = frozenset({"kind", "capability_id", "invocation_id", "payload_hash", "approver", "signature"})
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,96}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_INVOCATION = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_TOKEN = re.compile(r"[a-z0-9]{3,}")
_PROTECTED = ("typesafe_api_key", "jev_api", "authorization: bearer", "bearer ", "-----begin ")

# Concrete contract for a later harness worker. Native MCP remains the call
# path when the harness can defer tools. This core implements the passthrough
# below and does not register it as a model-facing tool, so a model cannot
# mint its own approval. Writes run only when the harness passes its verifier.
CAPABILITY_CALL_CONTRACT = {
    "name": "capability_call",
    "preferred_path": "After capability_describe, call the selected tool through the harness MCP client when deferred tools exist.",
    "fallback": "capability_call(manifest, receipt, capability_id, arguments, invocation_id=..., list_tools=..., call_tool=..., approval=None, write_verifier=None)",
    "reads": "writes=false, receipt authorizes the id, live name and schema digest match, approval is None.",
    "writes": "Rejected unless write_verifier is a HarnessWriteVerifier and approval is an artifact that verifier signed for this invocation. A boolean or an unsigned dict is not approval.",
    "approval_artifact": {
        "kind": WRITE_APPROVAL_KIND,
        "capability_id": "manifest id being called",
        "invocation_id": "same value passed to this call",
        "payload_hash": "sha256 of canonical JSON arguments",
        "approver": "non-empty harness identity string",
        "signature": "hmac-sha256 from the HarnessWriteVerifier secret over the other artifact fields",
    },
    "not_in_scope": "Registering capability_call as a model tool. A caller-built dict cannot approve a write by itself.",
    "failure_codes": [
        "unknown_capability", "unauthorized_capability", "stale_manifest", "stale_schema",
        "identity_mismatch", "missing_tool", "write_unapproved", "invalid_approval",
        "payload_budget_exceeded", "result_budget_exceeded", "call_unavailable",
        "call_client_unconfigured",
    ],
}


class CapabilityError(ValueError):
    def __init__(self, code):
        if not isinstance(code, str) or not code or code != code.strip():
            code = "denied"
        super().__init__(code)
        self.code = code


def schema_digest(value):
    """sha256 of canonical JSON. Manifest pins use this on the tool input object."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        raise CapabilityError("identity_mismatch") from None
    return hashlib.sha256(encoded).hexdigest()


def _size(value, code):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        raise CapabilityError(code) from None
    return len(encoded)


@dataclass(frozen=True)
class CapabilityManifestEntry:
    id: str
    server: str
    operation: str
    summary: str
    writes: bool
    schema_hash: str
    source: str
    provenance: str

    def record(self):
        return {
            "id": self.id, "server": self.server, "operation": self.operation, "summary": self.summary,
            "writes": self.writes, "schema_hash": self.schema_hash, "source": self.source,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class CapabilityManifest:
    version: int
    description: str
    entries: MappingProxyType
    content_hash: str


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise CapabilityError("invalid_manifest")
    if len(value.encode("utf-8")) > limit or "{" in value or "inputSchema" in value:
        raise CapabilityError("invalid_manifest")
    lowered = value.lower()
    if any(marker in lowered for marker in _PROTECTED):
        raise CapabilityError("invalid_manifest")
    return value.strip()


def _entry(raw):
    if not isinstance(raw, dict) or set(raw) != _ENTRY_KEYS or type(raw["writes"]) is not bool:
        raise CapabilityError("invalid_manifest")
    identifier = raw["id"]
    server = raw["server"]
    operation = raw["operation"]
    schema_hash = raw["schema_hash"]
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise CapabilityError("invalid_manifest")
    if not isinstance(server, str) or not _NAME.fullmatch(server):
        raise CapabilityError("invalid_manifest")
    if not isinstance(operation, str) or not _NAME.fullmatch(operation):
        raise CapabilityError("invalid_manifest")
    if not isinstance(schema_hash, str) or not _HASH.fullmatch(schema_hash):
        raise CapabilityError("invalid_manifest")
    summary = _text(raw["summary"], SUMMARY_MAX_BYTES)
    return CapabilityManifestEntry(
        id=identifier, server=server, operation=operation, summary=summary, writes=raw["writes"],
        schema_hash=schema_hash, source=_text(raw["source"], 240), provenance=_text(raw["provenance"], 240),
    )


def load_manifest(path):
    file = Path(path)
    if not file.is_file() or file.is_symlink():
        raise CapabilityError("invalid_manifest")
    try:
        loaded = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CapabilityError("invalid_manifest") from None
    if not isinstance(loaded, dict) or set(loaded) != _TOP_KEYS or type(loaded["manifest_version"]) is not int:
        raise CapabilityError("invalid_manifest")
    if loaded["manifest_version"] != 1:
        raise CapabilityError("invalid_manifest")
    rows = loaded["entries"]
    if not isinstance(rows, list) or not rows or len(rows) > MANIFEST_MAX_ENTRIES:
        raise CapabilityError("invalid_manifest")
    entries = {}
    seen_ops = set()
    for row in rows:
        entry = _entry(row)
        if entry.id in entries or (entry.server, entry.operation) in seen_ops:
            raise CapabilityError("invalid_manifest")
        entries[entry.id] = entry
        seen_ops.add((entry.server, entry.operation))
    description = _text(loaded["description"], 240)
    payload = {"manifest_version": 1, "description": description,
               "entries": [entries[key].record() for key in sorted(entries)]}
    return CapabilityManifest(1, description, MappingProxyType(entries), schema_digest(payload))


def selection_cards(manifest, query=""):
    """Preview cards only. Lexical overlap does not authorize a capability.

    Authorized ids come from capability_choice.resolve_capabilities.
    """
    if not isinstance(manifest, CapabilityManifest) or not isinstance(query, str):
        raise CapabilityError("invalid_manifest")
    tokens = set(_TOKEN.findall(query.lower()))
    scored = []
    for entry in manifest.entries.values():
        haystack = set(_TOKEN.findall(f"{entry.id} {entry.operation} {entry.summary}".lower()))
        scored.append((len(tokens & haystack) if tokens else 0, entry))
    positive = [row for row in scored if row[0] > 0]
    pool = positive if positive else [(score, entry) for score, entry in scored if not entry.writes]
    pool.sort(key=lambda row: (-row[0], row[1].id))
    cards = []
    for _, entry in pool:
        card = {"id": entry.id, "description": entry.summary}
        if _size(cards + [card], "invalid_manifest") > STARTUP_CAPABILITY_BUDGET_BYTES:
            break
        cards.append(card)
        if len(cards) >= SELECTION_MAX_CAPABILITIES:
            break
    return cards


def startup_disclosure(manifest, query=""):
    cards = selection_cards(manifest, query)
    used = _size(cards, "invalid_manifest")
    return {"capabilities": cards, "budget": {
        "max_cards": SELECTION_MAX_CAPABILITIES,
        "max_bytes": STARTUP_CAPABILITY_BUDGET_BYTES,
        "bytes": used,
    }}


def _fresh(expires_at):
    if not isinstance(expires_at, str):
        return False
    try:
        stamp = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return stamp > datetime.now(timezone.utc)


def _authorize(manifest, receipt, capability_id):
    if not isinstance(manifest, CapabilityManifest) or not isinstance(receipt, dict):
        raise CapabilityError("invalid_receipt")
    if not isinstance(capability_id, str) or capability_id not in manifest.entries:
        raise CapabilityError("unknown_capability")
    pinned = receipt.get("manifest_hash")
    digest = manifest.content_hash
    if not isinstance(pinned, str) or len(pinned) != len(digest) or not hmac.compare_digest(pinned, digest):
        raise CapabilityError("stale_manifest")
    if "expires_at" in receipt and not _fresh(receipt.get("expires_at")):
        raise CapabilityError("unauthorized_capability")
    authorized = receipt.get("authorized_ids")
    if not isinstance(authorized, list) or any(not isinstance(item, str) for item in authorized):
        raise CapabilityError("invalid_receipt")
    if capability_id not in authorized:
        raise CapabilityError("unauthorized_capability")
    return manifest.entries[capability_id]


def _tools_of(value):
    if isinstance(value, dict) and isinstance(value.get("tools"), list):
        return value["tools"]
    if isinstance(value, list):
        return value
    raise CapabilityError("describe_unavailable")


def _invoke(client, method_name, server, code):
    method = getattr(client, method_name, None)
    try:
        if callable(method):
            return method(server)
        if callable(client):
            return client(server)
    except Exception:
        raise CapabilityError(code) from None
    raise CapabilityError(code if method_name == "list_tools" else "call_client_unconfigured")


def _live_schema(entry, list_tools, failure_code):
    if list_tools is None:
        raise CapabilityError("describe_client_unconfigured")
    try:
        listed = _invoke(list_tools, "list_tools", entry.server, failure_code)
    except CapabilityError:
        raise
    except Exception:
        raise CapabilityError(failure_code) from None
    matches = []
    for tool in _tools_of(listed):
        if isinstance(tool, dict) and tool.get("name") == entry.operation:
            matches.append(tool)
    if not matches:
        raise CapabilityError("missing_tool")
    if len(matches) != 1:
        raise CapabilityError("identity_mismatch")
    tool = matches[0]
    if tool.get("server") not in {None, entry.server}:
        raise CapabilityError("identity_mismatch")
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        raise CapabilityError("identity_mismatch")
    live = schema_digest(schema)
    if len(live) != len(entry.schema_hash) or not hmac.compare_digest(live, entry.schema_hash):
        raise CapabilityError("stale_schema")
    if _size(schema, "schema_budget_exceeded") > DESCRIBE_SCHEMA_MAX_BYTES:
        raise CapabilityError("schema_budget_exceeded")
    return schema


def capability_describe(manifest, receipt, capability_id, list_tools):
    """Return the exact live input schema for one authorized capability."""
    entry = _authorize(manifest, receipt, capability_id)
    schema = _live_schema(entry, list_tools, "describe_unavailable")
    return {
        "status": "described", "id": entry.id, "server": entry.server, "operation": entry.operation,
        "schema": schema, "budget": {"max_bytes": DESCRIBE_SCHEMA_MAX_BYTES, "bytes": _size(schema, "schema_budget_exceeded")},
    }


def _invocation(invocation_id):
    if not isinstance(invocation_id, str) or not _INVOCATION.fullmatch(invocation_id):
        raise CapabilityError("invalid_arguments")


def _approval_message(approval):
    material = {
        "kind": approval.get("kind"), "capability_id": approval.get("capability_id"),
        "invocation_id": approval.get("invocation_id"), "payload_hash": approval.get("payload_hash"),
        "approver": approval.get("approver"),
    }
    return json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")


class HarnessWriteVerifier:
    """Harness-held write check. A caller-built dict is not a verifier."""

    def __init__(self, secret):
        if type(secret) is not bytes or len(secret) < 16:
            raise CapabilityError("invalid_approval")
        self._secret = secret

    def __repr__(self):
        return "HarnessWriteVerifier()"

    def issue(self, *, capability_id, invocation_id, arguments, approver):
        approval = {
            "kind": WRITE_APPROVAL_KIND, "capability_id": capability_id, "invocation_id": invocation_id,
            "payload_hash": schema_digest(arguments), "approver": approver,
        }
        approval["signature"] = hmac.new(self._secret, _approval_message(approval), hashlib.sha256).hexdigest()
        return approval

    def __call__(self, approval):
        if type(approval) is not dict:
            return False
        signature = approval.get("signature")
        if not isinstance(signature, str):
            return False
        expected = hmac.new(self._secret, _approval_message(approval), hashlib.sha256).hexdigest()
        if len(signature) != len(expected):
            return False
        return hmac.compare_digest(signature, expected)


def _require_write_approval(entry, invocation_id, arguments, approval):
    if type(approval) is not dict or set(approval) != _APPROVAL_KEYS:
        raise CapabilityError("write_unapproved")
    approver = approval["approver"]
    payload_hash = schema_digest(arguments)
    signature = approval["signature"]
    if (approval["kind"] != WRITE_APPROVAL_KIND or approval["capability_id"] != entry.id
            or approval["invocation_id"] != invocation_id or not isinstance(approval["payload_hash"], str)
            or len(approval["payload_hash"]) != len(payload_hash)
            or not hmac.compare_digest(approval["payload_hash"], payload_hash)):
        raise CapabilityError("write_unapproved")
    if not isinstance(signature, str) or not _HASH.fullmatch(signature):
        raise CapabilityError("write_unapproved")
    if not isinstance(approver, str) or not approver.strip() or len(approver.encode("utf-8")) > 120:
        raise CapabilityError("write_unapproved")
    if any(marker in approver.lower() for marker in _PROTECTED):
        raise CapabilityError("write_unapproved")


def capability_call(manifest, receipt, capability_id, arguments, *, invocation_id, list_tools, call_tool, approval=None, write_verifier=None):
    """Call one authorized tool through an injected client.

    Reads proceed when the live digest matches. Writes do not run unless
    ``write_verifier`` is the harness verifier and ``approval`` is an artifact
    that verifier signed. This function is not an MCP tool.
    """
    entry = _authorize(manifest, receipt, capability_id)
    if not isinstance(arguments, dict) or type(arguments) is not dict:
        raise CapabilityError("invalid_arguments")
    _invocation(invocation_id)
    if entry.writes:
        if type(write_verifier) is not HarnessWriteVerifier:
            raise CapabilityError("write_unapproved")
        _require_write_approval(entry, invocation_id, arguments, approval)
        try:
            trusted = write_verifier(approval)
        except Exception:
            raise CapabilityError("write_unapproved") from None
        if trusted is not True:
            raise CapabilityError("write_unapproved")
    elif approval is not None:
        raise CapabilityError("invalid_approval")
    if _size(arguments, "invalid_arguments") > CALL_ARGUMENT_MAX_BYTES:
        raise CapabilityError("payload_budget_exceeded")
    if call_tool is None:
        raise CapabilityError("call_client_unconfigured")
    _live_schema(entry, list_tools, "call_unavailable")
    method = getattr(call_tool, "call_tool", None)
    try:
        if callable(method):
            result = method(entry.server, entry.operation, arguments)
        elif callable(call_tool):
            result = call_tool(entry.server, entry.operation, arguments)
        else:
            raise CapabilityError("call_client_unconfigured")
    except CapabilityError:
        raise
    except Exception:
        raise CapabilityError("call_unavailable") from None
    if _size(result, "call_unavailable") > CALL_RESULT_MAX_BYTES:
        raise CapabilityError("result_budget_exceeded")
    return {"status": "called", "id": entry.id, "server": entry.server, "operation": entry.operation, "result": result}
