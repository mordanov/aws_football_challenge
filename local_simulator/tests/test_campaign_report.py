"""Tests for campaign_report: standings and block-bootstrap CI."""
from football_lab.campaign_report import (
    bootstrap_ci,
    compute_standings,
    head_to_head_matrix,
    operational_summary,
    winner_claim,
)
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


# ── Task 1: head-to-head matrix and operational summary ──────────────────────

_H2H_RESULTS = [
    _r("a__vs__b__b0000__l0", "a", "b", [2, 0]),  # a wins
    _r("b__vs__a__b0000__l1", "b", "a", [1, 0]),  # b wins
    _r("a__vs__b__b0001__l0", "a", "b", [1, 1]),  # draw
    _r("b__vs__a__b0001__l1", "b", "a", [1, 1]),  # draw
]


def test_head_to_head_wins():
    h = head_to_head_matrix(_H2H_RESULTS)
    assert h["a"]["b"]["wins"] == 1
    assert h["a"]["b"]["losses"] == 1
    assert h["a"]["b"]["draws"] == 2


def test_head_to_head_goals():
    h = head_to_head_matrix(_H2H_RESULTS)
    # a as team0 (2 fixtures): gf=2+1=3, ga=0+1=1
    # a as team1 (2 fixtures): gf=0+1=1, ga=1+1=2
    assert h["a"]["b"]["goals_for"] == 4
    assert h["a"]["b"]["goals_against"] == 3


def test_head_to_head_symmetry():
    h = head_to_head_matrix(_H2H_RESULTS)
    assert h["a"]["b"]["wins"] == h["b"]["a"]["losses"]
    assert h["a"]["b"]["goals_for"] == h["b"]["a"]["goals_against"]


def _make_test_manifest(fixtures, results):
    from football_lab.entrant_contracts import CampaignManifest
    return CampaignManifest({
        "campaign_id": "t", "track": "t", "entrants": [],
        "seed_pool_start": 0,
        "budget": {"startup_timeout_s": 5.0, "request_timeout_s": 5.0, "output_size_limit": 65536},
        "rules_version": "v1", "locked": False,
        "fixtures": fixtures,
        "results": results,
    })


def test_operational_summary_complete():
    from football_lab.campaign import round_robin_fixtures
    fixtures = round_robin_fixtures(["a", "b"], seed_pool_start=0, n_blocks=1)
    extra = dict(fixtures[0])
    extra["fixture_id"] = "a__vs__b__b0001__l0"
    fixtures_3 = fixtures + [extra]
    manifest = _make_test_manifest(fixtures_3, [
        {"fixture_id": fixtures_3[0]["fixture_id"], "status": "complete",
         "score": [1, 0], "failure_reason": None, "replay_path": None,
         "duration_ms": 10.0, "team0_rejections": 2, "team1_rejections": 1},
        {"fixture_id": fixtures_3[1]["fixture_id"], "status": "failed",
         "score": [0, 0], "failure_reason": "timeout", "replay_path": None,
         "duration_ms": 10.0, "team0_rejections": 0, "team1_rejections": 0},
    ])
    summary, _rejections = operational_summary(manifest)
    assert summary["total"] == 3
    assert summary["complete"] == 1
    assert summary["failed"] == 1


def test_operational_summary_rejections():
    from football_lab.campaign import round_robin_fixtures
    fixtures = round_robin_fixtures(["a", "b"], seed_pool_start=0, n_blocks=1)
    manifest = _make_test_manifest(fixtures, [
        {"fixture_id": f["fixture_id"], "status": "complete",
         "score": [0, 0], "failure_reason": None, "replay_path": None,
         "duration_ms": 10.0, "team0_rejections": 5, "team1_rejections": 3}
        for f in fixtures
    ])
    _summary, rejections = operational_summary(manifest)
    # leg0: a=team0 (5 rej), b=team1 (3 rej); leg1: b=team0 (5 rej), a=team1 (3 rej)
    assert rejections["a"] == 5 + 3  # 8
    assert rejections["b"] == 3 + 5  # 8


def test_bootstrap_ci_confidence_99_5():
    results = (
        [_r(f"a__vs__b__b{i:04d}__l0", "a", "b", [3, 0]) for i in range(10)]
        + [_r(f"b__vs__a__b{i:04d}__l1", "b", "a", [0, 3]) for i in range(10)]
    )
    ci_95  = bootstrap_ci(results, rng_seed=0, confidence=0.95)
    ci_995 = bootstrap_ci(results, rng_seed=0, confidence=0.995)
    lo_95,  hi_95  = ci_95["a"]["vs_b"]
    lo_995, hi_995 = ci_995["a"]["vs_b"]
    assert lo_995 <= lo_95
    assert hi_995 >= hi_95
    assert lo_995 > 0


def test_winner_claim_clear_winner():
    results = (
        [_r(f"a__vs__b__b{i:04d}__l0", "a", "b", [2, 0]) for i in range(20)]
        + [_r(f"b__vs__a__b{i:04d}__l1", "b", "a", [0, 2]) for i in range(20)]
        + [_r(f"a__vs__c__b{i:04d}__l0", "a", "c", [2, 0]) for i in range(20)]
        + [_r(f"c__vs__a__b{i:04d}__l1", "c", "a", [0, 2]) for i in range(20)]
        + [_r(f"b__vs__c__b{i:04d}__l0", "b", "c", [1, 1]) for i in range(20)]
        + [_r(f"c__vs__b__b{i:04d}__l1", "c", "b", [1, 1]) for i in range(20)]
    )
    claim = winner_claim(results, ["a", "b", "c"], bootstrap_seed=0, n_resamples=500)
    assert claim["winner_id"] == "a"
    assert claim["is_conclusive"] is True
    assert claim["reason"] == "clear_winner"


def test_winner_claim_inconclusive():
    results = (
        [_r(f"a__vs__b__b{i:04d}__l0", "a", "b", [2, 0]) for i in range(2)]
        + [_r(f"b__vs__a__b{i:04d}__l1", "b", "a", [1, 0]) for i in range(2)]
    )
    claim = winner_claim(results, ["a", "b"], bootstrap_seed=0, n_resamples=500)
    assert claim["winner_id"] is None
    assert claim["is_conclusive"] is False


def test_winner_claim_incomplete_data():
    results = [_r("a__vs__b__b0000__l0", "a", "b", [2, 0])]
    claim = winner_claim(results, ["a", "b", "c"], bootstrap_seed=0)
    assert claim["reason"] == "incomplete_data"
    assert claim["winner_id"] is None


def test_winner_claim_operational_failure_provisional():
    results = [
        _r("a__vs__b__b0000__l0", "a", "b", [2, 0]),
        {"fixture_id": "a__vs__b__b0000__l1", "status": "failed",
         "score": [0, 0], "failure_reason": "timeout", "replay_path": None,
         "duration_ms": 0.0, "team0_rejections": 0, "team1_rejections": 0},
    ]
    claim = winner_claim(results, ["a", "b"], bootstrap_seed=0)
    assert claim["reason"] == "incomplete_data"
