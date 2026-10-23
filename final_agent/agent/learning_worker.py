"""Scheduled evidence -> bounded proposal -> paired evaluation -> conditional promotion."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Protocol

from .learning_policy import propose
from .model import Profile
from .profile_registry import DynamoProfileRegistry, canonical, profile_document


class ObservationSource(Protocol):
    def load(self, *, now_ms: int) -> list[dict]: ...


def run_learning(
    *,
    registry: DynamoProfileRegistry,
    source: ObservationSource,
    evaluator: Callable[[Profile, Profile], dict],
    now_ms: int,
) -> dict:
    baseline = registry.active()
    documents = source.load(now_ms=now_ms)
    candidate = propose(baseline.selection.profile, documents)
    if candidate is None:
        return {"status": "insufficient_evidence", "generation": baseline.generation}
    if not registry.begin_attempt(baseline, candidate.profile, candidate.evidence_digest):
        return {"status": "already_attempted", "generation": baseline.generation}
    try:
        evaluation = evaluator(baseline.selection.profile, candidate.profile)
    except Exception as exc:
        # Isolate the external evaluator only; persist failure and surface it to monitoring.
        registry.finish_attempt(
            baseline,
            candidate.profile,
            candidate.evidence_digest,
            {
                "accepted": False,
                "reason": "evaluation_failed",
                "error_class": type(exc).__name__,
                "baseline_digest": baseline.selection.digest,
                "candidate_digest": profile_document(candidate.profile)[0],
            },
        )
        raise RuntimeError("automatic evaluation failed; generation remains unchanged") from exc
    return registry.finish_attempt(
        baseline, candidate.profile, candidate.evidence_digest, evaluation
    )


def handler(event: dict, context) -> dict:
    if os.environ.get("AFC_LEARNING_ENABLED", "0") != "1":
        return {"status": "disabled"}
    import boto3
    from botocore.config import Config

    from .learning_evaluation import evaluate_profiles
    from .learning_source import MemoryObservationSource

    config = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 2})
    dynamo = boto3.client("dynamodb", config=config)
    team = os.environ["AFC_TEAM_ID"]
    source = MemoryObservationSource(
        dynamo,
        boto3.client("bedrock-agentcore", config=config),
        table=os.environ["AFC_OBSERVATION_TABLE"],
        memory_id=os.environ["AFC_MEMORY_ID"],
        team_id=team,
    )
    result = run_learning(
        registry=DynamoProfileRegistry(dynamo, os.environ["AFC_PROFILE_TABLE"], team),
        source=source,
        evaluator=evaluate_profiles,
        now_ms=int(time.time() * 1000),
    )
    print(
        canonical(
            {
                "type": "automatic_learning",
                "status": result["status"],
                "generation": result["generation"],
            }
        ),
        flush=True,
    )
    return {"status": result["status"], "generation": result["generation"]}
