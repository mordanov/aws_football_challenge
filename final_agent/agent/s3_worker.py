"""Log-triggered S3/Memory learning; no scheduler or per-tick learning calls."""

from __future__ import annotations

import json
import os
import time

from .learning_collector import collect
from .profile_registry import canonical
from .s3_trigger import S3LearningTrigger

DIAGNOSTIC_MATCH_ID = "6dc3a574-299e-44e4-81aa-6eb36c5776ef"


class EvaluationDeferred(RuntimeError):
    """No attempt was reserved because evaluation cannot finish in this invocation."""


def require_evaluation_budget(context) -> None:
    if context is not None and context.get_remaining_time_in_millis() < 660_000:
        raise EvaluationDeferred("continue in a fresh invocation before reserving an attempt")


class CompetitionEvidence:
    def __init__(self, source):
        self.source = source

    def load(self, *, now_ms: int) -> list[dict]:
        return [
            doc
            for doc in self.source.load(now_ms=now_ms)
            if doc["content"]["match_id"] != DIAGNOSTIC_MATCH_ID
        ]


def clients():
    import boto3
    from botocore.config import Config

    config = Config(connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 2})
    return (
        boto3.client("s3", config=config),
        boto3.client("bedrock-agentcore", config=config),
        boto3.client("lambda", config=config),
    )


def settings() -> dict:
    return {
        "bucket": os.environ["AFC_LEARNING_BUCKET"],
        "team_id": os.environ["AFC_TEAM_ID"],
        "owner_account": os.environ["AFC_OWNER_ACCOUNT"],
    }


def dispatch(client, token: str) -> None:
    response = client.invoke(
        FunctionName=os.environ["AFC_LEARNER_FUNCTION"],
        InvocationType="Event",
        Payload=canonical({"token": token}).encode(),
    )
    if response["StatusCode"] != 202:
        raise RuntimeError("learner invocation was not accepted")


def collector(event: dict, context) -> dict:
    from .s3_learning import S3ObservationOutbox

    s3, memory, invocation = clients()
    config = settings()
    outbox = S3ObservationOutbox(s3, memory, memory_id=os.environ["AFC_MEMORY_ID"], **config)
    result = collect(event, outbox, log_groups=tuple(json.loads(os.environ["AFC_LOG_GROUPS"])))
    if (
        result["published"] + result["duplicates"] > 0
        and os.environ.get("AFC_LEARNING_ENABLED", "0") == "1"
    ):
        result["wake"] = S3LearningTrigger(s3, **config).signal(
            now_ms=round(time.time() * 1000), invoke=lambda token: dispatch(invocation, token)
        )
    print(canonical({"type": "s3_learning_collection", **result}), flush=True)
    return result


def learner(event: dict, context) -> dict:
    if os.environ.get("AFC_LEARNING_ENABLED", "0") != "1":
        return {"status": "disabled"}
    from .learning_evaluation import evaluate_profiles
    from .learning_worker import run_learning
    from .s3_learning import S3MemoryObservationSource, S3ObservationOutbox
    from .s3_registry import S3ProfileRegistry

    token = event.get("token")
    if not isinstance(token, str) or len(token) != 32:
        raise ValueError("learner requires an S3 work token")
    s3, memory, invocation = clients()
    config = settings()
    trigger = S3LearningTrigger(s3, **config)
    source = S3MemoryObservationSource(s3, memory, memory_id=os.environ["AFC_MEMORY_ID"], **config)
    outbox = S3ObservationOutbox(s3, memory, memory_id=os.environ["AFC_MEMORY_ID"], **config)
    registry = S3ProfileRegistry(s3, **config)
    started = time.monotonic()
    while True:
        state, _ = trigger.read()
        if state["token"] != token:
            return {"status": "superseded"}
        now_ms = round(time.time() * 1000)
        remaining = context.get_remaining_time_in_millis() if context is not None else 900_000
        if time.monotonic() - started > 600 or remaining < 90_000:
            if trigger.renew(token, now_ms=now_ms):
                dispatch(invocation, token)
            return {"status": "continued"}
        if now_ms - state["latest_ms"] < 130_000:
            time.sleep(10)
            continue
        analysis_started = time.monotonic()
        recovery = outbox.recover_pending(now_ms=now_ms)
        try:
            result = run_learning(
                registry=registry,
                source=CompetitionEvidence(source),
                evaluator=evaluate_profiles,
                now_ms=now_ms,
                before_reservation=lambda: require_evaluation_budget(context),
            )
        except EvaluationDeferred:
            if trigger.renew(token, now_ms=round(time.time() * 1000)):
                dispatch(invocation, token)
            return {"status": "evaluation_deferred"}
        summary = {
            "type": "s3_automatic_learning",
            "status": result["status"],
            "generation": result["generation"],
            "checked_ms": now_ms,
            "revision": state["revision"],
            "evidence_plan": source.last_plan,
            "publication_recovery": recovery,
            "analysis_ms": round((time.monotonic() - analysis_started) * 1000),
        }
        s3.put_object(
            Bucket=config["bucket"],
            Key=f"final-agent-learning/{config['team_id']}/last-learning.json",
            ExpectedBucketOwner=config["owner_account"],
            ServerSideEncryption="AES256",
            ContentType="application/json",
            Body=canonical(summary).encode(),
        )
        print(canonical(summary), flush=True)
        if trigger.settle(token, state["revision"]):
            return summary
