from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib, json, sqlite3, tempfile, unittest
from pathlib import Path

from jev_skill_advisor.profile import load_profile
from jev_skill_advisor.runtime import ServiceRuntime, credential

class SQLiteRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); root=Path(self.tmp.name)
        warehouse=root/"warehouse"; skill=warehouse/"alpha"/"SKILL.md"; skill.parent.mkdir(parents=True); skill.write_text("---\nname: alpha\ndescription: alpha\n---\n")
        catalog=root/"catalog.json"; catalog.write_text(json.dumps({"entries":[{"stable_id":"warehouse:alpha","name":"alpha","description":"alpha","relative_path":"alpha","content_hash":hashlib.sha256(skill.read_bytes()).hexdigest()}]}))
        self.state=root/"state"; config=root/"config.json"; config.write_text(json.dumps({"config_version":1,"profile_id":"sqlite-test","harness":"test","warehouse_root":str(warehouse),"catalog_path":str(catalog),"state_dir":str(self.state),"mode":"shadow","provider_enabled":False,"read_enabled":False,"eligible_ids":["warehouse:alpha"],"read_allowlist":[],"prompt_limit":20,"provider_attempt_limit":160}))
        self.profile=load_profile(config)
        ServiceRuntime(self.profile,initialize=True)

    def test_concurrent_budget_reservation_is_atomic(self):
        runtimes=[ServiceRuntime(self.profile) for _ in range(30)]
        with ThreadPoolExecutor(max_workers=12) as pool: results=list(pool.map(lambda runtime: runtime.reserve_prompt(),runtimes))
        self.assertEqual(sum(results),20); self.assertEqual(ServiceRuntime(self.profile).counts()["prompts"],20)

    def test_receipt_update_is_atomic(self):
        runtime=ServiceRuntime(self.profile); receipt={"receipt_id":"r1","profile_id":"sqlite-test","session_id":"s","expires_at":"2099-01-01T00:00:00Z","reads":{}}
        runtime.save_receipt(receipt)
        def add(name):
            return ServiceRuntime(self.profile).update_receipt("r1",lambda value: value["reads"].__setitem__(name,{"content_hash":"a"*64,"body_bytes":1}))
        with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(add,[f"s{i}" for i in range(20)]))
        self.assertEqual(len(runtime.load_receipt("r1")["reads"]),20)

    def test_legacy_migration_is_backed_up_and_idempotent(self):
        self.state.mkdir(parents=True,exist_ok=True); (self.state/"counters.json").write_text(json.dumps({"prompts":7,"provider_attempts":9}))
        runtime=ServiceRuntime(self.profile,allow_pending_legacy=True); first=runtime.migrate_legacy(); second=runtime.migrate_legacy()
        self.assertEqual(first["imported"],1); self.assertEqual(second["imported"],0); self.assertTrue(Path(first["backup"]).is_dir())
        self.assertEqual(runtime.counts(),{"prompts":7,"provider_attempts":9})

    def test_prune_does_not_reset_budgets(self):
        runtime=ServiceRuntime(self.profile); runtime.reserve_prompt(); runtime.prune(now=2_000_000_000)
        self.assertEqual(runtime.counts()["prompts"],1)

    def test_startup_rejects_unknown_schema_and_missing_budget(self):
        db=self.state/"advisor.sqlite3"
        with sqlite3.connect(db) as connection: connection.execute("INSERT INTO schema_versions VALUES(999,'now')")
        with self.assertRaisesRegex(RuntimeError,"unsupported_schema_version"): ServiceRuntime(self.profile)
        with sqlite3.connect(db) as connection:
            connection.execute("DELETE FROM schema_versions WHERE version=999"); connection.execute("DELETE FROM budget_domains WHERE name='prompts'")
        with self.assertRaisesRegex(RuntimeError,"invalid_budget_state"): ServiceRuntime(self.profile)

    def test_migration_rejects_conflicting_outcome(self):
        runtime=ServiceRuntime(self.profile); existing={"dedupe_key":"same","value":1}; runtime.record_outcome(existing)
        (self.state/"outcomes.jsonl").write_text(json.dumps({"dedupe_key":"same","value":2})+"\n")
        runtime=ServiceRuntime(self.profile,allow_pending_legacy=True)
        with self.assertRaisesRegex(ValueError,"conflicting_legacy_outcome"): runtime.migrate_legacy()

    def test_pending_legacy_blocks_normal_runtime_even_with_database(self):
        (self.state/"counters.json").write_text(json.dumps({"prompts":20,"provider_attempts":160}))
        with self.assertRaisesRegex(RuntimeError,"legacy_migration_required"): ServiceRuntime(self.profile)
        migration=ServiceRuntime(self.profile,allow_pending_legacy=True); migration.migrate_legacy()
        self.assertEqual(ServiceRuntime(self.profile).counts(),{"prompts":20,"provider_attempts":160})

    def test_credential_file_works_without_inherited_environment(self):
        shared = Path(self.tmp.name) / "shared.env"
        shared.write_text("JEV_API=placeholder-value\n", encoding="utf-8"); shared.chmod(0o600)
        profile = replace(self.profile, provider_enabled=True, credential_env="TYPESAFE_API_KEY",
                          credential_file=shared)
        prior = __import__("os").environ.pop("TYPESAFE_API_KEY", None)
        try:
            self.assertEqual(credential(profile), "placeholder-value")
        finally:
            if prior is not None:
                __import__("os").environ["TYPESAFE_API_KEY"] = prior

if __name__=="__main__": unittest.main()
