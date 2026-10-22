from football_lab.evolution.fitness import EvaluationCache, fitness_from_blocks, rank_key
from football_lab.evolution.pools import make_pool

POOL = make_pool("training", 100_000, 2, 2)
OPPS = ["a", "b"]


def _blocks(points_gd):
    return [
        {"opponent": o, "block": b, "points": p, "goal_diff": g, "legs": 2}
        for (o, b), (p, g) in zip([(o, b) for o in OPPS for b in range(2)], points_gd)
    ]


def test_fitness_per_match():
    f = fitness_from_blocks("h", _blocks([(6, 2), (0, -2), (2, 0), (2, 0)]))
    assert f["n_matches"] == 8
    assert f["mean_points"] == 10 / 8
    assert f["mean_goal_diff"] == 0.0


def test_rank_key_points_then_gd_then_hash():
    hi = {"profile_hash": "b", "mean_points": 1.5, "mean_goal_diff": 0.0, "n_matches": 8}
    gd = {"profile_hash": "c", "mean_points": 1.5, "mean_goal_diff": 0.5, "n_matches": 8}
    tie = {"profile_hash": "a", "mean_points": 1.5, "mean_goal_diff": 0.5, "n_matches": 8}
    assert sorted([hi, gd, tie], key=rank_key) == [tie, gd, hi]


class CountingEvaluator:
    evaluator_id = "fake-v1"

    def __init__(self):
        self.calls = 0

    def evaluate(self, settings, pool, opponents):
        self.calls += 1
        return _blocks([(3, 1)] * 4)


def test_cache_reuses_identical_inputs_only(tmp_path):
    ev, cache = CountingEvaluator(), EvaluationCache(tmp_path / "evals.jsonl")
    s = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}
    cache.get_or_run(ev, s, POOL, OPPS)
    cache.get_or_run(ev, s, POOL, OPPS)
    assert (ev.calls, cache.unique_evaluations, cache.cache_hits) == (1, 1, 1)
    cache.get_or_run(ev, s, make_pool("training", 100_001, 2, 2), OPPS)
    assert ev.calls == 2  # different pool start = different input
    resumed = EvaluationCache(tmp_path / "evals.jsonl")
    resumed.get_or_run(ev, s, POOL, OPPS)
    assert ev.calls == 2  # persisted entry reused after "restart"
