from __future__ import annotations
from copy import deepcopy
import hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

from jev_skill_advisor.client import AdvisorError
from jev_skill_advisor.exposure import (
    MODEL, Capability, Registry, fits, rank_choice_confirm_envelope,
    rank_choice_scan, rank_choice_shortlist_envelope, scan,
)
from jev_skill_advisor.profile import load_profile
from jev_skill_advisor.runtime import CACHE_VERSION, ServiceRuntime, iso
from jev_skill_advisor.retrieval import retrieve
from jev_skill_advisor.service import SkillAdvisorService


def skill(tmp, name, body=None, *, disclose=True):
    path = tmp / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body or f"---\nname: {name}\ndescription: {name} procedure\n---\n# {name}\nBODY_{name}\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return Capability(f"warehouse:{name}", "skill", f"{name} procedure", source=str(path),
                      source_hash=digest, policy_hash="p", disclose=disclose), path, digest


def choice_response(payload, choice, confidence=0.4, cache_hit=False):
    options = list(payload["questions"]["winner"]["criteria"])
    probs = {opt: 0.0 for opt in options}
    remainder = round((1.0 - 0.7) / max(1, len(options) - 1), 4) if len(options) > 1 else 0
    for opt in options:
        probs[opt] = 0.7 if opt == choice else remainder
    leftover = round(1.0 - sum(probs.values()), 10)
    probs[choice] = round(probs[choice] + leftover, 10)
    return {"model": MODEL, "usage": {"input_tokens": 3},
            "answers": {"winner": {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": probs}},
            "_cache_hit": cache_hit}


class RankChoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.alpha, self.alpha_path, self.alpha_hash = skill(self.root, "alpha")
        self.beta, self.beta_path, self.beta_hash = skill(self.root, "beta")
        self.registry = Registry([self.alpha, self.beta])

    def test_low_confidence_named_choice_is_accepted(self):
        calls = []
        def evaluator(payload, timeout):
            calls.append(payload)
            stage = payload["_cache_identity"]["stage"]
            choice = "A" if stage == "shortlist" else "skill"
            return choice_response(payload, choice, confidence=0.21)
        result = rank_choice_scan(self.registry, "use alpha procedure", "", evaluator)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["selected"], ["warehouse:alpha"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["choices"], ["A", "skill"])
        self.assertEqual(result["stages"][0]["confidence"], 0.21)
        self.assertLessEqual(result["attempts"], 2)
        self.assertTrue(all(fits(call) for call in calls))
        self.assertEqual(calls[0]["_cache_identity"]["selection_contract"]["version"], 3)
        self.assertNotIn("noul", json.dumps(calls[0]["questions"]))
        self.assertIn("choose none", calls[0]["questions"]["winner"]["instructions"].lower())
        self.assertIn("applicability_evidence", calls[0]["state"]["candidates"][0])
        self.assertLessEqual(len(calls[0]["state"]["candidates"][0]["applicability_evidence"].encode()), 240)
        self.assertEqual(result["stages"][0]["option_ids"]["A"], "warehouse:alpha")
        self.assertIn("payload_hash", result["stages"][0])
        self.assertIn("contract_hash", result["stages"][0])
        self.assertEqual(len(result["selected"]), 1)

    def test_representative_twelve_card_shortlist_fits_wire_budget(self):
        entries = []
        for index in range(12):
            name = f"representative-skill-{index:02d}"
            body = ("## When to use\nUse this procedure for a representative scoped workflow with "
                    "clear product and harness boundaries.\n\n## Procedure\nFollow the verified steps.\n")
            entry, _, _ = skill(self.root, name, body)
            entry = Capability(entry.id, entry.kind,
                               (f"Procedure for representative workflow {index}; use for bounded operational tasks. "
                                + "Detailed routing metadata. " * 30),
                               source=entry.source, source_hash=entry.source_hash,
                               policy_hash=entry.policy_hash, disclose=True)
            entries.append(entry)
        payload = rank_choice_shortlist_envelope("Choose the best procedure for this workflow", "", entries)
        self.assertEqual(len(payload["state"]["candidates"]), 12)
        self.assertTrue(fits(payload))
        self.assertTrue(all("source_hash" not in card for card in payload["state"]["candidates"]))
        self.assertTrue(all(len(card["description"].encode()) <= 240 for card in payload["state"]["candidates"]))
        self.assertEqual(len(payload["_cache_identity"]["capabilities"]), 12)

    def test_shortlist_none_and_confirm_none_terminate(self):
        def shortlist_none(payload, timeout):
            return choice_response(payload, "none", confidence=0.99)
        none = rank_choice_scan(self.registry, "translate hello", "", shortlist_none)
        self.assertEqual(none["reason"], "rank_choice_none")
        self.assertEqual(none["selected"], [])
        self.assertEqual(none["attempts"], 1)
        sequence = iter(["A", "none"])
        def confirm_none(payload, timeout):
            return choice_response(payload, next(sequence), confidence=0.5)
        confirmed = rank_choice_scan(self.registry, "use alpha procedure", "", confirm_none)
        self.assertEqual(confirmed["reason"], "rank_choice_confirm_none")
        self.assertEqual(confirmed["selected"], [])
        self.assertEqual(confirmed["attempts"], 2)

    def test_malformed_choice_and_distribution_fail_closed(self):
        def bad_choice(payload, timeout):
            value = choice_response(payload, "A")
            value["answers"]["winner"]["choice"] = "Z"
            return value
        malformed = rank_choice_scan(self.registry, "use alpha procedure", "", bad_choice)
        self.assertEqual(malformed["status"], "incomplete")
        self.assertEqual(malformed["selected"], [])
        def bad_dist(payload, timeout):
            value = choice_response(payload, "A")
            value["answers"]["winner"]["probabilities"]["A"] = 0.4
            return value
        dist = rank_choice_scan(self.registry, "use alpha procedure", "", bad_dist)
        self.assertEqual(dist["reason"], "rank_choice_malformed")
        self.assertEqual(dist["selected"], [])

    def test_privacy_tamper_and_oversize_are_rejected(self):
        secret, path, digest = skill(self.root, "secret", "## Procedure\nAuthorization: Bearer SECRET\n")
        registry = Registry([secret])
        result = rank_choice_scan(registry, "secret procedure", "", lambda *args: (_ for _ in ()).throw(AssertionError("called")))
        self.assertEqual(result["reason"], "none")
        self.assertEqual(result["attempts"], 0)
        stale = Capability(self.alpha.id, "skill", self.alpha.description, source=self.alpha.source,
                           source_hash="0"*64, policy_hash="p", disclose=True)
        with self.assertRaises(ValueError):
            rank_choice_shortlist_envelope("task", "", [stale])
        huge = skill(self.root, "huge", "# Huge\n" + ("x"*20000))[0]
        payload = rank_choice_confirm_envelope("task", "", huge)
        self.assertLessEqual(len(payload["state"]["candidates"][0]["scope_excerpt"].encode()), 1200)
        self.assertTrue(fits(payload))

    def test_source_loss_after_shortlist_preserves_call_accounting(self):
        calls = 0
        def evaluator(payload, timeout):
            nonlocal calls
            calls += 1
            response = choice_response(payload, "A")
            self.alpha_path.unlink()
            return response
        result = rank_choice_scan(self.registry, "use alpha procedure", "", evaluator)
        self.assertEqual(result["reason"], "source_unavailable")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["provider_attempts"], 1)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["cache_hits"], 0)
        self.assertEqual(result["selected"], [])
        self.assertEqual(calls, 1)

    def test_v1_scan_is_retained(self):
        def evaluator(payload, timeout):
            return {"model": MODEL, "usage": {"input_tokens": 1},
                    "answers": {key: {"type": "noul", "noul": 0.95 if i == 0 else 0.1} for i, key in enumerate(payload["questions"])}}
        result = scan(self.registry, "use alpha procedure", "", evaluator, max_optional=3)
        self.assertEqual(result["status"], "complete")
        self.assertIn("warehouse:alpha", result["selected"])


class RankChoiceServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); warehouse = root / "warehouse"; state = root / "state"
        rows = []
        for name in ("alpha", "beta"):
            path = warehouse / name / "SKILL.md"; path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: {name} procedure\n---\n# {name}\nBODY_{name}\n")
            rows.append({"stable_id": f"warehouse:{name}", "name": name, "description": f"{name} procedure",
                         "relative_path": name, "content_hash": hashlib.sha256(path.read_bytes()).hexdigest()})
        catalog = root / "catalog.json"; catalog.write_text(json.dumps({"entries": rows}))
        self.config = root / "config.json"
        self.config.write_text(json.dumps({"config_version": 1, "profile_id": "rank", "harness": "test",
            "warehouse_root": str(warehouse), "catalog_path": str(catalog), "state_dir": str(state),
            "mode": "advisory", "provider_enabled": True, "read_enabled": True,
            "eligible_ids": ["warehouse:alpha", "warehouse:beta"], "read_allowlist": ["warehouse:alpha"],
            "credential_env": "TEST_JEV_KEY"}))
        self.profile = load_profile(self.config); ServiceRuntime(self.profile, initialize=True)

    def service(self, evaluate_fn=None):
        runtime = ServiceRuntime(self.profile, evaluate_fn=evaluate_fn or (lambda *args: None), initialize=True)
        runtime.key = "fake"
        class Adapter(runtime.__class__):
            def evaluator(inner, payload, timeout):
                inner.calls = getattr(inner, "calls", [])
                inner.calls.append(deepcopy(payload))
                stage = payload["_cache_identity"]["stage"]
                return choice_response(payload, "A" if stage == "shortlist" else "skill", confidence=0.3)
        adapter = Adapter(self.profile, evaluate_fn=evaluate_fn or (lambda *args: None), initialize=True)
        adapter.key = "fake"
        adapter.calls = []
        return SkillAdvisorService(self.profile, adapter), adapter

    def test_service_emits_one_skill_and_records_stages(self):
        service, runtime = self.service()
        result = service.suggest({"protocol_version": 1, "request_id": "r1", "session_id": "s1", "task": "use alpha procedure"})
        self.assertEqual(result["status"], "suggested")
        self.assertEqual([row["id"] for row in result["selected"]], ["warehouse:alpha"])
        self.assertEqual(len(result["selected"]), 1)
        self.assertEqual(result["telemetry"]["evaluations"], 2)
        self.assertEqual(result["telemetry"]["choices"], ["A", "skill"])
        self.assertEqual(runtime.calls[0]["_cache_identity"]["selection_contract"]["version"], 3)

    def test_none_receipt_never_authorizes_skill_read(self):
        class NoneRuntime(ServiceRuntime):
            choices = ["none"]
            def evaluator(inner, payload, timeout):
                return choice_response(payload, inner.choices.pop(0), confidence=0.9)
        for request_id, choices in (("shortlist-none", ["none"]), ("confirm-none", ["A", "none"])):
            runtime = NoneRuntime(self.profile, evaluate_fn=lambda *args: None, initialize=True); runtime.key = "fake"
            runtime.choices = list(choices)
            service = SkillAdvisorService(self.profile, runtime)
            result = service.suggest({"protocol_version": 1, "request_id": request_id, "session_id": "s",
                                      "task": "use alpha procedure"})
            self.assertEqual(result["status"], "none")
            self.assertEqual(result["candidates"], [])
            receipt = runtime.load_receipt(result["receipt_id"])
            self.assertEqual(receipt["allowed_ids"], [])
            self.assertEqual(receipt["selected_ids"], [])
            self.assertEqual(receipt["candidate_ids"], [])
            denied = service.read({"protocol_version": 1, "session_id": "s", "receipt_id": result["receipt_id"],
                                   "skill_id": "warehouse:alpha", "expected_content_hash": self.profile.entries["warehouse:alpha"].source_hash})
            self.assertEqual(denied["status"], "denied")
            also = service.read({"protocol_version": 1, "session_id": "s", "receipt_id": result["receipt_id"],
                                 "skill_id": "warehouse:beta", "expected_content_hash": self.profile.entries["warehouse:beta"].source_hash})
            self.assertEqual(also["status"], "denied")

    def test_explicit_is_zero_provider_and_shadow_reads_nothing(self):
        service, runtime = self.service()
        result = service.suggest({"protocol_version": 1, "request_id": "r2", "session_id": "s1",
                                  "task": "x", "explicit_skills": ["alpha"]})
        self.assertEqual(result["status"], "explicit_selection")
        self.assertEqual(runtime.calls, [])
        raw = json.loads(self.config.read_text()); raw["mode"] = "shadow"; self.config.write_text(json.dumps(raw))
        profile = load_profile(self.config); runtime = ServiceRuntime(profile, initialize=True); runtime.key = "fake"
        shadow = SkillAdvisorService(profile, runtime)
        shadowed = shadow.suggest({"protocol_version": 1, "request_id": "r3", "session_id": "s",
                                   "task": "use alpha", "explicit_skills": ["alpha"]})
        self.assertEqual(shadowed["status"], "shadow")
        denied = shadow.read({"protocol_version": 1, "session_id": "s", "receipt_id": shadowed["receipt_id"],
                              "skill_id": "warehouse:alpha", "expected_content_hash": profile.entries["warehouse:alpha"].source_hash})
        self.assertEqual(denied["status"], "denied")

    def test_cache_identity_isolates_contract_versions(self):
        calls = []
        class Cached(ServiceRuntime):
            def evaluator(inner, payload, timeout):
                calls.append(deepcopy(payload))
                key = hashlib.sha256(json.dumps({"version": "service-v1", "profile": inner.profile.profile_id,
                                                 "payload": payload}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                inner.keys = getattr(inner, "keys", [])
                if key in inner.keys:
                    response = choice_response(payload, "A" if payload["_cache_identity"]["stage"] == "shortlist" else "skill",
                                               confidence=0.5, cache_hit=True)
                    return response
                inner.keys.append(key)
                return choice_response(payload, "A" if payload["_cache_identity"]["stage"] == "shortlist" else "skill",
                                       confidence=0.5, cache_hit=False)
        runtime = Cached(self.profile, evaluate_fn=lambda *args: None, initialize=True); runtime.key = "fake"; runtime.keys = []
        service = SkillAdvisorService(self.profile, runtime)
        first = service.suggest({"protocol_version": 1, "request_id": "c1", "session_id": "s", "task": "use alpha procedure"})
        second = service.suggest({"protocol_version": 1, "request_id": "c2", "session_id": "s", "task": "use alpha procedure"})
        self.assertEqual(first["status"], "suggested")
        self.assertEqual(second["telemetry"]["cache_hits"], 2)
        v1 = deepcopy(calls[0]); v2 = deepcopy(calls[0])
        v2["_cache_identity"]["selection_contract"] = {**v1["_cache_identity"]["selection_contract"], "version": 1}
        key = lambda payload: hashlib.sha256(json.dumps({"version": "service-v1", "profile": self.profile.profile_id,
                                                         "payload": payload}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertNotEqual(key(v1), key(v2))

    def test_subprocess_parity_with_inline_scan(self):
        class Inline(ServiceRuntime):
            def evaluator(self, payload, timeout):
                stage = payload["_cache_identity"]["stage"]
                return choice_response(payload, "A" if stage == "shortlist" else "skill", confidence=0.4)
        inline_runtime = Inline(self.profile, evaluate_fn=lambda *args: None, initialize=True); inline_runtime.key = "fake"
        inline = SkillAdvisorService(self.profile, inline_runtime).suggest(
            {"protocol_version": 1, "request_id": "i1", "session_id": "s", "task": "use alpha procedure"})
        # Seed exact v2 cache entries so the spawned CLI exercises both implicit
        # stages without making a provider call.
        task = "use alpha procedure"
        retrieval = retrieve(self.profile, task, "", None, limit=12)
        registry = self.profile.registry(retrieval["candidate_ids"], implicit_only=True)
        entries = registry.eligible()
        shortlist = rank_choice_shortlist_envelope(task, "", entries)
        provisional = entries[0]
        confirm = rank_choice_confirm_envelope(task, "", provisional)
        import sqlite3
        with sqlite3.connect(self.profile.state_dir / "advisor.sqlite3") as db:
            for payload, choice in ((shortlist, "A"), (confirm, "skill")):
                material = {"version": CACHE_VERSION, "profile": self.profile.profile_id, "payload": payload}
                key = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                db.execute("INSERT OR REPLACE INTO response_cache VALUES(?,?,?)",
                           (key, iso(), json.dumps(choice_response(payload, choice))))
        env = os.environ.copy(); env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src"); env["TEST_JEV_KEY"] = "fake"
        proc = subprocess.run([sys.executable, "-m", "jev_skill_advisor.service_cli", "--config", str(self.config), "suggest"],
                              input=json.dumps({"protocol_version": 1, "request_id": "cli", "session_id": "s",
                                                "task": task}).encode(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        cli = json.loads(proc.stdout)
        self.assertEqual(cli["status"], "suggested")
        self.assertEqual(cli["telemetry"]["cache_hits"], 2)
        self.assertEqual(cli["telemetry"]["provider_attempts"], 0)
        self.assertEqual(inline["status"], "suggested")
        self.assertEqual(len(inline["selected"]), 1)


if __name__ == "__main__":
    unittest.main()
