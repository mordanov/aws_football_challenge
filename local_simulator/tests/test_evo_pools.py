import pytest

from football_lab.campaign import fixture_seed
from football_lab.evolution.pools import (
    PoolOverlap,
    assert_disjoint,
    make_pool,
    pool_seed,
    stage1_range,
)


def test_pool_size_and_seed_layout():
    p = make_pool("training", start=100_000, blocks=8, n_opponents=5)
    assert p["size"] == 80
    assert pool_seed(p, opp_index=0, block=0, leg=0) == 100_000
    assert pool_seed(p, opp_index=1, block=0, leg=0) == 100_000 + 16
    assert pool_seed(p, opp_index=4, block=7, leg=1) == 100_000 + 79
    assert pool_seed(p, 2, 3, 1) == fixture_seed(100_000 + 2 * 16, 3, 1)


def test_all_seeds_unique_within_pool():
    p = make_pool("selection", 200_000, 20, 5)
    seeds = {pool_seed(p, o, b, l) for o in range(5) for b in range(20) for l in range(2)}
    assert len(seeds) == p["size"]


def test_overlap_detected_between_pools_and_reserved_ranges():
    a = make_pool("training", 100_000, 8, 5)
    b = make_pool("selection", 100_050, 20, 5)
    with pytest.raises(PoolOverlap):
        assert_disjoint([a, b], reserved=[])
    c = make_pool("final", 10_100, 100, 5)
    with pytest.raises(PoolOverlap):
        assert_disjoint([c], reserved=[range(10_000, 10_200)])
    assert_disjoint([a], reserved=[range(10_000, 10_200)])


def test_stage1_range_from_manifest_fixtures():
    manifest = {"fixtures": [{"seed": 10_000}, {"seed": 10_199}]}
    assert stage1_range(manifest) == range(10_000, 10_200)
