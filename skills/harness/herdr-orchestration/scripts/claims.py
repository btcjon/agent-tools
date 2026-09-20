#!/usr/bin/env python3
"""Advisory Herdr worker reservation CLI. Cooperative dispatch only.

POSIX/macOS/Linux MVP (fcntl file locks). Not a Windows runtime adapter.
Runtime state defaults to ~/.local/state/herdr-mode — keep it outside git.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from typing import Any


def emit(payload: dict, code: int) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    raise SystemExit(code)


def make_key(session: str, worker: str) -> str:
    return json.dumps([session, worker], separators=(",", ":"))


def acquire_lock(lock_path: str):
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fh = open(lock_path, "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return fh


def release_lock(fh) -> None:
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def validate_state(raw: Any) -> dict[str, str] | None:
    """Return a clean dict[str,str] or None to fail closed."""
    if not isinstance(raw, dict):
        return None
    clean: dict[str, str] = {}
    for key, owner in raw.items():
        if not isinstance(key, str) or key == "":
            return None
        if not isinstance(owner, str) or owner == "":
            return None
        clean[key] = owner
    return clean


def load_state(state_path: str) -> dict[str, str] | None:
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return None
    return validate_state(raw)


def save_state(state_path: str, state: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, separators=(",", ":"), sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, state_path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("action", choices=["claim", "status", "release"])
    parser.add_argument(
        "--state-dir",
        default=os.path.expanduser("~/.local/state/herdr-mode"),
    )
    parser.add_argument("--session", required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--owner", default=None)
    args = parser.parse_args(argv)

    session = args.session
    worker = args.worker
    if not isinstance(session, str) or not session.strip():
        emit({"ok": False, "error": "blank_session"}, 1)
    if not isinstance(worker, str) or not worker.strip():
        emit({"ok": False, "error": "blank_worker"}, 1)

    if args.action in ("claim", "release"):
        owner = args.owner
        if not isinstance(owner, str) or not owner.strip():
            emit({"ok": False, "error": "blank_owner"}, 1)
    else:
        owner = args.owner

    state_dir = args.state_dir
    lock_path = os.path.join(state_dir, "lock")
    state_path = os.path.join(state_dir, "state.json")
    key = make_key(session, worker)

    lock_fh = None
    try:
        lock_fh = acquire_lock(lock_path)
        state = load_state(state_path)
        if state is None:
            emit({"ok": False, "error": "corrupt_state"}, 2)

        current = state.get(key)

        if args.action == "claim":
            if current is None:
                state[key] = owner  # type: ignore[arg-type]
                save_state(state_path, state)
                emit({"ok": True, "action": "claim", "owner": owner, "created": True}, 0)
            if current == owner:
                emit(
                    {"ok": True, "action": "claim", "owner": owner, "created": False},
                    0,
                )
            emit(
                {
                    "ok": False,
                    "error": "conflict",
                    "current_owner": current,
                    "session": session,
                    "worker": worker,
                },
                3,
            )

        if args.action == "release":
            if current is None:
                emit({"ok": True, "action": "release", "released": False}, 0)
            if current == owner:
                del state[key]
                save_state(state_path, state)
                emit({"ok": True, "action": "release", "released": True}, 0)
            emit(
                {
                    "ok": False,
                    "error": "owner_mismatch",
                    "current_owner": current,
                },
                4,
            )

        # status
        if current is None:
            emit({"ok": True, "status": "available"}, 0)
        emit({"ok": True, "status": "claimed", "owner": current}, 0)
    except SystemExit:
        raise
    except Exception:
        emit({"ok": False, "error": "internal"}, 5)
    finally:
        if lock_fh is not None:
            release_lock(lock_fh)


if __name__ == "__main__":
    main()
