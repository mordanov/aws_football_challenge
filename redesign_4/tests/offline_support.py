"""Fake service harness and deterministic fixture builders for offline coaching tests.

All fakes record requests, support a configurable exception, and only expose
operations defined in the corresponding service protocol. No AWS credentials
are discovered, cached or used.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from agent.advisor.offline.contracts import (
    AdviceEvaluator,
    AnalysisRequest,
    AnalysisResult,
    AnalysisSandbox,
    EvaluationReport,
    EvidenceClaim,
    EvidencePacket,
    JsonValue,
    OfflineAdvice,
    OfflineConfig,
    ScoutingContext,
    ScoutingNote,
    ScoutStore,
    UsageReceipt,
)
from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE

# ── Canonical test timestamps ─────────────────────────────────────────────────

_PLAYED_AT = datetime(2026, 10, 23, 14, 0, 0, tzinfo=timezone.utc)
_EXPIRES_AT = datetime(2027, 10, 23, 14, 0, 0, tzinfo=timezone.utc)

# ── Source IDs / digests used across fixtures ─────────────────────────────────

MANIFEST_SOURCE_ID = "manifest-001"
REPORT_SOURCE_ID = "report-2026-10-23-r4-vs-redesign3"
_MANIFEST_DIGEST = "e3b0c44298fc1c149afbf4c8996fb924" + "27ae41e4649b934ca495991b7852b855"
_REPORT_DIGEST = "2c624232cdd221771294dfbb310aca00" + "0a0df6ac8b66b696d90ef06fdefb64a3"


# ── Fixture builders ──────────────────────────────────────────────────────────


def make_packet(
    *,
    team_id: str = "aleksalotl",
    opponent_id: str = "redesign_3_v1",
    match_id: str = "match-2026-10-23-r4-vs-redesign3",
    profile_version: str = "baseline",
    result: tuple[int, int] | None = (0, 1),
    extra_claims: tuple[EvidenceClaim, ...] = (),
) -> EvidencePacket:
    """Return a deterministic EvidencePacket representing the report3_case fixture.

    Includes one ``observed`` SHOOT=3 claim and one ``report_claim`` SHOOT=0 claim
    to model the SHOOT contradiction. Neither is a causal goal attribution.
    """
    claims: tuple[EvidenceClaim, ...] = (
        EvidenceClaim(
            id="claim-shoot-observed",
            text="SHOOT command issued 3 times (manifest decision count)",
            kind="observed",
            source_ids=(MANIFEST_SOURCE_ID,),
        ),
        EvidenceClaim(
            id="claim-shoot-report",
            text="Report records SHOOT=0; no shot events confirmed by report API",
            kind="report_claim",
            source_ids=(REPORT_SOURCE_ID,),
        ),
        EvidenceClaim(
            id="claim-own-coach",
            text=(
                "Own coach advice: reduce press_radius, increase support_width. "
                "Not a verified observation."
            ),
            kind="report_claim",
            source_ids=(REPORT_SOURCE_ID,),
        ),
        EvidenceClaim(
            id="claim-opponent-coach",
            text=(
                "Opponent coach advice inferred from report: tight press radius ~12, "
                "aggressive tackling. Counter-strategy, not observed behavior."
            ),
            kind="inferred",
            source_ids=(REPORT_SOURCE_ID,),
        ),
        *extra_claims,
    )
    return EvidencePacket(
        team_id=team_id,
        opponent_id=opponent_id,
        match_id=match_id,
        profile_version=profile_version,
        played_at=_PLAYED_AT,
        expires_at=_EXPIRES_AT,
        complete=True,
        result=result,
        claims=claims,
        source_digests={
            MANIFEST_SOURCE_ID: _MANIFEST_DIGEST,
            REPORT_SOURCE_ID: _REPORT_DIGEST,
        },
    )


def make_note(*, packet: EvidencePacket | None = None) -> ScoutingNote:
    """Return a ScoutingNote derived from ``packet`` (or a default packet)."""
    if packet is None:
        packet = make_packet()
    return ScoutingNote(
        note_id="note-001",
        team_id=packet.team_id,
        opponent_id=packet.opponent_id,
        match_id=packet.match_id,
        profile_version=packet.profile_version,
        played_at=packet.played_at,
        expires_at=packet.expires_at,
        packet_digest=packet.digest,
        claims=packet.claims,
    )


def make_config(
    *,
    cache_mode: str = "off",
    explicit_cache: bool = False,
    structured_outputs: bool = False,
) -> OfflineConfig:
    """Return a minimal valid OfflineConfig for unit tests."""
    return OfflineConfig(
        expected_account="123456789012",
        region="us-east-1",
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        judge_model_id="anthropic.claude-3-5-haiku-20241022-v1:0",
        structured_outputs=structured_outputs,
        explicit_cache=explicit_cache,
        cache_mode=cache_mode,  # type: ignore[arg-type]
        memory_store_id="memory-store-001",
        interpreter_id="interpreter-001",
        evaluator_id="evaluator-001",
        max_input_tokens=8000,
        max_output_tokens=1000,
        timeout_seconds=60,
        max_output_bytes=65536,
        max_rounds=1,
        judge_min_score=3,
    )


def _default_usage() -> UsageReceipt:
    return UsageReceipt(
        input_tokens=100,
        output_tokens=50,
        elapsed_ms=120.0,
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        cache_mode="off",
    )


def _default_advice() -> OfflineAdvice:
    return OfflineAdvice(
        settings={k: v for k, v in asdict(DEFAULT_SETTINGS).items()},
        style={k: v for k, v in asdict(DEFAULT_STYLE).items()},
        rationale="maintain current settings; evidence is insufficient for a change",
        evidence_ids=("claim-shoot-observed",),
        uncertainties=("SHOOT contradiction unresolved between manifest and report",),
        counter_tests=("verify shot_range adequacy if opponent press intensifies",),
    )


# ── Fake service adapters ─────────────────────────────────────────────────────


class FakeConverse:
    """Fake LangChain-compatible LLM that records invoke calls.

    Returns a fixed JSON reply or raises ``side_effect`` if set.
    Only exposes ``invoke`` — the boundary used by ``Advisor``.
    """

    def __init__(
        self,
        reply_json: str | None = None,
        *,
        side_effect: Exception | None = None,
    ) -> None:
        self.calls: list[Any] = []
        self._reply_json = reply_json
        self._side_effect = side_effect

    def invoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        if self._side_effect is not None:
            raise self._side_effect

        class _FakeMessage:
            def __init__(self, content: str) -> None:
                self.content = content

        default_reply = (
            '{"settings": %s, "style": %s, "rationale": "maintain baseline"}'
            % (
                str({k: v for k, v in asdict(DEFAULT_SETTINGS).items()}).replace("'", '"'),
                str({k: v for k, v in asdict(DEFAULT_STYLE).items()})
                .replace("'", '"')
                .replace("True", "true")
                .replace("False", "false"),
            )
        )
        return _FakeMessage(self._reply_json if self._reply_json is not None else default_reply)


class FakeMemory:
    """Fake ScoutStore that satisfies the ScoutStore protocol.

    Records publish/retrieve calls; raises ``side_effect`` if set.
    Does not discover or use any AWS credentials.
    """

    def __init__(self, *, side_effect: Exception | None = None) -> None:
        self.published: list[tuple[ScoutingNote, EvidencePacket]] = []
        self.retrieved: list[EvidencePacket] = []
        self._side_effect = side_effect
        self._notes: list[ScoutingNote] = []

    def publish(self, note: ScoutingNote, packet: EvidencePacket) -> str:
        if self._side_effect is not None:
            raise self._side_effect
        self.published.append((note, packet))
        self._notes.append(note)
        return note.note_id

    def retrieve(self, packet: EvidencePacket) -> ScoutingContext:
        if self._side_effect is not None:
            raise self._side_effect
        self.retrieved.append(packet)
        matching = tuple(
            n
            for n in self._notes
            if n.opponent_id == packet.opponent_id and n.expires_at > packet.played_at
        )
        return ScoutingContext(notes=matching, extracted_text=(), excluded=())


# Structural check: FakeMemory satisfies ScoutStore at runtime.
_: ScoutStore = FakeMemory()


class FakeInterpreter:
    """Fake AnalysisSandbox that satisfies the AnalysisSandbox protocol.

    Records execute calls; raises ``side_effect`` if set.
    Returns a minimal AnalysisResult with the request's digests.
    """

    def __init__(self, *, side_effect: Exception | None = None) -> None:
        self.requests: list[AnalysisRequest] = []
        self._side_effect = side_effect

    def execute(self, request: AnalysisRequest) -> AnalysisResult:
        if self._side_effect is not None:
            raise self._side_effect
        self.requests.append(request)
        return AnalysisResult(
            stdout="",
            result_json={},
            session_id="fake-session-001",
            code_digest=request.code_digest,
            data_digest=request.data_digest,
            elapsed_ms=10.0,
            cleanup="confirmed",
        )


# Structural check: FakeInterpreter satisfies AnalysisSandbox at runtime.
__: AnalysisSandbox = FakeInterpreter()


class FakeEvaluator:
    """Fake AdviceEvaluator that satisfies the AdviceEvaluator protocol.

    Records evaluate calls; raises ``side_effect`` if set.
    Returns a fixed EvaluationReport at or above judge_min_score=3.
    """

    def __init__(
        self,
        *,
        score: float = 3.0,
        side_effect: Exception | None = None,
    ) -> None:
        self.span_batches: list[list[dict[str, JsonValue]]] = []
        self._score = score
        self._side_effect = side_effect

    def evaluate(self, spans: list[dict[str, JsonValue]]) -> EvaluationReport:
        if self._side_effect is not None:
            raise self._side_effect
        self.span_batches.append(spans)
        return EvaluationReport(
            rubric_version="v1",
            evaluator_id="fake-evaluator",
            trace_id="fake-trace-001",
            local_checks={"schema_valid": True, "profile_in_bounds": True},
            scores={"faithfulness": self._score},
            explanations=("advice references provided evidence IDs",),
            status="ok",
        )


# Structural check: FakeEvaluator satisfies AdviceEvaluator at runtime.
___: AdviceEvaluator = FakeEvaluator()


# ── Default Converse response JSON for FakeBedrockClient ─────────────────────
# References "claim-shoot-observed" which must exist in make_packet().

def _make_default_advice_json() -> str:
    return json.dumps(
        {
            "settings": asdict(DEFAULT_SETTINGS),
            "style": asdict(DEFAULT_STYLE),
            "rationale": "maintain baseline; evidence insufficient for a specific change",
            "evidence_ids": ["claim-shoot-observed"],
            "uncertainties": ["SHOOT count contradicts between manifest and report"],
            "counter_tests": ["verify shot range if opponent increases press intensity"],
        }
    )


class FakeBedrockClient:
    """Fake Bedrock Converse client that records calls and returns a fixed response.

    Satisfies the ``_ConverseClient`` protocol used by ``GroundedAdvisor``.
    No AWS credentials are discovered, cached or used.
    """

    def __init__(
        self,
        response_json: str | None = None,
        *,
        stop_reason: str = "end_turn",
        side_effect: Exception | None = None,
        cache_tokens: dict[str, int] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response_json = response_json
        self._stop_reason = stop_reason
        self._side_effect = side_effect
        self._cache_tokens = cache_tokens

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._side_effect is not None:
            raise self._side_effect
        content = self._response_json if self._response_json is not None else _make_default_advice_json()
        usage: dict[str, int] = {"inputTokens": 100, "outputTokens": 50}
        if self._cache_tokens:
            usage.update(self._cache_tokens)
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": content}],
                }
            },
            "stopReason": self._stop_reason,
            "usage": usage,
            "ResponseMetadata": {"RequestId": "fake-request-id-001"},
        }
