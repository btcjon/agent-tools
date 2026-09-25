"""Opt-in read-only Notion MCP transport for dest Hermes.

The client is the same one ``hermes mcp test`` uses: ``_connect_server`` and
``session.call_tool``. This module does not open token files, source env files,
or change auth or config. The model-facing call is ``notion-fetch`` only.
Writes are rejected before any RPC, including when a caller passes an approval
artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

FETCH_OPERATION = "notion-fetch"
SAMPLE_MANIFEST_NAME = "notion-mcp-capability-manifest.json"
DEFAULT_DESCRIPTION = "Live Notion MCP pins from dest Hermes OAuth tools/list. Ids and one-line summaries only."
DEFAULT_PROVENANCE = (
    "dest Hermes OAuth tools/list 2026-09-24; writes true if a write verb is in the name "
    "or readOnlyHint is not true"
)
_PAGE_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_WRITE_VERB = re.compile(
    r"(?:^|[-_])(?:create|update|delete|move|duplicate|upload|append|insert|remove|trash|archive|restore|edit)(?:$|[-_])",
    re.IGNORECASE,
)


class TransportError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    try:
        from .capability_core import CapabilityError
    except ImportError:
        raise TransportError(code) from None
    raise CapabilityError(code)


def schema_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def one_line(value, limit=160):
    if not isinstance(value, str):
        value = ""
    text = value.replace("{", " ").replace("}", " ").replace("inputSchema", "schema")
    for marker in ("typesafe_api_key", "jev_api", "authorization: bearer", "bearer ", "-----begin "):
        text = re.sub(re.escape(marker), " ", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    if not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    clipped = encoded[:limit].decode("utf-8", errors="ignore").rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0].rstrip()
    return clipped


def writes_flag(name, read_only):
    """False only for a live readOnlyHint of True and a name with no write verb."""
    if not isinstance(name, str) or _WRITE_VERB.search(name):
        return True
    return read_only is not True


def capability_id(operation):
    slug = operation[len("notion-"):] if operation.startswith("notion-") else operation
    return "notion.mcp." + slug.lower()


def _plain(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    elif not isinstance(value, (dict, list, str, int, float, bool)):
        value = getattr(value, "__dict__", value)
    return json.loads(json.dumps(value))


def read_only_hint(tool):
    annotations = tool.get("annotations") if isinstance(tool, dict) else getattr(tool, "annotations", None)
    if annotations is None:
        return None
    if hasattr(annotations, "model_dump"):
        annotations = annotations.model_dump(mode="json", by_alias=True)
    if isinstance(annotations, dict):
        if "readOnlyHint" in annotations:
            return annotations.get("readOnlyHint")
        return annotations.get("read_only_hint")
    if hasattr(annotations, "readOnlyHint"):
        return getattr(annotations, "readOnlyHint")
    return getattr(annotations, "read_only_hint", None)


def plain_tool(tool):
    if isinstance(tool, dict):
        name = tool.get("name")
        description = tool.get("description") or ""
        schema = tool.get("inputSchema", tool.get("input_schema"))
        read_only = tool.get("read_only", read_only_hint(tool))
    else:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", "") or ""
        schema = getattr(tool, "input_schema", None)
        if schema is None:
            schema = getattr(tool, "inputSchema", None)
        read_only = read_only_hint(tool)
    if not isinstance(name, str) or not name:
        _fail("identity_mismatch")
    schema = _plain(schema) if schema is not None else {}
    if not isinstance(schema, dict):
        _fail("identity_mismatch")
    return {
        "name": name,
        "description": description if isinstance(description, str) else "",
        "inputSchema": schema,
        "read_only": read_only is True,
    }


def build_manifest_document(tools, *, source, provenance=DEFAULT_PROVENANCE, description=DEFAULT_DESCRIPTION):
    rows = []
    seen = set()
    for tool in tools:
        item = tool if "inputSchema" in tool and "read_only" in tool else plain_tool(tool)
        operation = item["name"]
        identifier = capability_id(operation)
        if identifier in seen:
            _fail("invalid_manifest")
        seen.add(identifier)
        summary = one_line(item.get("description") or "") or one_line(operation.replace("-", " "))
        if not summary:
            _fail("invalid_manifest")
        rows.append({
            "id": identifier,
            "server": "notion",
            "operation": operation,
            "summary": summary,
            "writes": writes_flag(operation, item.get("read_only") is True),
            "schema_hash": schema_digest(item["inputSchema"]),
            "source": source,
            "provenance": provenance,
        })
    rows.sort(key=lambda row: row["id"])
    return {"manifest_version": 1, "description": description, "entries": rows}


def write_manifest(path, document, *, overwrite=False):
    """Write a manifest. An existing file is left untouched unless overwrite is set."""
    target = Path(path)
    if target.name == SAMPLE_MANIFEST_NAME:
        raise FileExistsError("refusing_sample_manifest")
    if target.is_symlink():
        raise FileExistsError("manifest_symlink")
    if target.exists() and not overwrite:
        raise FileExistsError("manifest_exists")
    text = json.dumps(document, indent=2) + "\n"
    if "inputSchema" in text or "\"schema\"" in text:
        raise ValueError("manifest_leaks_schema")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, prefix=".manifest-", suffix=".json", delete=False)
    temporary = Path(handle.name)
    try:
        handle.write(text)
        handle.close()
        try:
            from .capability_core import load_manifest
        except ImportError:
            load_manifest = None
        if load_manifest is not None:
            load_manifest(temporary)
        if target.exists() and not overwrite:
            raise FileExistsError("manifest_exists")
        if overwrite:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target)
            except FileExistsError:
                raise FileExistsError("manifest_exists") from None
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _text_of(result):
    if not isinstance(result, dict):
        return ""
    text = result.get("text")
    if isinstance(text, str) and text:
        return text
    markdown = result.get("markdown")
    if isinstance(markdown, dict) and isinstance(markdown.get("markdown"), str):
        return markdown["markdown"]
    if isinstance(markdown, str):
        return markdown
    return ""


def body_candidates(result):
    """Hashes of candidate markdown slices. The page text is not returned."""
    text = _text_of(result)
    if not text:
        return []
    variants = [("body_rstrip", markdown_body(result)), ("full_text_rstrip", text.rstrip())]
    found = []
    seen = set()
    for label, blob in variants:
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        found.append({"label": label, "sha256": digest, "chars": len(blob)})
    return found


def markdown_body(result):
    text = _text_of(result)
    if not text:
        _fail("call_unavailable")
    start = text.find("<content>")
    if start < 0:
        return text.rstrip()
    start += len("<content>")
    end = text.find("</content>", start)
    if end < 0:
        _fail("call_unavailable")
    return text[start:end].strip("\n").rstrip()


def body_sha256(result):
    body = markdown_body(result)
    return hashlib.sha256(body.encode("utf-8")).hexdigest(), len(body)


def _result_plain(result):
    is_error = False
    if isinstance(result, dict):
        is_error = bool(result.get("is_error") or result.get("isError"))
        content = result.get("content") or []
    else:
        for name in ("is_error", "isError"):
            value = getattr(result, name, None)
            if isinstance(value, bool):
                is_error = value
                break
        content = getattr(result, "content", None) or []
    if is_error:
        _fail("call_unavailable")
    texts = []
    for block in content:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(text, str):
            texts.append(text)
    if len(texts) != 1:
        _fail("call_unavailable")
    try:
        parsed = json.loads(texts[0])
    except json.JSONDecodeError:
        parsed = {"text": texts[0]}
    if not isinstance(parsed, dict):
        parsed = {"text": texts[0]}
    return parsed


def hermes_root():
    override = os.environ.get("HERMES_AGENT_ROOT")
    candidates = []
    if override:
        candidates.append(Path(override))
    candidates.extend((Path("/home/dev/.hermes/hermes-agent"), Path.home() / ".hermes" / "hermes-agent"))
    for path in candidates:
        if (path / "tools" / "mcp_tool_discovery.py").is_file():
            return path
    _fail("describe_client_unconfigured")


def _quiet_logs():
    # A filter on the root logger does not catch propagated child records.
    # This dedicated bridge has content-free events, so disable Python logging
    # before loading the Hermes OAuth client rather than risk auth/body logs.
    logging.disable(logging.CRITICAL)


def connect_hermes(server_name="notion"):
    """Open the configured Hermes Notion server. Does not read token files itself."""
    root = hermes_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    _quiet_logs()
    from hermes_cli.mcp_config import _get_mcp_servers, _resolve_mcp_server_config
    from tools.mcp_tool_discovery import _connect_server
    from tools.mcp_tool_loop import _ensure_mcp_loop, _run_on_mcp_loop, _stop_mcp_loop

    servers = _get_mcp_servers()
    config = servers.get(server_name) if isinstance(servers, dict) else None
    if not isinstance(config, dict):
        _fail("describe_client_unconfigured")
    config = _resolve_mcp_server_config(dict(config))
    _ensure_mcp_loop()

    async def _open():
        return await _connect_server(server_name, config)

    server = _run_on_mcp_loop(_open, timeout=60)
    tools = [plain_tool(tool) for tool in list(getattr(server, "_tools", []) or [])]

    def refresh():
        async def _list():
            session = getattr(server, "session", None)
            if session is None:
                raise RuntimeError("session_unavailable")
            return await session.list_tools()

        listed = _run_on_mcp_loop(_list, timeout=60)
        return [plain_tool(tool) for tool in list(getattr(listed, "tools", []) or [])]

    def call(operation, arguments):
        async def _call():
            session = getattr(server, "session", None)
            if session is None:
                raise RuntimeError("session_unavailable")
            return await session.call_tool(operation, arguments=arguments)

        return _result_plain(_run_on_mcp_loop(_call, timeout=60))

    def close():
        async def _close():
            shutdown = getattr(server, "shutdown", None)
            if callable(shutdown):
                await shutdown()

        try:
            _run_on_mcp_loop(_close, timeout=15)
        finally:
            _stop_mcp_loop()

    return tools, call, close, refresh


class HermesNotionTransport:
    """list_tools/call_tool client. call_tool refuses every operation except notion-fetch."""

    def __init__(self, *, server_name="notion", connector=None):
        self.server_name = server_name
        self._connector = connector or (lambda: connect_hermes(server_name))
        self._tools = None
        self._call = None
        self._close = None
        self._refresh = None

    def _require(self, server):
        if server != self.server_name:
            _fail("identity_mismatch")

    def _load(self):
        if self._tools is not None:
            return
        connection = self._connector()
        if not isinstance(connection, tuple) or len(connection) not in {3, 4}:
            _fail("describe_client_unconfigured")
        tools, call, close = connection[:3]
        refresh = connection[3] if len(connection) == 4 else None
        if not isinstance(tools, list) or not callable(call):
            _fail("describe_client_unconfigured")
        if refresh is not None and not callable(refresh):
            _fail("describe_client_unconfigured")
        self._tools = [plain_tool(tool) for tool in tools]
        self._call = call
        self._close = close if callable(close) else None
        self._refresh = refresh

    def list_tools(self, server):
        self._require(server)
        self._load()
        if self._refresh is not None:
            try:
                refreshed = self._refresh()
                if not isinstance(refreshed, list):
                    _fail("describe_unavailable")
                self._tools = [plain_tool(tool) for tool in refreshed]
            except Exception:
                _fail("describe_unavailable")
        return {"tools": [
            {"name": tool["name"], "inputSchema": tool["inputSchema"], "server": self.server_name}
            for tool in self._tools
        ]}

    def call_tool(self, server, operation, arguments):
        self._require(server)
        self._load()
        matches = [tool for tool in self._tools if tool["name"] == operation]
        if not matches:
            _fail("missing_tool")
        if len(matches) != 1 or operation != FETCH_OPERATION or writes_flag(operation, matches[0]["read_only"] is True):
            _fail("write_unapproved")
        if type(arguments) is not dict or set(arguments) != {"id"} or not isinstance(arguments.get("id"), str):
            _fail("invalid_arguments")
        if not _PAGE_ID.fullmatch(arguments["id"]):
            _fail("invalid_arguments")
        return self._call(operation, arguments)

    def close(self):
        if self._close is not None:
            self._close()
            self._close = None


def fetch_entry(manifest):
    matches = [entry for entry in manifest.entries.values() if entry.operation == FETCH_OPERATION]
    if len(matches) != 1:
        _fail("missing_tool")
    return matches[0]


def read_bridge_call(manifest, receipt, page_id, *, list_tools, call_tool, approval=None,
                     write_verifier=None, capability_id=None, invocation_id=None):
    """Call notion-fetch when the receipt and live schema hash allow it.

    Any write id, and any approval or verifier, raises write_unapproved and
    does not call the client.
    """
    from .capability_core import CapabilityError, capability_call

    if approval is not None or write_verifier is not None:
        raise CapabilityError("write_unapproved")
    if capability_id is not None:
        target = manifest.entries.get(capability_id) if hasattr(manifest, "entries") else None
        if target is None or target.writes or target.operation != FETCH_OPERATION:
            raise CapabilityError("write_unapproved")
    entry = fetch_entry(manifest)
    if entry.writes or entry.operation != FETCH_OPERATION:
        raise CapabilityError("write_unapproved")
    if not isinstance(page_id, str) or not _PAGE_ID.fullmatch(page_id):
        raise CapabilityError("invalid_arguments")
    if invocation_id is None:
        invocation_id = "fetch-" + hashlib.sha256(page_id.encode("utf-8")).hexdigest()[:20]
    return capability_call(
        manifest, receipt, entry.id, {"id": page_id},
        invocation_id=invocation_id, list_tools=list_tools, call_tool=call_tool,
        approval=None, write_verifier=None,
    )


def capture_proof(transport, document):
    fetch = next(row for row in document["entries"] if row["operation"] == FETCH_OPERATION)
    return {
        "tool_count": len(document["entries"]),
        "names": [row["operation"] for row in document["entries"]],
        "write_count": sum(1 for row in document["entries"] if row["writes"]),
        "read_count": sum(1 for row in document["entries"] if not row["writes"]),
        "fetch_schema_hash": fetch["schema_hash"],
        "fetch_writes": fetch["writes"],
    }


def live_canary(transport, *, page_id, expected_body_sha256):
    if not isinstance(page_id, str) or not _PAGE_ID.fullmatch(page_id):
        _fail("invalid_arguments")
    if not isinstance(expected_body_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_body_sha256):
        _fail("invalid_arguments")
    result = transport.call_tool(transport.server_name, FETCH_OPERATION, {"id": page_id})
    digest, chars = body_sha256(result)
    return {
        "page_id": page_id,
        "body_sha256": digest,
        "body_chars": chars,
        "canary_match": digest == expected_body_sha256,
        "candidates": body_candidates(result),
    }


def redact(text):
    text = re.sub(r"(?i)(authorization|bearer|token|secret|password|api[_-]?key)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    return text[:300]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="notion-mcp-transport")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--source", default="examples/notion-mcp-live-manifest-2026-09-24.json")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--canary", action="store_true")
    parser.add_argument("--canary-page-id")
    parser.add_argument("--canary-body-sha256")
    parser.add_argument("--server", default="notion")
    args = parser.parse_args(argv)
    if args.canary and (not args.canary_page_id or not args.canary_body_sha256):
        parser.error("--canary requires --canary-page-id and --canary-body-sha256")
    transport = HermesNotionTransport(server_name=args.server)
    try:
        listed = transport.list_tools(args.server)
        tools = []
        by_name = {tool["name"]: tool for tool in transport._tools}
        for item in listed["tools"]:
            tools.append(by_name[item["name"]])
        document = build_manifest_document(tools, source=args.source)
        write_manifest(args.output, document, overwrite=args.overwrite)
        proof = capture_proof(transport, document)
        if args.canary:
            proof["canary"] = live_canary(
                transport, page_id=args.canary_page_id, expected_body_sha256=args.canary_body_sha256,
            )
        args.proof.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        sys.stderr.write("CAPTURE_ERROR " + type(exc).__name__ + " " + redact(str(exc)) + "\n")
        raise SystemExit(1) from None
