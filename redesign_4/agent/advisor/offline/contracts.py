"""Offline coaching contracts: strict Pydantic models, service protocols and errors.

All models use extra="forbid" and frozen=True. Datetime fields require timezone
awareness; naive datetimes are a validation error. Digests are derived from
canonical sorted JSON (allow_nan=False), never user-supplied.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Protocol, Self, TypeAlias, Union

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ...model import Profile
from ...profile import checked_settings, checked_style

# ── JSON value type ──────────────────────────────────────────────────────────

# Recursive JSON value: None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
# Declared as a practical union for Python 3.11 runtime and Pydantic compatibility.
JsonValue: TypeAlias = Union[None, bool, int, float, str, list[Any], dict[str, Any]]

# ── Internal constants ────────────────────────────────────────────────────────

_MAX_ID = 256
_MAX_TEXT = 4000
_MAX_RATIONALE = 4000
_SHA256_HEX_LEN = 64


def _canonical_digest(data: Any) -> str:
    """SHA-256 of canonical sorted JSON (allow_nan=False)."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


# ── Errors ────────────────────────────────────────────────────────────────────


class OfflineServiceError(Exception):
    """A required service adapter failed or produced an unusable outcome.

    Keep SDK/provider exceptions as ``__cause__``. Never include credentials
    or unbounded raw service messages in the ``args`` or attributes.
    """

    def __init__(
        self,
        message: str,
        *,
        service: str,
        stage: str,
        error_code: str | None = None,
        retryable: bool = False,
        outcome: Literal["failed", "unknown"] = "failed",
    ) -> None:
        super().__init__(message)
        self.service = service
        self.stage = stage
        self.error_code = error_code
        self.retryable = retryable
        self.outcome = outcome


# ── Evidence models ───────────────────────────────────────────────────────────


class EvidenceClaim(BaseModel):
    """A single labelled claim with its evidence source references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=_MAX_ID)
    text: str = Field(min_length=1, max_length=_MAX_TEXT)
    kind: Literal["observed", "report_claim", "inferred", "hypothesis"]
    source_ids: tuple[str, ...]

    @field_validator("source_ids", mode="before")
    @classmethod
    def _nonempty_source_ids(cls, v: Any) -> Any:
        if isinstance(v, (list, tuple)):
            for sid in v:
                if not isinstance(sid, str) or not sid:
                    raise ValueError("each source_id must be a nonempty string")
        return v


class EvidencePacket(BaseModel):
    """Deterministic evidence packet derived from a match manifest and report.

    The ``digest`` property is computed from canonical JSON of all other fields;
    it is not an input and is not included in ``model_dump()`` output.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    team_id: str = Field(min_length=1, max_length=_MAX_ID)
    opponent_id: str = Field(min_length=1, max_length=_MAX_ID)
    match_id: str = Field(min_length=1, max_length=_MAX_ID)
    profile_version: str = Field(min_length=1, max_length=_MAX_ID)
    played_at: AwareDatetime
    expires_at: AwareDatetime
    complete: bool
    result: tuple[int, int] | None = None
    claims: tuple[EvidenceClaim, ...]
    source_digests: dict[str, str]

    @model_validator(mode="after")
    def _expires_after_played(self) -> Self:
        if self.expires_at <= self.played_at:
            raise ValueError("expires_at must be after played_at")
        return self

    @property
    def digest(self) -> str:
        """SHA-256 of canonical packet content; recompute rather than store."""
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "team_id": self.team_id,
            "opponent_id": self.opponent_id,
            "match_id": self.match_id,
            "profile_version": self.profile_version,
            "played_at": self.played_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "complete": self.complete,
            "result": list(self.result) if self.result is not None else None,
            "claims": [c.model_dump() for c in self.claims],
            "source_digests": self.source_digests,
        }
        return _canonical_digest(data)


class ScoutingNote(BaseModel):
    """A Memory-stored scouting note with packet identity and claim provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    note_id: str = Field(min_length=1, max_length=_MAX_ID)
    team_id: str = Field(min_length=1, max_length=_MAX_ID)
    opponent_id: str = Field(min_length=1, max_length=_MAX_ID)
    match_id: str = Field(min_length=1, max_length=_MAX_ID)
    profile_version: str = Field(min_length=1, max_length=_MAX_ID)
    played_at: AwareDatetime
    expires_at: AwareDatetime
    packet_digest: str = Field(min_length=_SHA256_HEX_LEN, max_length=_SHA256_HEX_LEN)
    claims: tuple[EvidenceClaim, ...]


class ScoutingContext(BaseModel):
    """Bounded retrieval result from shared Memory.

    A service failure raises ``OfflineServiceError``; it does not produce an
    empty context with no error.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    notes: tuple[ScoutingNote, ...]
    extracted_text: tuple[str, ...]
    excluded: tuple[str, ...]


# ── Service receipt ───────────────────────────────────────────────────────────


class UsageReceipt(BaseModel):
    """Actual usage from one Bedrock Converse call. Unknown usage is None, not zero."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    elapsed_ms: float
    request_id: str | None = None
    model_id: str = Field(min_length=1)
    cache_mode: str = Field(min_length=1)


# ── Advice model ──────────────────────────────────────────────────────────────


class OfflineAdvice(BaseModel):
    """Bounded settings/style proposal validated through profile bounds.

    ``settings`` and ``style`` are validated via ``checked_settings`` /
    ``checked_style`` so they remain within BOUNDS and STYLE_BOUNDS.
    Requires at least one evidence_id; rationale must be nonempty.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    settings: dict[str, float]
    style: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=_MAX_RATIONALE)
    evidence_ids: tuple[str, ...]
    uncertainties: tuple[str, ...]
    counter_tests: tuple[str, ...]

    @field_validator("settings", mode="after")
    @classmethod
    def _valid_settings(cls, v: dict[str, float]) -> dict[str, float]:
        try:
            checked_settings(v)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"invalid settings: {exc}") from exc
        return v

    @field_validator("style", mode="after")
    @classmethod
    def _valid_style(cls, v: dict[str, Any]) -> dict[str, Any]:
        try:
            checked_style(v)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"invalid style: {exc}") from exc
        return v

    @field_validator("evidence_ids", mode="after")
    @classmethod
    def _at_least_one_evidence(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("evidence_ids must contain at least one ID")
        for eid in v:
            if not eid:
                raise ValueError("evidence_ids must not contain empty strings")
        return v


# ── Analysis models ───────────────────────────────────────────────────────────


class AnalysisRequest(BaseModel):
    """A bounded Python script and JSON dataset for the Code Interpreter sandbox."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    data_json: str = Field(min_length=1)
    timeout_seconds: int = Field(ge=1, le=300)

    @property
    def code_digest(self) -> str:
        return _canonical_digest({"code": self.code})

    @property
    def data_digest(self) -> str:
        return _canonical_digest({"data_json": self.data_json})


class AnalysisResult(BaseModel):
    """Output from one sandboxed interpreter session.

    Truncated or error output must not produce a successful result.
    ``cleanup`` records whether session termination was confirmed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stdout: str
    result_json: dict[str, JsonValue]
    session_id: str = Field(min_length=1)
    code_digest: str = Field(min_length=_SHA256_HEX_LEN, max_length=_SHA256_HEX_LEN)
    data_digest: str = Field(min_length=_SHA256_HEX_LEN, max_length=_SHA256_HEX_LEN)
    elapsed_ms: float = Field(ge=0.0)
    cleanup: Literal["confirmed", "unknown"]


# ── Evaluation model ──────────────────────────────────────────────────────────


class EvaluationReport(BaseModel):
    """Results from AgentCore Evaluations for one advisor invocation.

    ``scores`` are 0..4 per rubric criterion. A passing score does not
    approve or deploy a profile; it is advisory only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rubric_version: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    local_checks: dict[str, bool]
    scores: dict[str, float]
    explanations: tuple[str, ...]
    status: Literal["ok", "partial", "failed"]

    @field_validator("scores", mode="after")
    @classmethod
    def _scores_in_range(cls, v: dict[str, float]) -> dict[str, float]:
        for name, score in v.items():
            if not (0.0 <= score <= 4.0):
                raise ValueError(f"score {name!r} must be in 0..4, got {score}")
        return v


# ── Run artifact ──────────────────────────────────────────────────────────────


class RunArtifact(BaseModel):
    """Atomic local artifact written after one offline workflow run.

    ``status`` is ``proposal`` only when advice and profile are present and
    all required steps completed without error. ``approved`` and ``deployed``
    are never valid statuses; profile approval and deployment remain separate
    manual operator actions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    source_digest: str = Field(min_length=_SHA256_HEX_LEN, max_length=_SHA256_HEX_LEN)
    config_digest: str = Field(min_length=_SHA256_HEX_LEN, max_length=_SHA256_HEX_LEN)
    stage_receipts: dict[str, UsageReceipt]
    advice: OfflineAdvice | None = None
    candidate_profile: dict[str, Any] | None = None
    evaluation: EvaluationReport | None = None
    status: Literal["proposal", "rejected", "failed"]
    failure: str | None = None

    @field_validator("candidate_profile", mode="after")
    @classmethod
    def _valid_profile(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            try:
                from ...profile import parse_profile

                parse_profile(v)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"invalid candidate_profile: {exc}") from exc
        return v


# ── Configuration ─────────────────────────────────────────────────────────────


class OfflineConfig(BaseModel):
    """Non-secret configuration for one offline coaching run.

    Describes declared model capabilities and per-service resource/limit
    identifiers. Never contains credentials, role ARNs or secrets.
    ``cache_mode='explicit'`` requires ``explicit_cache=True``.
    ``judge_min_score`` is an advisory threshold 0..4.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_account: str = Field(min_length=1)
    region: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    judge_model_id: str = Field(min_length=1)
    # Model capability declarations — describe a verified selected model.
    structured_outputs: bool = False
    explicit_cache: bool = False
    cache_min_tokens: int | None = None
    supported_ttls: tuple[int, ...] | None = None
    cache_mode: Literal["off", "implicit", "explicit"] = "off"
    # Service resource identifiers.
    memory_store_id: str = Field(min_length=1)
    interpreter_id: str = Field(min_length=1)
    evaluator_id: str = Field(min_length=1)
    # Per-call and session limits.
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    max_output_bytes: int = Field(ge=1)
    max_rounds: int = Field(ge=1)
    judge_min_score: int = Field(ge=0, le=4)

    @model_validator(mode="after")
    def _explicit_cache_requires_capability(self) -> Self:
        if self.cache_mode == "explicit" and not self.explicit_cache:
            raise ValueError(
                "cache_mode='explicit' requires explicit_cache=True in capabilities"
            )
        return self


# ── Service protocols ─────────────────────────────────────────────────────────


class ScoutStore(Protocol):
    """Shared opponent Memory boundary."""

    def publish(self, note: ScoutingNote, packet: EvidencePacket) -> str: ...

    def retrieve(self, packet: EvidencePacket) -> ScoutingContext: ...


class AnalysisSandbox(Protocol):
    """Isolated Code Interpreter boundary."""

    def execute(self, request: AnalysisRequest) -> AnalysisResult: ...


class EvidenceAdvisor(Protocol):
    """Grounded offline advisor boundary (``invoke``-compatible provider inside)."""

    def review_evidence(
        self,
        packet: EvidencePacket,
        current: Profile,
        scouting: ScoutingContext,
        analysis: AnalysisResult | None,
    ) -> tuple[OfflineAdvice, Profile]: ...


class AdviceEvaluator(Protocol):
    """AgentCore Evaluations boundary accepting serialized GenAI spans."""

    def evaluate(self, spans: list[dict[str, JsonValue]]) -> EvaluationReport: ...


# ── Workflow callable ─────────────────────────────────────────────────────────


class RunOffline(Protocol):
    """Type of the ``run_offline`` workflow function defined in ``workflow.py``."""

    def __call__(
        self,
        packet: EvidencePacket,
        current: Profile,
        config: OfflineConfig,
        *,
        scouts: ScoutStore,
        advisor: EvidenceAdvisor,
        evaluator: AdviceEvaluator,
        sandbox: AnalysisSandbox | None = None,
        analysis: AnalysisRequest | None = None,
    ) -> RunArtifact: ...
