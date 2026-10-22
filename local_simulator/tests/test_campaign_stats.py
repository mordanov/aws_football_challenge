from __future__ import annotations

from typing import Any

import pytest

from football_lab.campaign_report import (
    discipline_by_entrant,
    discipline_counts,
    goal_diff_delta_ci,
    pooled_goal_diff_ci,
)


def _r(fixture_id: str, score: list[int], **extra: Any) -> Any:
    return {"fixture_id": fixture_id, "status": "complete", "score": score,
            "failure_reason": None, "replay_path": None, "duration_ms": 1.0,
            "team0_rejections": 0, "team1_rejections": 0, **extra}


A = [
    _r("a__vs__b__b0000__l0", [1, 0]), _r("b__vs__a__b0000__l1", [0, 0]),
    _r("a__vs__b__b0001__l0", [0, 0]), _r("b__vs__a__b0001__l1", [0, 0]),
    _r("a__vs__c__b0000__l0", [2, 1]), _r("c__vs__a__b0000__l1", [0, 1]),
]
X_BOTH_B = [
    _r("x__vs__b__b0000__l0", [0, 0]), _r("b__vs__x__b0000__l1", [0, 0]),
    _r("x__vs__b__b0001__l0", [0, 0]), _r("b__vs__x__b0001__l1", [0, 0]),
]
X_C = [_r("x__vs__c__b0000__l0", [0, 0]), _r("c__vs__x__b0000__l1", [0, 0])]


def test_pooled_goal_diff() -> None:
    p = pooled_goal_diff_ci(A, "a", rng_seed=1, n_resamples=2000)
    assert (p["entrant_id"], p["matches"], p["units"]) == ("a", 6, 3)
    assert p["mean"] == pytest.approx(0.5)
    assert 0.0 <= p["lo"] <= 0.5 <= p["hi"] <= 1.0
    with pytest.raises(ValueError):
        pooled_goal_diff_ci(A, "nobody", rng_seed=1)


def test_goal_diff_delta_is_paired_over_shared_units() -> None:
    mean, lo, hi = goal_diff_delta_ci(A + X_BOTH_B + X_C, "a", "x", rng_seed=1,
                                      n_resamples=2000)
    assert mean == pytest.approx(0.5) and lo <= mean <= hi   # units 0.5, 0, 1
    mean, _, _ = goal_diff_delta_ci(A + X_BOTH_B, "a", "x", rng_seed=1, n_resamples=2000)
    assert mean == pytest.approx(0.25)                       # (c,0) not shared
    with pytest.raises(ValueError):
        goal_diff_delta_ci(A, "a", "x", rng_seed=1)


def test_discipline_counts_and_by_entrant() -> None:
    ev = [{"kind": k, "team_id": t} for k, t in
          [("FOUL", 1), ("FOUL", 1), ("SEND_OFF", 1), ("TACKLE_SUCCESS", 0),
           ("PENALTY", 0), ("GOAL", 0)]]
    d = discipline_counts(ev)  # type: ignore[arg-type]
    assert d == {"fouls": [0, 2], "send_offs": [0, 1], "tackles_won": [1, 0],
                 "penalties_awarded": [1, 0]}
    by = discipline_by_entrant([
        _r("a__vs__b__b0000__l0", [0, 0], discipline=d),
        _r("b__vs__a__b0000__l1", [0, 0], discipline=d),
        _r("a__vs__c__b0000__l0", [0, 0]),  # no discipline recorded: ignored
    ])
    assert by["a"] == {"matches": 2, "fouls": 2, "send_offs": 1, "tackles_won": 1,
                       "penalties_awarded": 1}
    assert by["b"]["fouls"] == 2 and "c" not in by
