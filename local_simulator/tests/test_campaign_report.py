"""Tests for campaign_report: standings and block-bootstrap CI."""
import pytest

from football_lab.campaign_report import bootstrap_ci, compute_standings
from football_lab.entrant_contracts import FixtureResult


def _r(
    fixture_id: str,
    team0: str,
    team1: str,
    score: list[int],
) -> FixtureResult:
    """Build a FixtureResult; fixture_id encodes team0/team1 via leading segment."""
    # Override fixture_id to embed team names so compute_standings can decode them.
    # Format: "{team0}__vs__{team1}__b{block:04d}__l{leg}"
    return {
        "fixture_id": fixture_id,
        "status": "complete",
        "score": score,
        "failure_reason": None,
        "replay_path": None,
        "duration_ms": 10.0,
        "team0_rejections": 0,
        "team1_rejections": 0,
    }


# Dataset:
# fixture "a__vs__b__b0000__l0": team0=a, score=[2,0] → a=3pts, b=0pts
# fixture "b__vs__a__b0000__l1": team0=b, score=[1,0] → b=3pts, a=0pts
# fixture "a__vs__b__b0001__l0": team0=a, score=[3,0] → a=3pts, b=0pts
# fixture "a__vs__b__b0001__l1": team0=a, score=[2,0] → a=3pts, b=0pts
# Total: a=9pts (3 wins), b=3pts (1 win)
_KNOWN_RESULTS = [
    _r("a__vs__b__b0000__l0", "a", "b", [2, 0]),
    _r("b__vs__a__b0000__l1", "b", "a", [1, 0]),
    _r("a__vs__b__b0001__l0", "a", "b", [3, 0]),
    _r("a__vs__b__b0001__l1", "a", "b", [2, 0]),
]


def test_standings_points():
    standings = compute_standings(_KNOWN_RESULTS)
    a = next(s for s in standings if s["entrant_id"] == "a")
    b = next(s for s in standings if s["entrant_id"] == "b")
    assert a["points"] == 9   # 3 wins × 3 pts
    assert b["points"] == 3   # 1 win × 3 pts


def test_standings_goals():
    standings = compute_standings(_KNOWN_RESULTS)
    a = next(s for s in standings if s["entrant_id"] == "a")
    # a as team0: goals_for = 2+3+2=7; as team1 in b__vs__a: goals_against=1
    assert a["goals_for"] == 7
    assert a["goals_against"] == 1


def test_bootstrap_ci_all_wins():
    # 10 blocks, a wins every match in every leg
    results = [
        _r(f"a__vs__b__b{i:04d}__l0", "a", "b", [2, 0]) for i in range(10)
    ] + [
        _r(f"b__vs__a__b{i:04d}__l1", "b", "a", [0, 2]) for i in range(10)  # a wins as team1
    ]
    ci = bootstrap_ci(results, rng_seed=0, n_resamples=1000)
    lo, hi = ci["a"]["vs_b"]
    # CI is per-match point differential (a_pts - b_pts); a wins always → diff=3.0
    assert lo > 2.5


def test_bootstrap_block_integrity():
    # Block 0: a wins BOTH legs → differential = +3 per match
    # Block 1: b wins BOTH legs → differential = -3 per match (for a)
    # With block-level resampling, bootstrap spans from ~-3 to ~+3, including 0
    results = [
        _r("a__vs__b__b0000__l0", "a", "b", [2, 0]),   # block 0, a wins leg0
        _r("b__vs__a__b0000__l1", "b", "a", [0, 2]),   # block 0, a wins leg1 (score b:0, a:2)
        _r("a__vs__b__b0001__l0", "a", "b", [0, 2]),   # block 1, b wins leg0
        _r("b__vs__a__b0001__l1", "b", "a", [2, 0]),   # block 1, b wins leg1
    ]
    ci = bootstrap_ci(results, rng_seed=42, n_resamples=1000)
    lo, hi = ci["a"]["vs_b"]
    assert lo <= 0 <= hi   # high variance → CI spans 0
