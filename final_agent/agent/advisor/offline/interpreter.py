"""AgentCore control-plane preflight and bounded runtime stream execution.

Inject a control client separately for boto3; a combined client remains supported
for callers that already provide both APIs. Configure SDK connect/read timeouts
and retries on injected clients: adapter deadlines cannot cancel an SDK call
before it returns a session/stream. Stream reads have their own absolute deadline.
No local code execution or fallback is available.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Generator
from typing import Any, Literal, Protocol

from .contracts import AnalysisRequest, AnalysisResult, OfflineConfig, OfflineServiceError


class _InterpreterClient(Protocol):
    def start_code_interpreter_session(self, **kwargs: Any) -> dict[str, Any]: ...

    def invoke_code_interpreter(self, **kwargs: Any) -> dict[str, Any]: ...

    def stop_code_interpreter_session(self, **kwargs: Any) -> Any: ...


class _ControlClient(Protocol):
    def get_code_interpreter(self, **kwargs: Any) -> dict[str, Any]: ...


_MAX_EVENTS = 128
_MAX_OUTPUT_BYTES = 65_536
_MAX_INPUT_BYTES = 1_000_000


def _failure(stage: str, code: str) -> OfflineServiceError:
    return OfflineServiceError(
        f"Interpreter {stage} failed ({code})", service="interpreter", stage=stage,
        error_code=code,
    )


class AgentCoreInterpreter:
    """At most two tool invocations per session, with explicit cleanup outcomes."""

    def __init__(
        self, client: _InterpreterClient, config: OfflineConfig, *,
        control_client: _ControlClient | None = None,
    ) -> None:
        self._client = client
        self._control: Any = control_client if control_client is not None else client
        self._config = config

    def execute(self, request: AnalysisRequest) -> AnalysisResult:
        t0 = time.monotonic()
        deadline = t0 + request.timeout_seconds
        interp_id = self._config.interpreter_id
        if len(request.code.encode()) + len(request.data_json.encode()) > _MAX_INPUT_BYTES:
            raise _failure("validate", "INPUT_LIMIT")
        self._verify_sandbox(interp_id)
        self._check_deadline(deadline, "start_session")
        try:
            started = self._client.start_code_interpreter_session(
                codeInterpreterIdentifier=interp_id,
                sessionTimeoutSeconds=request.timeout_seconds,
            )
        except Exception as exc:
            raise _failure("start_session", type(exc).__name__) from exc
        session_id = started.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise _failure("start_session", "MISSING_SESSION")

        cleanup: Literal["confirmed", "unknown"] = "unknown"
        failure: OfflineServiceError | None = None
        result: AnalysisResult | None = None
        try:
            self._invoke(
                interp_id, session_id, "writeFiles",
                {"content": [{"path": "evidence.json", "text": request.data_json}]},
                deadline, "write_evidence",
            )
            stdout = self._invoke(
                interp_id, session_id, "executeCode",
                {"language": "python", "code": request.code},
                deadline, "execute",
            )
            result_json = self._parse_result(stdout)
            result = AnalysisResult(
                stdout=stdout, result_json=result_json, session_id=session_id,
                code_digest=request.code_digest, data_digest=request.data_digest,
                elapsed_ms=(time.monotonic() - t0) * 1000.0, cleanup="unknown",
            )
        except OfflineServiceError as exc:
            failure = exc
        except Exception as exc:
            failure = _failure("execute", type(exc).__name__)
            failure.__cause__ = exc
        finally:
            try:
                self._client.stop_code_interpreter_session(
                    codeInterpreterIdentifier=interp_id, sessionId=session_id,
                )
                cleanup = "confirmed"
            except Exception:
                # A successful computation is retained, but never claims confirmed cleanup.
                cleanup = "unknown"

        if failure is not None:
            failure.args = (f"{failure}; cleanup={cleanup}",)
            setattr(failure, "cleanup", cleanup)
            if cleanup == "unknown":
                failure.outcome = "unknown"
            raise failure
        assert result is not None
        return result.model_copy(update={
            "elapsed_ms": (time.monotonic() - t0) * 1000.0, "cleanup": cleanup,
        })

    def _verify_sandbox(self, interp_id: str) -> None:
        try:
            cfg = self._control.get_code_interpreter(codeInterpreterId=interp_id)
        except Exception as exc:
            raise _failure("preflight", type(exc).__name__) from exc
        if (cfg.get("networkConfiguration") or {}).get("networkMode") != "SANDBOX":
            raise _failure("preflight", "NOT_SANDBOX")
        if cfg.get("executionRoleArn"):
            raise _failure("preflight", "HAS_EXECUTION_ROLE")
        if cfg.get("filesystemConfigurations"):
            raise _failure("preflight", "HAS_FS_MOUNT")
        if cfg.get("certificates"):
            raise _failure("preflight", "HAS_CERTIFICATES")

    @staticmethod
    def _check_deadline(deadline: float, stage: str) -> None:
        if time.monotonic() >= deadline:
            raise _failure(stage, "TIME_LIMIT")

    def _invoke(
        self, interp_id: str, session_id: str, name: str, arguments: dict[str, Any],
        deadline: float, stage: str,
    ) -> str:
        self._check_deadline(deadline, stage)
        try:
            response = self._client.invoke_code_interpreter(
                codeInterpreterIdentifier=interp_id, sessionId=session_id,
                name=name, arguments=arguments,
            )
        except Exception as exc:
            raise _failure(stage, type(exc).__name__) from exc
        stream = response.get("stream")
        if stream is None or isinstance(stream, (dict, str, bytes)):
            raise _failure(stage, "MISSING_STREAM")
        parts: list[str] = []
        size = 0
        count = 0
        events = self._events(stream, deadline, stage)
        try:
            for event in events:
                count += 1
                if count > _MAX_EVENTS:
                    raise _failure(stage, "EVENT_LIMIT")
                if not isinstance(event, dict) or set(event) != {"result"}:
                    raise _failure(stage, "STREAM_ERROR")
                result = event["result"]
                if not isinstance(result, dict):
                    raise _failure(stage, "INVALID_RESULT")
                try:
                    size += len(json.dumps(result, allow_nan=False).encode())
                except (TypeError, ValueError) as exc:
                    raise _failure(stage, "INVALID_OUTPUT") from exc
                if size > _MAX_OUTPUT_BYTES:
                    raise _failure(stage, "OUTPUT_LIMIT")
                structured = result.get("structuredContent") or {}
                if not isinstance(structured, dict):
                    raise _failure(stage, "INVALID_RESULT")
                if (
                    result.get("isError") or result.get("truncated")
                    or structured.get("truncated") or structured.get("stderr")
                    or structured.get("exitCode", 0) != 0
                    or structured.get("taskStatus", "completed") != "completed"
                ):
                    raise _failure(stage, "EXECUTION_ERROR")
                content = result.get("content", [])
                if not isinstance(content, list):
                    raise _failure(stage, "INVALID_RESULT")
                texts: list[str] = []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "text":
                        raise _failure(stage, "UNSUPPORTED_OUTPUT")
                    text = block.get("text", "")
                    if not isinstance(text, str):
                        raise _failure(stage, "INVALID_OUTPUT")
                    texts.append(text)
                # SDK returns stdout both as display content and structured data.
                text = structured.get("stdout", "".join(texts))
                if not isinstance(text, str):
                    raise _failure(stage, "INVALID_OUTPUT")
                parts.append(text)
        except OfflineServiceError:
            raise
        except Exception as exc:
            raise _failure(stage, type(exc).__name__) from exc
        finally:
            events.close()
        self._check_deadline(deadline, stage)
        if not count:
            raise _failure(stage, "EMPTY_STREAM")
        return "".join(parts)

    @staticmethod
    def _events(stream: Any, deadline: float, stage: str) -> Generator[Any, None, None]:
        """Bound a blocking EventStream read without running analysis locally."""
        messages: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        stopped = threading.Event()

        def send(kind: str, value: Any) -> None:
            while not stopped.is_set():
                try:
                    messages.put((kind, value), timeout=0.05)
                    return
                except queue.Full:
                    continue

        def read() -> None:
            try:
                for index, event in enumerate(stream):
                    if stopped.is_set() or index > _MAX_EVENTS:
                        break
                    send("event", event)
                send("end", None)
            except Exception as exc:
                send("error", exc)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _failure(stage, "TIME_LIMIT")
                try:
                    kind, value = messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise _failure(stage, "TIME_LIMIT") from exc
                if kind == "end":
                    break
                if kind == "error":
                    raise _failure(stage, type(value).__name__) from value
                yield value
        finally:
            stopped.set()
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    raise _failure(stage, "STREAM_CLOSE_FAILED") from exc

    @staticmethod
    def _parse_result(stdout: str) -> dict[str, Any]:
        def reject_constant(value: str) -> None:
            raise ValueError(f"nonfinite JSON value: {value}")

        try:
            result = json.loads(stdout, parse_constant=reject_constant)
        except ValueError as exc:
            raise _failure("execute", "NO_RESULT") from exc
        if isinstance(result, dict):
            return result
        raise _failure("execute", "NO_RESULT")
