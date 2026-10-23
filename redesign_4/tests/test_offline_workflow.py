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


class CliArgparseTest(unittest.TestCase):
    """Argparse rejects missing required flags before any client is created."""

    def _run(self, *argv: str) -> tuple[int, str]:
        """Run CLI, return (exit_code, stderr)."""
        import io
        from agent.advisor.offline.__main__ import main, Services
        err = io.StringIO()
        import sys as _sys
        old_stderr = _sys.stderr
        _sys.stderr = err
        try:
            code = main(list(argv), services=Services())
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
        finally:
            _sys.stderr = old_stderr
        return code, err.getvalue()

    def test_run_missing_config_exits_nonzero(self):
        code, _ = self._run("run", "--evidence", "e.json", "--profile", "p.json", "--output", "o.json")
        self.assertNotEqual(code, 0)

    def test_run_missing_evidence_exits_nonzero(self):
        code, _ = self._run("run", "--config", "k.json", "--profile", "p.json", "--output", "o.json")
        self.assertNotEqual(code, 0)

    def test_run_missing_profile_exits_nonzero(self):
        code, _ = self._run("run", "--config", "k.json", "--evidence", "e.json", "--output", "o.json")
        self.assertNotEqual(code, 0)

    def test_doctor_missing_config_exits_nonzero(self):
        code, _ = self._run("doctor")
        self.assertNotEqual(code, 0)

    def test_publish_missing_config_exits_nonzero(self):
        code, _ = self._run("publish", "--note", "n.json", "--evidence", "e.json")
        self.assertNotEqual(code, 0)

    def test_analyze_missing_execute_flag_exits_2(self):
        # --execute-generated-code is required for sandbox execution
        import tempfile, json
        from tests.offline_support import make_config, make_packet
        from agent.advisor.offline.__main__ import Services
        from agent.advisor.offline.contracts import OfflineConfig

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = f"{tmp}/cfg.json"
            ev_path = f"{tmp}/ev.json"
            script_path = f"{tmp}/script.json"
            cfg = make_config()
            import pathlib
            pathlib.Path(cfg_path).write_text(cfg.model_dump_json())
            pkt = make_packet()
            pathlib.Path(ev_path).write_text(json.dumps(pkt.model_dump(mode="json")))
            pathlib.Path(script_path).write_text(json.dumps({"code": "print(1)", "evidence_digest": pkt.digest}))
            code, _ = self._run(
                "analyze",
                "--config", cfg_path,
                "--evidence", ev_path,
                "--script", script_path,
                # missing --execute-generated-code
                "--output", f"{tmp}/out.json",
            )
            self.assertEqual(code, 2)


class CliRunTest(unittest.TestCase):
    """CLI run command: exit codes match artifact status."""

    def _run_cli(self, argv: list[str], services: "Services") -> tuple[int, str, str]:
        import io
        from agent.advisor.offline.__main__ import main
        import sys as _sys
        out, err = io.StringIO(), io.StringIO()
        old_out, old_err = _sys.stdout, _sys.stderr
        _sys.stdout, _sys.stderr = out, err
        try:
            code = main(argv, services=services)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
        finally:
            _sys.stdout, _sys.stderr = old_out, old_err
        return code, out.getvalue(), err.getvalue()

    def test_run_proposal_exits_0(self):
        import tempfile, pathlib
        from agent.advisor.offline.__main__ import Services
        from tests.offline_support import FakeEvaluator, FakeAdvisor, FakeMemory, make_config, make_packet

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = f"{tmp}/cfg.json"
            ev_path = f"{tmp}/ev.json"
            prof_path = f"{tmp}/prof.json"
            out_path = f"{tmp}/run.json"

            cfg = make_config()
            pathlib.Path(cfg_path).write_text(cfg.model_dump_json())
            pkt = make_packet()
            from agent.model import Profile
            import json
            pathlib.Path(ev_path).write_text(json.dumps(pkt.model_dump(mode="json")))
            pathlib.Path(prof_path).write_text(json.dumps(Profile().to_dict()))

            svc = Services(
                scouts=FakeMemory(),
                advisor=FakeAdvisor(),
                evaluator=FakeEvaluator(score=3.0),
            )
            code, out, _ = self._run_cli(
                ["run", "--config", cfg_path, "--evidence", ev_path,
                 "--profile", prof_path, "--output", out_path],
                svc,
            )
            self.assertEqual(code, 0)
            result = json.loads(pathlib.Path(out_path).read_text())
            self.assertEqual(result["status"], "proposal")

    def test_run_rejected_exits_3(self):
        import tempfile, pathlib, json
        from agent.advisor.offline.__main__ import Services
        from tests.offline_support import FakeEvaluator, FakeAdvisor, FakeMemory, make_config, make_packet
        from agent.model import Profile

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = f"{tmp}/cfg.json"
            ev_path = f"{tmp}/ev.json"
            prof_path = f"{tmp}/prof.json"
            out_path = f"{tmp}/run.json"

            pathlib.Path(cfg_path).write_text(make_config().model_dump_json())
            pathlib.Path(ev_path).write_text(json.dumps(make_packet().model_dump(mode="json")))
            pathlib.Path(prof_path).write_text(json.dumps(Profile().to_dict()))

            svc = Services(
                scouts=FakeMemory(),
                advisor=FakeAdvisor(),
                evaluator=FakeEvaluator(score=0.0),
            )
            code, _, _ = self._run_cli(
                ["run", "--config", cfg_path, "--evidence", ev_path,
                 "--profile", prof_path, "--output", out_path],
                svc,
            )
            self.assertEqual(code, 3)

    def test_run_service_failure_exits_2(self):
        import tempfile, pathlib, json
        from agent.advisor.offline.__main__ import Services
        from agent.advisor.offline.contracts import OfflineServiceError
        from tests.offline_support import FakeAdvisor, FakeMemory, FakeEvaluator, make_config, make_packet
        from agent.model import Profile

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = f"{tmp}/cfg.json"
            ev_path = f"{tmp}/ev.json"
            prof_path = f"{tmp}/prof.json"
            out_path = f"{tmp}/run.json"

            pathlib.Path(cfg_path).write_text(make_config().model_dump_json())
            pathlib.Path(ev_path).write_text(json.dumps(make_packet().model_dump(mode="json")))
            pathlib.Path(prof_path).write_text(json.dumps(Profile().to_dict()))

            svc = Services(
                scouts=FakeMemory(),
                advisor=FakeAdvisor(
                    side_effect=OfflineServiceError("fail", service="advise", stage="advise")
                ),
                evaluator=FakeEvaluator(),
            )
            code, _, _ = self._run_cli(
                ["run", "--config", cfg_path, "--evidence", ev_path,
                 "--profile", prof_path, "--output", out_path],
                svc,
            )
            self.assertEqual(code, 2)

    def test_doctor_valid_config_exits_0(self):
        import tempfile, pathlib
        from agent.advisor.offline.__main__ import Services
        from tests.offline_support import make_config

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = f"{tmp}/cfg.json"
            pathlib.Path(cfg_path).write_text(make_config().model_dump_json())
            code, out, _ = self._run_cli(["doctor", "--config", cfg_path], Services())
            self.assertEqual(code, 0)
            import json
            report = json.loads(out)
            self.assertTrue(report["config_valid"])


if __name__ == "__main__":
    unittest.main()
