"""Optional official-SDK stdio MCP transport."""
from __future__ import annotations
import argparse
import hashlib
import time
from pathlib import Path
from typing import Literal
from .profile import load_profile
from .service import SkillAdvisorService
from .release import ReleaseError, ReleaseStore
from . import capability_choice, capability_core, capability_observability, notion_host_transport, notion_mcp_transport


class _TelemetryClosed(Exception):
    """Telemetry was requested and did not record an allowlisted event."""


class _ReleaseDenied(Exception):
    """Release binding failed. The exception carries no path or cause."""


def _bind_capabilities(decision):
    ids = list(decision["ids"])
    manifest_hash = decision["manifest_hash"]
    evidence = decision["evidence"]

    def update(receipt):
        receipt["capability_ids"] = ids
        receipt["capability_manifest_hash"] = manifest_hash
        receipt["capability_selection"] = evidence
        return None
    return update


def _resolved_harness(service, harness):
    if isinstance(harness, str) and harness:
        return harness
    profile_harness = getattr(service.profile, "harness", None)
    return profile_harness if isinstance(profile_harness, str) else None


def _emit(path, **fields):
    if path is None:
        return
    try:
        wrote = capability_observability.append_capability_event(path, **fields)
    except (TypeError, ValueError, OSError):
        raise _TelemetryClosed() from None
    if not wrote:
        raise _TelemetryClosed()


def _selection_fields(decision, manifest):
    status = decision.get("status") if isinstance(decision, dict) else None
    reason = decision.get("reason") if isinstance(decision, dict) else None
    ids = list(decision.get("ids") or []) if isinstance(decision, dict) else []
    if status not in capability_observability.SELECTION_STATUSES or reason not in capability_observability.SELECTION_REASONS:
        status = "fail_open"
        reason = "capability_choice_failed"
        ids = []
    if status != "selected":
        ids = []
    manifest_hash = decision.get("manifest_hash") if isinstance(decision, dict) else None
    if not isinstance(manifest_hash, str):
        manifest_hash = getattr(manifest, "content_hash", None)
    fields = {
        "stage": "discovery",
        "outcome": "selected" if status == "selected" else "absent",
        "capability_ids": ids,
        "context_bytes": 0,
        "manifest_hash": manifest_hash,
        "status": status,
        "reason": reason,
    }
    failure_class = decision.get("failure_class") if isinstance(decision, dict) else None
    if failure_class in capability_observability.FAILURE_CLASSES:
        fields["failure_class"] = failure_class
    return fields


def _fetch_capability_id(manifest, route=None):
    if route == "cli":
        entry = manifest.entries.get(notion_host_transport.CLI_CAPABILITY_ID)
        if (entry is None or entry.writes or entry.operation != "notion-fetch"
                or entry.server != notion_host_transport.CLI_SERVER):
            return None
        return entry.id
    matches = [
        entry.id for entry in manifest.entries.values()
        if entry.operation == "notion-fetch" and entry.writes is False
    ]
    return matches[0] if len(matches) == 1 else None


def _stored_capability_receipt(service, session_id, receipt_id):
    try:
        stored = service.runtime.load_receipt(receipt_id)
    except (FileNotFoundError, ValueError):
        return None
    if stored.get("session_id") != session_id or stored.get("profile_id") != service.profile.profile_id:
        return None
    return {
        "authorized_ids": list(stored.get("capability_ids") or []),
        "manifest_hash": stored.get("capability_manifest_hash"),
        "expires_at": stored.get("expires_at"),
    }


def build_server(config: Path | None = None, *, release_root: Path | None = None,
                 host: str | None = None, harness: str | None = None,
                 capability_manifest: Path | None = None, list_tools=None,
                 capability_evaluator=None, notion_bridge=None,
                 capability_events: Path | None = None, notion_route=None,
                 route_log: Path | None = None, expected_release_id: str | None = None):
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
    except ImportError as exc:
        raise RuntimeError("install jev-skill-advisor[mcp]") from exc
    ArgModelBase.model_config["extra"] = "forbid"
    if (config is None) == (release_root is None):
        raise ValueError("choose_config_or_release_root")
    if release_root is not None and capability_manifest is not None:
        raise ValueError("release_root_rejects_static_manifest")
    static_service = SkillAdvisorService(load_profile(config)) if config else None
    store = ReleaseStore(release_root) if release_root else None
    if expected_release_id is not None:
        if store is None or store.current() != expected_release_id:
            raise ReleaseError("current_release_changed")
    static_manifest = capability_core.load_manifest(capability_manifest) if capability_manifest else None
    # release_id -> (content_hash, manifest). Served only after a fresh hash check.
    manifest_cache: dict[str, tuple[str, object]] = {}

    def bound_manifest(release_id, release_manifest):
        files = release_manifest.get("files") if isinstance(release_manifest, dict) else None
        item = files.get("capability_manifest") if isinstance(files, dict) else None
        loaded = store.load_bound_capability_manifest(release_id)
        if not isinstance(item, dict):
            if loaded is not None:
                raise ReleaseError("capability_manifest_invalid")
            manifest_cache.pop(release_id, None)
            return None
        expected = item.get("content_hash")
        recorded = item.get("sha256")
        path_text = item.get("path")
        if loaded is None or not isinstance(expected, str) or loaded.content_hash != expected:
            raise ReleaseError("capability_manifest_invalid")
        if not isinstance(path_text, str) or not isinstance(recorded, str):
            raise ReleaseError("capability_manifest_invalid")
        path = Path(path_text)
        if path.is_symlink() or not path.is_file():
            raise ReleaseError("capability_manifest_missing")
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            raise ReleaseError("capability_manifest_invalid") from None
        if digest != recorded or loaded.content_hash != expected:
            raise ReleaseError("capability_manifest_invalid")
        cached = manifest_cache.get(release_id)
        if cached is not None and cached[0] == expected and getattr(cached[1], "content_hash", None) == expected:
            return cached[1]
        manifest_cache[release_id] = (loaded.content_hash, loaded)
        return loaded

    def binding_for(session_id):
        if static_service is not None:
            return static_service, static_manifest
        try:
            if not host or not harness:
                raise ValueError("release_resolution_requires_host_and_harness")
            if expected_release_id is not None and store._current_identity() != expected_release_id:
                raise ReleaseError("current_release_changed")
            release_id, release_manifest, profile = store.resolve_profile(
                host=host, harness=harness, session_id=session_id,
            )
            if expected_release_id is not None and release_id != expected_release_id:
                raise ReleaseError("current_release_changed")
            return SkillAdvisorService(profile), bound_manifest(release_id, release_manifest)
        except (ReleaseError, OSError, ValueError):
            raise _ReleaseDenied() from None

    def service_for(session_id):
        service, _bound = binding_for(session_id)
        return service

    def release_denial():
        """Stable denial. No path, exception text, skill body, or id."""
        started = time.perf_counter()
        try:
            _emit(
                capability_events,
                harness=harness if isinstance(harness, str) else "",
                host=host,
                stage="fallback",
                outcome="failed",
                latency_ms=(time.perf_counter() - started) * 1000,
                capability_ids=[],
                fallback_reason="other",
            )
        except _TelemetryClosed:
            pass
        return {"status": "denied", "reason": "release_unavailable"}
    server = MCPServer("jev-skill-advisor")

    def evaluator_for(service):
        if capability_evaluator is not None:
            return capability_evaluator
        if service.profile.provider_enabled:
            return service.runtime.evaluator
        return None

    @server.tool()
    def skill_suggest(protocol_version: object, request_id: str, session_id: str, task: str,
                      context: str = "", explicit_skills: list[str] | None = None,
                      available_ids: list[str] | None = None) -> dict:
        value = {"protocol_version": protocol_version, "request_id": request_id, "session_id": session_id,
                 "task": task, "context": context, "explicit_skills": explicit_skills or []}
        if available_ids is not None:
            value["available_ids"] = available_ids
        try:
            service, manifest = binding_for(session_id)
        except _ReleaseDenied:
            return release_denial()
        response = service.suggest(value)
        if manifest is None or response.get("status") not in {"suggested", "explicit_selection", "complete"}:
            return response
        if not response.get("selected"):
            return response
        response = dict(response)
        started = time.perf_counter()
        choice_failed = False
        try:
            decision = capability_choice.resolve_capabilities(
                manifest, task, context=context, selected_skills=response.get("selected") or [],
                evaluator=evaluator_for(service),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, capability_core.CapabilityError):
            choice_failed = True
            decision = {
                "status": "fail_open", "reason": "capability_choice_failed", "ids": [],
                "manifest_hash": manifest.content_hash, "cards": [],
            }
        try:
            _emit(
                capability_events, harness=_resolved_harness(service, harness), host=host,
                latency_ms=(time.perf_counter() - started) * 1000,
                receipt_id=response.get("receipt_id"), session_id=session_id,
                **_selection_fields(decision, manifest),
            )
        except _TelemetryClosed:
            response["capabilities"] = []
            return response
        if choice_failed:
            response["capabilities"] = []
            return response
        try:
            response["capabilities"] = decision["cards"]
            service.runtime.update_receipt(response["receipt_id"], _bind_capabilities(decision))
        except (FileNotFoundError, KeyError, TypeError, ValueError, capability_core.CapabilityError):
            response["capabilities"] = []
        return response

    @server.tool()
    def skill_read(protocol_version: object, session_id: str, receipt_id: str, skill_id: str,
                   expected_content_hash: str) -> dict:
        value = {"protocol_version": protocol_version, "session_id": session_id,
                             "receipt_id": receipt_id, "skill_id": skill_id,
                             "expected_content_hash": expected_content_hash}
        try:
            service = service_for(session_id)
        except _ReleaseDenied:
            return release_denial()
        return service.read(value)

    @server.tool()
    def skill_report_outcome(protocol_version: object, session_id: str, receipt_id: str, event_id: str,
                             skill_id: str, outcome: str, evidence: str,
                             reason_code: str | None = None) -> dict:
        value = {"protocol_version": protocol_version, "session_id": session_id,
            "receipt_id": receipt_id, "event_id": event_id, "skill_id": skill_id,
            "outcome": outcome, "evidence": evidence, "reason_code": reason_code}
        try:
            service = service_for(session_id)
        except _ReleaseDenied:
            return release_denial()
        return service.report_outcome(value)

    if static_manifest is not None or store is not None:
        @server.tool()
        def capability_describe(protocol_version: Literal[1], session_id: str, receipt_id: str, capability_id: str) -> dict:
            if protocol_version != 1 or isinstance(protocol_version, bool):
                raise ValueError("invalid_protocol_version")
            if not all(isinstance(item, str) for item in (session_id, receipt_id, capability_id)):
                raise ValueError("invalid_capability_request")
            try:
                service, manifest = binding_for(session_id)
            except _ReleaseDenied:
                return release_denial()
            if store is not None and manifest is None:
                return {"status": "denied", "reason": "capability_unbound"}
            receipt = _stored_capability_receipt(service, session_id, receipt_id)
            if receipt is None:
                return {"status": "denied", "reason": "unauthorized_capability"}
            if list_tools is None:
                return {"status": "denied", "reason": "describe_client_unconfigured"}
            try:
                return capability_core.capability_describe(manifest, receipt, capability_id, list_tools)
            except capability_core.CapabilityError as exc:
                return {"status": "denied", "reason": exc.code}

    if notion_bridge is not None and (static_manifest is not None or store is not None):
        @server.tool(name="notion-fetch", description="Read one Notion page by id when the stored receipt authorizes notion-fetch.")
        def notion_fetch(protocol_version: Literal[1], session_id: str, receipt_id: str, page_id: str) -> dict:
            if protocol_version != 1 or isinstance(protocol_version, bool):
                raise ValueError("invalid_protocol_version")
            if not all(isinstance(item, str) for item in (session_id, receipt_id, page_id)):
                raise ValueError("invalid_capability_request")
            try:
                service, manifest = binding_for(session_id)
            except _ReleaseDenied:
                return release_denial()
            if route_log is not None and notion_route is not None:
                try:
                    wrote = notion_host_transport.append_route_record(
                        route_log,
                        host=host if isinstance(host, str) else "",
                        harness=harness if isinstance(harness, str) else "",
                        route=notion_route.route,
                        fallback_reason=notion_route.fallback_reason,
                        executable=notion_route.executable,
                    )
                except (TypeError, ValueError, OSError):
                    return {"status": "denied", "reason": "telemetry_failed"}
                if not wrote:
                    return {"status": "denied", "reason": "telemetry_failed"}
            if notion_route is not None and not notion_route.executable:
                return {"status": "denied", "reason": notion_route.denial_code or "call_unavailable"}
            if store is not None and manifest is None:
                return {"status": "denied", "reason": "capability_unbound"}
            receipt = _stored_capability_receipt(service, session_id, receipt_id)
            route_name = notion_route.route if notion_route is not None else None
            fetch_id = _fetch_capability_id(manifest, route_name)
            obs = {
                "capability_id": fetch_id if route_name == "cli" else None,
                "credential": getattr(notion_bridge, "credential_source", None),
                "workspace_match": None,
                "body_sha256": None,
                "body_bytes": None,
                "result_code": "unauthorized_capability",
                "ntn_version": None,
            }
            started = time.perf_counter()
            reason_code = "unauthorized_capability"
            try:
                if fetch_id is None:
                    reason_code = "missing_tool"
                    obs["result_code"] = "missing_tool"
                    raise capability_core.CapabilityError("missing_tool")
                if receipt is None:
                    obs["result_code"] = "unauthorized_capability"
                    raise capability_core.CapabilityError("unauthorized_capability")
                if route_name != "cli" and list_tools is None:
                    reason_code = "describe_client_unconfigured"
                    raise capability_core.CapabilityError("describe_client_unconfigured")
                if route_name == "cli":
                    outcome = notion_host_transport.read_cli_page(
                        manifest, receipt, page_id, notion_bridge, obs=obs,
                    )
                else:
                    outcome = notion_mcp_transport.read_bridge_call(
                        manifest, receipt, page_id, list_tools=list_tools, call_tool=notion_bridge,
                    )
                succeeded = True
            except capability_core.CapabilityError as exc:
                succeeded = False
                reason_code = exc.code if isinstance(exc.code, str) else "other"
                obs["result_code"] = reason_code
                outcome = {"status": "denied", "reason": exc.code}
            latency_ms = (time.perf_counter() - started) * 1000
            try:
                _emit(
                    capability_events, harness=_resolved_harness(service, harness), host=host,
                    stage="invoke", outcome="success" if succeeded else "failed",
                    latency_ms=latency_ms, capability_ids=[fetch_id] if fetch_id else None,
                    receipt_id=receipt_id, session_id=session_id,
                    manifest_hash=manifest.content_hash,
                    status="success" if succeeded else "denied",
                    reason="bridge_read" if succeeded else capability_observability.fallback_reason_for_code(reason_code),
                )
            except _TelemetryClosed:
                return {"status": "denied", "reason": "telemetry_failed"}
            if route_name == "cli" and route_log is not None:
                try:
                    wrote = notion_host_transport.append_cli_read_record(
                        route_log,
                        host=host if isinstance(host, str) else "",
                        harness=harness if isinstance(harness, str) else "",
                        fallback_reason=notion_route.fallback_reason if notion_route is not None else None,
                        capability_id=obs.get("capability_id"),
                        credential=obs.get("credential") if obs.get("credential") in {"env", "saved"} else None,
                        workspace_match=obs.get("workspace_match"),
                        body_sha256=obs.get("body_sha256"),
                        body_bytes=obs.get("body_bytes"),
                        result_code=obs.get("result_code") or reason_code,
                        ntn_version=obs.get("ntn_version"),
                    )
                except (TypeError, ValueError, OSError):
                    return {"status": "denied", "reason": "telemetry_failed"}
                if not wrote:
                    return {"status": "denied", "reason": "telemetry_failed"}
            return outcome
    # capability_call stays a harness function. Do not register it as a model tool.
    return server


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="skill-advisor-mcp")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--release-root", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--harness")
    parser.add_argument("--capability-manifest", type=Path)
    parser.add_argument("--capability-events", type=Path)
    parser.add_argument("--notion-transport", choices=("hermes", "cli"))
    parser.add_argument("--notion-server", default="notion")
    parser.add_argument("--notion-fallback", choices=("mcp", "cli"))
    parser.add_argument(
        "--notion-fallback-reason",
        choices=tuple(sorted(notion_host_transport.FALLBACK_REASONS)),
    )
    parser.add_argument("--notion-host-config", type=Path)
    parser.add_argument("--route-log", type=Path)
    parser.add_argument("--expected-release")
    parser.add_argument("--notion-cli-path", type=Path)
    parser.add_argument("--notion-cli-version")
    args = parser.parse_args(argv)
    if args.release_root is not None and args.capability_manifest is not None:
        parser.error("--capability-manifest cannot be combined with --release-root")
    if args.notion_transport and args.capability_manifest is None and args.release_root is None:
        parser.error("--notion-transport requires --capability-manifest")
    if (args.notion_fallback is None) != (args.notion_fallback_reason is None):
        parser.error("--notion-fallback and --notion-fallback-reason are both required")
    preferred = {"hermes": "mcp", "cli": "cli"}.get(args.notion_transport)
    if args.notion_fallback is not None and args.notion_transport is None:
        parser.error("--notion-fallback requires --notion-transport")
    if args.notion_fallback is not None and args.notion_fallback == preferred:
        parser.error("--notion-fallback must name the other route")
    if args.expected_release is not None and args.release_root is None:
        parser.error("--expected-release requires --release-root")
    if (args.notion_cli_path is None) != (args.notion_cli_version is None):
        parser.error("--notion-cli-path and --notion-cli-version are both required")
    if args.notion_transport == "cli" and args.notion_cli_path is None and args.release_root is not None:
        parser.error("release CLI route requires a pinned ntn executable and version")
    return args


def main(argv=None):
    args = parse_args(argv)
    bridge, decision = notion_host_transport.prepare_notion_runtime(args)
    callable_bridge = bridge if decision is not None and decision.executable else None
    registered_bridge = bridge if decision is not None else None
    build_server(
        args.config, release_root=args.release_root, host=args.host, harness=args.harness,
        capability_manifest=args.capability_manifest, list_tools=callable_bridge,
        notion_bridge=registered_bridge, capability_events=args.capability_events,
        notion_route=decision, route_log=args.route_log,
        expected_release_id=args.expected_release,
    ).run(transport="stdio")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
