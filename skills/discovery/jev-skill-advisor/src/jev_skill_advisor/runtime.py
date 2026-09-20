"""Host-local SQLite persistence for the harness-neutral advisor."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import hashlib, json, os, shutil, sqlite3, time, uuid
from .client import AdvisorError, evaluate

CACHE_VERSION = "service-v1"

def iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class ServiceRuntime:
    def __init__(self, profile, evaluate_fn=evaluate, operation_id=None, initialize=False, allow_pending_legacy=False):
        self.profile, self.evaluate_fn, self.operation_id = profile, evaluate_fn, operation_id
        self.key = credential(profile) if profile.provider_enabled else None
        self.db_path = profile.state_dir / "advisor.sqlite3"
        if not self.db_path.exists():
            legacy = bool(self._legacy_sources())
            if legacy and not initialize: raise RuntimeError("legacy_migration_required")
            if legacy and not allow_pending_legacy: raise RuntimeError("legacy_migration_required")
            if not initialize: raise RuntimeError("database_not_initialized")
            self._initialize()
        self._validate()
        if not allow_pending_legacy: self._validate_legacy_imports()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=1.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA busy_timeout=1000"); db.execute("PRAGMA journal_mode=DELETE")
        return db

    def _initialize(self):
        self.profile.state_dir.mkdir(parents=True, exist_ok=True); os.chmod(self.profile.state_dir, 0o700)
        with self._connect() as db:
            db.executescript("""BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS budget_domains(name TEXT PRIMARY KEY, consumed INTEGER NOT NULL CHECK(consumed>=0));
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, provider_attempts INTEGER NOT NULL DEFAULT 0 CHECK(provider_attempts>=0), created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, session_id TEXT NOT NULL, expires_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipt_bindings(receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE, skill_id TEXT NOT NULL, content_hash TEXT NOT NULL, policy_hash TEXT NOT NULL, PRIMARY KEY(receipt_id,skill_id));
            CREATE TABLE IF NOT EXISTS receipt_reads(receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE, skill_id TEXT NOT NULL, content_hash TEXT NOT NULL, body_bytes INTEGER NOT NULL CHECK(body_bytes>=0), PRIMARY KEY(receipt_id,skill_id));
            CREATE TABLE IF NOT EXISTS outcomes(dedupe_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS response_cache(cache_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS migration_imports(source TEXT PRIMARY KEY, content_hash TEXT NOT NULL, imported_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
            INSERT OR IGNORE INTO budget_domains VALUES('prompts',0),('provider_attempts',0); COMMIT;""")
        os.chmod(self.db_path, 0o600)

    def _validate(self):
        with self._connect() as db:
            versions=[row[0] for row in db.execute("SELECT version FROM schema_versions ORDER BY version")]
            if versions != [1]: raise RuntimeError("unsupported_schema_version")
            budgets={row[0]:row[1] for row in db.execute("SELECT name,consumed FROM budget_domains")}
            if set(budgets)!={"prompts","provider_attempts"} or any(not isinstance(v,int) or v<0 for v in budgets.values()): raise RuntimeError("invalid_budget_state")

    def _legacy_sources(self):
        root=self.profile.state_dir; sources=[root/"counters.json",root/"outcomes.jsonl"]
        for folder in ("receipts","cache"):
            if (root/folder).is_dir(): sources += sorted((root/folder).glob("*.json"))
        return [path for path in sources if path.is_file()]

    def _validate_legacy_imports(self):
        with self._connect() as db:
            for path in self._legacy_sources():
                source=str(path.relative_to(self.profile.state_dir)); digest=hashlib.sha256(path.read_bytes()).hexdigest(); row=db.execute("SELECT content_hash FROM migration_imports WHERE source=?",(source,)).fetchone()
                if row is None or row[0]!=digest: raise RuntimeError("legacy_migration_required")

    def evaluator(self, payload, timeout):
        material = {"version": CACHE_VERSION, "profile": self.profile.profile_id, "payload": payload}
        key = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._connect() as db: row = db.execute("SELECT created_at,payload FROM response_cache WHERE cache_key=?",(key,)).fetchone()
        if row:
            age = (datetime.now(timezone.utc)-datetime.strptime(row["created_at"],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
            if 0 <= age <= 86400:
                response=json.loads(row["payload"]); response["usage"]={**(response.get("usage") or {}),"input_tokens":0}; response["_cache_hit"]=True; return response
        if not self.key: raise AdvisorError("missing_api_key")
        if not self._reserve("provider_attempts",self.profile.provider_attempt_limit): raise AdvisorError("provider_attempt_budget")
        wire=deepcopy(payload); wire.pop("_cache_identity",None); response,_=self.evaluate_fn(wire,self.key,timeout)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); db.execute("INSERT OR REPLACE INTO response_cache VALUES(?,?,?)",(key,iso(),json.dumps(response,sort_keys=True,separators=(",",":")))); db.commit()
        result=deepcopy(response); result["_cache_hit"]=False; return result

    def reserve_prompt(self): return self._reserve("prompts",self.profile.prompt_limit)
    def _reserve(self,name,limit):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT consumed FROM budget_domains WHERE name=?",(name,)).fetchone()
            if row is None: db.rollback(); raise ValueError("missing_budget_domain")
            if row[0] >= limit: db.rollback(); return False
            db.execute("UPDATE budget_domains SET consumed=consumed+1 WHERE name=?",(name,))
            if name=="provider_attempts" and self.operation_id:
                db.execute("INSERT OR IGNORE INTO operations(id,created_at) VALUES(?,?)",(self.operation_id,iso())); db.execute("UPDATE operations SET provider_attempts=provider_attempts+1 WHERE id=?",(self.operation_id,))
            db.commit(); return True
    def counts(self,operation_id=None):
        with self._connect() as db:
            result={r[0]:r[1] for r in db.execute("SELECT name,consumed FROM budget_domains")}
            if operation_id:
                row=db.execute("SELECT provider_attempts FROM operations WHERE id=?",(operation_id,)).fetchone(); result["operation_attempts"]=row[0] if row else 0
        return result

    def save_receipt(self,receipt):
        payload=json.dumps(receipt,sort_keys=True,separators=(",",":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); db.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?)",(receipt["receipt_id"],receipt["profile_id"],receipt["session_id"],receipt["expires_at"],payload))
            db.execute("DELETE FROM receipt_bindings WHERE receipt_id=?",(receipt["receipt_id"],)); db.execute("DELETE FROM receipt_reads WHERE receipt_id=?",(receipt["receipt_id"],))
            db.executemany("INSERT INTO receipt_bindings VALUES(?,?,?,?)",[(receipt["receipt_id"],sid,item["content_hash"],item["policy_hash"]) for sid,item in receipt.get("bindings",{}).items()])
            db.executemany("INSERT INTO receipt_reads VALUES(?,?,?,?)",[(receipt["receipt_id"],sid,item["content_hash"],item["body_bytes"]) for sid,item in receipt.get("reads",{}).items()]); db.commit()
    def update_receipt(self,receipt_id,update):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT payload FROM receipts WHERE id=?",(receipt_id,)).fetchone()
            if row is None: db.rollback(); raise FileNotFoundError("missing_receipt")
            receipt=json.loads(row[0]); result=update(receipt)
            if result is None:
                db.execute("UPDATE receipts SET payload=? WHERE id=?",(json.dumps(receipt,sort_keys=True,separators=(",",":")),receipt_id)); db.execute("DELETE FROM receipt_reads WHERE receipt_id=?",(receipt_id,)); db.executemany("INSERT INTO receipt_reads VALUES(?,?,?,?)",[(receipt_id,sid,item["content_hash"],item["body_bytes"]) for sid,item in receipt.get("reads",{}).items()])
            db.commit(); return result
    def new_receipt_id(self): return uuid.uuid4().hex
    def load_receipt(self,receipt_id):
        if not isinstance(receipt_id,str) or not re_safe(receipt_id): raise ValueError("invalid_receipt_id")
        with self._connect() as db: row=db.execute("SELECT payload FROM receipts WHERE id=?",(receipt_id,)).fetchone()
        if row is None: raise FileNotFoundError("missing_receipt")
        return json.loads(row[0])
    def record_outcome(self,value):
        payload=json.dumps(value,sort_keys=True,separators=(",",":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); row=db.execute("SELECT payload FROM outcomes WHERE dedupe_key=?",(value["dedupe_key"],)).fetchone()
            if row:
                db.rollback()
                if row[0]!=payload: raise ValueError("conflicting_event_id")
                return False
            db.execute("INSERT INTO outcomes VALUES(?,?)",(value["dedupe_key"],payload)); db.commit(); return True
    def prune(self,now=None):
        stamp=now or time.time(); cutoff=iso(stamp-86400); current=iso(stamp)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE"); cache=db.execute("DELETE FROM response_cache WHERE created_at<?",(cutoff,)).rowcount; receipts=db.execute("DELETE FROM receipts WHERE expires_at<?",(current,)).rowcount; db.commit()
        return {"cache":cache,"receipts":receipts}

    def migrate_legacy(self):
        root=self.profile.state_dir; sources=self._legacy_sources()
        if not sources: return {"status":"no_legacy_state","imported":0,"backup":None}
        backup=root/f"legacy-backup-{int(time.time())}-{uuid.uuid4().hex[:8]}"; backup.mkdir(mode=0o700)
        for path in sources:
            target=backup/path.relative_to(root); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,target)
            if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(path.read_bytes()).digest(): raise OSError("legacy_backup_verification_failed")
        imported=0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for path in sources:
                raw=path.read_bytes(); digest=hashlib.sha256(raw).hexdigest(); source=str(path.relative_to(root)); prior=db.execute("SELECT content_hash FROM migration_imports WHERE source=?",(source,)).fetchone()
                if prior:
                    if prior[0]!=digest: db.rollback(); raise ValueError("legacy_source_changed_after_import")
                    continue
                if source=="counters.json":
                    value=json.loads(raw)
                    for name in ("prompts","provider_attempts"):
                        count=value.get(name,0)
                        if not isinstance(count,int) or isinstance(count,bool) or count<0: raise ValueError("invalid_legacy_counter")
                        db.execute("UPDATE budget_domains SET consumed=MAX(consumed,?) WHERE name=?",(count,name))
                elif source=="outcomes.jsonl":
                    for line in raw.decode().splitlines():
                        if not line.strip(): continue
                        value=json.loads(line); key=value.get("dedupe_key")
                        if not isinstance(key,str): raise ValueError("invalid_legacy_outcome")
                        payload=json.dumps(value,sort_keys=True,separators=(",",":")); existing=db.execute("SELECT payload FROM outcomes WHERE dedupe_key=?",(key,)).fetchone()
                        if existing and existing[0]!=payload: raise ValueError("conflicting_legacy_outcome")
                        if not existing: db.execute("INSERT INTO outcomes VALUES(?,?)",(key,payload))
                elif source.startswith("receipts/"):
                    value=json.loads(raw)
                    if any(not isinstance(value.get(k),str) for k in ("receipt_id","profile_id","session_id","expires_at")): raise ValueError("invalid_legacy_receipt")
                    payload=json.dumps(value,sort_keys=True,separators=(",",":")); existing=db.execute("SELECT payload FROM receipts WHERE id=?",(value["receipt_id"],)).fetchone()
                    if existing and existing[0]!=payload: raise ValueError("conflicting_legacy_receipt")
                    if not existing: db.execute("INSERT INTO receipts VALUES(?,?,?,?,?)",(value["receipt_id"],value["profile_id"],value["session_id"],value["expires_at"],payload))
                    db.executemany("INSERT OR IGNORE INTO receipt_bindings VALUES(?,?,?,?)",[(value["receipt_id"],sid,item["content_hash"],item["policy_hash"]) for sid,item in value.get("bindings",{}).items()])
                    db.executemany("INSERT OR IGNORE INTO receipt_reads VALUES(?,?,?,?)",[(value["receipt_id"],sid,item["content_hash"],item["body_bytes"]) for sid,item in value.get("reads",{}).items()])
                elif source.startswith("cache/"):
                    value=json.loads(raw)
                    if value.get("version")!=CACHE_VERSION or not isinstance(value.get("response"),dict): raise ValueError("invalid_legacy_cache")
                    payload=json.dumps(value["response"],sort_keys=True,separators=(",",":")); existing=db.execute("SELECT created_at,payload FROM response_cache WHERE cache_key=?",(path.stem,)).fetchone()
                    if existing and (existing[0]!=value["created_at"] or existing[1]!=payload): raise ValueError("conflicting_legacy_cache")
                    if not existing: db.execute("INSERT INTO response_cache VALUES(?,?,?)",(path.stem,value["created_at"],payload))
                db.execute("INSERT INTO migration_imports VALUES(?,?,?)",(source,digest,iso())); imported+=1
            db.commit()
        return {"status":"migrated","imported":imported,"backup":str(backup)}

def credential(profile):
    if profile.credential_env:
        value=os.environ.get(profile.credential_env,"").strip()
        if value: return value
    if profile.credential_file:
        for line in profile.credential_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key,value=line.split("=",1)
                if key.strip() in {profile.credential_env,"TYPESAFE_API_KEY","JEV_API"}: return value.strip().strip("'\"")
    return None

def re_safe(value): return value and len(value)<=128 and all(c.isalnum() or c in "-_" for c in value)
