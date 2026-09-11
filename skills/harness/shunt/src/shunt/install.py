"""Idempotent harness hook install / uninstall / capability reporting."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SHUNT_MARKER = "skills/harness/shunt"
STATE_DIR_NAME = ".shunt"
BACKUP_SUBDIR = "backups"
STATE_FILE = "install-state.json"

# Never treat these adapter filenames as hook commands.
_SKIP_NAMES = {
    "readme.md",
    "registration.json",
    "install.sh",
    "hooks.snippet.json",
    "hooks.snippet.yaml",
    "hooks.json",  # grok template / not a cursor-merge script
    ".ds_store",
    ".gitkeep",
}

# Explicit gate script name patterns (used when registration.json is absent).
_GATE_NAME_RE = re.compile(
    r"(pre_tool_use|pretooluse|before_shell|pre_tool_call|shunt-pretool|shunt_gate|gate\.)",
    re.IGNORECASE,
)

# Wrong/legacy paths previously written by shunt install — cleaned on uninstall.
_OBSOLETE_HOOK_PATHS = (
    ".agy/hooks.json",
    ".pi/agent/hooks.json",
    ".hermes/shunt-hooks.json",
    ".grok/hooks.json",  # noise; authoritative WP4 path is .grok/hooks/shunt-pretooluse.json
)


@dataclass
class HarnessSpec:
    name: str
    config_rel: str  # under $HOME
    style: str  # "cursor" | "claude" | "external"
    # For claude settings.json, hooks live under top-level "hooks".
    hooks_key: str = "hooks"
    # Human hint for external (WP4) installers.
    external_hint: str = ""


@dataclass
class PlanItem:
    harness: str
    config_path: Path
    action: str  # skip | merge | remove | create
    detail: str
    adapter_scripts: list[Path] = field(default_factory=list)
    registration: Path | None = None


def package_root() -> Path:
    # src/shunt/install.py -> package root (…/harness/shunt)
    return Path(__file__).resolve().parents[2]


def adapters_root() -> Path:
    return package_root() / "adapters"


def state_dir(home: Path | None = None) -> Path:
    h = home or Path.home()
    return h / STATE_DIR_NAME


def backup_dir(home: Path | None = None) -> Path:
    return state_dir(home) / BACKUP_SUBDIR


def state_path(home: Path | None = None) -> Path:
    return state_dir(home) / STATE_FILE


HARNESSES: list[HarnessSpec] = [
    HarnessSpec("cursor", ".cursor/hooks.json", "cursor"),
    HarnessSpec("codex", ".codex/hooks.json", "cursor"),
    HarnessSpec("claude", ".claude/settings.json", "claude"),
    # WP4 installers own these; do not invent Cursor-shaped hooks.json entries.
    HarnessSpec(
        "pi",
        ".pi/agent/extensions/shunt-gate.ts",
        "external",
        external_hint="adapters/pi/install.sh → ~/.pi/agent/extensions/shunt-gate.ts",
    ),
    HarnessSpec(
        "hermes",
        ".hermes/config.yaml",
        "external",
        external_hint="adapters/hermes/install.sh → hooks.pre_tool_call in ~/.hermes/config.yaml",
    ),
    HarnessSpec(
        "grok",
        ".grok/hooks/shunt-pretooluse.json",
        "external",
        external_hint="adapters/grok/install.sh → ~/.grok/hooks/shunt-pretooluse.json",
    ),
    HarnessSpec(
        "agy",
        ".gemini/config/hooks.json",
        "external",
        external_hint="adapters/agy/install.sh → ~/.gemini/config/hooks.json (group shunt)",
    ),
]


def expand_home(rel: str, home: Path | None = None) -> Path:
    h = home or Path.home()
    if rel.startswith("~/"):
        return h / rel[2:]
    return Path(rel).expanduser() if str(rel).startswith("~") else h / rel


def adapter_dir(harness: str) -> Path:
    return adapters_root() / harness


def _is_skipped_name(name: str) -> bool:
    n = name.lower()
    if n in _SKIP_NAMES:
        return True
    if n.endswith(".md"):
        return True
    if n.startswith("install.") or n == "install":
        return True
    if n.startswith("test_") or n.endswith("_test.py") or n == "tests":
        return True
    return False


def _is_gate_script(name: str) -> bool:
    if _is_skipped_name(name):
        return False
    return bool(_GATE_NAME_RE.search(name))


def list_adapter_scripts(harness: str) -> list[Path]:
    """Return hook-eligible scripts (never install.sh / README / tests)."""
    d = adapter_dir(harness)
    if not d.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        if _is_skipped_name(p.name):
            continue
        if p.suffix.lower() not in {".sh", ".mjs", ".js", ".py", ".bash", ".ts"}:
            continue
        # TypeScript extensions are not registered via hooks.json merge.
        if p.suffix.lower() == ".ts":
            continue
        out.append(p)
    return out


def list_gate_scripts(harness: str) -> list[Path]:
    """Scripts allowed for default registration when registration.json is absent."""
    return [p for p in list_adapter_scripts(harness) if _is_gate_script(p.name)]


def registration_path(harness: str) -> Path | None:
    p = adapter_dir(harness) / "registration.json"
    return p if p.is_file() else None


def is_shunt_command(command: str, root: Path | None = None) -> bool:
    if not command:
        return False
    c = command.replace("\\", "/")
    if SHUNT_MARKER in c:
        return True
    root = root or package_root()
    root_s = str(root).replace("\\", "/")
    return root_s in c or f"{SHUNT_MARKER}/adapters/" in c


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def backup_config(path: Path, harness: str, home: Path | None = None) -> Path | None:
    if not path.is_file():
        return None
    dest_dir = backup_dir(home) / harness
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = dest_dir / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, dest)
    return dest


def load_state(home: Path | None = None) -> dict[str, Any]:
    p = state_path(home)
    if not p.is_file():
        return {"version": 1, "harnesses": {}}
    return _load_json(p)


def save_state(state: dict[str, Any], home: Path | None = None) -> None:
    _dump_json(state_path(home), state)


def _default_registration(harness: str, scripts: list[Path]) -> dict[str, Any]:
    """Build a minimal registration when scripts exist but no registration.json."""
    events: dict[str, list[Any]] = {}
    if harness == "claude":
        event = "PreToolUse"
        entries = []
        for s in scripts:
            entries.append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(s.resolve()),
                            "timeout": 30,
                        }
                    ]
                }
            )
        events[event] = entries
    else:
        event = "preToolUse"
        entries = [{"command": str(s.resolve())} for s in scripts]
        events[event] = entries
    return {"format": "hooks-v1", "events": events}


def load_registration(harness: str, scripts: list[Path]) -> dict[str, Any] | None:
    """Prefer registration.json only. Otherwise only explicit gate scripts."""
    reg = registration_path(harness)
    if reg is not None:
        data = _load_json(reg)
        # Expand using all non-skipped scripts so {{SCRIPT:…}} resolves.
        expand_scripts = list_adapter_scripts(harness)
    else:
        gate = list_gate_scripts(harness) if not scripts else [s for s in scripts if _is_gate_script(s.name)]
        if not gate:
            return None
        data = _default_registration(harness, gate)
        expand_scripts = gate

    adapter = adapter_dir(harness)
    text = json.dumps(data)
    text = text.replace("$ADAPTER_DIR", str(adapter.resolve()))
    text = text.replace("${ADAPTER_DIR}", str(adapter.resolve()))
    text = text.replace("$SHUNT_ROOT", str(package_root()))
    for s in expand_scripts:
        text = text.replace(f"{{{{SCRIPT:{s.name}}}}}", str(s.resolve()))
    return json.loads(text)


def _get_hooks_obj(doc: dict[str, Any], spec: HarnessSpec) -> dict[str, Any]:
    hooks = doc.get(spec.hooks_key)
    if hooks is None:
        doc[spec.hooks_key] = {}
        hooks = doc[spec.hooks_key]
    if not isinstance(hooks, dict):
        raise ValueError(f"{spec.name}: hooks must be an object")
    return hooks


def _entry_command(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if not isinstance(entry, dict):
        return ""
    if "command" in entry:
        return str(entry.get("command") or "")
    inner = entry.get("hooks")
    if isinstance(inner, list):
        cmds = []
        for h in inner:
            if isinstance(h, dict) and h.get("command"):
                cmds.append(str(h["command"]))
        return "\n".join(cmds)
    return ""


def _entry_is_shunt(entry: Any, root: Path | None = None) -> bool:
    if isinstance(entry, dict) and entry.get("_shunt"):
        return True
    return is_shunt_command(_entry_command(entry), root=root)


def _mark_shunt(entry: Any) -> Any:
    if isinstance(entry, dict):
        out = dict(entry)
        out["_shunt"] = True
        if "hooks" in out and isinstance(out["hooks"], list):
            out["hooks"] = [
                {**h, "_shunt": True} if isinstance(h, dict) else h for h in out["hooks"]
            ]
        return out
    return entry


def merge_hooks(
    doc: dict[str, Any],
    spec: HarnessSpec,
    registration: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Merge shunt events into doc. Returns (new_doc, changed)."""
    events = registration.get("events") or {}
    if not isinstance(events, dict) or not events:
        return doc, False
    hooks = _get_hooks_obj(doc, spec)
    changed = False
    root = package_root()

    for event, new_entries in events.items():
        if not isinstance(new_entries, list):
            continue
        existing = hooks.get(event)
        if existing is None:
            existing = []
            hooks[event] = existing
        if not isinstance(existing, list):
            raise ValueError(f"{spec.name}.{event}: expected list")

        kept = [e for e in existing if not _entry_is_shunt(e, root=root)]
        marked = [_mark_shunt(e) for e in new_entries]
        new_list = kept + marked
        if json.dumps(existing, sort_keys=True) != json.dumps(new_list, sort_keys=True):
            hooks[event] = new_list
            changed = True

    return doc, changed


def remove_shunt_hooks(doc: dict[str, Any], spec: HarnessSpec) -> tuple[dict[str, Any], bool]:
    if spec.hooks_key not in doc or not isinstance(doc.get(spec.hooks_key), dict):
        return doc, False
    hooks = doc[spec.hooks_key]
    root = package_root()
    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not _entry_is_shunt(e, root=root)]
        if len(kept) != len(entries):
            hooks[event] = kept
            changed = True
    return doc, changed


def plan_install(home: Path | None = None) -> list[PlanItem]:
    home = home or Path.home()
    items: list[PlanItem] = []
    for spec in HARNESSES:
        cfg = home / spec.config_rel
        scripts = list_adapter_scripts(spec.name)
        gate_scripts = list_gate_scripts(spec.name)
        reg = registration_path(spec.name)

        if spec.style == "external":
            items.append(
                PlanItem(
                    harness=spec.name,
                    config_path=cfg,
                    action="skip",
                    detail=f"external installer — {spec.external_hint}",
                    adapter_scripts=[],
                    registration=None,
                )
            )
            continue

        if reg is None and not gate_scripts:
            items.append(
                PlanItem(
                    harness=spec.name,
                    config_path=cfg,
                    action="skip",
                    detail="no registration.json and no explicit gate scripts",
                    adapter_scripts=[],
                    registration=None,
                )
            )
            continue

        action = "merge" if cfg.is_file() else "create"
        if reg is not None:
            detail = f"register via {reg.name}"
            shown = scripts
        else:
            detail = f"register {len(gate_scripts)} gate script(s) via default PreToolUse/preToolUse"
            shown = gate_scripts
        items.append(
            PlanItem(
                harness=spec.name,
                config_path=cfg,
                action=action,
                detail=detail,
                adapter_scripts=shown,
                registration=reg,
            )
        )
    return items


def plan_uninstall(home: Path | None = None) -> list[PlanItem]:
    home = home or Path.home()
    items: list[PlanItem] = []
    for spec in HARNESSES:
        if spec.style == "external":
            items.append(
                PlanItem(
                    spec.name,
                    home / spec.config_rel,
                    "skip",
                    f"external — uninstall via WP4 tooling if needed ({spec.external_hint})",
                    [],
                    None,
                )
            )
            continue
        cfg = home / spec.config_rel
        if not cfg.is_file():
            items.append(PlanItem(spec.name, cfg, "skip", "config absent", [], None))
            continue
        try:
            doc = _load_json(cfg)
            _, would = remove_shunt_hooks(dict(doc), spec)
        except Exception as e:  # noqa: BLE001
            items.append(PlanItem(spec.name, cfg, "skip", f"unreadable: {e}", [], None))
            continue
        if would:
            items.append(
                PlanItem(spec.name, cfg, "remove", "remove shunt-managed hook entries", [], None)
            )
        else:
            items.append(PlanItem(spec.name, cfg, "skip", "no shunt-managed entries", [], None))

    for rel in _OBSOLETE_HOOK_PATHS:
        cfg = home / rel
        if not cfg.is_file():
            continue
        try:
            doc = _load_json(cfg)
            # Treat as cursor-style hooks object for cleanup.
            fake = HarnessSpec("obsolete", rel, "cursor")
            _, would = remove_shunt_hooks(dict(doc), fake)
        except Exception:  # noqa: BLE001
            would = "shunt" in cfg.read_text(encoding="utf-8", errors="replace").lower()
        if would:
            items.append(
                PlanItem(
                    f"obsolete:{rel}",
                    cfg,
                    "remove",
                    "remove legacy/noise shunt hook entries",
                    [],
                    None,
                )
            )
    return items


def apply_install(*, dry_run: bool, home: Path | None = None) -> int:
    home = home or Path.home()
    items = plan_install(home)
    print(f"shunt install{' — dry-run' if dry_run else ''}")
    print(f"package: {package_root()}")
    print("Targets:")
    for it in items:
        scripts = ", ".join(s.name for s in it.adapter_scripts) or "-"
        print(f"  - {it.harness}: {it.config_path}  [{it.action}] {it.detail}  scripts=[{scripts}]")

    if dry_run:
        return 0

    state = load_state(home)
    state.setdefault("harnesses", {})
    errors = 0
    for it in items:
        if it.action == "skip":
            continue
        spec = next(s for s in HARNESSES if s.name == it.harness)
        registration = load_registration(it.harness, it.adapter_scripts)
        if not registration:
            continue
        # Refuse to write any command that points at install.sh
        blob = json.dumps(registration)
        if "install.sh" in blob:
            print(f"  ERROR {it.harness}: registration resolves to install.sh — refused")
            errors += 1
            continue
        bak = backup_config(it.config_path, it.harness, home) if it.config_path.is_file() else None
        try:
            doc = _load_json(it.config_path) if it.config_path.is_file() else {}
            if spec.name == "cursor" and "version" not in doc and not it.config_path.is_file():
                doc["version"] = 1
            new_doc, changed = merge_hooks(doc, spec, registration)
            if changed or not it.config_path.is_file():
                _dump_json(it.config_path, new_doc)
                print(f"  wrote {it.config_path}" + (f" (backup {bak})" if bak else " (new)"))
            else:
                print(f"  unchanged {it.config_path} (already merged)")
            state["harnesses"][it.harness] = {
                "config": str(it.config_path),
                "backup": str(bak) if bak else None,
                "scripts": [str(s) for s in it.adapter_scripts],
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {it.harness}: {e}")
            errors += 1
    save_state(state, home)
    return 1 if errors else 0


def apply_uninstall(*, dry_run: bool, home: Path | None = None) -> int:
    home = home or Path.home()
    items = plan_uninstall(home)
    print(f"shunt uninstall{' — dry-run' if dry_run else ''}")
    for it in items:
        print(f"  - {it.harness}: {it.config_path}  [{it.action}] {it.detail}")
    if dry_run:
        return 0
    state = load_state(home)
    errors = 0
    for it in items:
        if it.action != "remove":
            continue
        if it.harness.startswith("obsolete:"):
            spec = HarnessSpec("obsolete", str(it.config_path), "cursor")
        else:
            spec = next(s for s in HARNESSES if s.name == it.harness)
        bak = backup_config(it.config_path, it.harness.replace(":", "_"), home)
        try:
            doc = _load_json(it.config_path)
            new_doc, changed = remove_shunt_hooks(doc, spec)
            if changed:
                _dump_json(it.config_path, new_doc)
                print(f"  wrote {it.config_path}" + (f" (backup {bak})" if bak else ""))
            if not it.harness.startswith("obsolete:"):
                state.get("harnesses", {}).pop(it.harness, None)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {it.harness}: {e}")
            errors += 1
    save_state(state, home)
    return 1 if errors else 0


def doctor_report(home: Path | None = None) -> int:
    """Capability report. Exit 0 if core OK; 1 if OpenRouter key missing (soft)."""
    from shunt import __version__
    from shunt.bulk_read import OPENROUTER_BASE_URL, OPENROUTER_MODEL

    home = home or Path.home()
    key = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    cfg = package_root() / "config" / "default.toml"
    print(f"shunt {__version__} — doctor (capability report)")
    print(f"package_root={package_root()}")
    print(f"openrouter.base_url={OPENROUTER_BASE_URL}")
    print(f"openrouter.model={OPENROUTER_MODEL}  # LOCKED paid bulk-reader")
    print(f"OPENROUTER_API_KEY={'set' if key else 'MISSING'}")
    print(f"default_config={'present' if cfg.is_file() else 'missing'}: {cfg}")
    print("fallback=none (never CAPI/gflash/Google OAuth Flash)")
    print(f"state_dir={state_dir(home)} exists={state_dir(home).is_dir()}")
    print("harnesses:")
    for spec in HARNESSES:
        cfg_path = home / spec.config_rel
        scripts = list_adapter_scripts(spec.name)
        gate = list_gate_scripts(spec.name)
        reg = registration_path(spec.name)
        registered = False
        if spec.style == "external":
            if spec.name == "pi":
                registered = cfg_path.is_symlink() or cfg_path.is_file()
            elif spec.name == "hermes":
                if cfg_path.is_file():
                    text = cfg_path.read_text(encoding="utf-8", errors="replace")
                    registered = "pre_tool_call" in text and "shunt" in text.lower()
            elif spec.name == "agy":
                if cfg_path.is_file():
                    try:
                        doc = _load_json(cfg_path)
                        registered = isinstance(doc.get("shunt"), dict)
                    except Exception:  # noqa: BLE001
                        registered = False
            elif spec.name == "grok":
                registered = cfg_path.is_file()
            print(
                f"  - {spec.name}: style=external path={'yes' if cfg_path.exists() else 'no'} "
                f"shunt_registered={'yes' if registered else 'no'} "
                f"hint={spec.external_hint}"
            )
            continue
        if cfg_path.is_file():
            try:
                doc = _load_json(cfg_path)
                hooks = doc.get(spec.hooks_key) or {}
                if isinstance(hooks, dict):
                    for entries in hooks.values():
                        if isinstance(entries, list) and any(_entry_is_shunt(e) for e in entries):
                            registered = True
                            break
            except Exception:  # noqa: BLE001
                registered = False
        print(
            f"  - {spec.name}: config={'yes' if cfg_path.is_file() else 'no'} "
            f"scripts={len(scripts)} gate_scripts={len(gate)} registration={'yes' if reg else 'no'} "
            f"shunt_registered={'yes' if registered else 'no'} path={cfg_path}"
        )
    # Noise check
    for rel in _OBSOLETE_HOOK_PATHS:
        p = home / rel
        if p.is_file() and "install.sh" in p.read_text(encoding="utf-8", errors="replace"):
            print(f"  ! noise: {p} still references install.sh — run shunt uninstall")
    policy = Path(
        "/Users/jonbennett/Library/CloudStorage/Dropbox/AI-Control-Plane/contracts/HARNESS-HOOKS.md"
    )
    if policy.is_file():
        text = policy.read_text(encoding="utf-8", errors="replace")
        print(f"policy_HARNESS-HOOKS: present shunt_allowed={'shunt' in text.lower()}")
    else:
        print("policy_HARNESS-HOOKS: missing")
    return 0 if key else 1
