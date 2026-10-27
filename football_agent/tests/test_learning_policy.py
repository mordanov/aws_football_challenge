from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import pytest
from test_learning_automation import observation

from agent.learning_observations import prepare_observation
from agent.learning_policy import propose
from agent.model import Profile
from agent.profile_registry import (
    CODE_GENERATION,
    canonical,
    profile_document,
    replace_settings_only,
)


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
    assert propose(Profile(), docs[:5]) is None
    assert propose(Profile(), [docs[0], docs[5], docs[10]] * 50) is None


@pytest.mark.parametrize("damage", ["mixed_version", "mixed_digest", "conflict", "runtime_error"])
def test_mixed_or_unreliable_match_evidence_blocks_tuning(damage):
    docs = evidence()

    def make_damaged(src):
        d = copy.deepcopy(src)
        if damage == "mixed_version":
            d["content"]["profile_version"] = "other"
        elif damage == "mixed_digest":
            d["content"]["profile_digest"] = "a" * 64
            d["content"]["code_generation"] = "match_profiles_v1"
        elif damage == "conflict":
            d["content"]["snapshot"]["ball"] = [-20, 0]
        else:
            d["content"].update(kind="error", error_class="ValueError")
            d["content"].pop("snapshot")
        d["observation_id"] = hashlib.sha256(canonical(d["content"]).encode()).hexdigest()
        return d

    # Corrupt one doc from each of two matches; only the third qualifies → 1 < 2 → None.
    docs.append(make_damaged(docs[0]))   # damages match 0
    docs.append(make_damaged(docs[5]))   # damages match 1
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


@pytest.mark.parametrize("generation", ["match_profiles_v1", "match_profiles_rush_v2"])
def test_pre_tactics_v2_generations_cannot_support_new_tactics(generation):
    docs = evidence()
    for doc in docs:
        doc["content"]["code_generation"] = generation
        doc["observation_id"] = hashlib.sha256(canonical(doc["content"]).encode()).hexdigest()
    assert propose(Profile(), docs) is None


@pytest.mark.parametrize("field", ["profile_digest", "code_generation"])
def test_unbound_legacy_evidence_cannot_support_automatic_promotion(field):
    docs = evidence()
    for doc in docs:
        doc["content"][field] = None
        doc["observation_id"] = hashlib.sha256(canonical(doc["content"]).encode()).hexdigest()
    assert propose(Profile(), docs) is None


def _make_docs(ball, owner, player_overrides, match_prefix):
    """Build 3-match × 5-tick evidence with the given snapshot overrides."""
    docs = []
    for game in range(3):
        for tick in range(5):
            row = observation(clock=20 + tick * 2, tick=100 + tick)
            row["profile_digest"] = profile_document(Profile())[0]
            row["code_generation"] = CODE_GENERATION
            row["match_id"] = f"{match_prefix}{game}"
            state = row["snapshot"]
            state.update(ball=list(ball), owner=list(owner), free=False)
            for player in state["players"]:
                key = (player["team"], player["id"])
                if key in player_overrides:
                    player.update(player_overrides[key])
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


def evidence_shot():
    """Ball at x=20 (past shot threshold), we have ball, no opponents blocking lane."""
    # put all opponent non-GK behind the ball so no blocker condition triggers
    opp_overrides = {(1, i): {"x": -30, "y": 0} for i in range(1, 5)}
    return _make_docs(
        ball=(20, 0), owner=(0, 3),
        player_overrides=opp_overrides,
        match_prefix="3a5ee287-cb92-5ee7-99ef-f4f00c0b0ea",
    )


def evidence_width():
    """We have ball at x=10, all support players bunched narrow (|y| < 6 < 15*0.4=6)."""
    # carrier is id=2; supports are id=1,3,4 — set all to abs(y) < 6
    return _make_docs(
        ball=(10, 0), owner=(0, 2),
        player_overrides={(0, 1): {"x": -5, "y": 3}, (0, 3): {"x": 15, "y": 2}, (0, 4): {"x": 18, "y": -4}},
        match_prefix="4b6ff398-dc03-6ff8-aa0f-e5e11d1c1fb",
    )


def evidence_margin():
    """Opponent has ball at midfield (x=-5), ≥2 of our non-GK within 10m."""
    return _make_docs(
        ball=(-5, 0), owner=(1, 2),
        player_overrides={(0, 1): {"x": -3, "y": 5}, (0, 2): {"x": -8, "y": 0}},
        match_prefix="5c7aa409-ed14-7aa9-bb1f-f6f22e2d2fc",
    )


def test_shot_range_increase_on_clear_attack():
    candidate = propose(Profile(), evidence_shot())
    assert candidate is not None
    assert candidate.profile.settings.shot_range == 34.0


def test_support_width_increase_on_narrow_attack():
    candidate = propose(Profile(), evidence_width())
    assert candidate is not None
    assert candidate.profile.settings.support_width == 17.0


def test_pass_margin_decrease_on_midfield_pressure():
    candidate = propose(Profile(), evidence_margin())
    assert candidate is not None
    assert candidate.profile.settings.pass_margin == 2.5


def test_press_radius_takes_priority_over_shot_range():
    # press evidence (existing) + shot evidence — press should win (higher priority)
    docs = evidence() + evidence_shot()
    candidate = propose(Profile(), docs)
    assert candidate is not None
    assert candidate.profile.settings.press_radius == 20.0  # press wins


def test_new_params_bounded_at_limits():
    base = Profile()
    # shot_range already at max
    at_max_shot = replace(base, settings=replace(base.settings, shot_range=34.0))
    assert propose(at_max_shot, evidence_shot()) is None
    # support_width already at max
    at_max_width = replace(base, settings=replace(base.settings, support_width=22.0))
    assert propose(at_max_width, evidence_width()) is None
    # pass_margin already at min
    at_min_margin = replace(base, settings=replace(base.settings, pass_margin=1.5))
    assert propose(at_min_margin, evidence_margin()) is None


def test_replace_settings_only_validates_all_tuning_params():
    base = Profile()
    # Valid single-step changes
    assert replace_settings_only(base, replace(base, settings=replace(base.settings, shot_range=34.0)))
    assert replace_settings_only(base, replace(base, settings=replace(base.settings, support_width=17.0)))
    assert replace_settings_only(base, replace(base, settings=replace(base.settings, pass_margin=2.5)))
    # Wrong step size is rejected
    bad = replace(base, settings=replace(base.settings, shot_range=35.0))
    assert not replace_settings_only(base, bad)
    # Two params changed simultaneously is rejected
    two = replace(base, settings=replace(base.settings, shot_range=34.0, pass_margin=2.5))
    assert not replace_settings_only(base, two)
