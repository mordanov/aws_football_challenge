"""Local SDK-model regressions; explicit dummy credentials, no AWS requests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any

import botocore.session
from botocore.stub import ANY, Stubber
from botocore.validate import validate_parameters

from agent.advisor.offline.contracts import AnalysisRequest
from agent.advisor.offline.interpreter import AgentCoreInterpreter
from agent.advisor.offline.memory import AgentCoreMemory
from tests.offline_support import make_config, make_note, make_packet


def sdk_client(service: str) -> Any:
    return botocore.session.get_session().create_client(
        service,
        region_name="us-east-1",
        aws_access_key_id="local-test",
        aws_secret_access_key="local-test",
    )


class SDKAdapterTest(unittest.TestCase):
    def test_memory_create_and_list_use_real_sdk_parameters(self) -> None:
        client = sdk_client("bedrock-agentcore")
        packet = make_packet()
        with Stubber(client) as stub:
            stub.add_response(
                "create_event",
                {"event": {
                    "memoryId": make_config().memory_store_id,
                    "actorId": "team",
                    "sessionId": "opponent",
                    "eventId": "event-001",
                    "eventTimestamp": packet.played_at,
                    "payload": [],
                }},
                {
                    "memoryId": make_config().memory_store_id,
                    "actorId": ANY,
                    "sessionId": ANY,
                    "eventTimestamp": packet.played_at,
                    "payload": ANY,
                    "clientToken": ANY,
                },
            )
            stub.add_response(
                "list_events",
                {"events": []},
                {
                    "memoryId": make_config().memory_store_id,
                    "actorId": ANY,
                    "sessionId": ANY,
                    "includePayloads": True,
                    "maxResults": 20,
                },
            )
            memory = AgentCoreMemory(client, make_config())
            memory.publish(make_note(packet=packet), packet)
            self.assertEqual(memory.retrieve(packet).notes, ())
            stub.assert_no_pending_responses()

    def test_separate_control_client_and_sdk_shaped_runtime_stream(self) -> None:
        control = sdk_client("bedrock-agentcore-control")
        model = botocore.session.get_session().get_service_model("bedrock-agentcore")
        calls: list[str] = []

        class Runtime:
            def start_code_interpreter_session(self, **kwargs: Any) -> dict[str, Any]:
                validate_parameters(
                    kwargs, model.operation_model("StartCodeInterpreterSession").input_shape
                )
                calls.append("start")
                return {"sessionId": "test-session"}

            def invoke_code_interpreter(self, **kwargs: Any) -> dict[str, Any]:
                validate_parameters(
                    kwargs, model.operation_model("InvokeCodeInterpreter").input_shape
                )
                calls.append(kwargs["name"])
                output = "{}" if kwargs["name"] == "executeCode" else ""
                return {
                    "stream": iter(
                        [{"result": {"content": [{"type": "text", "text": output}],
                                     "isError": False}}]
                    )
                }

            def stop_code_interpreter_session(self, **kwargs: Any) -> None:
                validate_parameters(
                    kwargs, model.operation_model("StopCodeInterpreterSession").input_shape
                )
                calls.append("stop")

        now = datetime.now(timezone.utc)
        with Stubber(control) as stub:
            stub.add_response(
                "get_code_interpreter",
                {
                    "codeInterpreterId": "interpreter-001",
                    "codeInterpreterArn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:code-interpreter/test",
                    "name": "test",
                    "networkConfiguration": {"networkMode": "SANDBOX"},
                    "status": "READY",
                    "createdAt": now,
                    "lastUpdatedAt": now,
                },
                {"codeInterpreterId": "interpreter-001"},
            )
            interpreter = AgentCoreInterpreter(Runtime(), make_config(), control_client=control)
            result = interpreter.execute(
                AnalysisRequest(code="print('{}')", data_json="{}", timeout_seconds=30)
            )
            self.assertEqual(result.result_json, {})
            self.assertEqual(result.cleanup, "confirmed")
            self.assertEqual(calls, ["start", "writeFiles", "executeCode", "stop"])
            stub.assert_no_pending_responses()
