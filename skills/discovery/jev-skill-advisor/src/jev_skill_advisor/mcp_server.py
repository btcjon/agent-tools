"""Optional official-SDK stdio MCP transport."""
from __future__ import annotations
import argparse
from pathlib import Path
from .profile import load_profile
from .service import SkillAdvisorService
from .release import ReleaseStore


def build_server(config: Path | None = None, *, release_root: Path | None = None,
                 host: str | None = None, harness: str | None = None):
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
    except ImportError as exc:
        raise RuntimeError("install jev-skill-advisor[mcp]") from exc
    ArgModelBase.model_config["extra"] = "forbid"
    if (config is None) == (release_root is None):
        raise ValueError("choose_config_or_release_root")
    static_service = SkillAdvisorService(load_profile(config)) if config else None
    store = ReleaseStore(release_root) if release_root else None

    def service_for(session_id):
        if static_service is not None:
            return static_service
        if not host or not harness:
            raise ValueError("release_resolution_requires_host_and_harness")
        _, _, profile = store.resolve_profile(host=host, harness=harness, session_id=session_id)
        return SkillAdvisorService(profile)
    server = MCPServer("jev-skill-advisor")

    @server.tool()
    def skill_suggest(protocol_version: object, request_id: str, session_id: str, task: str,
                      context: str = "", explicit_skills: list[str] | None = None,
                      available_ids: list[str] | None = None) -> dict:
        value = {"protocol_version": protocol_version, "request_id": request_id, "session_id": session_id,
                 "task": task, "context": context, "explicit_skills": explicit_skills or []}
        if available_ids is not None:
            value["available_ids"] = available_ids
        return service_for(session_id).suggest(value)

    @server.tool()
    def skill_read(protocol_version: object, session_id: str, receipt_id: str, skill_id: str,
                   expected_content_hash: str) -> dict:
        value = {"protocol_version": protocol_version, "session_id": session_id,
                             "receipt_id": receipt_id, "skill_id": skill_id,
                             "expected_content_hash": expected_content_hash}
        return service_for(session_id).read(value)

    @server.tool()
    def skill_report_outcome(protocol_version: object, session_id: str, receipt_id: str, event_id: str,
                             skill_id: str, outcome: str, evidence: str,
                             reason_code: str | None = None) -> dict:
        value = {"protocol_version": protocol_version, "session_id": session_id,
            "receipt_id": receipt_id, "event_id": event_id, "skill_id": skill_id,
            "outcome": outcome, "evidence": evidence, "reason_code": reason_code}
        return service_for(session_id).report_outcome(value)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-advisor-mcp")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--release-root", type=Path)
    parser.add_argument("--host"); parser.add_argument("--harness")
    args = parser.parse_args(argv)
    build_server(args.config, release_root=args.release_root, host=args.host, harness=args.harness).run(transport="stdio")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
