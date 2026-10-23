"""Tests for AgentCoreInterpreter: sandbox verification, execution, cleanup."""

from __future__ import annotations

import json
import threading
import time
import unittest
from typing import Any
from unittest.mock import patch

from tests.offline_support import make_config

_SIMPLE_CODE = "import json; result = json.load(open('evidence.json')); print(json.dumps({'rows': len(result)}))"
_RESULT_JSON = {"rows": 4}
_STDOUT = json.dumps(_RESULT_JSON)
_DATA_JSON = json.dumps([1, 2, 3, 4])


# ── Fake boto3-style AgentCore Code Interpreter client ────────────────────────


class FakeInterpreterClient:
    """Records session/invoke calls; no AWS credentials used."""

    def __init__(
        self,
        *,
        interpreter_type: str = "SANDBOX",
        execution_role: str | None = None,
        mounts: list[str] | None = None,
        write_events: list[dict[str, Any]] | None = None,
        exec_events: list[dict[str, Any]] | None = None,
        start_side_effect: Exception | None = None,
        write_side_effect: Exception | None = None,
        exec_side_effect: Exception | None = None,
        stop_side_effect: Exception | None = None,
    ) -> None:
        self.started: list[dict[str, Any]] = []
        self.invocations: list[dict[str, Any]] = []
        self.stopped: list[dict[str, Any]] = []
        self._interpreter_type = interpreter_type
        self._execution_role = execution_role
        self._mounts = mounts or []
        # Default happy-path events
        self._write_events = write_events if write_events is not None else [
            {"result": {"isError": False, "content": []}}
        ]
        self._exec_events = exec_events if exec_events is not None else [
            {"result": {"isError": False, "content": [{"type": "text", "text": _STDOUT}],
                        "structuredContent": {"stdout": _STDOUT, "exitCode": 0,
                                              "taskStatus": "completed"}}},
        ]
        self._start_side_effect = start_side_effect
        self._write_side_effect = write_side_effect
        self._exec_side_effect = exec_side_effect
        self._stop_side_effect = stop_side_effect
        self._session_counter = 0

    def get_code_interpreter(self, **kwargs: Any) -> dict[str, Any]:
        self.control_params = kwargs
        config: dict[str, Any] = {"networkConfiguration": {"networkMode": self._interpreter_type}}
        if self._execution_role:
            config["executionRoleArn"] = self._execution_role
        if self._mounts:
            config["filesystemConfigurations"] = [{"efsConfiguration": {"mountPath": m}}
                                                   for m in self._mounts]
        return config

    def start_code_interpreter_session(self, **kwargs: Any) -> dict[str, Any]:
        if self._start_side_effect:
            raise self._start_side_effect
        self._session_counter += 1
        self.started.append(kwargs)
        return {"sessionId": f"sess-{self._session_counter:04d}"}

    def invoke_code_interpreter(self, **kwargs: Any) -> dict[str, Any]:
        self.invocations.append(kwargs)
        name = kwargs.get("name", "")
        if name == "writeFiles":
            if self._write_side_effect:
                raise self._write_side_effect
            return {"stream": iter(self._write_events)}
        if name == "executeCode":
            if self._exec_side_effect:
                raise self._exec_side_effect
            return {"stream": iter(self._exec_events)}
        return {"stream": iter(())}

    def stop_code_interpreter_session(self, **kwargs: Any) -> None:
        self.stopped.append(kwargs)
        if self._stop_side_effect:
            raise self._stop_side_effect


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_interpreter(client=None):
    from agent.advisor.offline.interpreter import AgentCoreInterpreter

    cfg = make_config()
    return AgentCoreInterpreter(client or FakeInterpreterClient(), cfg)


def _make_request(code: str = _SIMPLE_CODE, data_json: str = _DATA_JSON):
    from agent.advisor.offline.contracts import AnalysisRequest

    return AnalysisRequest(code=code, data_json=data_json, timeout_seconds=60)


class SandboxVerificationTest(unittest.TestCase):
    def test_public_interpreter_raises_before_session_start(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient(interpreter_type="PUBLIC")
        interp = _make_interpreter(client)
        req = _make_request()
        with self.assertRaises(OfflineServiceError) as ctx:
            interp.execute(req)
        self.assertEqual(ctx.exception.stage, "preflight")
        # No session should have been started
        self.assertEqual(len(client.started), 0)

    def test_execution_role_raises_before_session_start(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient(execution_role="arn:aws:iam::123:role/CodeRole")
        interp = _make_interpreter(client)
        req = _make_request()
        with self.assertRaises(OfflineServiceError) as ctx:
            interp.execute(req)
        self.assertEqual(ctx.exception.stage, "preflight")
        self.assertEqual(len(client.started), 0)

    def test_filesystem_mount_raises_before_session_start(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient(mounts=["/efs"])
        interp = _make_interpreter(client)
        req = _make_request()
        with self.assertRaises(OfflineServiceError) as ctx:
            interp.execute(req)
        self.assertEqual(ctx.exception.stage, "preflight")
        self.assertEqual(len(client.started), 0)

    def test_sandbox_type_proceeds_to_execution(self):
        client = FakeInterpreterClient(interpreter_type="SANDBOX")
        interp = _make_interpreter(client)
        req = _make_request()
        result = interp.execute(req)
        self.assertIsNotNone(result)


class HappyPathTest(unittest.TestCase):
    def test_returns_analysis_result(self):
        interp = _make_interpreter()
        result = interp.execute(_make_request())
        self.assertIsNotNone(result)

    def test_result_json_matches_execution_output(self):
        interp = _make_interpreter()
        result = interp.execute(_make_request())
        self.assertEqual(result.result_json, _RESULT_JSON)

    def test_pretty_printed_json_object_is_parsed_in_full(self):
        expected = {"observed": {"count": 3}, "report": {"count": 0}}
        stdout = "\n" + json.dumps(expected, indent=2) + "\n"
        client = FakeInterpreterClient(exec_events=[
            {"result": {"structuredContent": {"stdout": stdout}}}
        ])
        result = _make_interpreter(client).execute(_make_request())
        self.assertEqual(result.result_json, expected)
        self.assertEqual(result.stdout, stdout)
        self.assertEqual(result.cleanup, "confirmed")

    def test_stdout_captured(self):
        interp = _make_interpreter()
        result = interp.execute(_make_request())
        self.assertIn(_STDOUT, result.stdout)

    def test_session_id_captured(self):
        interp = _make_interpreter()
        result = interp.execute(_make_request())
        self.assertTrue(result.session_id.startswith("sess-"))

    def test_code_and_data_digests_match_request(self):
        req = _make_request()
        interp = _make_interpreter()
        result = interp.execute(req)
        self.assertEqual(result.code_digest, req.code_digest)
        self.assertEqual(result.data_digest, req.data_digest)

    def test_cleanup_confirmed_on_success(self):
        interp = _make_interpreter()
        result = interp.execute(_make_request())
        self.assertEqual(result.cleanup, "confirmed")

    def test_writes_evidence_json_before_execute(self):
        client = FakeInterpreterClient()
        interp = _make_interpreter(client)
        interp.execute(_make_request())
        write_calls = [i for i in client.invocations if i.get("name") == "writeFiles"]
        self.assertTrue(len(write_calls) >= 1)
        # write must come before execute
        write_idx = next(i for i, c in enumerate(client.invocations) if c.get("name") == "writeFiles")
        exec_idx = next(i for i, c in enumerate(client.invocations) if c.get("name") == "executeCode")
        self.assertLess(write_idx, exec_idx)

    def test_uses_interpreter_id_from_config(self):
        client = FakeInterpreterClient()
        interp = _make_interpreter(client)
        interp.execute(_make_request())
        for call in client.invocations:
            self.assertEqual(call["codeInterpreterIdentifier"], "interpreter-001")


class CleanupTest(unittest.TestCase):
    def test_session_stopped_after_successful_execution(self):
        client = FakeInterpreterClient()
        interp = _make_interpreter(client)
        interp.execute(_make_request())
        self.assertEqual(len(client.stopped), 1)

    def test_session_stopped_after_execution_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient(exec_side_effect=RuntimeError("timeout"))
        interp = _make_interpreter(client)
        with self.assertRaises(OfflineServiceError):
            interp.execute(_make_request())
        self.assertEqual(len(client.stopped), 1)

    def test_session_stopped_after_write_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient(write_side_effect=IOError("write failed"))
        interp = _make_interpreter(client)
        with self.assertRaises(OfflineServiceError):
            interp.execute(_make_request())
        self.assertEqual(len(client.stopped), 1)

    def test_cleanup_unknown_when_stop_raises(self):
        client = FakeInterpreterClient(stop_side_effect=RuntimeError("stop failed"))
        interp = _make_interpreter(client)
        # Should still return a result but cleanup=unknown
        result = interp.execute(_make_request())
        self.assertEqual(result.cleanup, "unknown")


class CodeFailureTest(unittest.TestCase):
    def test_execution_error_event_raises_offline_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        error_events = [{"internalServerException": {"message": "failure"}}]
        client = FakeInterpreterClient(exec_events=error_events)
        interp = _make_interpreter(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            interp.execute(_make_request())
        self.assertEqual(ctx.exception.stage, "execute")

    def test_start_failure_raises_offline_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient(start_side_effect=ConnectionError("unavailable"))
        interp = _make_interpreter(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            interp.execute(_make_request())
        self.assertEqual(ctx.exception.stage, "start_session")


# Structural check: AgentCoreInterpreter satisfies AnalysisSandbox at import time.
def _check_protocol() -> None:
    from agent.advisor.offline.contracts import AnalysisSandbox
    from agent.advisor.offline.interpreter import AgentCoreInterpreter

    _: AnalysisSandbox = AgentCoreInterpreter(FakeInterpreterClient(), make_config())


class StreamSafetyTest(unittest.TestCase):
    def test_stream_is_closed_when_output_is_rejected(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        class Stream:
            closed = False

            def __iter__(self):
                return iter([{"result": {"isError": True}}])

            def close(self):
                self.closed = True

        stream = Stream()

        class Client(FakeInterpreterClient):
            def invoke_code_interpreter(self, **kwargs):
                return {"stream": stream}

        client = Client()
        with self.assertRaises(OfflineServiceError):
            _make_interpreter(client).execute(_make_request())
        self.assertTrue(stream.closed)
        self.assertEqual(len(client.stopped), 1)

    def test_rejects_error_incomplete_empty_nonfinite_and_oversize_streams(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        cases: list[list[dict[str, Any]]] = [
            [],
            [{"result": {"isError": True, "content": []}}],
            [{"result": {"structuredContent": {"exitCode": 1}}}],
            [{"result": {"structuredContent": {"stderr": "traceback"}}}],
            [{"result": {"structuredContent": {"taskStatus": "working"}}}],
            [{"result": {"content": [{"type": "text", "text": '{"value": NaN}'}]}}],
            [{"result": {"content": [{"type": "text", "text": 'log\n{"count": 3}'}]}}],
            [{"result": {"content": [{"type": "text", "text": '{}\n{"count": 3}'}]}}],
            [{"result": {"content": [{"type": "text", "text": '[1, 2]'}]}}],
            [{"result": {"content": [{"type": "text", "text": '{}\n{"cut":'}]}}],
            [{"result": {"content": [{"type": "text", "text": "x" * 65537}]}}],
            [{"result": {"content": [], "truncated": True}}],
            [{"result": {"content": []}}] * 129,
        ]
        for events in cases:
            with self.subTest(events=str(events)[:80]):
                client = FakeInterpreterClient(exec_events=events)
                with self.assertRaises(OfflineServiceError) as ctx:
                    _make_interpreter(client).execute(_make_request())
                self.assertEqual(len(client.stopped), 1)
                self.assertEqual(getattr(ctx.exception, "cleanup"), "confirmed")
                self.assertIn("cleanup=confirmed", str(ctx.exception))

    def test_write_stream_error_prevents_code_execution(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient(write_events=[{"result": {"isError": True}}])
        with self.assertRaises(OfflineServiceError) as ctx:
            _make_interpreter(client).execute(_make_request())
        self.assertEqual(ctx.exception.stage, "write_evidence")
        self.assertEqual([c["name"] for c in client.invocations], ["writeFiles"])
        self.assertEqual(len(client.stopped), 1)

    def test_cleanup_failure_is_explicit_on_execution_failure(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient(
            exec_side_effect=RuntimeError("execute"), stop_side_effect=RuntimeError("stop"),
        )
        with self.assertRaises(OfflineServiceError) as ctx:
            _make_interpreter(client).execute(_make_request())
        self.assertEqual(getattr(ctx.exception, "cleanup"), "unknown")
        self.assertEqual(ctx.exception.outcome, "unknown")
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_blocked_stream_is_closed_on_absolute_deadline(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        released = threading.Event()

        class BlockingStream:
            closed = False

            def __iter__(self):
                released.wait(3)
                return iter(())

            def close(self):
                self.closed = True
                released.set()

        stream = BlockingStream()

        class Client(FakeInterpreterClient):
            def invoke_code_interpreter(self, **kwargs):
                return {"stream": stream}

        client = Client()
        started = time.monotonic()
        with self.assertRaises(OfflineServiceError) as ctx:
            _make_interpreter(client).execute(
                _make_request().model_copy(update={"timeout_seconds": 1})
            )
        self.assertEqual(ctx.exception.error_code, "TIME_LIMIT")
        self.assertTrue(stream.closed)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(len(client.stopped), 1)

    def test_deadline_after_start_still_stops_session(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = FakeInterpreterClient()
        with patch("agent.advisor.offline.interpreter.time.monotonic", side_effect=[0, 0, 61]):
            with self.assertRaises(OfflineServiceError):
                _make_interpreter(client).execute(_make_request())
        self.assertEqual(len(client.stopped), 1)

    def test_legacy_events_field_is_not_accepted(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        class Legacy(FakeInterpreterClient):
            def invoke_code_interpreter(self, **kwargs):
                return {"events": [{"result": {"content": []}}]}

        client = Legacy()
        with self.assertRaises(OfflineServiceError):
            _make_interpreter(client).execute(_make_request())
        self.assertEqual(len(client.stopped), 1)


if __name__ == "__main__":
    unittest.main()
