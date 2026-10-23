"""Tests for run_offline: stage failures, absent analysis, rejected advice, happy path."""

from __future__ import annotations

import unittest

from tests.offline_support import (
    FakeEvaluator,
    FakeInterpreter,
    FakeMemory,
    make_config,
    make_packet,
    make_workflow_case,
)


class StageFailureTest(unittest.TestCase):
    """Each required-stage failure must produce status=failed, no candidate profile."""

    def test_required_evaluation_failure_cannot_produce_a_proposal(self):
        case = make_workflow_case(fail_stage="evaluate")
        result = case.run()
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.candidate_profile)
        self.assertEqual(case.memory.publish_calls, [])
        self.assertEqual(case.deployment_calls, [])

    def test_retrieve_failure_produces_failed_artifact(self):
        case = make_workflow_case(fail_stage="retrieve")
        result = case.run()
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.candidate_profile)
        self.assertIsNone(result.advice)

    def test_advise_failure_produces_failed_artifact(self):
        case = make_workflow_case(fail_stage="advise")
        result = case.run()
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.candidate_profile)

    def test_analysis_failure_produces_failed_artifact(self):
        case = make_workflow_case(fail_stage="analyze", with_sandbox=True)
        result = case.run()
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.candidate_profile)

    def test_failure_artifact_has_failure_message(self):
        case = make_workflow_case(fail_stage="evaluate")
        result = case.run()
        self.assertIsNotNone(result.failure)
        self.assertIn("evaluate", result.failure)  # type: ignore[operator]


class RejectionTest(unittest.TestCase):
    """Low faithfulness score → rejected, not failed."""

    def test_score_below_judge_min_produces_rejected_artifact(self):
        # judge_min_score=3 in make_config; score 2 is below threshold
        case = make_workflow_case(score=2.0)
        result = case.run()
        self.assertEqual(result.status, "rejected")
        self.assertIsNotNone(result.evaluation)

    def test_score_zero_produces_rejected_not_failed(self):
        case = make_workflow_case(score=0.0)
        result = case.run()
        self.assertEqual(result.status, "rejected")

    def test_rejected_artifact_has_no_candidate_profile(self):
        case = make_workflow_case(score=1.0)
        result = case.run()
        self.assertIsNone(result.candidate_profile)


class HappyPathTest(unittest.TestCase):
    """All required stages succeed → proposal with full artifact fields."""

    def test_no_sandbox_produces_proposal(self):
        case = make_workflow_case()
        result = case.run()
        self.assertEqual(result.status, "proposal")

    def test_proposal_includes_candidate_profile(self):
        case = make_workflow_case()
        result = case.run()
        self.assertIsNotNone(result.candidate_profile)
        self.assertIsInstance(result.candidate_profile, dict)

    def test_proposal_includes_advice(self):
        case = make_workflow_case()
        result = case.run()
        self.assertIsNotNone(result.advice)

    def test_proposal_includes_evaluation(self):
        case = make_workflow_case()
        result = case.run()
        self.assertIsNotNone(result.evaluation)
        self.assertEqual(result.evaluation.status, "ok")  # type: ignore[union-attr]

    def test_with_sandbox_produces_proposal(self):
        case = make_workflow_case(with_sandbox=True)
        result = case.run()
        self.assertEqual(result.status, "proposal")
        self.assertEqual(len(case.sandbox.requests), 1)  # type: ignore[union-attr]

    def test_run_id_is_nonempty_string(self):
        case = make_workflow_case()
        result = case.run()
        self.assertIsInstance(result.run_id, str)
        self.assertGreater(len(result.run_id), 0)

    def test_source_digest_is_sha256_hex(self):
        case = make_workflow_case()
        result = case.run()
        self.assertEqual(len(result.source_digest), 64)

    def test_no_publish_calls_during_workflow(self):
        case = make_workflow_case()
        case.run()
        self.assertEqual(case.memory.publish_calls, [])

    def test_no_deployment_calls_during_workflow(self):
        case = make_workflow_case()
        case.run()
        self.assertEqual(case.deployment_calls, [])


class AbsentAnalysisTest(unittest.TestCase):
    """Absent sandbox or request must not fail; analysis stage is not_requested."""

    def test_absent_sandbox_skips_analysis(self):
        # FakeInterpreter is not provided; must not error
        case = make_workflow_case()  # no with_sandbox=True
        result = case.run()
        self.assertEqual(result.status, "proposal")

    def test_absent_analysis_request_skips_sandbox(self):
        # with_sandbox=True but no analysis request provided to run_offline
        case = make_workflow_case(with_sandbox=True, with_analysis=False)
        result = case.run()
        self.assertEqual(result.status, "proposal")
        self.assertEqual(len(case.sandbox.requests), 0)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
