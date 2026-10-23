from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path

import pytest

from agent import learning_evaluation as evaluation
from agent.archetypes import ARCHETYPES
from agent.model import Profile
from agent.profile_registry import profile_document


def outcome(profile, opponent, seed, side, goals=0):
    score = [0, 0]
    score[side] = goals
    return {
        "profile_digest": profile_document(profile)[0],
        "opponent_digest": profile_document(opponent)[0],
        "seed": seed,
        "side": side,
        "score": score,
        "status": "complete",
        "rules_version": "v2",
        "duration_ticks": 2400,
        "illegal_actions": 0,
        "agent_crashes": 0,
        "opponent_illegal_actions": 0,
        "opponent_agent_crashes": 0,
        "rejections": [],
    }


@pytest.fixture
def profiles():
    baseline = Profile()
    candidate = replace(
        baseline, version="candidate", settings=replace(baseline.settings, shot_range=30.0)
    )
    return baseline, candidate


def schedule(baseline, candidate):
    pool = {
        "baseline": baseline,
        **{name: ARCHETYPES[name] for name in ("press_aggressive", "park_the_bus")},
    }
    return [
        {
            "seed": seed,
            "opponent": name,
            "side": side,
            "baseline": outcome(baseline, opponent, seed, side),
            "candidate": outcome(candidate, opponent, seed, side, 1),
        }
        for seed in evaluation.SEEDS
        for name, opponent in pool.items()
        for side in (0, 1)
    ]


def test_predeclared_sign_gate_accepts_consistent_paired_improvement(profiles):
    baseline, candidate = profiles
    rows = schedule(*profiles)
    report = evaluation.evaluate_results(baseline, candidate, rows)
    assert report["accepted"] is True
    assert report["pairs"] == rows
    assert report["baseline_digest"] == profile_document(baseline)[0]
    assert report["candidate_digest"] == profile_document(candidate)[0]
    assert report["policy_id"] == evaluation.POLICY_ID
    assert report["statistics"]["seed_blocks"] == 12
    assert report["statistics"]["sign_test_p"] == 1 / 4096
    assert report["statistics"]["mean_paired_goal_difference"] == 1.0


def test_identical_profiles_cannot_be_accepted_even_with_fabricated_gain():
    baseline = Profile()
    report = evaluation.evaluate_results(baseline, baseline, schedule(baseline, baseline))
    assert not report["accepted"]
    assert report["reason"] == "identical_profiles"


def test_all_ties_and_no_positive_mean_fail(profiles):
    rows = schedule(*profiles)
    for row in rows:
        row["candidate"]["score"] = [0, 0]
    report = evaluation.evaluate_results(*profiles, rows)
    assert not report["accepted"]
    assert report["statistics"]["sign_test_p"] == 1.0


def test_sign_test_uses_seed_blocks_and_counts_ties_as_failures(profiles):
    rows = schedule(*profiles)
    for row in rows:
        if row["seed"] in evaluation.SEEDS[:2]:
            row["candidate"]["score"] = [0, 0]
    report = evaluation.evaluate_results(*profiles, rows)
    assert not report["accepted"]
    assert report["reason"] == "insufficient_sign_evidence"
    assert report["statistics"]["positive_blocks"] == 10
    assert report["statistics"]["sign_test_p"] == 79 / 4096


def test_one_opponent_regression_rejects_despite_positive_blocks(profiles):
    rows = schedule(*profiles)
    for row in rows:
        if row["opponent"] == "park_the_bus":
            row["candidate"]["score"][1 - row["side"]] = 2
    report = evaluation.evaluate_results(*profiles, rows)
    assert not report["accepted"]
    assert report["reason"] == "opponent_regression"
    assert report["statistics"]["per_opponent"]["park_the_bus"]["mean_delta"] == -1


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "wrong_seed",
        "wrong_digest",
        "wrong_score",
        "partial",
        "wrong_rules",
        "unreported_illegal",
    ],
)
def test_incomplete_or_corrupt_evidence_fails_closed(profiles, mutation):
    rows = schedule(*profiles)
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = deepcopy(rows[0])
    elif mutation == "wrong_seed":
        rows[0]["seed"] = 9999
    elif mutation == "wrong_digest":
        rows[0]["candidate"]["profile_digest"] = "other"
    elif mutation == "wrong_score":
        rows[0]["candidate"]["score"] = [True, 0]
    elif mutation == "partial":
        rows[0]["candidate"]["status"] = "partial"
    elif mutation == "wrong_rules":
        rows[0]["candidate"]["rules_version"] = "v1"
    else:
        rows[0]["candidate"]["illegal_actions"] = 1
    report = evaluation.evaluate_results(*profiles, rows)
    assert not report["accepted"]
    assert report["reason"] == "invalid_or_incomplete_pairs"
    assert report["pairs"] == rows


@pytest.mark.parametrize("crash", [False, True])
def test_rejection_or_crash_increase_is_not_hidden_by_scores(profiles, crash):
    rows = schedule(*profiles)
    result = rows[0]["candidate"]
    result["rejections"] = [
        {
            "tick": 0,
            "team_id": rows[0]["side"],
            "player_id": 0,
            "accepted": False,
            "attempted_type": "CRASH" if crash else "PASS",
            "attempted_params": {},
            "rejection_reason": "agent_crash" if crash else "no_possession",
        }
    ]
    result["agent_crashes" if crash else "illegal_actions"] = 1
    report = evaluation.evaluate_results(*profiles, rows)
    assert not report["accepted"]
    assert report["reason"] == ("agent_crash_increase" if crash else "illegal_action_increase")
    assert report["pairs"][0]["candidate"]["rejections"] == result["rejections"]


def test_full_evaluation_runs_fixed_identical_schedule_without_external_services(
    profiles, monkeypatch
):
    calls = []

    def fake_play(profile, opponent, seed, side):
        calls.append((profile, opponent, seed, side))
        return outcome(profile, opponent, seed, side, int(profile == profiles[1]))

    monkeypatch.setattr(evaluation, "play", fake_play)
    report = evaluation.evaluate_profiles(*profiles)
    assert report["accepted"]
    assert len(calls) == 144
    assert len(report["pairs"]) == 72
    for baseline_call, candidate_call in zip(calls[::2], calls[1::2]):
        assert baseline_call[1:] == candidate_call[1:]


def test_budget_and_simulator_errors_are_explicit_and_preserve_evidence(profiles, monkeypatch):
    def fake_play(profile, opponent, seed, side):
        return outcome(profile, opponent, seed, side, int(profile == profiles[1]))

    clock = iter((0.0, 0.0, 0.0, 0.0, 601.0))
    monkeypatch.setattr(evaluation, "play", fake_play)
    monkeypatch.setattr(evaluation.time, "monotonic", lambda: next(clock, 601.0))
    report = evaluation.evaluate_profiles(*profiles)
    assert not report["accepted"]
    assert report["reason"] == "runtime_budget_exceeded"
    assert report["pairs"]

    monkeypatch.setattr(evaluation.time, "monotonic", lambda: 0.0)

    def broken_play(*args):
        raise ImportError("football_lab unavailable")

    monkeypatch.setattr(evaluation, "play", broken_play)
    report = evaluation.evaluate_profiles(*profiles)
    assert not report["accepted"]
    assert report["reason"] == "simulator_error"
    assert "football_lab unavailable" in report["statistics"]["error"]


def test_real_v2_simulator_two_sides_use_current_agent_adapter(monkeypatch):
    if find_spec("football_lab") is None:
        simulator_src = Path(__file__).resolve().parents[2] / "local_simulator" / "src"
        if (simulator_src / "football_lab").is_dir():
            monkeypatch.syspath_prepend(str(simulator_src))
        else:
            pytest.skip("football_lab is neither installed nor available in the repository")
    profile, opponent = Profile(), ARCHETYPES["park_the_bus"]
    for side in (0, 1):
        result = evaluation.play(profile, opponent, evaluation.SEEDS[0], side)
        assert result["status"] == "complete"
        assert result["rules_version"] == "v2"
        assert result["duration_ticks"] == 2400
        assert len(result["score"]) == 2
        assert all(type(score) is int and score >= 0 for score in result["score"])
        assert result["agent_crashes"] == 0
        assert result["opponent_agent_crashes"] == 0
        assert result["profile_digest"] == profile_document(profile)[0]
        assert result["opponent_digest"] == profile_document(opponent)[0]
        assert result["side"] == side
