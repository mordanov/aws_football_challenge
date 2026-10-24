from types import SimpleNamespace

import pytest
from test_learning_worker import accepted, setup

from agent.learning_worker import run_learning
from agent.s3_worker import (
    DIAGNOSTIC_MATCH_ID,
    CompetitionEvidence,
    EvaluationDeferred,
    require_evaluation_budget,
)


def test_rollout_cooldown_preserves_attempt_until_legacy_sessions_expire(monkeypatch):
    from agent.s3_worker import RolloutCooldown, require_promotion_ready

    table, registry, source = setup()
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 800_000)
    monkeypatch.setenv("AFC_PROMOTION_NOT_BEFORE_MS", "300000")
    with pytest.raises(RolloutCooldown):
        run_learning(
            registry=registry,
            source=source,
            evaluator=accepted,
            now_ms=200_000,
            before_reservation=lambda: require_promotion_ready(context, now_ms=200_000),
        )
    assert not any("#ATTEMPT#" in key for key in table.items)
    assert registry.active().generation == 1
    assert run_learning(
        registry=registry,
        source=source,
        evaluator=accepted,
        now_ms=300_000,
        before_reservation=lambda: require_promotion_ready(context, now_ms=300_000),
    )["status"] == "promoted"


def test_short_lambda_budget_does_not_consume_generation_attempt_after_evidence_load():
    table, registry, source = setup()
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 100_000)
    with pytest.raises(EvaluationDeferred):
        run_learning(
            registry=registry,
            source=source,
            evaluator=accepted,
            now_ms=200_000,
            before_reservation=lambda: require_evaluation_budget(context),
        )
    assert not any("#ATTEMPT#" in key for key in table.items)
    assert registry.active().generation == 1
    context.get_remaining_time_in_millis = lambda: 800_000
    assert (
        run_learning(
            registry=registry,
            source=source,
            evaluator=accepted,
            now_ms=200_000,
            before_reservation=lambda: require_evaluation_budget(context),
        )["status"]
        == "promoted"
    )


def test_diagnostic_match_never_enters_candidate_evidence():
    source = SimpleNamespace(
        load=lambda **kwargs: [
            {"content": {"match_id": DIAGNOSTIC_MATCH_ID}},
            {"content": {"match_id": "real-match"}},
        ]
    )
    assert CompetitionEvidence(source).load(now_ms=1) == [{"content": {"match_id": "real-match"}}]
