from test_service import ServiceTests
from jev_skill_advisor.context import prepare_context
import pytest
import sqlite3


class ContextTests(ServiceTests):
    def test_prepare_context_delivers_only_selected_body(self):
        result = prepare_context(self.service, task="use alpha procedure", harness="test", session_id="ctx")
        self.assertEqual(result["selected_ids"], ["warehouse:alpha"])
        self.assertEqual(len(result["skills"]), 1)
        self.assertIn("BODY_alpha", result["skills"][0]["body"])
        self.assertNotIn("BODY_beta", result["skills"][0]["body"])

    def test_prepare_context_falls_back_when_body_is_not_readable(self):
        result = prepare_context(self.service, task="use beta procedure", harness="test", session_id="ctx",
                                 explicit_skills=["beta"])
        self.assertEqual(result["fallback"], "native_discovery")
        self.assertEqual(result["skills"], [])

    def test_prepare_context_rejects_malformed_budget(self):
        with pytest.raises(ValueError, match="invalid_prepare_context_budget"):
            prepare_context(self.service, task="x", harness="test", session_id="ctx", max_body_bytes=float("nan"))

    def test_prepare_context_discards_partial_bodies_after_read_failure(self):
        original = self.service.read
        calls = 0
        def failing(value):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise FileNotFoundError("gone")
            return original(value)
        self.service.read = failing
        result = prepare_context(self.service, task="x", harness="test", session_id="ctx",
                                 explicit_skills=["alpha", "beta"])
        self.assertEqual(result["fallback"], "native_discovery")
        self.assertEqual(result["skills"], [])

    def test_prepare_context_falls_back_on_locked_database(self):
        self.service.suggest = lambda value: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked"))
        result = prepare_context(self.service, task="x", harness="test", session_id="ctx")
        self.assertEqual(result["fallback"], "native_discovery")
