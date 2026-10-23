"""AgentCore Code Interpreter sandbox adapter.

Verifies the interpreter is a SANDBOX type before starting a session.
Writes evidence.json, executes code, and always stops the session.
No local exec, subprocess fallback, shell tool, or AWS credential upload.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from .contracts import (
    AnalysisRequest,
    AnalysisResult,
    OfflineConfig,
    OfflineServiceError,
)

# ── Interpreter client protocol ───────────────────────────────────────────────


class _InterpreterClient(Protocol):
    def get_code_interpreter(self, **kwargs: Any) -> dict[str, Any]: ...

    def start_code_interpreter_session(self, **kwargs: Any) -> dict[str, Any]: ...

    def invoke_code_interpreter(self, **kwargs: Any) -> dict[str, Any]: ...

    def stop_code_interpreter_session(self, **kwargs: Any) -> None: ...


# ── Preflight constants ───────────────────────────────────────────────────────

_REQUIRED_TYPE = "SANDBOX"
_MAX_STDOUT_CHARS = 16_384


# ── AgentCore Code Interpreter adapter ───────────────────────────────────────


class AgentCoreInterpreter:
    """AnalysisSandbox backed by AgentCore Code Interpreter.

    execute: verifies SANDBOX config, starts session, writes evidence.json,
             runs code, stops session in finally, returns AnalysisResult.
    """

    def __init__(self, client: _InterpreterClient, config: OfflineConfig) -> None:
        self._client = client
        self._config = config

    def execute(self, request: AnalysisRequest) -> AnalysisResult:
        """Run request.code in a verified SANDBOX session.

        Raises OfflineServiceError for any failure; always stops the session.
        """
        interp_id = self._config.interpreter_id

        # 1. Preflight: verify interpreter type before any session creation
        self._verify_sandbox(interp_id)

        # 2. Start session
        try:
            started = self._client.start_code_interpreter_session(
                codeInterpreterIdentifier=interp_id,
                sessionTimeoutSeconds=request.timeout_seconds,
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Failed to start interpreter session: {type(exc).__name__}",
                service="interpreter",
                stage="start_session",
            ) from exc

        session_id: str = started["sessionId"]
        t0 = time.monotonic()
        cleanup: str = "unknown"

        try:
            # 3. Write evidence data
            self._write_evidence(interp_id, session_id, request.data_json)

            # 4. Execute code and consume events
            stdout, result_json = self._run_code(interp_id, session_id, request)

        finally:
            # 5. Always attempt to stop the session
            try:
                self._client.stop_code_interpreter_session(
                    codeInterpreterIdentifier=interp_id,
                    sessionId=session_id,
                )
                cleanup = "confirmed"
            except Exception:
                cleanup = "unknown"

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        return AnalysisResult(
            stdout=stdout,
            result_json=result_json,
            session_id=session_id,
            code_digest=request.code_digest,
            data_digest=request.data_digest,
            elapsed_ms=elapsed_ms,
            cleanup=cleanup,  # type: ignore[arg-type]
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _verify_sandbox(self, interp_id: str) -> None:
        try:
            resp = self._client.get_code_interpreter(
                codeInterpreterIdentifier=interp_id
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Cannot verify interpreter config: {type(exc).__name__}",
                service="interpreter",
                stage="preflight",
            ) from exc

        cfg = resp.get("codeInterpreter") or {}
        net_type = (cfg.get("networkConfiguration") or {}).get("networkType", "")
        if net_type != _REQUIRED_TYPE:
            raise OfflineServiceError(
                f"Interpreter must be SANDBOX type; got {net_type!r}. "
                "Refusing to downgrade isolation.",
                service="interpreter",
                stage="preflight",
                error_code="NOT_SANDBOX",
            )
        if cfg.get("executionRole"):
            raise OfflineServiceError(
                "Interpreter has an execution role; credential upload not allowed.",
                service="interpreter",
                stage="preflight",
                error_code="HAS_EXECUTION_ROLE",
            )
        if cfg.get("customFileSystem"):
            raise OfflineServiceError(
                "Interpreter has filesystem mounts; isolation boundary violated.",
                service="interpreter",
                stage="preflight",
                error_code="HAS_FS_MOUNT",
            )

    def _write_evidence(self, interp_id: str, session_id: str, data_json: str) -> None:
        try:
            resp = self._client.invoke_code_interpreter(
                codeInterpreterIdentifier=interp_id,
                sessionId=session_id,
                name="writeFiles",
                arguments={"content": [{"path": "evidence.json", "text": data_json}]},
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Failed to write evidence file: {type(exc).__name__}",
                service="interpreter",
                stage="write_evidence",
            ) from exc

        for event in resp.get("events") or []:
            if "error" in event:
                raise OfflineServiceError(
                    f"writeFiles failed: {event['error'].get('message', 'unknown')}",
                    service="interpreter",
                    stage="write_evidence",
                    error_code="WRITE_ERROR",
                )

    def _run_code(
        self,
        interp_id: str,
        session_id: str,
        request: AnalysisRequest,
    ) -> tuple[str, dict[str, Any]]:
        try:
            resp = self._client.invoke_code_interpreter(
                codeInterpreterIdentifier=interp_id,
                sessionId=session_id,
                name="executeCode",
                arguments={"language": "python", "code": request.code},
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Code execution call failed: {type(exc).__name__}",
                service="interpreter",
                stage="execute",
            ) from exc

        stdout_parts: list[str] = []
        result_json: dict[str, Any] | None = None

        for event in resp.get("events") or []:
            if "error" in event:
                msg = (event["error"] or {}).get("message", "execution failed")
                raise OfflineServiceError(
                    f"Code execution error: {msg}",
                    service="interpreter",
                    stage="execute",
                    error_code="EXECUTION_ERROR",
                )
            if "output" in event:
                text = (event["output"] or {}).get("text", "")
                stdout_parts.append(text)
            if "result" in event:
                for block in (event["result"] or {}).get("content") or []:
                    if block.get("type") == "json":
                        result_json = block.get("value") or {}

        stdout = "".join(stdout_parts)[:_MAX_STDOUT_CHARS]

        if result_json is None:
            # Fall back: parse last JSON line from stdout
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        result_json = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        pass

        if result_json is None:
            raise OfflineServiceError(
                "Code execution produced no JSON result",
                service="interpreter",
                stage="execute",
                error_code="NO_RESULT",
            )

        return stdout, result_json
