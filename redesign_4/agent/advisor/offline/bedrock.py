"""Converse-backed offline advisor. Preserves Advisor._ask() for legacy pick/review.

No LangChain types are referenced at module level — _ConverseInvoker.invoke()
reads only .type and .content, so bedrock.py stays importable without langchain.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any, Protocol

from ...model import Profile
from ...profile import parse_profile
from ..core import Advisor
from .contracts import (
    AnalysisResult,
    EvidencePacket,
    OfflineAdvice,
    OfflineConfig,
    OfflineServiceError,
    ScoutingContext,
    UsageReceipt,
)

# ── Minimal Converse client protocol ─────────────────────────────────────────


class _ConverseClient(Protocol):
    def converse(self, **kwargs: Any) -> dict[str, Any]: ...


# ── Structured output schema ──────────────────────────────────────────────────

_ADVICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "settings",
        "style",
        "rationale",
        "evidence_ids",
        "uncertainties",
        "counter_tests",
    ],
    "additionalProperties": False,
    "properties": {
        "settings": {"type": "object"},
        "style": {"type": "object"},
        "rationale": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "counter_tests": {"type": "array", "items": {"type": "string"}},
    },
}

# ── Stable system prefix (same for all packets; enables prompt-cache reuse) ──

_SYSTEM_PREFIX = (
    "You advise a 5-a-side football team between matches using ONLY the evidence provided. "
    "Reply with one JSON object matching the schema. "
    "evidence_ids must contain only IDs from the provided evidence claims. "
    "Settings and style must remain within declared bounds. "
    "Separate telemetry observations from coach report claims. "
    "Record your uncertainty about conflicting sources. "
    "The deployed formation and live player assignments must not change. "
    "Schema: " + json.dumps(_ADVICE_SCHEMA, sort_keys=True)
)


# ── AIMessage-compatible carrier ───────────────────────────────────────────────


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


# ── Legacy Advisor._ask() adapter ────────────────────────────────────────────


class _ConverseInvoker:
    """Adapts a Converse client to LangChain's .invoke(messages) interface.

    Used only for the legacy Advisor._ask() path (pick_archetype, review_match).
    review_evidence calls client.converse() directly for UsageReceipt capture.
    """

    def __init__(self, client: _ConverseClient, config: OfflineConfig) -> None:
        self._client = client
        self._config = config

    def invoke(self, messages: list[Any]) -> _Message:
        system_text = ""
        user_text = ""
        for msg in messages:
            text = str(getattr(msg, "content", msg))
            if getattr(msg, "type", "") == "system":
                system_text = text
            else:
                user_text = text

        cfg = self._config
        try:
            response = self._client.converse(
                modelId=cfg.model_id,
                system=[{"text": system_text}] if system_text else [],
                messages=[{"role": "user", "content": [{"text": user_text}]}],
                inferenceConfig={"maxTokens": cfg.max_output_tokens, "temperature": 0},
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Converse call failed: {exc}",
                service="bedrock",
                stage="legacy_invoke",
            ) from exc

        blocks = response.get("output", {}).get("message", {}).get("content", [])
        return _Message(
            "".join(b["text"] for b in blocks if isinstance(b, dict) and "text" in b)
        )


# ── Usage extraction ──────────────────────────────────────────────────────────


def _extract_usage(
    response: dict[str, Any], elapsed_ms: float, config: OfflineConfig
) -> UsageReceipt:
    usage = response.get("usage", {})
    meta = response.get("ResponseMetadata", {})
    return UsageReceipt(
        input_tokens=usage.get("inputTokens", 0),
        output_tokens=usage.get("outputTokens", 0),
        cache_read_tokens=usage.get("cacheReadInputTokens"),
        cache_write_tokens=usage.get("cacheWriteInputTokens"),
        elapsed_ms=elapsed_ms,
        request_id=meta.get("RequestId"),
        model_id=config.model_id,
        cache_mode=config.cache_mode,
    )


# ── Grounded offline advisor ──────────────────────────────────────────────────


class GroundedAdvisor(Advisor):
    """Bedrock-backed offline advisor implementing the EvidenceAdvisor protocol.

    Uses Converse directly in review_evidence for UsageReceipt capture.
    Uses _ConverseInvoker for the legacy Advisor._ask() path.
    """

    def __init__(
        self,
        client: _ConverseClient,
        config: OfflineConfig,
        archetypes: dict[str, Profile],
    ) -> None:
        self._converse_client = client
        self._config = config
        self._last_receipt: UsageReceipt | None = None
        super().__init__(_ConverseInvoker(client, config), archetypes)

    @property
    def last_receipt(self) -> UsageReceipt | None:
        """Usage receipt from the most recent review_evidence call; None before first call."""
        return self._last_receipt

    def review_evidence(
        self,
        packet: EvidencePacket,
        current: Profile,
        scouting: ScoutingContext,
        analysis: AnalysisResult | None,
    ) -> tuple[OfflineAdvice, Profile]:
        """Produce a grounded advice proposal.

        Raises OfflineServiceError for every failure mode — no silent degraded proposal.
        On success sets self._last_receipt for the workflow to collect.
        """
        cfg = self._config
        valid_ids = {c.id for c in packet.claims}

        # Build evidence payload (only claim/source data — no raw manifests)
        evidence_payload: dict[str, Any] = {
            "match_id": packet.match_id,
            "opponent_id": packet.opponent_id,
            "profile_version": packet.profile_version,
            "claims": [
                {"id": c.id, "text": c.text, "kind": c.kind} for c in packet.claims
            ],
            "current_settings": asdict(current.settings),
            "current_style": asdict(current.style),
            "source_digests": packet.source_digests,
        }
        if scouting.notes:
            evidence_payload["scouting_notes"] = [
                {
                    "note_id": n.note_id,
                    "opponent_id": n.opponent_id,
                    "claims": [
                        {"id": c.id, "text": c.text, "kind": c.kind} for c in n.claims
                    ],
                }
                for n in scouting.notes
            ]
        if analysis is not None:
            evidence_payload["analysis_result"] = {
                "result_json": analysis.result_json,
                "code_digest": analysis.code_digest,
                "data_digest": analysis.data_digest,
            }

        evidence_json = json.dumps(evidence_payload, sort_keys=True, allow_nan=False)

        system_blocks: list[dict[str, Any]] = [{"text": _SYSTEM_PREFIX}]
        converse_kwargs: dict[str, Any] = {
            "modelId": cfg.model_id,
            "system": system_blocks,
            "messages": [{"role": "user", "content": [{"text": evidence_json}]}],
            "inferenceConfig": {"maxTokens": cfg.max_output_tokens, "temperature": 0},
        }
        if cfg.structured_outputs:
            converse_kwargs["outputConfig"] = {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "offline_football_advice",
                            "schema": _ADVICE_SCHEMA,
                        }
                    },
                }
            }

        t0 = time.monotonic()
        try:
            response = self._converse_client.converse(**converse_kwargs)
        except Exception as exc:
            raise OfflineServiceError(
                f"Converse call failed: {exc}",
                service="bedrock",
                stage="review_evidence",
            ) from exc
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        stop_reason = response.get("stopReason", "")
        if stop_reason != "end_turn":
            raise OfflineServiceError(
                f"Unexpected stopReason: {stop_reason!r}",
                service="bedrock",
                stage="review_evidence",
                error_code="UNEXPECTED_STOP_REASON",
            )

        blocks = response.get("output", {}).get("message", {}).get("content", [])
        reply_text = "".join(
            b["text"] for b in blocks if isinstance(b, dict) and "text" in b
        ).strip()

        if not reply_text:
            raise OfflineServiceError(
                "Bedrock returned empty content",
                service="bedrock",
                stage="review_evidence",
                error_code="EMPTY_RESPONSE",
            )

        try:
            raw = json.loads(reply_text)
        except json.JSONDecodeError as exc:
            raise OfflineServiceError(
                f"Reply is not valid JSON: {exc}",
                service="bedrock",
                stage="review_evidence",
                error_code="INVALID_JSON",
            ) from exc

        cited_ids: list[str] = raw.get("evidence_ids") or []
        unknown_ids = set(cited_ids) - valid_ids
        if unknown_ids:
            raise OfflineServiceError(
                f"Reply cited unknown evidence IDs: {sorted(unknown_ids)}",
                service="bedrock",
                stage="review_evidence",
                error_code="UNKNOWN_EVIDENCE_IDS",
            )

        try:
            advice = OfflineAdvice(
                settings=raw.get("settings") or {},
                style=raw.get("style") or {},
                rationale=raw.get("rationale") or "",
                evidence_ids=tuple(cited_ids),
                uncertainties=tuple(raw.get("uncertainties") or []),
                counter_tests=tuple(raw.get("counter_tests") or []),
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Advice validation failed: {exc}",
                service="bedrock",
                stage="review_evidence",
                error_code="INVALID_ADVICE",
            ) from exc

        try:
            candidate = parse_profile(
                {
                    **current.to_dict(),
                    "version": f"{current.version}-offline-advice",
                    "settings": advice.settings,
                    "style": advice.style,
                }
            )
        except Exception as exc:
            raise OfflineServiceError(
                f"Candidate profile rejected: {exc}",
                service="bedrock",
                stage="review_evidence",
                error_code="INVALID_CANDIDATE",
            ) from exc

        self._last_receipt = _extract_usage(response, elapsed_ms, cfg)
        return advice, candidate
