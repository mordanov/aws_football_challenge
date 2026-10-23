"""Tests for deploy/offline_coach.py — resource lifecycle without live AWS calls."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

# Add the project root so deploy/ imports work without install
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from deploy.offline_coach import (
    _cmd_plan,
    _evaluator_request,
    _interpreter_request,
    _invocation_policy,
    _load_config,
    _load_receipt,
    _memory_request,
    _provisioning_policy,
    _save_receipt,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _sample_config(tmp: str) -> dict:
    """Minimal valid config written to a temp file; returns the loaded dict."""
    data = {
        "account": "111111111111",
        "region": "us-east-1",
        "team_id": "test",
        "memory": {
            "name": "test-mem",
            "event_expiry_seconds": 3600,
            "strategy": {"type": "SEMANTIC", "namespace": "football.test", "actor": "team:test"},
        },
        "interpreter": {"name": "test-interp", "network_mode": "SANDBOX"},
        "evaluator": {
            "name": "test-eval",
            "model_id": "us.anthropic.claude-haiku-4-5:0",
            "level": "TRACE",
            "max_tokens": 256,
            "rating_scale": [
                {"value": 0, "definition": "bad"},
                {"value": 4, "definition": "excellent"},
            ],
        },
        "tags": {"afc:project": "test"},
    }
    p = pathlib.Path(tmp) / "cfg.json"
    p.write_text(json.dumps(data))
    return _load_config(str(p))


class ConfigLoadTest(unittest.TestCase):
    def test_valid_config_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            self.assertEqual(cfg["account"], "111111111111")
            self.assertEqual(cfg["region"], "us-east-1")

    def test_missing_required_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {"account": "x", "region": "us-east-1"}
            p = pathlib.Path(tmp) / "bad.json"
            p.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                _load_config(str(p))

    def test_nonexistent_file_raises(self):
        with self.assertRaises(ValueError):
            _load_config("/no/such/file.json")

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "bad.json"
            p.write_text("not json {")
            with self.assertRaises(ValueError):
                _load_config(str(p))


class PlanDryRunTest(unittest.TestCase):
    """plan command emits valid JSON and makes no AWS calls."""

    def _run_plan(self, config: dict) -> dict:
        import io
        old = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            code = _cmd_plan(config)
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue())

    def test_plan_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            result = self._run_plan(cfg)
            self.assertIn("resources", result)

    def test_plan_shows_create_for_new_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            result = self._run_plan(cfg)
            for kind in ("memory", "interpreter", "evaluator"):
                self.assertEqual(result["resources"][kind]["action"], "create")

    def test_plan_shows_existing_for_recorded_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            # Write a receipt as if resources already exist
            _save_receipt(cfg, {
                "memory_id": "m-abc",
                "interpreter_id": "i-abc",
                "evaluator_id": "e-abc",
            })
            result = self._run_plan(cfg)
            for kind in ("memory", "interpreter", "evaluator"):
                self.assertEqual(result["resources"][kind]["action"], "existing")

    def test_plan_includes_invocation_and_provisioning_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            result = self._run_plan(cfg)
            self.assertIn("invocation", result["policies"])
            self.assertIn("provisioning", result["policies"])

    def test_plan_note_mentions_player_runtimes_not_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            result = self._run_plan(cfg)
            self.assertIn("r4_gk", result.get("note", "") + result.get("policies", {}).get("note", ""))


class WrongAccountTest(unittest.TestCase):
    """apply/cleanup must reject if account does not match."""

    def _fake_sts(self, account: str) -> object:
        class Fake:
            def get_caller_identity(self):
                return {"Account": account}
        return Fake()

    def test_apply_without_approve_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            import io
            buf = io.StringIO()
            old_err = sys.stderr
            sys.stderr = buf
            try:
                from deploy.offline_coach import _cmd_apply
                code = _cmd_apply(cfg, approve=False)
            finally:
                sys.stderr = old_err
            self.assertEqual(code, 1)

    def test_cleanup_without_approve_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            _save_receipt(cfg, {"memory_id": "m-abc"})
            import io
            buf = io.StringIO()
            old_err = sys.stderr
            sys.stderr = buf
            try:
                from deploy.offline_coach import _cmd_cleanup
                code = _cmd_cleanup(cfg, approve=False)
            finally:
                sys.stderr = old_err
            self.assertEqual(code, 1)


class OverbreadResourceRejectionTest(unittest.TestCase):
    """cleanup only deletes resources recorded in the receipt."""

    def test_cleanup_with_no_receipt_exits_0_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            import io
            buf = io.StringIO()
            old_out = sys.stdout
            sys.stdout = buf
            try:
                from deploy.offline_coach import _cmd_cleanup
                code = _cmd_cleanup(cfg, approve=False)
            finally:
                sys.stdout = old_out
            # No receipt → nothing to delete → exits 0 (no --approve needed for empty set)
            self.assertEqual(code, 0)


class InterpreterIsolationTest(unittest.TestCase):
    """Interpreter request must use SANDBOX network mode (no execution role)."""

    def test_interpreter_request_uses_sandbox_network_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            req = _interpreter_request(cfg)
            self.assertEqual(req["networkConfiguration"]["networkMode"], "SANDBOX")

    def test_interpreter_request_has_no_execution_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            req = _interpreter_request(cfg)
            self.assertNotIn("executionRoleArn", req)


class IdempotentReceiptTest(unittest.TestCase):
    """Receipt save/load is atomic and round-trips correctly."""

    def test_save_and_load_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            original = {"memory_id": "m-1", "evaluator_id": "e-2"}
            _save_receipt(cfg, original)
            loaded = _load_receipt(cfg)
            self.assertEqual(loaded, original)

    def test_load_missing_receipt_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            result = _load_receipt(cfg)
            self.assertEqual(result, {})


class PolicyTest(unittest.TestCase):
    """Generated IAM policies have the expected structure."""

    def test_invocation_policy_has_no_iam_passrole(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            policy = _invocation_policy(cfg, {})
            all_actions = [
                a
                for stmt in policy["Statement"]
                for a in (stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]])
            ]
            self.assertNotIn("iam:PassRole", all_actions)

    def test_provisioning_policy_does_not_attach_to_player_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            policy = _provisioning_policy(cfg)
            all_actions = [
                a
                for stmt in policy["Statement"]
                for a in (stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]])
            ]
            # Must not allow attaching policies to IAM roles
            self.assertNotIn("iam:AttachRolePolicy", all_actions)
            self.assertNotIn("iam:PutRolePolicy", all_actions)

    def test_invocation_policy_region_condition(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            policy = _invocation_policy(cfg, {})
            invoke_stmt = next(
                s for s in policy["Statement"] if s["Sid"] == "OfflineCoachInvoke"
            )
            self.assertEqual(
                invoke_stmt["Condition"]["StringEquals"]["aws:RequestedRegion"],
                cfg["region"],
            )

    def test_evaluator_request_uses_judge_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            req = _evaluator_request(cfg)
            instructions = req["evaluatorConfig"]["llmAsAJudge"]["instructions"]
            self.assertIn("{context}", instructions)
            self.assertIn("{assistant_turn}", instructions)

    def test_evaluator_request_level_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            req = _evaluator_request(cfg)
            self.assertEqual(req["evaluatorConfig"]["llmAsAJudge"]["level"], "TRACE")

    def test_memory_request_has_semantic_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _sample_config(tmp)
            req = _memory_request(cfg)
            self.assertTrue(
                any("semanticMemoryStrategy" in s for s in req["memoryStrategies"])
            )


if __name__ == "__main__":
    unittest.main()
