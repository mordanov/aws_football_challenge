"""Offline provisioning contracts, checked against the installed AWS SDK; never call AWS."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import boto3
from botocore.config import Config
from botocore.stub import Stubber
from botocore.validate import validate_parameters

from deploy import offline_coach as resources

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
IDS = {
    "memory": "r4_offline_memory-0123456789",
    "interpreter": "r4_offline_interpreter-0123456789",
    "evaluator": "r4_offline_evaluator-0123456789",
}


class ProvisioningTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT, prefix=".offline_test_")
        self.addCleanup(self.directory.cleanup)
        self.config = json.loads((ROOT / "deploy/offline_coach.example.json").read_text())
        self.config["evaluator"]["model_id"] = "configured-model"
        self.config["evaluator"]["model_resource_arns"] = [
            "arn:aws:bedrock:us-east-1::foundation-model/configured-model"
        ]
        self.config["_data_dir"] = self.directory.name
        self.client = boto3.client(
            "bedrock-agentcore-control",
            region_name=self.config["region"],
            aws_access_key_id="offline-test",
            aws_secret_access_key="offline-test",
            config=Config(retries={"total_max_attempts": 1}),
        )
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)

    def invoke(self, command, *args):
        calls = []

        def client(service, **kwargs):
            calls.append((service, kwargs))
            if service == "sts":
                return SimpleNamespace(
                    get_caller_identity=lambda: {"Account": self.config["account"]}
                )
            self.assertEqual(service, "bedrock-agentcore-control")
            self.assertEqual(kwargs["config"].retries["total_max_attempts"], 1)
            return self.client

        with (
            patch("boto3.client", side_effect=client),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = command(self.config, *args)
        return result, calls

    def memory_response(self, status="CREATING"):
        return {"memory": {
            "id": IDS["memory"],
            "arn": (
                f"arn:aws:bedrock-agentcore:{self.config['region']}:"
                f"{self.config['account']}:memory/{IDS['memory']}"
            ),
            "name": self.config["memory"]["name"],
            "eventExpiryDuration": 7,
            "status": status,
            "createdAt": NOW,
            "updatedAt": NOW,
        }}

    def queue_memory(self, status="ACTIVE"):
        self.stub.add_response(
            "create_memory", self.memory_response(), resources._memory_request(self.config)
        )
        self.stub.add_response(
            "get_memory", self.memory_response(status), {"memoryId": IDS["memory"]}
        )

    def test_all_create_requests_match_sdk(self):
        for operation, builder in [
            ("CreateMemory", resources._memory_request),
            ("CreateCodeInterpreter", resources._interpreter_request),
            ("CreateEvaluator", resources._evaluator_request),
        ]:
            with self.subTest(operation=operation):
                request = builder(self.config)
                shape = self.client.meta.service_model.operation_model(operation).input_shape
                validate_parameters(request, shape)
        memory = resources._memory_request(self.config)
        self.assertEqual(memory["eventExpiryDuration"], 7)
        strategy = memory["memoryStrategies"][0]["semanticMemoryStrategy"]
        self.assertIn("{actorId}", strategy["namespaces"][0])
        evaluator = resources._evaluator_request(self.config)
        self.assertEqual(evaluator["level"], "TRACE")
        self.assertIn("modelConfig", evaluator["evaluatorConfig"]["llmAsAJudge"])

    def test_sandbox_rejects_role_mounts_and_public_network(self):
        for key, value in [
            ("network_mode", "PUBLIC"),
            ("executionRoleArn", "arn:aws:iam::123456789012:role/player"),
            ("execution_role_arn", "arn:aws:iam::123456789012:role/player"),
            ("filesystemConfigurations", [{"efsConfiguration": {}}]),
            ("mounts", ["/data"]),
        ]:
            with self.subTest(key=key):
                config = copy.deepcopy(self.config)
                config["interpreter"][key] = value
                with self.assertRaises(ValueError):
                    resources._interpreter_request(config)

    def test_plan_validates_names_enums_ttl_before_any_aws_call(self):
        for kind, key, value in [
            ("memory", "name", "bad-hyphen"),
            ("interpreter", "name", "bad-hyphen"),
            ("evaluator", "name", "bad-hyphen"),
            ("evaluator", "level", "NOT_A_LEVEL"),
            ("memory", "event_expiry_days", 1),
        ]:
            with self.subTest(kind=kind, key=key):
                config = copy.deepcopy(self.config)
                config[kind][key] = value
                with patch("boto3.client") as factory, self.assertRaises(ValueError):
                    resources._cmd_plan(config)
                factory.assert_not_called()

    def test_unconfigured_model_fails_before_identity_or_creation(self):
        self.config["evaluator"]["model_id"] = ""
        result, calls = self.invoke(resources._cmd_apply, True)
        self.assertEqual(result, 2)
        self.assertEqual(calls, [])

    def test_unsupported_evaluator_role_is_not_silently_ignored(self):
        self.config["evaluator"]["execution_role_arn"] = (
            f"arn:aws:iam::{self.config['account']}:role/offline_evaluator"
        )
        result, calls = self.invoke(resources._cmd_apply, True)
        self.assertEqual(result, 2)
        self.assertEqual(calls, [])

    def test_apply_requires_approval_without_aws_calls(self):
        result, calls = self.invoke(resources._cmd_apply, False)
        self.assertEqual(result, 1)
        self.assertEqual(calls, [])

    def test_receipt_exists_before_each_readiness_poll(self):
        self.queue_memory()
        interpreter_arn = (
            f"arn:aws:bedrock-agentcore:{self.config['region']}:"
            f"{self.config['account']}:code-interpreter-custom/{IDS['interpreter']}"
        )
        self.stub.add_response("create_code_interpreter", {
            "codeInterpreterId": IDS["interpreter"],
            "codeInterpreterArn": interpreter_arn,
            "createdAt": NOW,
            "status": "CREATING",
        }, resources._interpreter_request(self.config))
        self.stub.add_response("get_code_interpreter", {
            "codeInterpreterId": IDS["interpreter"],
            "codeInterpreterArn": interpreter_arn,
            "name": self.config["interpreter"]["name"],
            "networkConfiguration": {"networkMode": "SANDBOX"},
            "status": "READY",
            "createdAt": NOW,
            "lastUpdatedAt": NOW,
        }, {"codeInterpreterId": IDS["interpreter"]})
        evaluator_arn = (
            f"arn:aws:bedrock-agentcore:{self.config['region']}:"
            f"{self.config['account']}:evaluator/{IDS['evaluator']}"
        )
        self.stub.add_response("create_evaluator", {
            "evaluatorId": IDS["evaluator"], "evaluatorArn": evaluator_arn,
            "createdAt": NOW, "status": "CREATING",
        }, resources._evaluator_request(self.config))
        self.stub.add_response("get_evaluator", {
            "evaluatorId": IDS["evaluator"], "evaluatorArn": evaluator_arn,
            "evaluatorName": self.config["evaluator"]["name"],
            "evaluatorConfig": resources._evaluator_request(self.config)["evaluatorConfig"],
            "level": "TRACE", "status": "ACTIVE", "createdAt": NOW, "updatedAt": NOW,
        }, {"evaluatorId": IDS["evaluator"]})
        original = resources._poll_ready

        def poll(client, kind, resource_id):
            receipt = resources._load_receipt(self.config)
            self.assertEqual(receipt[f"{kind}_id"], resource_id)
            return original(client, kind, resource_id)

        with patch.object(resources, "_poll_ready", side_effect=poll):
            result, _ = self.invoke(resources._cmd_apply, True)
        self.assertEqual(result, 0)
        self.stub.assert_no_pending_responses()
        for kind, resource_id in IDS.items():
            self.assertEqual(resources._load_receipt(self.config)[f"{kind}_id"], resource_id)

    def test_poll_failure_keeps_id_and_stops_before_next_resource(self):
        self.queue_memory("FAILED")
        with patch.object(self.client, "create_code_interpreter") as next_create:
            result, _ = self.invoke(resources._cmd_apply, True)
        self.assertEqual(result, 2)
        next_create.assert_not_called()
        self.assertEqual(resources._load_receipt(self.config)["memory_id"], IDS["memory"])
        self.stub.assert_no_pending_responses()

    def test_poll_service_error_and_timeout_preserve_memory_id(self):
        for failure in ("service", "timeout"):
            with self.subTest(failure=failure):
                resources._receipt_path(self.config).unlink(missing_ok=True)
                self.stub.add_response(
                    "create_memory", self.memory_response(), resources._memory_request(self.config)
                )
                if failure == "service":
                    self.stub.add_client_error(
                        "get_memory", service_error_code="AccessDeniedException",
                        expected_params={"memoryId": IDS["memory"]},
                    )
                else:
                    self.stub.add_response(
                        "get_memory", self.memory_response(), {"memoryId": IDS["memory"]}
                    )
                with (
                    patch.object(resources.time, "monotonic", side_effect=[0, 121]),
                    patch.object(self.client, "create_code_interpreter") as next_create,
                ):
                    result, _ = self.invoke(resources._cmd_apply, True)
                self.assertEqual(result, 2)
                next_create.assert_not_called()
                self.assertEqual(
                    resources._load_receipt(self.config)["memory_id"], IDS["memory"]
                )
                self.stub.assert_no_pending_responses()

    def test_interpreter_create_failure_stops_before_evaluator(self):
        self.queue_memory()
        self.stub.add_client_error(
            "create_code_interpreter", service_error_code="AccessDeniedException",
            expected_params=resources._interpreter_request(self.config),
        )
        with patch.object(self.client, "create_evaluator") as next_create:
            result, _ = self.invoke(resources._cmd_apply, True)
        self.assertEqual(result, 2)
        next_create.assert_not_called()
        self.assertEqual(resources._load_receipt(self.config)["memory_id"], IDS["memory"])
        self.stub.assert_no_pending_responses()

    def test_create_error_is_not_retried_and_stops(self):
        self.stub.add_client_error(
            "create_memory", service_error_code="AccessDeniedException",
            expected_params=resources._memory_request(self.config),
        )
        with patch.object(self.client, "create_code_interpreter") as next_create:
            result, _ = self.invoke(resources._cmd_apply, True)
        self.assertEqual(result, 2)
        next_create.assert_not_called()
        self.stub.assert_no_pending_responses()

    def test_existing_pending_resource_is_polled_not_recreated(self):
        resources._save_receipt(self.config, {"memory_id": IDS["memory"]})
        self.stub.add_response(
            "get_memory", self.memory_response("FAILED"), {"memoryId": IDS["memory"]}
        )
        with (
            patch.object(self.client, "create_memory") as create,
            patch.object(self.client, "create_code_interpreter") as next_create,
        ):
            result, _ = self.invoke(resources._cmd_apply, True)
        self.assertEqual(result, 2)
        create.assert_not_called()
        next_create.assert_not_called()
        self.stub.assert_no_pending_responses()

    def test_terminal_failure_status_does_not_wait(self):
        for kind in ("interpreter", "evaluator"):
            with self.subTest(kind=kind):
                client = SimpleNamespace(
                    get_memory=lambda **kw: {},
                    get_code_interpreter=lambda **kw: {"status": "CREATE_FAILED"},
                    get_evaluator=lambda **kw: {"status": "UPDATE_FAILED"},
                )
                with patch.object(resources.time, "sleep") as sleep:
                    with self.assertRaises(RuntimeError):
                        resources._poll_ready(client, kind, IDS[kind])
                sleep.assert_not_called()

    def test_memory_waits_for_semantic_strategy_and_rejects_failed_strategy(self):
        with (
            patch.object(self.client, "get_memory", side_effect=[
                {"memory": {"status": "ACTIVE", "strategies": [{"status": "CREATING"}]}},
                {"memory": {"status": "ACTIVE", "strategies": [{"status": "ACTIVE"}]}},
            ]),
            patch.object(resources.time, "sleep") as sleep,
        ):
            self.assertEqual(resources._poll_ready(self.client, "memory", IDS["memory"]), "ACTIVE")
            sleep.assert_called_once()
        with (
            patch.object(self.client, "get_memory", return_value={
                "memory": {"status": "ACTIVE", "strategies": [{"status": "FAILED"}]},
            }),
            self.assertRaises(RuntimeError),
        ):
            resources._poll_ready(self.client, "memory", IDS["memory"])

    def test_corrupt_and_foreign_receipts_are_refused(self):
        for content in ("{", "[]", "{}", '{"memory_id": "foreign"}'):
            with self.subTest(content=content):
                resources._receipt_path(self.config).write_text(content)
                with self.assertRaises(ValueError):
                    resources._load_receipt(self.config)
        resources._save_receipt(self.config, {"memory_id": IDS["memory"]})
        for field, other in [
            ("account", "999999999999"), ("region", "eu-west-1"), ("team_id", "foreign"),
        ]:
            receipt = json.loads(resources._receipt_path(self.config).read_text())
            receipt["_identity"][field] = other
            resources._receipt_path(self.config).write_text(json.dumps(receipt))
            with self.subTest(field=field), self.assertRaises(ValueError):
                resources._load_receipt(self.config)
            resources._receipt_path(self.config).unlink()
            resources._save_receipt(self.config, {"memory_id": IDS["memory"]})

    def test_corrupt_receipt_prevents_all_aws_calls(self):
        resources._receipt_path(self.config).write_text("{")
        for command, args in [
            (resources._cmd_apply, (True,)), (resources._cmd_cleanup, (True,)),
            (resources._cmd_status, ()),
        ]:
            with self.subTest(command=command.__name__):
                result, calls = self.invoke(command, *args)
                self.assertEqual(result, 2)
                self.assertEqual(calls, [])

    def test_cleanup_only_deletes_recorded_id_using_control_api(self):
        resources._save_receipt(self.config, {"memory_id": IDS["memory"]})
        self.stub.add_response(
            "delete_memory", {"memoryId": IDS["memory"], "status": "DELETING"},
            {"memoryId": IDS["memory"]},
        )
        self.assertEqual(self.invoke(resources._cmd_cleanup, True)[0], 0)
        self.assertNotIn("memory_id", resources._load_receipt(self.config))
        self.stub.assert_no_pending_responses()

    def test_status_nested_memory_failure_and_service_error_are_nonzero(self):
        resources._save_receipt(self.config, {"memory_id": IDS["memory"]})
        self.stub.add_response(
            "get_memory", self.memory_response("FAILED"), {"memoryId": IDS["memory"]}
        )
        self.assertEqual(self.invoke(resources._cmd_status)[0], 2)
        self.stub.add_client_error(
            "get_memory", service_error_code="AccessDeniedException",
            expected_params={"memoryId": IDS["memory"]},
        )
        self.assertEqual(self.invoke(resources._cmd_status)[0], 2)
        self.stub.assert_no_pending_responses()

    def test_policy_has_only_actual_operations_and_custom_interpreter_arn(self):
        receipt = {f"{kind}_id": value for kind, value in IDS.items()}
        policy = resources._invocation_policy(self.config, receipt)
        actions = {action for stmt in policy["Statement"] for action in stmt["Action"]}
        for action in ["CreateEvent", "ListEvents", "GetCodeInterpreter", "Evaluate"]:
            self.assertIn(f"bedrock-agentcore:{action}", actions)
        for action in [
            "IngestMemoryRecord", "CreateEvaluationJob", "GetEvaluationJob",
            "RetrieveMemoryRecords", "GetCodeInterpreterSession",
        ]:
            self.assertNotIn(f"bedrock-agentcore:{action}", actions)
        interpreter = next(
            stmt for stmt in policy["Statement"]
            if "bedrock-agentcore:InvokeCodeInterpreter" in stmt["Action"]
        )
        self.assertTrue(all(":code-interpreter-custom/" in arn for arn in interpreter["Resource"]))
        self.assertNotIn("iam:PassRole", actions)
        provisioning = resources._provisioning_policy(self.config)
        manage_actions = {a for stmt in provisioning["Statement"] for a in stmt["Action"]}
        self.assertIn("bedrock-agentcore:TagResource", manage_actions)
        self.assertFalse(any(a.startswith("iam:") for a in manage_actions))

    def test_direct_script_plan_resolves_project_imports(self):
        config_path = Path(self.directory.name) / "config.json"
        config_path.write_text(json.dumps(self.config))
        result = subprocess.run(
            [sys.executable, str(ROOT / "deploy/offline_coach.py"),
             "--config", str(config_path), "plan"],
            cwd=self.directory.name, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("resources", json.loads(result.stdout))


if __name__ == "__main__":
    unittest.main()
