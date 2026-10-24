from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

import pytest
from test_learning_policy import evidence
from test_profile_registry import Table, TransactionConflict, rotate

from agent.learning_policy import propose
from agent.learning_worker import run_learning
from agent.model import Profile
from agent.profile_registry import DynamoProfileRegistry, RegistryError, profile_document


class PromotionTable(Table):
    def transact_write_items(self, **request):
        if "Update" not in request["TransactItems"][0]:
            return super().transact_write_items(**request)
        if self.before_transaction:
            hook, self.before_transaction = self.before_transaction, None
            hook()
        update, put = request["TransactItems"]
        update, put = update["Update"], put["Put"]
        with self.lock:
            active = self.items[update["Key"]["pk"]["S"]]
            values = update["ExpressionAttributeValues"]
            if active["digest"] != values[":old"] or active["generation"] != values[":generation"]:
                raise TransactionConflict
            key = put["Item"]["pk"]["S"]
            if key in self.items:
                raise TransactionConflict
            active.update(digest=values[":new"], generation=values[":next"])
            self.items[key] = copy.deepcopy(put["Item"])
            if self.lose_ack:
                self.lose_ack = False
                raise TimeoutError("promotion succeeded; response lost")
        return {}


class Source:
    def __init__(self, documents):
        self.documents = documents

    def load(self, *, now_ms):
        return self.documents


def setup():
    table = PromotionTable()
    registry = DynamoProfileRegistry(table, "registry", "our_team")
    registry.bootstrap(Profile())
    return table, registry, Source(evidence())


def accepted(baseline, candidate):
    return {
        "accepted": True,
        "reason": "paired_simulator_gate",
        "baseline_digest": profile_document(baseline)[0],
        "candidate_digest": profile_document(candidate)[0],
    }


def test_eligible_evidence_is_evaluated_and_promoted_only_for_new_match_pins():
    _, registry, source = setup()
    first = "a5ddac07-007f-4d78-8c97-cb7f9b1fcc42"
    following = "2d4dd176-ca81-4dd6-88de-e3e99b9a3e97"
    old = registry.resolve(first, 0)
    result = run_learning(registry=registry, source=source, evaluator=accepted, now_ms=100_000)
    assert result["status"] == "promoted"
    assert registry.resolve(first, 0) == old
    assert registry.resolve(following, 0).profile.settings.press_radius == 20
    assert registry.active().generation == 2


def test_rejected_candidate_is_recorded_and_cannot_be_retested_with_more_evidence():
    _, registry, source = setup()
    old = registry.active()

    def reject(base, candidate):
        return {**accepted(base, candidate), "accepted": False, "reason": "inconclusive"}

    result = run_learning(registry=registry, source=source, evaluator=reject, now_ms=100_000)
    assert result["status"] == "rejected"
    assert registry.active() == old
    source.documents += source.documents

    def forbidden(*args):
        raise AssertionError("repeated significance test")

    assert (
        run_learning(registry=registry, source=source, evaluator=forbidden, now_ms=200_000)[
            "status"
        ]
        == "already_attempted"
    )


def test_concurrent_active_change_records_stale_baseline_without_overwrite():
    table, registry, source = setup()
    alternative = replace(Profile(), version="operator-intervention")

    def evaluate(base, candidate):
        table.before_transaction = lambda: rotate(table, alternative)
        return accepted(base, candidate)

    result = run_learning(registry=registry, source=source, evaluator=evaluate, now_ms=100_000)
    assert result["status"] == "stale_baseline"
    assert registry.active().selection.profile == alternative


def test_uncertain_promotion_recovers_committed_receipt():
    table, registry, source = setup()
    table.lose_ack = True
    result = run_learning(registry=registry, source=source, evaluator=accepted, now_ms=100_000)
    assert result["status"] == "promoted"
    assert registry.active().generation == 2


def test_evaluator_failure_is_persisted_and_never_promotes_or_retries():
    _, registry, source = setup()

    def fail(base, candidate):
        raise TimeoutError("evaluation unavailable")

    with pytest.raises(RuntimeError, match="evaluation"):
        run_learning(registry=registry, source=source, evaluator=fail, now_ms=100_000)
    assert registry.active().generation == 1
    assert (
        run_learning(registry=registry, source=source, evaluator=fail, now_ms=200_000)["status"]
        == "already_attempted"
    )


def test_source_failure_or_insufficient_evidence_does_not_consume_attempt():
    _, registry, source = setup()
    source.documents = source.documents[:5]
    assert (
        run_learning(registry=registry, source=source, evaluator=accepted, now_ms=100_000)["status"]
        == "insufficient_evidence"
    )
    source.documents = evidence()
    assert (
        run_learning(registry=registry, source=source, evaluator=accepted, now_ms=100_000)["status"]
        == "promoted"
    )


def test_promotion_rejects_unbound_receipt_or_nonallowlisted_change():
    _, registry, source = setup()
    base = registry.active()
    candidate = propose(base.selection.profile, source.documents)
    assert candidate is not None
    assert registry.begin_attempt(base, candidate.profile, candidate.evidence_digest)
    bad = replace(candidate.profile, coach_policy="rules")
    with pytest.raises(RegistryError, match="allowlist"):
        registry.finish_attempt(
            base, bad, candidate.evidence_digest, accepted(base.selection.profile, bad)
        )
    receipt = accepted(base.selection.profile, candidate.profile)
    receipt["candidate_digest"] = "a" * 64
    with pytest.raises(RegistryError, match="bound"):
        registry.finish_attempt(base, candidate.profile, candidate.evidence_digest, receipt)
    assert registry.active() == base


def test_lost_reservation_ack_continues_only_its_own_invocation():
    table, registry, source = setup()
    put = table.put_item

    def uncertain(**request):
        result = put(**request)
        if "#ATTEMPT#" in request["Item"]["pk"]["S"]:
            raise TimeoutError("reservation committed; response lost")
        return result

    table.put_item = uncertain
    assert (
        run_learning(registry=registry, source=source, evaluator=accepted, now_ms=100_000)["status"]
        == "promoted"
    )


def test_large_rejection_is_durable_in_verified_immutable_chunks():
    table, registry, source = setup()
    raw = {}

    def reject(base, candidate):
        raw.update(**accepted(base, candidate), pairs=[{"rejections": ["crash"] * 10_000}] * 12)
        raw["accepted"] = False
        return raw

    receipt = run_learning(registry=registry, source=source, evaluator=reject, now_ms=100_000)
    assert receipt["status"] == "rejected"
    stored = json.loads(table.items["TEAM#our_team#RESULT#1"]["document"]["S"])
    reference = stored["evaluation"]
    text = "".join(table.items[key]["document"]["S"] for key in reference["chunk_keys"])
    assert hashlib.sha256(text.encode()).hexdigest() == reference["sha256"]
    assert json.loads(text) == raw
    assert registry.active().generation == 1
