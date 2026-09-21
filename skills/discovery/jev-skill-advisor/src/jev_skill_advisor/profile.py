"""Trusted host profile and frozen catalog loading."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import math
import shutil

from .exposure import Capability, Registry


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class HostProfile:
    profile_id: str
    harness: str
    warehouse_root: Path
    catalog_path: Path
    state_dir: Path
    mode: str
    provider_enabled: bool
    read_enabled: bool
    eligible_ids: frozenset[str]
    read_allowlist: frozenset[str]
    credential_env: str | None
    credential_file: Path | None
    deadline_s: float
    max_calls: int
    max_tokens: int
    receipt_ttl_s: int
    prompt_limit: int
    provider_attempt_limit: int
    emergency_stop_file: Path | None
    entries: dict[str, Capability]
    names: dict[str, list[str]]
    catalog_hash: str
    policy_hash: str
    package_roots: dict[str, str]
    runtime_missing: frozenset[str]

    def registry(self, available_ids=None, *, implicit_only=True):
        allowed = set(self.eligible_ids)
        if available_ids is not None:
            allowed &= set(available_ids)
        entries = []
        for sid in sorted(allowed):
            entry = self.entries.get(sid)
            if entry is None:
                continue
            if implicit_only:
                implicit, policy_hash = current_policy(Path(entry.source), Path(self.package_roots[sid]))
                body = Path(entry.source).read_text(encoding="utf-8", errors="replace").lower()
                protected = any(marker in (body + "\n" + entry.description.lower()) for marker in ("typesafe_api_key=", "jev_api=", "authorization: bearer", "-----begin "))
                if not implicit or policy_hash != entry.policy_hash or protected:
                    continue
            if sid in self.runtime_missing:
                continue
            entries.append(entry)
        return Registry(entries)


def _policy(source: Path):
    text = source.read_text(encoding="utf-8", errors="replace")
    front = ""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end is None:
            raise ProfileError("malformed_frontmatter")
        front = "\n".join(lines[1:end])
    disabled = bool(re.search(r"(?mi)^\s*disable-model-invocation:\s*true\s*(?:#.*)?$", front))
    sidecar = source.parent / "agents" / "openai.yaml"
    raw = sidecar.read_bytes() if sidecar.is_file() else b""
    match = re.search(rb"(?mi)^\s*allow_implicit_invocation:\s*([^#\r\n]+?)\s*(?:#.*)?$", raw)
    explicit_only = bool(match and match.group(1).strip().lower() != b"true")
    return not (disabled or explicit_only), hashlib.sha256(raw or b"default-implicit-policy").hexdigest()


def current_policy(source: Path, package_root: Path | None = None):
    """Re-read invocation policy so an old receipt cannot bypass a policy change."""
    implicit, digest = _policy(source)
    manifest = (package_root or source.parent) / "skill-package.json"
    if manifest.is_file():
        try:
            raw=manifest.read_bytes(); value=json.loads(raw)
        except (OSError,json.JSONDecodeError) as exc:
            raise ProfileError("invalid_package_manifest") from exc
        policy=value.get("invocation_policy")
        if policy not in {"implicit","explicit","source"}: raise ProfileError("invalid_package_manifest")
        if policy == "explicit": implicit=False
        digest=hashlib.sha256(digest.encode()+b":"+raw).hexdigest()
    return implicit,digest


def load_profile(path: Path) -> HostProfile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = {"config_version", "profile_id", "harness", "warehouse_root", "catalog_path", "state_dir", "mode", "provider_enabled", "read_enabled", "eligible_ids", "read_allowlist"}
    if not isinstance(raw, dict) or set(raw) - (required | {"credential_env", "credential_file", "deadline_s", "max_calls", "max_tokens", "receipt_ttl_s", "prompt_limit", "provider_attempt_limit", "emergency_stop_file"}) or not required <= set(raw):
        raise ProfileError("invalid_profile_fields")
    if raw["config_version"] != 1 or raw["mode"] not in {"off", "shadow", "advisory"}:
        raise ProfileError("invalid_profile_version_or_mode")
    if any(type(raw.get(key)) is not bool for key in ("provider_enabled", "read_enabled")):
        raise ProfileError("invalid_boolean")
    if any(not isinstance(raw.get(key), list) or any(not isinstance(item, str) or not item for item in raw[key])
           for key in ("eligible_ids", "read_allowlist")):
        raise ProfileError("invalid_id_lists")
    numbers = {"deadline_s": (0.001, 5), "max_calls": (1, 32), "max_tokens": (1, 200000), "receipt_ttl_s": (1, 86400),
               "prompt_limit": (1, 20), "provider_attempt_limit": (1, 160)}
    defaults = {"deadline_s": 5, "max_calls": 32, "max_tokens": 200000, "receipt_ttl_s": 86400,
                "prompt_limit": 20, "provider_attempt_limit": 160}
    for key, (low, high) in numbers.items():
        value = raw.get(key, defaults[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ProfileError("invalid_" + key)
        if key != "deadline_s" and not isinstance(value, int):
            raise ProfileError("invalid_" + key)
    warehouse = Path(raw["warehouse_root"]).expanduser().resolve()
    catalog_path = Path(raw["catalog_path"]).expanduser().resolve()
    state = Path(raw["state_dir"]).expanduser().resolve()
    if not warehouse.is_dir() or not catalog_path.is_file():
        raise ProfileError("missing_warehouse_or_catalog")
    if warehouse.name == "skills" and re.fullmatch(r"[0-9a-f]{64}",warehouse.parent.name) and warehouse.parent.parent.name == "snapshots":
        from .library_cache import LibraryCache, LibraryCacheError
        try: LibraryCache(warehouse.parent.parent.parent)._verify(warehouse.parent.name)
        except LibraryCacheError as exc: raise ProfileError("invalid_library_snapshot") from exc
    control_root = next((parent for parent in (warehouse, *warehouse.parents) if parent.name == "AI-Control-Plane"), warehouse)
    if control_root == state or control_root in state.parents:
        raise ProfileError("state_inside_warehouse")
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    rows = catalog.get("entries")
    if not isinstance(rows, list):
        raise ProfileError("invalid_catalog")
    entries, names, package_roots, runtime_missing = {}, {}, {}, set()
    eligible = frozenset(raw["eligible_ids"])
    read_allow = frozenset(raw["read_allowlist"])
    if not read_allow <= eligible:
        raise ProfileError("read_allowlist_expands_eligibility")
    policy_digest = hashlib.sha256()
    for row in rows:
        sid = row.get("stable_id")
        if sid not in eligible:
            continue
        rel = Path(row.get("relative_path", ""))
        if rel.is_absolute() or ".." in rel.parts:
            raise ProfileError("invalid_relative_path")
        entrypoint = Path(row.get("entrypoint", "SKILL.md"))
        if entrypoint.is_absolute() or ".." in entrypoint.parts:
            raise ProfileError("invalid_entrypoint")
        candidate = warehouse / rel / entrypoint
        if candidate.is_symlink():
            raise ProfileError("source_escape_or_missing")
        source = candidate.resolve()
        if warehouse not in source.parents or not source.is_file():
            raise ProfileError("source_escape_or_missing")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != row.get("content_hash"):
            raise ProfileError("stale_catalog_source")
        package_root=(warehouse/Path(row.get("package_root",rel))).resolve()
        if package_root != source.parent and package_root not in source.parents: raise ProfileError("invalid_package_root")
        implicit, policy_hash = current_policy(source,package_root)
        policy_digest.update(f"{sid}:{policy_hash}:{implicit}".encode())
        cap = Capability(sid, "skill", row["description"], source=str(source), source_hash=digest,
                         policy_hash=policy_hash, disclose=implicit, available=True, permitted=True)
        if sid in entries:
            raise ProfileError("duplicate_identity")
        entries[sid] = cap
        names.setdefault(row["name"], []).append(sid)
        for alias in row.get("aliases", []): names.setdefault(alias, []).append(sid)
        package_roots[sid]=str(package_root)
        required=row.get("required_runtimes",[])
        if not isinstance(required,list) or any(not isinstance(item,str) or not item for item in required): raise ProfileError("invalid_required_runtimes")
        if any(shutil.which(item) is None for item in required): runtime_missing.add(sid)
    if set(eligible) - set(entries):
        raise ProfileError("eligible_id_missing")
    credential_file = Path(raw["credential_file"]).expanduser().resolve() if raw.get("credential_file") else None
    if credential_file and (control_root == credential_file or control_root in credential_file.parents):
        raise ProfileError("credential_inside_warehouse")
    emergency_stop_file = Path(raw["emergency_stop_file"]).expanduser().resolve() if raw.get("emergency_stop_file") else None
    if emergency_stop_file and (control_root == emergency_stop_file or control_root in emergency_stop_file.parents):
        raise ProfileError("emergency_stop_inside_warehouse")
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    return HostProfile(raw["profile_id"], raw["harness"], warehouse, catalog_path, state, raw["mode"],
        bool(raw["provider_enabled"]), bool(raw["read_enabled"]), eligible, read_allow,
        raw.get("credential_env"), credential_file, float(raw.get("deadline_s", 5)),
        int(raw.get("max_calls", 32)), int(raw.get("max_tokens", 200000)),
        int(raw.get("receipt_ttl_s", 86400)), int(raw.get("prompt_limit", 20)),
        int(raw.get("provider_attempt_limit", 160)), emergency_stop_file, entries, names,
        hashlib.sha256(catalog_bytes).hexdigest(), policy_digest.hexdigest(), package_roots, frozenset(runtime_missing))
