import asyncio
import unittest

from worker import METRIC_NAMES, RagasScorer, aggregate_case_results, hit_at_k, with_retries


class _Result:
    value = 0.75
    reason = "fake judge reason"


class _Metric:
    async def ascore(self, **kwargs):
        return _Result()


class _FakeScorer(RagasScorer):
    def _initialize(self):
        return {name: _Metric() for name in METRIC_NAMES}


class EvaluationWorkerUnitTest(unittest.TestCase):
    def test_hit_at_k_boundaries(self) -> None:
        self.assertTrue(hit_at_k(["a", "b"], ["b"], 2))
        self.assertFalse(hit_at_k(["a", "b"], ["b"], 1))
        self.assertFalse(hit_at_k([], ["b"], 5))
        self.assertFalse(hit_at_k(["a"], [], 5))

    def test_aggregate_keeps_metric_coverage(self) -> None:
        rows = [
            {"hit_at_k": True, "scores": {name: 1.0 for name in METRIC_NAMES}},
            {"hit_at_k": False, "scores": {**{name: 0.0 for name in METRIC_NAMES}, "faithfulness": None}},
        ]
        metrics, coverage = aggregate_case_results(rows, 2)
        self.assertEqual(0.5, metrics["hit_at_k"])
        self.assertEqual(1.0, metrics["faithfulness"])
        self.assertEqual({"scored": 1, "total": 2}, coverage["faithfulness"])
        self.assertEqual(0.5, metrics["context_recall"])

    def test_fake_ragas_scores_and_reasons_are_preserved(self) -> None:
        scores, reasons, errors = asyncio.run(_FakeScorer(
            model="fake", embedding_model="fake"
        ).score(question="q", answer="a", reference="r", contexts=["c"]))
        self.assertFalse(errors)
        self.assertEqual(0.75, scores["faithfulness"])
        self.assertEqual("fake judge reason", reasons["context_recall"])

    def test_retry_creates_a_fresh_awaitable(self) -> None:
        calls = 0

        async def run():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("retry")
            return "ok"

        result = asyncio.run(with_retries(run, retries=1))
        self.assertEqual("ok", result)
        self.assertEqual(2, calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
