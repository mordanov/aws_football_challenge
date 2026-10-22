from football_lab.evolution.bounds import load_bounds
from football_lab.evolution.fitness import EvaluationCache
from football_lab.evolution.mutation import profile_hash
from football_lab.evolution.pools import make_pool
from football_lab.evolution.search import SearchConfig, run_search

INC = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}
OPPS = ["a", "b"]
POOL = make_pool("training", 100_000, 2, 2)


class QuadraticEvaluator:
    evaluator_id = "quad-v1"

    def evaluate(self, settings, pool, opponents):
        score = -sum((settings[k] - INC[k] - 1.0) ** 2 for k in settings)
        return [
            {"opponent": o, "block": b, "points": score, "goal_diff": 0, "legs": 2}
            for o in opponents
            for b in range(pool["blocks"])
        ]


def _run(seed=1):
    cfg = SearchConfig(population=12, generations=10, experiment_seed=seed, pool=POOL, opponents=OPPS)
    cache = EvaluationCache()
    return run_search(cfg, INC, QuadraticEvaluator(), cache, load_bounds()), cache


def test_population_shape_and_incumbent_retained_every_generation():
    result, _ = _run()
    inc = profile_hash(INC)
    assert len(result["generations"]) == 10
    assert all(len(g) == 12 and g[0] == inc for g in result["generations"])


def test_lineage_records_parents_seed_and_origin():
    result, _ = _run()
    by_origin = {e["origin"] for e in result["lineage"]}
    assert {"incumbent", "initial_variant", "retained_best", "mutation"} <= by_origin
    gen0 = [e for e in result["lineage"] if e["generation"] == 0]
    assert sum(e["origin"] == "initial_variant" for e in gen0) == 11
    for e in result["lineage"]:
        if e["origin"] in ("initial_variant", "mutation"):
            assert e["parents"] and e["mutation_seed"] is not None and e["mutated_params"]


def test_reproducible_and_all_candidates_in_bounds():
    a, _ = _run(seed=5)
    b, _ = _run(seed=5)
    assert a == b
    bounds = load_bounds()
    for e in a["lineage"]:
        assert all(bounds[k][0] <= v <= bounds[k][1] for k, v in e["settings"].items())


def test_cache_disclosure_counts_unique_evaluations():
    result, cache = _run()
    assert result["unique_evaluations"] == cache.unique_evaluations
    assert result["unique_evaluations"] + result["cache_hits"] == 10 * 12
    assert len(result["training_fitness"]) == result["unique_evaluations"]
