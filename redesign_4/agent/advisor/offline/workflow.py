"""Offline coaching workflow: retrieve → [analyze] → advise → evaluate → artifact.

publish() is a separate operator step; run_offline() never writes to Memory.
No live formation changes, profile approval or player deployment occur here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from typing import Any

from ...model import Profile
from .contracts import (
    AdviceEvaluator,
    AnalysisRequest,
    AnalysisResult,
    AnalysisSandbox,
    EvidenceAdvisor,
    EvidencePacket,
    JsonValue,
    OfflineConfig,
    OfflineServiceError,
    RunArtifact,
    ScoutStore,
    UsageReceipt,
)


def run_offline(
    packet: EvidencePacket,
    current: Profile,
    config: OfflineConfig,
    *,
    scouts: ScoutStore,
    advisor: EvidenceAdvisor,
    evaluator: AdviceEvaluator,
    sandbox: AnalysisSandbox | None = None,
    analysis: AnalysisRequest | None = None,
) -> RunArtifact:
    """Run all offline coaching stages; return an atomic RunArtifact.

    publish() is a separate operator step — this function never calls scouts.publish().
    No writes to player config, recommended profile or trainer approval events.
    """
    run_id = uuid.uuid4().hex
    source_digest = packet.digest
    config_digest = hashlib.sha256(config.model_dump_json().encode()).hexdigest()
    stage_receipts: dict[str, UsageReceipt] = {}

    # 1. Retrieve scouting context (required)
    try:
        scouting = scouts.retrieve(packet)
    except OfflineServiceError as exc:
        return _failed(run_id, source_digest, config_digest, stage_receipts, f"retrieve: {exc}")

    # 2. Optional analysis
    analysis_result: AnalysisResult | None = None
    if sandbox is not None and analysis is not None:
        try:
            analysis_result = sandbox.execute(analysis)
        except OfflineServiceError as exc:
            return _failed(run_id, source_digest, config_digest, stage_receipts, f"analyze: {exc}")

    # 3. Bounded advice (required)
    try:
        advice, candidate = advisor.review_evidence(packet, current, scouting, analysis_result)
    except OfflineServiceError as exc:
        return _failed(run_id, source_digest, config_digest, stage_receipts, f"advise: {exc}")

    # Capture Bedrock usage receipt if the advisor exposes it (duck-typing; not in protocol)
    last_receipt = getattr(advisor, "last_receipt", None)
    if isinstance(last_receipt, UsageReceipt):
        stage_receipts["advise"] = last_receipt

    # 4. Evaluate using a synthetic GenAI span built from the advisor call
    spans = _build_advisor_span(run_id, packet, advice)
    try:
        evaluation = evaluator.evaluate(spans)
    except OfflineServiceError as exc:
        return RunArtifact(
            run_id=run_id,
            source_digest=source_digest,
            config_digest=config_digest,
            stage_receipts=stage_receipts,
            advice=advice,
            status="failed",
            failure=f"evaluate: {exc}",
        )

    # Partial evaluation (faithfulness missing) is a required-stage failure
    if evaluation.status == "partial":
        return RunArtifact(
            run_id=run_id,
            source_digest=source_digest,
            config_digest=config_digest,
            stage_receipts=stage_receipts,
            advice=advice,
            evaluation=evaluation,
            status="failed",
            failure="evaluate partial: faithfulness score missing",
        )

    # Score below threshold → rejected (not a service failure)
    faithfulness = evaluation.scores.get("faithfulness", 0.0)
    if faithfulness < config.judge_min_score:
        return RunArtifact(
            run_id=run_id,
            source_digest=source_digest,
            config_digest=config_digest,
            stage_receipts=stage_receipts,
            advice=advice,
            evaluation=evaluation,
            status="rejected",
            failure=f"faithfulness {faithfulness:.1f} below minimum {config.judge_min_score}",
        )

    # Proposal: all stages succeeded and score meets threshold
    return RunArtifact(
        run_id=run_id,
        source_digest=source_digest,
        config_digest=config_digest,
        stage_receipts=stage_receipts,
        advice=advice,
        candidate_profile=asdict(candidate),
        evaluation=evaluation,
        status="proposal",
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


def _failed(
    run_id: str,
    source_digest: str,
    config_digest: str,
    stage_receipts: dict[str, UsageReceipt],
    failure: str,
) -> RunArtifact:
    return RunArtifact(
        run_id=run_id,
        source_digest=source_digest,
        config_digest=config_digest,
        stage_receipts=stage_receipts,
        status="failed",
        failure=failure,
    )


def _build_advisor_span(
    run_id: str,
    packet: EvidencePacket,
    advice: Any,
) -> list[dict[str, JsonValue]]:
    """Build a minimal GenAI span from the advisor call for evaluation."""
    trace_id = hashlib.sha256(f"{packet.match_id}:{run_id}".encode()).hexdigest()[:32]
    span_id = hashlib.sha256(f"advise:{run_id}".encode()).hexdigest()[:16]

    task_input = json.dumps({
        "match_id": packet.match_id,
        "opponent_id": packet.opponent_id,
        "claims": [{"id": c.id, "kind": c.kind} for c in packet.claims],
    })
    task_output = (
        advice.model_dump_json()
        if hasattr(advice, "model_dump_json")
        else json.dumps({})
    )

    return [{
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": None,
        "name": "gen_ai.inference",
        "attributes": {
            "gen_ai.operation.name": "chat",
            "gen_ai.task.input": task_input,
            "gen_ai.task.output": task_output,
            "football.trace.match_id": packet.match_id,
        },
        "status": {"code": "OK"},
    }]
