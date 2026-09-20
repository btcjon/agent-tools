"""Optional official-SDK stdio MCP transport."""
from __future__ import annotations
import argparse
from pathlib import Path
from .profile import load_profile
from .service import SkillAdvisorService


def build_server(config: Path):
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
    except ImportError as exc:
        raise RuntimeError("install jev-skill-advisor[mcp]") from exc
    ArgModelBase.model_config["extra"] = "forbid"
    service = SkillAdvisorService(load_profile(config))
    server = MCPServer("jev-skill-advisor")

    @server.tool()
    def skill_suggest(protocol_version: object, request_id: str, session_id: str, task: str,
                      context: str = "", explicit_skills: list[str] | None = None,
                      available_ids: list[str] | None = None) -> dict:
        value = {"protocol_version": protocol_version, "request_id": request_id, "session_id": session_id,
                 "task": task, "context": context, "explicit_skills": explicit_skills or []}
        if available_ids is not None:
            value["available_ids"] = available_ids
        return service.suggest(value)

    @server.tool()
    def skill_read(protocol_version: object, session_id: str, receipt_id: str, skill_id: str,
                   expected_content_hash: str) -> dict:
        value = {"protocol_version": protocol_version, "session_id": session_id,
                             "receipt_id": receipt_id, "skill_id": skill_id,
                             "expected_content_hash": expected_content_hash}
        return service.read(value)

    @server.tool()
    def skill_report_outcome(protocol_version: object, session_id: str, receipt_id: str, event_id: str,
                             skill_id: str, outcome: str, evidence: str,
                             reason_code: str | None = None) -> dict:
        value = {"protocol_version": protocol_version, "session_id": session_id,
            "receipt_id": receipt_id, "event_id": event_id, "skill_id": skill_id,
            "outcome": outcome, "evidence": evidence, "reason_code": reason_code}
        return service.report_outcome(value)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-advisor-mcp")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    build_server(args.config).run(transport="stdio")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
