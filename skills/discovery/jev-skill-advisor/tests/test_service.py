from __future__ import annotations
from copy import deepcopy
import hashlib, json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

from jev_skill_advisor.exposure import MODEL
from jev_skill_advisor.profile import load_profile, ProfileError
from jev_skill_advisor.protocol import ProtocolError, validate_suggest
from jev_skill_advisor.runtime import ServiceRuntime
from jev_skill_advisor.service import SkillAdvisorService


class FakeRuntime(ServiceRuntime):
    def __init__(self, profile):
        super().__init__(profile, evaluate_fn=lambda *args: None)
        self.key = "fake"
        self.calls = []

    def evaluator(self, payload, timeout):
        self.calls.append(deepcopy(payload))
        questions = payload["questions"]
        if "winner" in questions:
            options = list(questions["winner"]["criteria"])
            if any(key.startswith("fit_") for key in questions):
                labels = [key[4:] for key in questions if key.startswith("fit_")]
                choice = labels[0]
                probs = {label: 0.0 for label in [*labels, "none"]}; probs[choice] = .95; probs["none"] = .05
                answers = {"winner": {"type": "choice", "choice": choice, "confidence": .95, "probabilities": probs}}
                answers.update({f"fit_{label}": {"type": "noul", "noul": .95 if i == 0 else .1} for i, label in enumerate(labels)})
            else:
                named = [opt for opt in options if opt != "none"]
                choice = "skill" if "skill" in options else (named[0] if named else "none")
                probs = {opt: 0.0 for opt in options}; probs[choice] = .95
                if "none" in options and choice != "none":
                    probs["none"] = .05
                elif named and choice != named[0]:
                    probs[named[0]] = .05
                answers = {"winner": {"type": "choice", "choice": choice, "confidence": .95, "probabilities": probs}}
        else:
            answers = {key: {"type": "noul", "noul": .95 if i == 0 else .1} for i, key in enumerate(questions)}
        return {"model": MODEL, "usage": {"input_tokens": 5}, "answers": answers, "_cache_hit": False}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); self.warehouse = root / "warehouse"; self.state = root / "state"
        rows = []
        for name in ("alpha", "beta"):
            path = self.warehouse / name / "SKILL.md"; path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: {name} procedure\n---\n# {name}\nBODY_{name}\n", encoding="utf-8")
            rows.append({"stable_id": f"warehouse:{name}", "name": name, "description": f"{name} procedure",
                         "relative_path": name, "content_hash": hashlib.sha256(path.read_bytes()).hexdigest()})
        self.catalog = root / "catalog.json"; self.catalog.write_text(json.dumps({"entries": rows}), encoding="utf-8")
        self.config = root / "config.json"
        self.config.write_text(json.dumps({"config_version": 1, "profile_id": "test", "harness": "test",
            "warehouse_root": str(self.warehouse), "catalog_path": str(self.catalog), "state_dir": str(self.state),
            "mode": "advisory", "provider_enabled": True, "read_enabled": True,
            "eligible_ids": ["warehouse:alpha", "warehouse:beta"], "read_allowlist": ["warehouse:alpha"],
            "credential_env": "TEST_JEV_KEY"}), encoding="utf-8")
        self.profile = load_profile(self.config); ServiceRuntime(self.profile, initialize=True); self.runtime = FakeRuntime(self.profile); self.service = SkillAdvisorService(self.profile, self.runtime)

    def suggest(self, **overrides):
        value = {"protocol_version": 1, "request_id": "r1", "session_id": "s1", "task": "use alpha procedure"}
        value.update(overrides); return self.service.suggest(value)

    def test_suggest_read_and_outcome(self):
        result = self.suggest()
        self.assertEqual(result["status"], "suggested"); self.assertEqual(result["selected"][0]["id"], "warehouse:alpha")
        read = self.service.read({"protocol_version": 1, "session_id": "s1", "receipt_id": result["receipt_id"],
            "skill_id": "warehouse:alpha", "expected_content_hash": result["selected"][0]["content_hash"]})
        self.assertEqual(read["status"], "read"); self.assertIn("BODY_alpha", read["body"])
        event = {"protocol_version": 1, "session_id": "s1", "receipt_id": result["receipt_id"], "event_id": "e1",
                 "skill_id": "warehouse:alpha", "outcome": "applied", "evidence": "self_reported"}
        self.assertEqual(self.service.report_outcome(event)["status"], "recorded")
        self.assertEqual(self.service.report_outcome(event)["status"], "duplicate")
        stats = self.runtime.stats()
        self.assertEqual(stats["statuses"]["suggested"], 1)
        self.assertEqual(stats["outcomes"]["applied"], 1)
        self.assertEqual(stats["records"]["receipt_reads"], 1)
        self.assertNotIn("use alpha procedure", json.dumps(stats))

    def test_lifetime_prompt_count_does_not_disable_later_requests(self):
        with sqlite3.connect(self.state / "advisor.sqlite3") as connection:
            connection.execute("UPDATE budget_domains SET consumed=20 WHERE name='prompts'")
        for number in range(2):
            result = self.suggest(request_id=f"after-limit-{number}")
            self.assertEqual(result["status"], "suggested")
        self.assertEqual(self.runtime.counts()["prompts"], 22)

    def test_explicit_is_zero_call_and_availability_only_narrows(self):
        result = self.suggest(explicit_skills=["alpha"], available_ids=["warehouse:alpha"])
        self.assertEqual(result["status"], "explicit_selection"); self.assertEqual(self.runtime.calls, [])
        unavailable = self.suggest(request_id="r2", explicit_skills=["beta"], available_ids=["warehouse:alpha"])
        self.assertEqual(unavailable["status"], "unavailable")

    def test_shadow_is_zero_call_and_never_authorizes_reads(self):
        raw = json.loads(self.config.read_text())
        raw["mode"] = "shadow"
        self.config.write_text(json.dumps(raw))
        profile = load_profile(self.config)
        runtime = FakeRuntime(profile)
        service = SkillAdvisorService(profile, runtime)
        result = service.suggest({"protocol_version": 1, "request_id": "shadow", "session_id": "s",
                                  "task": "use alpha", "explicit_skills": ["alpha"]})
        self.assertEqual(result["status"], "shadow")
        self.assertEqual(result["selected"], [])
        self.assertEqual(runtime.calls, [])
        read = service.read({"protocol_version": 1, "session_id": "s", "receipt_id": result["receipt_id"],
                             "skill_id": "warehouse:alpha",
                             "expected_content_hash": profile.entries["warehouse:alpha"].source_hash})
        self.assertEqual(read["status"], "denied")
        self.assertEqual(read["reason"], "profile_shadow")

    def test_emergency_stop_overrides_pinned_profile_for_selection_and_read(self):
        stop = Path(self.tmp.name) / "EMERGENCY_STOP"
        raw = json.loads(self.config.read_text())
        raw["emergency_stop_file"] = str(stop)
        self.config.write_text(json.dumps(raw))
        profile = load_profile(self.config); runtime = FakeRuntime(profile)
        service = SkillAdvisorService(profile, runtime)
        before = service.suggest({"protocol_version": 1, "request_id": "before", "session_id": "pinned", "task": "alpha"})
        self.assertEqual(before["status"], "suggested")
        stop.touch()
        after = service.suggest({"protocol_version": 1, "request_id": "after", "session_id": "pinned", "task": "alpha"})
        self.assertEqual((after["status"], after["reason"]), ("off", "emergency_stop"))
        read = service.read({"protocol_version": 1, "session_id": "pinned", "receipt_id": before["receipt_id"],
                             "skill_id": "warehouse:alpha", "expected_content_hash": before["selected"][0]["content_hash"]})
        self.assertEqual((read["status"], read["reason"]), ("denied", "emergency_stop"))

    def test_denied_read_and_foreign_receipt(self):
        result = self.suggest()
        denied = self.service.read({"protocol_version": 1, "session_id": "s1", "receipt_id": result["receipt_id"],
            "skill_id": "warehouse:beta", "expected_content_hash": self.profile.entries["warehouse:beta"].source_hash})
        self.assertEqual(denied["status"], "denied")
        foreign = self.service.read({"protocol_version": 1, "session_id": "other", "receipt_id": result["receipt_id"],
            "skill_id": "warehouse:alpha", "expected_content_hash": self.profile.entries["warehouse:alpha"].source_hash})
        self.assertEqual(foreign["status"], "denied")

    def test_protected_input_is_zero_call(self):
        result = self.suggest(task="Authorization: Bearer secret")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "protected_input")
        self.assertEqual(self.runtime.calls, [])

    def test_policy_change_excludes_without_restart(self):
        sidecar = self.warehouse / "alpha" / "agents" / "openai.yaml"
        sidecar.parent.mkdir()
        sidecar.write_text("allow_implicit_invocation: false\n", encoding="utf-8")
        result = self.suggest()
        self.assertNotIn("warehouse:alpha", [card["id"] for card in result["selected"]])

    def test_protected_skill_excerpt_is_zero_call(self):
        path = self.warehouse / "alpha" / "SKILL.md"
        path.write_text(path.read_text() + "\nAuthorization: Bearer SECRET_SENTINEL\n")
        row = json.loads(self.catalog.read_text())
        row["entries"][0]["content_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.catalog.write_text(json.dumps(row))
        profile = load_profile(self.config); runtime = FakeRuntime(profile)
        result = SkillAdvisorService(profile, runtime).suggest({"protocol_version": 1, "request_id": "x", "session_id": "s", "task": "alpha"})
        self.assertNotIn("warehouse:alpha", [card["id"] for card in result["selected"]])
        self.assertTrue(all("SECRET_SENTINEL" not in json.dumps(call) for call in runtime.calls))

    def test_protocol_unknown_fields_and_profile_traversal(self):
        with self.assertRaises(ProtocolError):
            validate_suggest({"protocol_version": 1, "request_id": "r", "session_id": "s", "task": "x", "extra": 1})
        catalog = json.loads(self.catalog.read_text()); catalog["entries"][0]["relative_path"] = "../escape"
        self.catalog.write_text(json.dumps(catalog))
        with self.assertRaises(ProfileError): load_profile(self.config)

    def test_available_id_inventory_limit(self):
        base={"protocol_version":1,"request_id":"r","session_id":"s","task":"x"}
        self.assertEqual(len(validate_suggest({**base,"available_ids":[f"warehouse:s{i}" for i in range(1024)]})["available_ids"]),1024)
        with self.assertRaises(ProtocolError): validate_suggest({**base,"available_ids":[f"warehouse:s{i}" for i in range(1025)]})

    def test_cli_explicit_parity(self):
        payload = {"protocol_version": 1, "request_id": "cli", "session_id": "s", "task": "x", "explicit_skills": ["alpha"]}
        env = os.environ.copy(); env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        proc = subprocess.run([sys.executable, "-m", "jev_skill_advisor.service_cli", "--config", str(self.config), "suggest"],
                              input=json.dumps(payload).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode()); result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "explicit_selection"); self.assertEqual(result["selected"][0]["id"], "warehouse:alpha")

    def test_spawn_worker_receives_retrieved_ids(self):
        from unittest.mock import patch
        class Process:
            def __init__(self,target,args,daemon): self.args=args
            def start(self):
                profile,data,operation_id,queue=self.args
                self.data=data; queue.put({"status":"complete","reason":"none","selected":[],"detail_reviewed":[],"attempts":0,"provider_attempts":0,"cache_hits":0,"input_tokens":0,"unknown_usage":0})
            def join(self,*_): pass
            def is_alive(self): return False
        class Context:
            def Queue(self,maxsize):
                import queue
                q=queue.Queue(maxsize); q.close=lambda:None; return q
            def Process(self,*args,**kwargs):
                process=Process(*args,**kwargs); self.process=process; return process
        context=Context()
        with patch("jev_skill_advisor.service.multiprocessing.get_context",return_value=context):
            service=SkillAdvisorService(self.profile)
            service.runtime.key="fake"
            service.suggest({"protocol_version":1,"request_id":"spawn","session_id":"s","task":"alpha"})
        self.assertEqual(context.process.data["available_ids"],["warehouse:alpha"])

    def test_spawn_worker_reconciles_recorded_provider_attempt(self):
        from unittest.mock import patch
        class Process:
            def __init__(self,target,args,daemon): self.args=args
            def start(self):
                profile,data,operation_id,queue=self.args
                child = ServiceRuntime(profile, operation_id=operation_id)
                self.assert_reserved = child._reserve_provider_attempt()
                queue.put({"status":"incomplete","reason":"worker_failure","selected":[],"attempts":0,
                           "provider_attempts":0,"cache_hits":0,"input_tokens":0,"unknown_usage":0})
            def join(self,*_): pass
            def is_alive(self): return False
        class Context:
            def Queue(self,maxsize):
                import queue
                q=queue.Queue(maxsize); q.close=lambda:None; return q
            def Process(self,*args,**kwargs): return Process(*args,**kwargs)
        with patch("jev_skill_advisor.service.multiprocessing.get_context",return_value=Context()):
            service=SkillAdvisorService(self.profile); service.runtime.key="fake"
            result=service.suggest({"protocol_version":1,"request_id":"ledger","session_id":"s","task":"alpha"})
        self.assertEqual(result["telemetry"]["provider_attempts"],1)
        self.assertEqual(result["telemetry"]["unknown_usage"],1)


if __name__ == "__main__": unittest.main()
