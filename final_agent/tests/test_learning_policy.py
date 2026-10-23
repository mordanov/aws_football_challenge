from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import pytest
from test_learning_automation import observation

from agent.learning_observations import prepare_observation
from agent.learning_policy import propose
from agent.model import Profile
from agent.profile_registry import CODE_GENERATION, canonical, profile_document


def evidence():
    docs = []
    for game in range(3):
        for tick in range(5):
            row = observation(clock=20 + tick * 2, tick=100 + tick)
            row["profile_digest"] = profile_document(Profile())[0]
            row["code_generation"] = CODE_GENERATION
            row["match_id"] = f"2d4dd176-ca81-4dd6-88de-e3e99b9a3e9{game}"
            state = row["snapshot"]
            state.update(ball=[-30, 0], owner=[1, 2], free=False)
            for player in state["players"]:
                if player["team"] == 0 and player["id"] != 0:
                    player["x"] = -10
            prepared = prepare_observation(
                row,
                team_id="our_team",
                source={
                    "timestamp_ms": 1000,
                    "message_digest": hashlib.sha256(b"source").hexdigest(),
                },
            )
            assert prepared is not None
            docs.append(
                {
                    "observation_id": prepared.observation_id,
                    "content": prepared.content,
                    "source": prepared.source,
                }
            )
    return docs


def test_repeated_central_exposure_proposes_only_bounded_press_radius_change():
    base = Profile()
    docs = evidence()
    candidate = propose(base, docs)
    assert candidate is not None
    assert candidate.profile.settings.press_radius == 20
    assert candidate.profile.style == base.style
    assert candidate.profile.coach_policy == base.coach_policy
    assert candidate.profile.opponents == base.opponents
    assert len(candidate.supporting_matches) == 3
    assert candidate == propose(base, list(reversed(docs)) + docs)
    assert (
        candidate.evidence_digest
        == hashlib.sha256(canonical(sorted(d["observation_id"] for d in docs)).encode()).hexdigest()
    )


def test_insufficient_matches_or_distinct_clocks_do_not_tune():
    docs = evidence()
    assert propose(Profile(), docs[:10]) is None
    assert propose(Profile(), [docs[0], docs[5], docs[10]] * 50) is None


@pytest.mark.parametrize("damage", ["mixed_version", "mixed_digest", "conflict", "runtime_error"])
def test_mixed_or_unreliable_match_evidence_blocks_tuning(damage):
    docs = evidence()
    damaged = copy.deepcopy(docs[0])
    if damage == "mixed_version":
        damaged["content"]["profile_version"] = "other"
    elif damage == "mixed_digest":
        damaged["content"]["profile_digest"] = "a" * 64
        damaged["content"]["code_generation"] = "match_profiles_v1"
    elif damage == "conflict":
        damaged["content"]["snapshot"]["ball"] = [-20, 0]
    else:
        damaged["content"].update(kind="error", error_class="ValueError")
        damaged["content"].pop("snapshot")
    damaged["observation_id"] = hashlib.sha256(canonical(damaged["content"]).encode()).hexdigest()
    docs.append(damaged)
    assert propose(Profile(), docs) is None


def test_central_cover_and_no_numeric_room_leave_baseline_unchanged():
    docs = evidence()
    for doc in docs:
        for player in doc["content"]["snapshot"]["players"]:
            if player["team"] == 0 and player["id"] == 1:
                player["x"] = -40
        doc["observation_id"] = hashlib.sha256(canonical(doc["content"]).encode()).hexdigest()
    assert propose(Profile(), docs) is None
    base = Profile()
    assert (
        propose(replace(base, settings=replace(base.settings, press_radius=12)), evidence()) is None
    )


def test_homogeneous_but_incompatible_code_generation_does_not_propose():
    docs = evidence()
    for doc in docs:
        doc["content"]["code_generation"] = "different_tactical_semantics"
        doc["observation_id"] = hashlib.sha256(canonical(doc["content"]).encode()).hexdigest()
    assert propose(Profile(), docs) is None


@pytest.mark.parametrize("field", ["profile_digest", "code_generation"])
def test_unbound_legacy_evidence_cannot_support_automatic_promotion(field):
    docs = evidence()
    for doc in docs:
        doc["content"][field] = None
        doc["observation_id"] = hashlib.sha256(canonical(doc["content"]).encode()).hexdigest()
    assert propose(Profile(), docs) is None
