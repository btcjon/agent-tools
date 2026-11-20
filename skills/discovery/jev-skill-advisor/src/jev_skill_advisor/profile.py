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
import stat as statmod

from .exposure import Capability, Registry
from .library_cache import LibraryCacheError


class ProfileError(ValueError):
    pass


def _skills_tree_fingerprint(skills_path: Path) -> str:
    """SHA-256 of sorted metadata records under skills/; never follows links."""
    try:
        st = os.lstat(skills_path)
    except OSError as exc:
        raise LibraryCacheError("invalid_snapshot") from exc
    if statmod.S_ISLNK(st.st_mode) or not statmod.S_ISDIR(st.st_mode):
        raise LibraryCacheError("invalid_snapshot")
    records = []
    stack = [(str(skills_path), "")]
    while stack:
        current, prefix = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise LibraryCacheError("invalid_snapshot") from exc
        for entry in entries:
            if entry.is_symlink():
                raise LibraryCacheError("snapshot_contains_link")
            rel = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
                info = entry.stat(follow_symlinks=False) if is_file else None
            except OSError as exc:
                raise LibraryCacheError("invalid_snapshot") from exc
            if is_dir:
                stack.append((entry.path, rel))
            elif is_file:
                records.append({
                    "path": rel,
                    "size": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "ctime_ns": info.st_ctime_ns,
                    "ino": info.st_ino,
                    "exec": bool(info.st_mode & 0o111),
                })
            else:
                raise LibraryCacheError("snapshot_contains_special_file")
    records.sort(key=lambda row: row["path"])
    payload = json.dumps(records, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _read_manifest_sha256(path: Path) -> str:
    manifest_path = path / "manifest.json"
    try:
        st = os.lstat(manifest_path)
    except OSError as exc:
        raise LibraryCacheError("invalid_snapshot") from exc
    if statmod.S_ISLNK(st.st_mode) or not statmod.S_ISREG(st.st_mode):
        raise LibraryCacheError("invalid_snapshot")
    try:
        data = Path(manifest_path).read_bytes()
    except OSError as exc:
        raise LibraryCacheError("invalid_snapshot") from exc
    return hashlib.sha256(data).hexdigest()


def _load_verify_marker(marker: Path, identity: str, manifest_sha256: str, tree_fingerprint: str):
    """Return True when marker matches current values; otherwise treat as absent."""
    try:
        st = os.lstat(marker)
    except OSError:
        return False
    if statmod.S_ISLNK(st.st_mode) or not statmod.S_ISREG(st.st_mode) or st.st_size > 4096:
        return False
    try:
        raw = marker.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    expected = {"version", "snapshot_id", "manifest_sha256", "tree_fingerprint"}
    if not isinstance(value, dict) or set(value) != expected:
        return False
    if type(value.get("version")) is not int or value["version"] != 1:
        return False
    if not all(isinstance(value.get(key), str) for key in ("snapshot_id", "manifest_sha256", "tree_fingerprint")):
        return False
    return (
        value["snapshot_id"] == identity
        and value["manifest_sha256"] == manifest_sha256
        and value["tree_fingerprint"] == tree_fingerprint
    )


def _write_verify_marker(state_dir: Path, identity: str, manifest_sha256: str, tree_fingerprint: str):
    payload = json.dumps({
        "version": 1,
        "snapshot_id": identity,
        "manifest_sha256": manifest_sha256,
        "tree_fingerprint": tree_fingerprint,
    }, separators=(",", ":"), sort_keys=True).encode()
    marker = state_dir / "snapshot-verify.json"
    tmp_name = f".snapshot-verify.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    tmp_path = state_dir / tmp_name
    fd = None
    try:
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, payload)
        os.close(fd)
        fd = None
        os.replace(str(tmp_path), str(marker))
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(str(tmp_path))
        except OSError:
            pass


def _verify_snapshot_cached(cache, identity, state_dir):
    """Verify a library snapshot once per release; reuse a local marker after that."""
    if os.environ.get("JEV_SNAPSHOT_VERIFY_CACHE") == "0":
        cache._verify(identity)
        return

    path = cache._snapshot_path(identity)
    manifest_sha256 = _read_manifest_sha256(path)
    tree_fingerprint = _skills_tree_fingerprint(path / "skills")

    state_path = Path(state_dir)
    try:
        state_st = os.lstat(state_path)
        state_is_dir = statmod.S_ISDIR(state_st.st_mode) and not statmod.S_ISLNK(state_st.st_mode)
    except OSError:
        state_is_dir = False

    if state_is_dir and _load_verify_marker(
        state_path / "snapshot-verify.json", identity, manifest_sha256, tree_fingerprint
    ):
        return

    f1 = tree_fingerprint
    cache._verify(identity)
    manifest_sha256_after = _read_manifest_sha256(path)
    f2 = _skills_tree_fingerprint(path / "skills")
    if state_is_dir and f1 == f2 and manifest_sha256 == manifest_sha256_after:
        _write_verify_marker(state_path, identity, manifest_sha256_after, f2)


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
                protected = bool(guard_hits(Path(entry.source).read_text(encoding="utf-8", errors="replace"), entry.description))
                if not implicit or policy_hash != entry.policy_hash or protected:
                    continue
            if sid in self.runtime_missing:
                continue
            entries.append(entry)
        return Registry(entries)


# --- guard-narrow-20261115 (COS): the Bearer check fires only on a literal-looking token. ---
_GUARD_STRICT = ("typesafe_api_key=", "jev_api=", "-----begin ")
_GUARD_DESC = ("typesafe_api_key=", "jev_api=", "authorization: bearer", "-----begin ")
_GUARD_BEARER = re.compile(r"""(?i)["']?authorization["']?\s*:\s*["']?\s*bearer\b[ \t]*["'`]?([^\s"'`]*)""")
_GUARD_PREFIXES = ("sk-", "crsr_", "eyj", "ghp_", "xox")


def _guard_literal(token):
    token = token.strip(".,;:)]}*")
    if not token:
        return False
    low = token.lower()
    if low.startswith(_GUARD_PREFIXES):
        return True
    if token[0] in "$<{" or token.startswith("{{"):
        return False
    if re.fullmatch(r"[A-Z_]+", token):
        return False
    if re.match(r"your[_-]", low) or "..." in token or "<" in token or ">" in token:
        return False
    if re.fullmatch(r"x+", low.replace("-", "").replace("_", "")):
        return False
    return len(token) >= 16


def guard_hits(body, description=""):
    """Locations that keep a skill out of implicit selection: ("description", marker) or (line_no, marker).
    Never returns the matched text. The description (sent to the judge) keeps the strict markers."""
    hits = []
    desc = (description or "").lower()
    for marker in _GUARD_DESC:
        if marker in desc:
            hits.append(("description", marker.strip()))
    lines = (body or "").splitlines()
    for number, line in enumerate(lines, 1):
        low = line.lower()
        for marker in _GUARD_STRICT:
            if marker in low:
                hits.append((number, marker.strip()))
        for match in _GUARD_BEARER.finditer(line):
            token = match.group(1)
            if token in ("", "\\") and line.rstrip().endswith("\\") and number < len(lines):
                nxt = lines[number].strip().strip("\"'`")
                token = nxt.split()[0] if nxt else ""
            if _guard_literal(token):
                hits.append((number, "authorization: bearer <literal>"))
    return hits


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
    if not isinstance(raw, dict) or set(raw) - (required | {"credential_env", "credential_file", "deadline_s", "max_calls", "max_tokens", "receipt_ttl_s", "prompt_limit", "provider_attempt_limit"}) or not required <= set(raw):
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
        from .library_cache import LibraryCache
        try: _verify_snapshot_cached(LibraryCache(warehouse.parent.parent.parent), warehouse.parent.name, state)
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
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    return HostProfile(raw["profile_id"], raw["harness"], warehouse, catalog_path, state, raw["mode"],
        bool(raw["provider_enabled"]), bool(raw["read_enabled"]), eligible, read_allow,
        raw.get("credential_env"), credential_file, float(raw.get("deadline_s", 5)),
        int(raw.get("max_calls", 32)), int(raw.get("max_tokens", 200000)),
        int(raw.get("receipt_ttl_s", 86400)), int(raw.get("prompt_limit", 20)),
        int(raw.get("provider_attempt_limit", 160)), entries, names,
        hashlib.sha256(catalog_bytes).hexdigest(), policy_digest.hexdigest(), package_roots, frozenset(runtime_missing))
