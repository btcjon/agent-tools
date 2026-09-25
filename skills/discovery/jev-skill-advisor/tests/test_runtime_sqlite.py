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
        runtimes=[ServiceRuntime(self.profile, operation_id="shared-request") for _ in range(30)]
        with ThreadPoolExecutor(max_workers=12) as pool: results=list(pool.map(lambda runtime: runtime.reserve_prompt(),runtimes))
        self.assertEqual(sum(results),20); self.assertEqual(ServiceRuntime(self.profile).counts()["prompts"],20)

    def test_anonymous_runtime_keeps_one_window(self):
        limited = replace(self.profile, prompt_limit=2)
        runtime = ServiceRuntime(limited)
        self.assertTrue(runtime.reserve_prompt())
        self.assertTrue(runtime.reserve_prompt())
        self.assertFalse(runtime.reserve_prompt())
        self.assertEqual(runtime.counts()["prompts"], 2)

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

    def test_provider_window_survives_a_full_lifetime_counter(self):
        from jev_skill_advisor.client import AdvisorError
        db=self.state/"advisor.sqlite3"
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE budget_domains SET consumed=160 WHERE name='provider_attempts'")
        calls=[]
        def fake(payload, key, timeout):
            calls.append(payload); return {"answers":{}, "usage":{"input_tokens":1}}, {}
        runtime=ServiceRuntime(replace(self.profile, provider_enabled=True, provider_attempt_limit=2), evaluate_fn=fake, operation_id="req-1")
        runtime.key="test"
        self.assertEqual(runtime.evaluator({"n": 1}, 1)["_cache_hit"], False)
        self.assertEqual(runtime.counts("req-1")["provider_attempts"], 161)
        self.assertEqual(runtime.counts("req-1")["request_provider_attempts"], 1)
        runtime.evaluator({"n": 2}, 1)
        with self.assertRaisesRegex(AdvisorError, "local_provider_attempt_budget"):
            runtime.evaluator({"n": 3}, 1)
        self.assertEqual(len(calls), 2)
        fresh=ServiceRuntime(replace(self.profile, provider_enabled=True, provider_attempt_limit=2), evaluate_fn=fake, operation_id="req-2")
        fresh.key="test"
        self.assertEqual(fresh.evaluator({"n": 4}, 1)["_cache_hit"], False)
        self.assertEqual(fresh.counts()["provider_attempts"], 163)

    def test_anonymous_windows_do_not_share_a_prompt_budget(self):
        from concurrent.futures import as_completed
        limited = replace(self.profile, prompt_limit=1)
        def once(_):
            return ServiceRuntime(limited).reserve_prompt()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(once, range(12)))
        self.assertEqual(results, [True] * 12)
        self.assertEqual(ServiceRuntime(self.profile).counts()["prompts"], 12)

    def test_one_request_cannot_overspend_prompts(self):
        limited = replace(self.profile, prompt_limit=2)
        runtime = ServiceRuntime(limited, operation_id="same-request")
        assert runtime.reserve_prompt() and runtime.reserve_prompt()
        self.assertFalse(runtime.reserve_prompt())
        self.assertEqual(runtime.counts("same-request")["prompts"], 2)

    def test_startup_migrates_v1_without_resetting_provider_history(self):
        db=self.state/"advisor.sqlite3"
        with sqlite3.connect(db) as connection:
            connection.execute("DELETE FROM schema_versions WHERE version=2")
            connection.execute("DROP TABLE request_windows")
            connection.execute("UPDATE budget_domains SET consumed=160 WHERE name='provider_attempts'")
        runtime=ServiceRuntime(self.profile)
        self.assertEqual(runtime.counts()["provider_attempts"], 160)
        with sqlite3.connect(db) as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT version FROM schema_versions ORDER BY version")], [1, 2])

    def test_startup_migrates_early_v2_window_without_losing_counts(self):
        from jev_skill_advisor.client import AdvisorError
        db = self.state / "advisor.sqlite3"
        with sqlite3.connect(db) as connection:
            connection.execute("DROP TABLE request_windows")
            connection.execute("CREATE TABLE request_windows(operation_id TEXT PRIMARY KEY, provider_attempts INTEGER NOT NULL, created_at TEXT NOT NULL)")
            connection.execute("INSERT INTO request_windows VALUES('old-request', 3, '2026-09-20T00:00:00Z')")
            connection.execute("UPDATE budget_domains SET consumed=160 WHERE name='provider_attempts'")
            connection.execute("UPDATE budget_domains SET consumed=20 WHERE name='prompts'")
        runtime = ServiceRuntime(self.profile)
        limited = replace(self.profile, prompt_limit=2, provider_attempt_limit=2)
        request = ServiceRuntime(limited, operation_id="new-request", evaluation_id="migration-eval", evaluation_limit=2)
        self.assertTrue(request.reserve_prompt())
        self.assertTrue(request.reserve_prompt())
        self.assertFalse(request.reserve_prompt())
        self.assertTrue(request._reserve_provider_attempt())
        self.assertTrue(request._reserve_provider_attempt())
        self.assertFalse(request._reserve_provider_attempt())
        another = ServiceRuntime(limited, operation_id="another-request", evaluation_id="migration-eval", evaluation_limit=2)
        with self.assertRaisesRegex(AdvisorError, "evaluation_provider_attempt_budget"):
            another._reserve_provider_attempt()
        with sqlite3.connect(db) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(request_windows)")}
            self.assertIn("prompts", columns)
            self.assertEqual(connection.execute("SELECT provider_attempts, prompts FROM request_windows WHERE operation_id='old-request'").fetchone(), (3, 0))
            self.assertEqual(connection.execute("SELECT provider_attempts, prompts FROM request_windows WHERE operation_id='new-request'").fetchone(), (2, 2))
            self.assertEqual(connection.execute("SELECT provider_attempts, prompts FROM request_windows WHERE operation_id='eval-migration-eval'").fetchone(), (2, 0))
            for prompts, created_at in connection.execute("SELECT prompts, created_at FROM request_windows"):
                self.assertIsInstance(prompts, int)
                self.assertRegex(created_at, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(runtime.counts()["provider_attempts"], 162)
        self.assertEqual(runtime.counts()["prompts"], 22)

    def test_evaluation_allowance_is_atomic_and_cache_hits_are_free(self):
        from jev_skill_advisor.client import AdvisorError
        enabled = replace(self.profile, provider_enabled=True)
        calls = []
        def fake(payload, key, timeout):
            calls.append(payload)
            return {"answers": {}, "usage": {"input_tokens": 1}}, {}
        runtimes = [ServiceRuntime(enabled, evaluate_fn=fake, operation_id=f"eval-request-{n}",
                                   evaluation_id="shared-eval", evaluation_limit=3) for n in range(12)]
        for runtime in runtimes:
            runtime.key = "test"
        def call(item):
            index, runtime = item
            try:
                return runtime.evaluator({"n": index}, 1)["_cache_hit"]
            except AdvisorError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(call, enumerate(runtimes)))
        self.assertEqual(results.count(False), 3)
        self.assertEqual(results.count("evaluation_provider_attempt_budget"), 9)
        self.assertEqual(len(calls), 3)
        self.assertEqual(ServiceRuntime(self.profile).counts()["provider_attempts"], 3)
        cached_index = results.index(False)
        self.assertTrue(runtimes[cached_index].evaluator({"n": cached_index}, 1)["_cache_hit"])
        self.assertEqual(ServiceRuntime(self.profile).counts()["provider_attempts"], 3)

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
