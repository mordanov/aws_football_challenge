"""Tests for AgentCoreEvaluator: span validation, local checks, judge submission."""

from __future__ import annotations

import json
import pathlib
import unittest
from typing import Any

from tests.offline_support import make_config

_FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "offline" / "advisor_spans.json"


# ── Fake boto3-style AgentCore Evaluations client ─────────────────────────────


class FakeEvaluationClient:
    """Records evaluate calls; no AWS credentials used."""

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        side_effect: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self._side_effect = side_effect

    def evaluate(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._side_effect:
            raise self._side_effect
        return self._result or _default_eval_result()


def _default_eval_result(score: float = 3.0) -> dict[str, Any]:
    return {
        "evaluationResults": [
            {
                "evaluatorId": "evaluator-001",
                "context": {"spanContext": {"sessionId": "run-001", "traceId": "eval-trace-001"}},
                "value": score,
                "explanation": "advice references provided evidence IDs",
            }
        ],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_evaluator(client=None):
    from agent.advisor.offline.evaluation import AgentCoreEvaluator

    cfg = make_config()
    return AgentCoreEvaluator(client or FakeEvaluationClient(), cfg)


def _valid_span(
    *,
    trace_id: str = "aabbccdd00112233aabbccdd00112233",
    span_id: str = "0011223344556677",
    match_id: str = "match-001",
) -> dict[str, Any]:
    """Minimal GenAI span with required attributes."""
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": None,
        "name": "gen_ai.inference",
        "kind": 1,
        "startTimeUnixNano": 1729692000000000000,
        "endTimeUnixNano": 1729692002000000000,
        "attributes": {
            "gen_ai.operation.name": "chat",
            "gen_ai.task.input": "evidence payload",
            "gen_ai.task.output": json.dumps(
                {
                    "settings": {},
                    "style": {},
                    "rationale": "maintain baseline",
                    "evidence_ids": ["claim-shoot-observed"],
                    "uncertainties": ["SHOOT contradiction"],
                    "counter_tests": ["verify shot range"],
                }
            ),
            "football.trace.match_id": match_id,
        },
        "status": {"code": "OK"},
    }


class SpanValidationTest(unittest.TestCase):
    """Raw football JSON records must be rejected; GenAI spans are accepted."""

    def test_raw_football_json_rejected_as_span(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        evaluator = _make_evaluator()
        raw_football_record = {"match_id": "m1", "score": [0, 1], "outcomes": {"SHOOT": 3}}
        with self.assertRaises(OfflineServiceError) as ctx:
            evaluator.evaluate([raw_football_record])
        self.assertEqual(ctx.exception.stage, "validate_spans")

    def test_span_without_gen_ai_operation_name_rejected(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        evaluator = _make_evaluator()
        bad_span = _valid_span()
        del bad_span["attributes"]["gen_ai.operation.name"]
        with self.assertRaises(OfflineServiceError) as ctx:
            evaluator.evaluate([bad_span])
        self.assertEqual(ctx.exception.stage, "validate_spans")

    def test_valid_span_accepted(self):
        evaluator = _make_evaluator()
        # Should not raise
        evaluator.evaluate([_valid_span()])

    def test_empty_span_list_rejected(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        evaluator = _make_evaluator()
        with self.assertRaises(OfflineServiceError) as ctx:
            evaluator.evaluate([])
        self.assertEqual(ctx.exception.stage, "validate_spans")


class SubmissionTest(unittest.TestCase):
    def test_evaluate_calls_service_with_session_spans(self):
        client = FakeEvaluationClient()
        evaluator = _make_evaluator(client)
        evaluator.evaluate([_valid_span()])
        self.assertEqual(len(client.calls), 1)
        self.assertIn("sessionSpans", client.calls[0]["evaluationInput"])

    def test_evaluate_uses_evaluator_id_from_config(self):
        client = FakeEvaluationClient()
        evaluator = _make_evaluator(client)
        evaluator.evaluate([_valid_span()])
        self.assertEqual(client.calls[0]["evaluatorId"], "evaluator-001")

    def test_spans_passed_to_service(self):
        client = FakeEvaluationClient()
        evaluator = _make_evaluator(client)
        spans = [_valid_span()]
        evaluator.evaluate(spans)
        submitted = client.calls[0]["evaluationInput"]["sessionSpans"]
        self.assertEqual(len(submitted), 1)


class ResultParsingTest(unittest.TestCase):
    def test_faithfulness_score_captured(self):
        evaluator = _make_evaluator(FakeEvaluationClient(result=_default_eval_result(3.5)))
        report = evaluator.evaluate([_valid_span()])
        self.assertAlmostEqual(report.scores["faithfulness"], 3.5)

    def test_evaluator_id_captured(self):
        evaluator = _make_evaluator()
        report = evaluator.evaluate([_valid_span()])
        self.assertEqual(report.evaluator_id, "evaluator-001")

    def test_trace_id_captured(self):
        evaluator = _make_evaluator()
        report = evaluator.evaluate([_valid_span()])
        self.assertIsNotNone(report.trace_id)

    def test_status_ok_on_completion(self):
        evaluator = _make_evaluator()
        report = evaluator.evaluate([_valid_span()])
        self.assertEqual(report.status, "ok")

    def test_rubric_version_is_v1(self):
        evaluator = _make_evaluator()
        report = evaluator.evaluate([_valid_span()])
        self.assertEqual(report.rubric_version, "v1")


class LowScoreTest(unittest.TestCase):
    """Low score is a successful evaluation of rejected advice, not a failure."""

    def test_score_below_threshold_is_ok_status_not_failure(self):
        evaluator = _make_evaluator(FakeEvaluationClient(result=_default_eval_result(1.0)))
        report = evaluator.evaluate([_valid_span()])
        self.assertEqual(report.status, "ok")
        self.assertAlmostEqual(report.scores["faithfulness"], 1.0)

    def test_score_zero_is_ok_status(self):
        evaluator = _make_evaluator(FakeEvaluationClient(result=_default_eval_result(0.0)))
        report = evaluator.evaluate([_valid_span()])
        self.assertEqual(report.status, "ok")


class ServiceErrorTest(unittest.TestCase):
    def test_service_exception_raises_offline_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeEvaluationClient(side_effect=PermissionError("access denied"))
        evaluator = _make_evaluator(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            evaluator.evaluate([_valid_span()])
        self.assertEqual(ctx.exception.service, "evaluator")

    def test_missing_faithfulness_score_raises_partial_status(self):
        result_no_faithfulness = {
            "evaluatorId": "evaluator-001",
            "traceId": "eval-trace-001",
            "evaluationResults": [
                {"evaluatorId": "evaluator-001", "explanation": "score not available"}
            ],
            "status": "COMPLETED",
        }
        client = FakeEvaluationClient(result=result_no_faithfulness)
        evaluator = _make_evaluator(client)
        report = evaluator.evaluate([_valid_span()])
        self.assertEqual(report.status, "partial")


class FixtureTest(unittest.TestCase):
    def test_reference_spans_fixture_passes_validation(self):
        data = json.loads(_FIXTURE_PATH.read_text())
        evaluator = _make_evaluator()
        # Fixture spans should pass span validation without raising
        evaluator.evaluate(data["spans"])

    def test_reference_fixture_has_expected_schema(self):
        data = json.loads(_FIXTURE_PATH.read_text())
        self.assertIn("spans", data)
        self.assertIn("rubric_version", data)
        span = data["spans"][0]
        self.assertIn("traceId", span)
        self.assertIn("attributes", span)
        self.assertIn("gen_ai.operation.name", span["attributes"])


# Structural check
def _check_protocol() -> None:
    from agent.advisor.offline.contracts import AdviceEvaluator
    from agent.advisor.offline.evaluation import AgentCoreEvaluator

    _: AdviceEvaluator = AgentCoreEvaluator(FakeEvaluationClient(), make_config())  # type: ignore[type-abstract]


if __name__ == "__main__":
    unittest.main()
