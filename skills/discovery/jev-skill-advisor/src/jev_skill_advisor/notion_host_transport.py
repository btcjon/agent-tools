"""Per-host Notion route for the shared stdio bridge.

One host uses one route, ``mcp`` or ``cli``. A fallback runs only when the
caller named both the other route and the reason the preferred route cannot
be used. The CLI route is a read of one page through ``ntn api`` GET. It does
not open a shell, copy credentials, or call any write command.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .capability_core import CALL_RESULT_MAX_BYTES, CapabilityError, _authorize, schema_digest
from .notion_mcp_transport import FETCH_OPERATION, _PAGE_ID, writes_flag

CLI_CAPABILITY_ID = "notion.cli.page_read"
CLI_SERVER = "ntn"
MCP_FETCH_ID = "notion.mcp.fetch"
CREDENTIAL_SOURCES = frozenset({"env", "saved"})
CREDENTIAL_ENV = "NOTION_CREDENTIAL_SOURCE"
WORKSPACE_ENV = "NOTION_EXPECTED_WORKSPACE_ID"
HOST_CONFIG_ENV = "NOTION_HOST_CONFIG"
TOKEN_ENV = "NOTION_API_TOKEN"
PINNED_NTN_VERSION = "0.23.2"
ROUTES = frozenset({"mcp", "cli"})
FALLBACK_REASONS = frozenset({
    "unauthenticated",
    "operation_gap",
    "harness_missing",
    "identity_mismatch",
    "mcp_absent",
})
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_RESULT_CODE = re.compile(r"^[a-z0-9_]{1,40}$")
_LOG_MARKERS = ("bearer", "token", "secret", "markdown", "authorization", "api_key", "workspace_id", "page_id")
WHOAMI_ARGV = ("ntn", "whoami", "--json")
CLI_FETCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
}
_MARKDOWN_KEYS = frozenset({"object", "id", "markdown", "truncated", "unknown_block_ids"})
_DENIED_STATUS = frozenset({401, 403, 404})


@dataclass(frozen=True)
class RouteDecision:
    """Content-free choice. ``fallback_reason`` is null when the preferred route is used."""

    route: str
    fallback_reason: str | None
    executable: bool
    denial_code: str | None

    def record(self):
        return {
            "route": self.route,
            "fallback_reason": self.fallback_reason,
            "executable": self.executable,
        }


def _ready(probe):
    return bool(
        isinstance(probe, dict)
        and probe.get("available") is True
        and probe.get("authenticated") is True
        and probe.get("identity_ok") is True
        and probe.get("operation_ok") is True
    )


def _denial(probe):
    if not isinstance(probe, dict) or probe.get("available") is not True:
        return "harness_missing"
    if probe.get("authenticated") is not True:
        return "unauthenticated"
    if probe.get("identity_ok") is not True:
        return "identity_mismatch"
    return "operation_gap"


def _denial_code(reason):
    if reason == "identity_mismatch":
        return "identity_mismatch"
    if reason == "harness_missing":
        return "describe_client_unconfigured"
    return "call_unavailable"


def _fallback_matches(denial, declared):
    if denial == declared:
        return True
    return denial == "harness_missing" and declared == "mcp_absent"


def resolve_host_route(*, preferred, probes, fallback_route=None, fallback_reason=None):
    """Choose one route. A ready preferred route never consults the other."""

    if preferred not in ROUTES:
        raise ValueError("invalid_route")
    if (fallback_route is None) != (fallback_reason is None):
        raise ValueError("fallback_requires_reason")
    if fallback_route is not None:
        if fallback_route not in ROUTES or fallback_route == preferred:
            raise ValueError("invalid_fallback")
        if fallback_reason not in FALLBACK_REASONS:
            raise ValueError("invalid_fallback_reason")
    if not isinstance(probes, dict) or any(route not in probes for route in ROUTES):
        raise ValueError("invalid_route_probes")
    preferred_probe = probes[preferred]
    if _ready(preferred_probe):
        return RouteDecision(preferred, None, True, None)
    denial = _denial(preferred_probe)
    if fallback_route is None or not _fallback_matches(denial, fallback_reason):
        return RouteDecision(preferred, denial, False, _denial_code(denial))
    if _ready(probes[fallback_route]):
        return RouteDecision(fallback_route, fallback_reason, True, None)
    blocking = _denial(probes[fallback_route])
    return RouteDecision(fallback_route, blocking, False, _denial_code(blocking))


def append_route_record(path, *, host, harness, route, fallback_reason, executable):
    """Append one content-free route row. ``path`` None writes nothing."""

    if path is None:
        return False
    if route not in ROUTES:
        raise ValueError("invalid_route")
    if fallback_reason is not None and fallback_reason not in FALLBACK_REASONS:
        raise ValueError("invalid_fallback_reason")
    if type(executable) is not bool:
        raise ValueError("invalid_executable")
    if not isinstance(host, str) or not isinstance(harness, str):
        raise ValueError("invalid_route_host")
    if len(host) > 80 or len(harness) > 80:
        raise ValueError("invalid_route_host")
    row = {
        "event": "notion_route",
        "executable": executable,
        "fallback_reason": fallback_reason,
        "harness": harness,
        "host": host,
        "route": route,
        "schema_verification": "static_pinned" if route == "cli" else "live",
    }
    _write_log_line(path, row)
    return True


def append_cli_read_record(path, *, host, harness, fallback_reason, capability_id, credential,
                           workspace_match, body_sha256, body_bytes, result_code, ntn_version):
    """Append one CLI read row. The row has no page, body, token, or workspace id."""

    if path is None:
        return False
    if fallback_reason is not None and fallback_reason not in FALLBACK_REASONS:
        raise ValueError("invalid_fallback_reason")
    if not isinstance(host, str) or not isinstance(harness, str) or len(host) > 80 or len(harness) > 80:
        raise ValueError("invalid_route_host")
    if capability_id not in {None, CLI_CAPABILITY_ID}:
        raise ValueError("invalid_capability")
    if credential not in {None, "env", "saved"}:
        raise ValueError("invalid_credential")
    if workspace_match is not None and type(workspace_match) is not bool:
        raise ValueError("invalid_workspace_match")
    if body_sha256 is not None and (not isinstance(body_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", body_sha256)):
        raise ValueError("invalid_body_hash")
    if body_bytes is not None and (type(body_bytes) is not int or body_bytes < 0 or body_bytes > 65536):
        raise ValueError("invalid_body_bytes")
    if not isinstance(result_code, str) or not _RESULT_CODE.fullmatch(result_code):
        raise ValueError("invalid_result_code")
    if ntn_version is not None and (not isinstance(ntn_version, str) or not _VERSION.fullmatch(ntn_version)):
        raise ValueError("invalid_ntn_version")
    row = {
        "body_bytes": body_bytes,
        "body_sha256": body_sha256,
        "capability_id": capability_id,
        "credential": credential,
        "event": "notion_cli_read",
        "fallback_reason": fallback_reason,
        "harness": harness,
        "host": host,
        "ntn_version": ntn_version,
        "result_code": result_code,
        "route": "cli",
        "schema_verification": "static_pinned",
        "workspace_match": workspace_match,
    }
    _write_log_line(path, row)
    return True


def _write_log_line(path, row):
    line = json.dumps(row, ensure_ascii=True, sort_keys=True)
    if len(line) > 2000 or _UUID.search(line):
        raise ValueError("route_log_rejected")
    lowered = line.lower()
    if any(marker in lowered for marker in _LOG_MARKERS):
        raise ValueError("route_log_rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def validate_read_argv(argv):
    """Allow only whoami JSON or one markdown GET. Anything else is not a read."""

    if type(argv) is not list or not argv or any(type(item) is not str or item == "" for item in argv):
        raise CapabilityError("invalid_arguments")
    if argv == list(WHOAMI_ARGV):
        return
    if len(argv) != 5 or argv[0] != "ntn" or argv[1] != "api" or argv[3:] != ["-X", "GET"]:
        raise CapabilityError("write_unapproved")
    prefix = "v1/pages/"
    suffix = "/markdown"
    path = argv[2]
    if not path.startswith(prefix) or not path.endswith(suffix):
        raise CapabilityError("write_unapproved")
    page_id = path[len(prefix):-len(suffix)]
    if page_id != path.removeprefix(prefix).removesuffix(suffix) or not _PAGE_ID.fullmatch(page_id):
        raise CapabilityError("write_unapproved")
    if "/" in page_id or path.count("/") != 3:
        raise CapabilityError("write_unapproved")


def markdown_argv(page_id):
    if not isinstance(page_id, str) or not _PAGE_ID.fullmatch(page_id):
        raise CapabilityError("invalid_arguments")
    argv = ["ntn", "api", f"v1/pages/{page_id}/markdown", "-X", "GET"]
    validate_read_argv(argv)
    return argv


def cli_capability_card():
    """One static read card. Its schema hash is the digest of ``CLI_FETCH_SCHEMA``."""

    return {
        "id": CLI_CAPABILITY_ID,
        "server": CLI_SERVER,
        "operation": FETCH_OPERATION,
        "summary": "Read one Notion page by id through the pinned ntn CLI.",
        "writes": False,
        "schema_hash": schema_digest(CLI_FETCH_SCHEMA),
        "source": "static-cli-schema",
        "provenance": "Pinned ntn markdown GET. Verification is static_pinned.",
    }


def host_cli_manifest_document(base):
    """Derive a CLI-only host view from a verified MCP source manifest.

    MCP cards remain in the source release for provenance, but are not offered
    to Jev on a host where those MCP operations cannot execute.
    """

    if not isinstance(base, dict) or not isinstance(base.get("entries"), list):
        raise CapabilityError("invalid_manifest")
    if not any(isinstance(row, dict) and row.get("id") == MCP_FETCH_ID for row in base["entries"]):
        raise CapabilityError("invalid_manifest")
    return {
        "manifest_version": 1,
        "description": "Host CLI route derived from Notion MCP source; only executable cards are offered.",
        "entries": [cli_capability_card()],
    }


def _present(value):
    return isinstance(value, str) and value.strip() != ""


def _configured_pair(environ, config_path):
    file_source = file_workspace = None
    path = config_path if config_path is not None else environ.get(HOST_CONFIG_ENV)
    if path:
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            raise CapabilityError("unauthenticated") from None
        if not isinstance(loaded, dict) or set(loaded) != {"credential_source", "expected_workspace_id"}:
            raise CapabilityError("unauthenticated")
        file_source = loaded.get("credential_source")
        file_workspace = loaded.get("expected_workspace_id")
    return file_source, file_workspace


def resolve_cli_identity(*, environ=None, credential_source=None, expected_workspace_id=None, config_path=None):
    """Resolve one declared credential source and workspace. Mismatched inputs are refused."""

    environ = os.environ if environ is None else environ
    if not isinstance(environ, dict):
        raise CapabilityError("unauthenticated")
    file_source, file_workspace = _configured_pair(environ, config_path)
    sources = [item for item in (credential_source, file_source, environ.get(CREDENTIAL_ENV)) if item is not None]
    workspaces = [item for item in (expected_workspace_id, file_workspace, environ.get(WORKSPACE_ENV)) if item is not None]
    if not sources or not workspaces or len(set(sources)) != 1 or len(set(workspaces)) != 1:
        raise CapabilityError("unauthenticated" if not sources or len(set(sources)) != 1 else "identity_mismatch")
    source = sources[0]
    workspace = workspaces[0]
    if source not in CREDENTIAL_SOURCES:
        raise CapabilityError("unauthenticated")
    if source == "env" and not _present(environ.get(TOKEN_ENV)):
        raise CapabilityError("unauthenticated")
    if not isinstance(workspace, str) or not _PAGE_ID.fullmatch(workspace):
        raise CapabilityError("identity_mismatch")
    return source, workspace


def _require_declared_credential(credential_source, environ):
    if credential_source == "saved":
        return
    if credential_source == "env" and isinstance(environ, dict) and _present(environ.get(TOKEN_ENV)):
        return
    raise CapabilityError("unauthenticated")


def _child_env(credential_source, environ):
    _require_declared_credential(credential_source, environ)
    if credential_source == "env":
        # The declared token may come from the host-local launcher rather than
        # the parent process. Pass that exact value to ntn; never let it fall
        # back to an unrelated saved login or inherited token.
        return {**os.environ, **environ}
    return {key: value for key, value in {**os.environ, **environ}.items() if key != TOKEN_ENV}


def _safe_ntn_version(payload):
    if not isinstance(payload, dict):
        return None
    for key in ("ntn_version", "version"):
        value = payload.get(key)
        if isinstance(value, str) and _VERSION.fullmatch(value):
            return value
    return None


def default_runner(argv, *, credential_source, environ=None, executable_path=None):
    """Run an allowlisted argv. Stdin is closed so a GET cannot gain a body."""

    validate_read_argv(list(argv))
    environ = os.environ if environ is None else environ
    child = _child_env(credential_source, environ)
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "check": False,
        "shell": False,
        "timeout": 10,
    }
    kwargs["env"] = child
    command = [str(executable_path) if executable_path is not None else "ntn", *list(argv)[1:]]
    try:
        return subprocess.run(command, **kwargs)
    except FileNotFoundError:
        raise CapabilityError("describe_client_unconfigured") from None
    except subprocess.TimeoutExpired:
        raise CapabilityError("call_unavailable") from None


def _stdout(result):
    raw = getattr(result, "stdout", b"")
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes):
        raise CapabilityError("call_unavailable")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise CapabilityError("call_unavailable") from None
    try:
        payload = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        raise CapabilityError("call_unavailable") from None
    if payload is not None and not isinstance(payload, dict):
        raise CapabilityError("call_unavailable")
    return payload


def workspace_matches(payload, expected):
    if not isinstance(payload, dict) or payload.get("object") != "user" or payload.get("type") != "bot":
        return False
    bot = payload.get("bot")
    if not isinstance(bot, dict):
        return False
    found = bot.get("workspace_id")
    if not isinstance(found, str) or not isinstance(expected, str):
        return False
    if not _PAGE_ID.fullmatch(found) or not _PAGE_ID.fullmatch(expected):
        return False
    return found.lower() == expected.lower()


def _status_of(payload):
    if not isinstance(payload, dict):
        return None
    for key in ("status", "status_code"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def cli_probe_from_result(result, expected_workspace_id):
    returncode = getattr(result, "returncode", 1)
    if not isinstance(returncode, int) or isinstance(returncode, bool):
        return {"available": True, "authenticated": False, "identity_ok": False, "operation_ok": True}
    try:
        payload = _stdout(result)
    except CapabilityError:
        return {"available": True, "authenticated": False, "identity_ok": False, "operation_ok": True}
    if returncode != 0 or payload is None:
        return {"available": True, "authenticated": False, "identity_ok": False, "operation_ok": True}
    return {
        "available": True,
        "authenticated": True,
        "identity_ok": workspace_matches(payload, expected_workspace_id),
        "operation_ok": True,
    }


def parse_page_markdown(payload, page_id):
    """Keep the spec fields of one complete page. Refuse a partial or mismatched body."""

    if not isinstance(payload, dict) or not _MARKDOWN_KEYS <= set(payload):
        raise CapabilityError("call_unavailable")
    if payload.get("object") != "page_markdown":
        raise CapabilityError("call_unavailable")
    found = payload.get("id")
    if not isinstance(found, str) or not isinstance(page_id, str) or found.lower() != page_id.lower():
        raise CapabilityError("identity_mismatch")
    markdown = payload.get("markdown")
    truncated = payload.get("truncated")
    unknown = payload.get("unknown_block_ids")
    if not isinstance(markdown, str) or type(truncated) is not bool or not isinstance(unknown, list):
        raise CapabilityError("call_unavailable")
    if truncated or unknown:
        raise CapabilityError("call_unavailable")
    return {
        "object": "page_markdown",
        "id": page_id,
        "markdown": markdown,
        "truncated": False,
        "unknown_block_ids": [],
    }


class CliNotionTransport:
    """Read-only ntn client. list_tools returns the pinned schema and does not spawn ntn."""

    server_name = CLI_SERVER

    def __init__(self, *, runner=None, expected_workspace_id, credential_source, environ=None, executable_path=None):
        self._runner = runner or default_runner
        if credential_source not in CREDENTIAL_SOURCES:
            raise CapabilityError("unauthenticated")
        if not isinstance(expected_workspace_id, str) or not _PAGE_ID.fullmatch(expected_workspace_id):
            raise CapabilityError("identity_mismatch")
        self.credential_source = credential_source
        self._expected_workspace_id = expected_workspace_id
        self.environ = environ
        self.executable_path = executable_path
        self.workspace_match = None
        self.ntn_version = None

    def _environ(self):
        return os.environ if self.environ is None else self.environ

    def _run(self, argv):
        _require_declared_credential(self.credential_source, self._environ())
        validate_read_argv(list(argv))
        try:
            if self._runner is default_runner:
                result = default_runner(list(argv), credential_source=self.credential_source,
                                        environ=self._environ(), executable_path=self.executable_path)
            else:
                result = self._runner(list(argv))
        except CapabilityError:
            raise
        except Exception:
            raise CapabilityError("call_unavailable") from None
        if result is None:
            raise CapabilityError("call_unavailable")
        return result

    def probe(self):
        try:
            _require_declared_credential(self.credential_source, self._environ())
        except CapabilityError:
            return {"available": True, "authenticated": False, "identity_ok": False, "operation_ok": True}
        try:
            result = self._run(list(WHOAMI_ARGV))
        except CapabilityError as exc:
            if exc.code == "describe_client_unconfigured":
                return {"available": False, "authenticated": False, "identity_ok": False, "operation_ok": False}
            return {"available": True, "authenticated": False, "identity_ok": False, "operation_ok": True}
        try:
            payload = _stdout(result)
        except CapabilityError:
            payload = None
        self.ntn_version = _safe_ntn_version(payload)
        probed = cli_probe_from_result(result, self._expected_workspace_id)
        self.workspace_match = probed.get("identity_ok") is True
        return probed

    def list_tools(self, server):
        if server != self.server_name:
            raise CapabilityError("identity_mismatch")
        return {"tools": [{
            "name": FETCH_OPERATION,
            "inputSchema": CLI_FETCH_SCHEMA,
            "server": self.server_name,
        }]}

    def call_tool(self, server, operation, arguments):
        if server != self.server_name:
            raise CapabilityError("identity_mismatch")
        if operation != FETCH_OPERATION or writes_flag(operation, True):
            raise CapabilityError("write_unapproved")
        if type(arguments) is not dict or set(arguments) != {"id"} or not isinstance(arguments.get("id"), str):
            raise CapabilityError("invalid_arguments")
        page_id = arguments["id"]
        if not _PAGE_ID.fullmatch(page_id):
            raise CapabilityError("invalid_arguments")
        _require_declared_credential(self.credential_source, self._environ())
        probe = self.probe()
        if probe.get("identity_ok") is not True:
            raise CapabilityError(_denial_code(_denial(probe)))
        result = self._run(markdown_argv(page_id))
        returncode = getattr(result, "returncode", 1)
        try:
            payload = _stdout(result)
        except CapabilityError:
            raise
        if not isinstance(returncode, int) or isinstance(returncode, bool) or returncode != 0:
            raise CapabilityError("call_unavailable")
        if _status_of(payload) in _DENIED_STATUS:
            raise CapabilityError("call_unavailable")
        parsed = parse_page_markdown(payload, page_id)
        if len(json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")) > CALL_RESULT_MAX_BYTES:
            raise CapabilityError("result_budget_exceeded")
        raw = parsed["markdown"].encode("utf-8")
        self.body_sha256 = hashlib.sha256(raw).hexdigest()
        self.body_bytes = len(raw)
        return parsed


class DeniedTransport:
    """Registered bridge that refuses every call with one stable code."""

    def __init__(self, code):
        if not isinstance(code, str) or not code:
            code = "call_unavailable"
        self.code = code
        self.calls = 0

    def list_tools(self, server):
        self.calls += 1
        raise CapabilityError(self.code)

    def call_tool(self, server, operation, arguments):
        self.calls += 1
        raise CapabilityError(self.code)


def _absent_probe():
    return {"available": False, "authenticated": False, "identity_ok": False, "operation_ok": False}


def probe_cli(runner, *, credential_source, expected_workspace_id, environ=None, executable_path=None):
    try:
        return CliNotionTransport(
            runner=runner,
            credential_source=credential_source,
            expected_workspace_id=expected_workspace_id,
            environ=environ,
            executable_path=executable_path,
        ).probe()
    except CapabilityError:
        return {"available": True, "authenticated": False, "identity_ok": False, "operation_ok": True}


def _cli_probe(runner, environ, config_path, executable_path=None):
    try:
        source, workspace = resolve_cli_identity(environ=environ, config_path=config_path)
    except CapabilityError as exc:
        if exc.code == "identity_mismatch":
            return {"available": True, "authenticated": True, "identity_ok": False, "operation_ok": True}
        return {"available": True, "authenticated": False, "identity_ok": False, "operation_ok": True}
    return probe_cli(runner, credential_source=source, expected_workspace_id=workspace,
                     environ=environ, executable_path=executable_path)


def verify_ntn_executable(path, version):
    if version != PINNED_NTN_VERSION or path is None:
        raise CapabilityError("describe_client_unconfigured")
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise CapabilityError("describe_client_unconfigured")
    try:
        result = subprocess.run([str(path), "--version"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        raise CapabilityError("describe_client_unconfigured") from None
    if result.returncode != 0 or result.stdout.strip() != f"ntn {version}".encode():
        raise CapabilityError("stale_schema")
    return path


def mcp_installed_probe(installed):
    if installed is True:
        return {"available": True, "authenticated": True, "identity_ok": True, "operation_ok": True}
    return _absent_probe()


def read_cli_page(manifest, receipt, page_id, transport, *, approval=None, write_verifier=None, obs=None):
    """Read one page only when the receipt authorizes ``notion.cli.page_read``.

    The schema pin is compared to the static CLI schema before any ntn argv runs.
    A receipt for ``notion.mcp.fetch`` is ``unauthorized_capability``.
    """

    if obs is None:
        obs = {}
    credential = getattr(transport, "credential_source", None)
    obs.update({
        "capability_id": CLI_CAPABILITY_ID,
        "schema_verification": "static_pinned",
        "credential": credential if credential in CREDENTIAL_SOURCES else None,
        "workspace_match": None,
        "body_sha256": None,
        "body_bytes": None,
        "result_code": "unauthorized_capability",
        "ntn_version": None,
    })
    try:
        entry = _authorize(manifest, receipt, CLI_CAPABILITY_ID)
        if approval is not None or write_verifier is not None or entry.writes:
            raise CapabilityError("write_unapproved")
        if entry.server != CLI_SERVER or entry.operation != FETCH_OPERATION:
            raise CapabilityError("identity_mismatch")
        pinned = schema_digest(CLI_FETCH_SCHEMA)
        if len(pinned) != len(entry.schema_hash) or not hmac.compare_digest(pinned, entry.schema_hash):
            raise CapabilityError("stale_schema")
        if not isinstance(page_id, str) or not _PAGE_ID.fullmatch(page_id):
            raise CapabilityError("invalid_arguments")
        if credential not in CREDENTIAL_SOURCES:
            raise CapabilityError("unauthenticated")
        _require_declared_credential(credential, transport._environ() if hasattr(transport, "_environ") else os.environ)
        if getattr(transport, "server_name", None) != CLI_SERVER:
            raise CapabilityError("identity_mismatch")
        result = transport.call_tool(CLI_SERVER, FETCH_OPERATION, {"id": page_id})
        obs["workspace_match"] = getattr(transport, "workspace_match", None)
        obs["ntn_version"] = getattr(transport, "ntn_version", None)
        obs["body_sha256"] = getattr(transport, "body_sha256", None)
        obs["body_bytes"] = getattr(transport, "body_bytes", None)
        obs["result_code"] = "ok"
        return {
            "status": "called",
            "id": entry.id,
            "server": entry.server,
            "operation": entry.operation,
            "schema_verification": "static_pinned",
            "result": result,
        }
    except CapabilityError as exc:
        obs["result_code"] = exc.code
        obs["workspace_match"] = getattr(transport, "workspace_match", None)
        obs["ntn_version"] = getattr(transport, "ntn_version", None)
        raise


def prepare_notion_runtime(args, *, runner=None, mcp_factory=None, mcp_installed=None, environ=None):
    """Build the one bridge named by the host. No transport request returns ``(None, None)``."""

    environ = os.environ if environ is None else environ
    transport = getattr(args, "notion_transport", None)
    if transport is None:
        return None, None
    preferred = {"hermes": "mcp", "cli": "cli"}.get(transport)
    if preferred is None:
        raise ValueError("invalid_route")
    fallback = getattr(args, "notion_fallback", None)
    executable_path = None
    if preferred == "cli":
        executable_path = verify_ntn_executable(getattr(args, "notion_cli_path", None),
                                                 getattr(args, "notion_cli_version", None))
    reason = getattr(args, "notion_fallback_reason", None)
    if transport == "hermes" and fallback is None:
        factory = mcp_factory or (lambda: _hermes_transport(getattr(args, "notion_server", "notion")))
        return factory(), RouteDecision("mcp", None, True, None)
    if mcp_installed is None:
        mcp_installed = _hermes_installed()
    probes = {
        "mcp": mcp_installed_probe(mcp_installed is True),
        "cli": _absent_probe(),
    }
    config_path = getattr(args, "notion_host_config", None)
    # A ready preferred MCP route does not open the CLI, even when a fallback is named.
    if preferred == "cli" or (fallback == "cli" and not _ready(probes["mcp"])):
        if executable_path is None:
            executable_path = verify_ntn_executable(getattr(args, "notion_cli_path", None),
                                                     getattr(args, "notion_cli_version", None))
        probes["cli"] = _cli_probe(runner or default_runner, environ, config_path, executable_path)
    decision = resolve_host_route(
        preferred=preferred, probes=probes, fallback_route=fallback, fallback_reason=reason,
    )
    if not decision.executable:
        return DeniedTransport(decision.denial_code or "call_unavailable"), decision
    if decision.route == "mcp":
        factory = mcp_factory or (lambda: _hermes_transport(getattr(args, "notion_server", "notion")))
        return factory(), decision
    source, workspace = resolve_cli_identity(environ=environ, config_path=config_path)
    return CliNotionTransport(
        runner=runner or default_runner,
        credential_source=source,
        expected_workspace_id=workspace,
        environ=environ,
        executable_path=executable_path,
    ), decision


def _hermes_transport(server_name):
    from .notion_mcp_transport import HermesNotionTransport
    return HermesNotionTransport(server_name=server_name or "notion")


def _hermes_installed():
    from .notion_mcp_transport import hermes_root
    try:
        hermes_root()
    except CapabilityError:
        return False
    return True
