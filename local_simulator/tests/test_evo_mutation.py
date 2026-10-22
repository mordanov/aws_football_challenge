import random

from football_lab.evolution.bounds import load_bounds, validate_settings
from football_lab.evolution.mutation import derive_seed, mutate, profile_hash

INC = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}


def test_hash_is_stable_and_order_independent():
    shuffled = dict(reversed(list(INC.items())))
    assert profile_hash(INC) == profile_hash(shuffled)
    assert profile_hash(INC) != profile_hash({**INC, "pass_margin": 3.1})


def test_derive_seed_deterministic_and_distinct():
    assert derive_seed(7, 0, 1) == derive_seed(7, 0, 1)
    assert derive_seed(7, 0, 1) != derive_seed(7, 0, 2)
    assert derive_seed(7, 0, 1) != derive_seed(8, 0, 1)


def test_mutation_valid_reproducible_and_changes_at_least_one_param():
    bounds = load_bounds()
    for s in range(200):
        child, mutated = mutate(INC, random.Random(s), bounds)
        validate_settings(child)
        assert len(mutated) >= 1
        for k in bounds:
            assert (child[k] != INC[k]) <= (k in mutated)
        again, again_mut = mutate(INC, random.Random(s), bounds)
        assert (child, mutated) == (again, again_mut)


def test_mutation_clamps_at_bounds():
    bounds = load_bounds()
    edge = {k: hi for k, (_, hi) in bounds.items()}
    for s in range(100):
        child, _ = mutate(edge, random.Random(s), bounds)
        assert all(child[k] <= bounds[k][1] for k in bounds)
