#!/usr/bin/env python3
"""Classify Herdr panes for post-shunt reload (list-only; never interrupts)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RELOAD_AGENTS = {"pi", "grok", "cursor", "codex", "claude", "agy", "hermes"}
ARTIFACTS = (
    Path.home() / ".pi/agent/extensions/shunt-gate.ts",
    Path.home() / ".grok/hooks/shunt-pretooluse.json",
    Path.home() / ".cursor/hooks.json",
    Path.home() / ".codex/hooks.json",
    Path.home() / ".claude/settings.json",
)


def newest_artifact_mtime() -> int | None:
    best: int | None = None
    for p in ARTIFACTS:
        try:
            mt = int(p.stat().st_mtime)
        except OSError:
            continue
        if best is None or mt > best:
            best = mt
    return best


def load_claims(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def claim_owner(claims: dict[str, str], session: str, pane_id: str) -> str | None:
    key = json.dumps([session, pane_id], separators=(",", ":"))
    return claims.get(key)


def classify(pane: dict, session: str, claims: dict[str, str], apply_hints: bool) -> dict:
    agent = (pane.get("agent") or "").lower() or "unknown"
    status = (pane.get("agent_status") or "unknown").lower()
    pane_id = pane.get("pane_id") or ""
    label = pane.get("label") or ""
    cwd = pane.get("cwd") or ""
    owner = claim_owner(claims, session, pane_id)

    needs = agent in RELOAD_AGENTS
    if not needs and agent == "unknown":
        needs = "/herdr-workers/" in cwd

    if status in {"working", "blocked"}:
        action, reason = "DO_NOT_INTERRUPT", f"agent_status={status}"
    elif owner:
        action, reason = "DO_NOT_INTERRUPT", f"foreign_or_active_claim owner={owner}"
    elif not needs:
        action, reason = "SKIP", f"agent={agent} not a shunt host surface"
    elif status in {"idle", "done", "unknown"}:
        action, reason = "RELOAD_CANDIDATE", f"status={status}; may predate shunt install"
    else:
        action, reason = "DO_NOT_INTERRUPT", f"status={status}"

    suggest = None
    if action == "RELOAD_CANDIDATE" and apply_hints:
        if agent == "pi":
            suggest = (
                f"# only if you own this pane: herdr --session {session} "
                f"agent start <name> --kind pi --pane {pane_id}"
            )
        elif agent == "grok":
            suggest = (
                f"# only if you own this pane: herdr --session {session} "
                f"agent start <name> --kind grok --pane {pane_id}"
            )
        elif agent in {"cursor", "codex", "claude"}:
            suggest = (
                f"# open a NEW {agent} session in pane {pane_id} "
                "(do not send-keys into foreign work)"
            )
        else:
            suggest = f"# restart {agent} session in pane {pane_id} after confirming ownership"

    return {
        "pane_id": pane_id,
        "label": label,
        "agent": agent,
        "agent_status": status,
        "cwd": cwd,
        "claim_owner": owner,
        "action": action,
        "reason": reason,
        "suggested_command": suggest,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--apply-hints",
        action="store_true",
        help="Print suggested restart commands (still does not execute them)",
    )
    p.add_argument("--session", default=os.environ.get("HERDR_SESSION", "launch"))
    p.add_argument(
        "--claims",
        default=os.environ.get(
            "HERDR_CLAIMS_STATE",
            str(Path.home() / ".local/state/herdr-mode/state.json"),
        ),
    )
    args = p.parse_args(argv)

    try:
        proc = subprocess.run(
            ["herdr", "--session", args.session, "pane", "list"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("herdr not on PATH", file=sys.stderr)
        return 1
    if not proc.stdout.strip():
        print(f"herdr pane list empty (session={args.session}): {proc.stderr.strip()}", file=sys.stderr)
        return 1
    doc = json.loads(proc.stdout)
    panes = (doc.get("result") or {}).get("panes") or []
    claims = load_claims(Path(args.claims))
    rows = [classify(pane, args.session, claims, args.apply_hints) for pane in panes]

    mt = newest_artifact_mtime()
    install_iso = (
        datetime.fromtimestamp(mt, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if mt else None
    )
    out = {
        "mode": "dry-run+hints" if args.apply_hints else "dry-run",
        "interrupts": False,
        "session": args.session,
        "newest_shunt_artifact_mtime": install_iso,
        "counts": {
            "reload_candidate": sum(1 for r in rows if r["action"] == "RELOAD_CANDIDATE"),
            "do_not_interrupt": sum(1 for r in rows if r["action"] == "DO_NOT_INTERRUPT"),
            "skip": sum(1 for r in rows if r["action"] == "SKIP"),
        },
        "panes": rows,
    }

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"herdr-shunt-reload-hint  mode={out['mode']}  interrupts=false  session={args.session}")
    print(f"newest_shunt_artifact_mtime={install_iso}")
    c = out["counts"]
    print(
        f"counts: reload_candidate={c['reload_candidate']} "
        f"do_not_interrupt={c['do_not_interrupt']} skip={c['skip']}"
    )
    print("")
    print(f"{'ACTION':<18} {'PANE':<8} {'AGENT':<8} {'STATUS':<10} LABEL / REASON")
    for r in rows:
        if r["action"] == "SKIP":
            continue
        lab = r["label"] or "-"
        print(
            f"{r['action']:<18} {r['pane_id']:<8} {r['agent']:<8} {r['agent_status']:<10} "
            f"{lab} | {r['reason']}"
        )
        if r.get("suggested_command"):
            print(f"  hint: {r['suggested_command']}")
    print("")
    print("No panes were restarted. See docs/RELOAD-POLICY.md for exact reload commands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
