from football_lab.evolution.promotion import decide_promotion, paired_block_diffs

OPPS = ["a", "b", "c", "d", "e"]


def _blocks(points_by_opp, n=20):
    return [{"opponent": o, "block": b, "points": points_by_opp[o], "goal_diff": 0, "legs": 2}
            for o in OPPS for b in range(n)]


def test_paired_diffs_per_match_and_grouped_by_block():
    diffs = paired_block_diffs(_blocks({o: 4 for o in OPPS}, 3), _blocks({o: 2 for o in OPPS}, 3))
    assert set(diffs) == set(OPPS)
    assert diffs["a"] == [1.0, 1.0, 1.0]  # (4-2)/2 legs; one entry per block, not per leg


def test_clear_improvement_promotes():
    d = decide_promotion("fin", _blocks({o: 4 for o in OPPS}), _blocks({o: 2 for o in OPPS}),
                         bootstrap_seed=1, n_resamples=500)
    assert d["promoted"] and d["reasons"] == []
    assert d["aggregate"]["lo"] == 1.0 and d["aggregate"]["confidence"] == 0.95
    assert all(v["confidence"] == 0.99 for v in d["per_opponent"].values())


def test_no_change_rejected_for_aggregate_not_positive():
    same = _blocks({o: 2 for o in OPPS})
    d = decide_promotion("fin", same, same, bootstrap_seed=1, n_resamples=500)
    assert not d["promoted"]
    assert any("aggregate" in r for r in d["reasons"])


def test_one_opponent_regression_beyond_tolerance_rejects_despite_higher_mean():
    cand = _blocks({"a": 0, "b": 6, "c": 6, "d": 6, "e": 6})
    ctrl = _blocks({o: 2 for o in OPPS})
    d = decide_promotion("fin", cand, ctrl, bootstrap_seed=1, n_resamples=500)
    assert d["aggregate"]["lo"] > 0
    assert not d["promoted"]
    assert any("opponent a" in r for r in d["reasons"])


def test_incomplete_evidence_rejected():
    cand, ctrl = _blocks({o: 4 for o in OPPS}), _blocks({o: 2 for o in OPPS})
    d = decide_promotion("fin", cand[:-1], ctrl[:-1], bootstrap_seed=1, n_resamples=500,
                         expected_blocks=20)
    assert not d["promoted"] and any("incomplete" in r for r in d["reasons"])
