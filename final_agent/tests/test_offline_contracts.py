"""Public contract boundary tests — no adapter internals accessed."""

import json
import unittest
from datetime import datetime


class EvidenceClaimContractTest(unittest.TestCase):
    def _model(self):
        from agent.advisor.offline.contracts import EvidenceClaim

        return EvidenceClaim

    def test_unknown_field_rejected(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self._model()(
                id="c1",
                text="shot attempted",
                kind="observed",
                source_ids=("s1",),
                extra_field="oops",
            )

    def test_empty_id_rejected(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self._model()(id="", text="shot attempted", kind="observed", source_ids=())

    def test_empty_source_id_rejected(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self._model()(id="c1", text="x", kind="observed", source_ids=("",))

    def test_invalid_kind_rejected(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            self._model()(id="c1", text="x", kind="verified", source_ids=())

    def test_valid_claim_round_trips(self):
        claim = self._model()(
            id="c1",
            text="SHOOT command observed",
            kind="observed",
            source_ids=("manifest-001",),
        )
        data = claim.model_dump()
        restored = self._model().model_validate(data)
        self.assertEqual(restored.id, claim.id)
        self.assertEqual(restored.kind, claim.kind)


class EvidencePacketContractTest(unittest.TestCase):
    def _make(self):
        from tests.offline_support import make_packet

        return make_packet()

    def test_naive_played_at_is_rejected(self):
        from pydantic import ValidationError

        data = self._make().model_dump()
        data["played_at"] = datetime(2026, 10, 23)
        with self.assertRaises(ValidationError):
            type(self._make()).model_validate(data)

    def test_naive_expires_at_is_rejected(self):
        from pydantic import ValidationError

        data = self._make().model_dump()
        data["expires_at"] = datetime(2027, 1, 1)
        with self.assertRaises(ValidationError):
            type(self._make()).model_validate(data)

    def test_expiry_before_played_at_is_rejected(self):
        from pydantic import ValidationError

        data = self._make().model_dump()
        # Set expires_at earlier than played_at.
        data["expires_at"] = data["played_at"]
        with self.assertRaises(ValidationError):
            type(self._make()).model_validate(data)

    def test_unknown_field_rejected(self):
        from pydantic import ValidationError

        data = self._make().model_dump()
        data["unknown"] = "x"
        with self.assertRaises(ValidationError):
            type(self._make()).model_validate(data)

    def test_schema_version_enforced(self):
        from pydantic import ValidationError

        data = self._make().model_dump()
        data["schema_version"] = 2
        with self.assertRaises(ValidationError):
            type(self._make()).model_validate(data)

    def test_empty_team_id_rejected(self):
        from pydantic import ValidationError

        data = self._make().model_dump()
        data["team_id"] = ""
        with self.assertRaises(ValidationError):
            type(self._make()).model_validate(data)

    def test_digest_is_stable_and_derived(self):
        p = self._make()
        self.assertEqual(len(p.digest), 64)
        # Recomputed digest must be identical.
        self.assertEqual(p.digest, p.digest)

    def test_digest_changes_on_content_change(self):
        p1 = self._make()

        data = p1.model_dump()
        data["team_id"] = "other_team"
        p2 = type(p1).model_validate(data)
        self.assertNotEqual(p1.digest, p2.digest)

    def test_json_round_trip(self):
        p = self._make()
        # model_dump → JSON string → model_validate reproduces the packet.
        dumped = p.model_dump(mode="json")
        serialized = json.dumps(dumped)
        restored = type(p).model_validate_json(serialized)
        self.assertEqual(restored.team_id, p.team_id)
        self.assertEqual(restored.match_id, p.match_id)


class OfflineAdviceContractTest(unittest.TestCase):
    def _valid_kwargs(self):
        from dataclasses import asdict

        from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE

        return {
            "settings": {k: v for k, v in asdict(DEFAULT_SETTINGS).items()},
            "style": {k: v for k, v in asdict(DEFAULT_STYLE).items()},
            "rationale": "press higher to exploit opponent's slow build-up",
            "evidence_ids": ("c1",),
            "uncertainties": (),
            "counter_tests": (),
        }

    def test_valid_advice_accepted(self):
        from agent.advisor.offline.contracts import OfflineAdvice

        OfflineAdvice(**self._valid_kwargs())

    def test_empty_evidence_ids_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import OfflineAdvice

        kwargs = {**self._valid_kwargs(), "evidence_ids": ()}
        with self.assertRaises(ValidationError):
            OfflineAdvice(**kwargs)

    def test_out_of_bounds_settings_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import OfflineAdvice

        kwargs = self._valid_kwargs()
        kwargs["settings"] = {**kwargs["settings"], "shot_range": 99.0}
        with self.assertRaises(ValidationError):
            OfflineAdvice(**kwargs)

    def test_empty_rationale_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import OfflineAdvice

        kwargs = {**self._valid_kwargs(), "rationale": ""}
        with self.assertRaises(ValidationError):
            OfflineAdvice(**kwargs)

    def test_unknown_field_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import OfflineAdvice

        kwargs = {**self._valid_kwargs(), "extra": "nope"}
        with self.assertRaises(ValidationError):
            OfflineAdvice(**kwargs)


class RunArtifactContractTest(unittest.TestCase):
    def _valid_kwargs(self):
        digest = "a" * 64
        return {
            "run_id": "run-001",
            "source_digest": digest,
            "config_digest": digest,
            "stage_receipts": {},
            "status": "failed",
            "failure": "evidence stage error",
        }

    def test_invalid_status_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import RunArtifact

        kwargs = {**self._valid_kwargs(), "status": "approved"}
        with self.assertRaises(ValidationError):
            RunArtifact(**kwargs)

    def test_deployed_status_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import RunArtifact

        kwargs = {**self._valid_kwargs(), "status": "deployed"}
        with self.assertRaises(ValidationError):
            RunArtifact(**kwargs)

    def test_invalid_candidate_profile_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import RunArtifact

        kwargs = {**self._valid_kwargs(), "candidate_profile": {"version": 123}}
        with self.assertRaises(ValidationError):
            RunArtifact(**kwargs)

    def test_failed_artifact_without_advice_is_valid(self):
        from agent.advisor.offline.contracts import RunArtifact

        RunArtifact(**self._valid_kwargs())

    def test_schema_version_enforced(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import RunArtifact

        kwargs = {**self._valid_kwargs(), "schema_version": 2}
        with self.assertRaises(ValidationError):
            RunArtifact(**kwargs)

    def test_json_round_trip(self):
        from agent.advisor.offline.contracts import RunArtifact

        a = RunArtifact(**self._valid_kwargs())
        serialized = a.model_dump_json()
        restored = RunArtifact.model_validate_json(serialized)
        self.assertEqual(restored.run_id, a.run_id)
        self.assertEqual(restored.status, a.status)


class EvaluationReportContractTest(unittest.TestCase):
    def test_score_out_of_range_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import EvaluationReport

        with self.assertRaises(ValidationError):
            EvaluationReport(
                rubric_version="v1",
                evaluator_id="eval-1",
                trace_id="trace-1",
                local_checks={},
                scores={"faithfulness": 5.0},
                explanations=(),
                status="ok",
            )

    def test_negative_score_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import EvaluationReport

        with self.assertRaises(ValidationError):
            EvaluationReport(
                rubric_version="v1",
                evaluator_id="eval-1",
                trace_id="trace-1",
                local_checks={},
                scores={"faithfulness": -1.0},
                explanations=(),
                status="ok",
            )


class OfflineConfigContractTest(unittest.TestCase):
    def _valid_kwargs(self):
        return {
            "expected_account": "123456789012",
            "region": "us-east-1",
            "model_id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "judge_model_id": "anthropic.claude-3-5-haiku-20241022-v1:0",
            "memory_store_id": "memory-store-001",
            "interpreter_id": "interpreter-001",
            "evaluator_id": "evaluator-001",
            "max_input_tokens": 8000,
            "max_output_tokens": 1000,
            "timeout_seconds": 60,
            "max_output_bytes": 65536,
            "max_rounds": 1,
            "judge_min_score": 3,
        }

    def test_explicit_cache_without_capability_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import OfflineConfig

        kwargs = {**self._valid_kwargs(), "cache_mode": "explicit", "explicit_cache": False}
        with self.assertRaises(ValidationError):
            OfflineConfig(**kwargs)

    def test_explicit_cache_with_capability_accepted(self):
        from agent.advisor.offline.contracts import OfflineConfig

        kwargs = {
            **self._valid_kwargs(),
            "cache_mode": "explicit",
            "explicit_cache": True,
            "cache_min_tokens": 1024,
        }
        cfg = OfflineConfig(**kwargs)
        self.assertEqual(cfg.cache_mode, "explicit")

    def test_judge_min_score_bounds(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import OfflineConfig

        with self.assertRaises(ValidationError):
            OfflineConfig(**{**self._valid_kwargs(), "judge_min_score": 5})
        with self.assertRaises(ValidationError):
            OfflineConfig(**{**self._valid_kwargs(), "judge_min_score": -1})

    def test_unknown_field_rejected(self):
        from pydantic import ValidationError

        from agent.advisor.offline.contracts import OfflineConfig

        with self.assertRaises(ValidationError):
            OfflineConfig(**{**self._valid_kwargs(), "secret_key": "nope"})


class OfflineServiceErrorTest(unittest.TestCase):
    def test_attributes_accessible(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        err = OfflineServiceError(
            "timeout",
            service="memory",
            stage="retrieve",
            error_code="ThrottlingException",
            retryable=True,
            outcome="unknown",
        )
        self.assertEqual(err.service, "memory")
        self.assertEqual(err.stage, "retrieve")
        self.assertEqual(err.retryable, True)
        self.assertEqual(err.outcome, "unknown")
        self.assertIsInstance(err, Exception)

    def test_default_outcome_is_failed(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        err = OfflineServiceError("disk full", service="eval", stage="write")
        self.assertEqual(err.outcome, "failed")
        self.assertFalse(err.retryable)


if __name__ == "__main__":
    unittest.main()
