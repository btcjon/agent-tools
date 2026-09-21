from test_service import ServiceTests
from jev_skill_advisor.retrieval import retrieve


class RetrievalTests(ServiceTests):
    def test_local_retrieval_bounds_candidates_and_keeps_target(self):
        result=retrieve(self.profile,"use alpha procedure","harness=codex",limit=1)
        self.assertEqual(result["candidate_ids"],["warehouse:alpha"])
        self.assertEqual(result["ranks"][0]["rank"],1)

    def test_service_sends_only_retrieved_candidates(self):
        result=self.suggest()
        self.assertLessEqual(result["retrieval"]["retrieved_count"],12)
        sent=" ".join(str(call) for call in self.runtime.calls)
        self.assertIn("warehouse:alpha",sent)
