"""Seed pools for Stage 2: allocation and disjointness checks."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..campaign import fixture_seed
from .contracts import PoolName, PoolSpec


class PoolOverlap(Exception):
    pass


def make_pool(name: PoolName, start: int, blocks: int, n_opponents: int) -> PoolSpec:
    return {
        "name": name,
        "start": start,
        "blocks": blocks,
        "n_opponents": n_opponents,
        "size": n_opponents * blocks * 2,
    }


def pool_range(pool: PoolSpec) -> range:
    return range(pool["start"], pool["start"] + pool["size"])


def pool_seed(pool: PoolSpec, opp_index: int, block: int, leg: int) -> int:
    assert 0 <= opp_index < pool["n_opponents"] and 0 <= block < pool["blocks"]
    return fixture_seed(pool["start"] + opp_index * pool["blocks"] * 2, block, leg)


def stage1_range(manifest: Mapping[str, Any]) -> range:
    seeds = [f["seed"] for f in manifest["fixtures"]]
    return range(min(seeds), max(seeds) + 1)


def _overlaps(a: range, b: range) -> bool:
    return a.start < b.stop and b.start < a.stop


def assert_disjoint(pools: Sequence[PoolSpec], reserved: Sequence[range]) -> None:
    ranges = [(p["name"], pool_range(p)) for p in pools]
    for i, (na, ra) in enumerate(ranges):
        for nb, rb in ranges[i + 1:]:
            if _overlaps(ra, rb):
                raise PoolOverlap(f"pools {na} and {nb} overlap")
        for rr in reserved:
            if _overlaps(ra, rr):
                raise PoolOverlap(f"pool {na} overlaps reserved range {rr}")
