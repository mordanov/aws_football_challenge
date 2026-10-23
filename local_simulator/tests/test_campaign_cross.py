from __future__ import annotations

import json
from pathlib import Path

import pytest

from football_lab.campaign import cross_fixtures, load_manifest, round_robin_fixtures


def test_cross_fixtures_pair_every_challenger_with_every_pool_member() -> None:
    fx = cross_fixtures(["b", "a"], ["y", "x", "z"], seed_pool_start=100, n_blocks=2)
    assert len(fx) == 2 * 3 * 2 * 2
    assert fx[0] == {"fixture_id": "a__vs__x__b0000__l0", "team0_entrant": "a",
                     "team1_entrant": "x", "seed_block": 0, "leg": 0, "seed": 100}
    assert fx[1]["fixture_id"] == "x__vs__a__b0000__l1" and fx[1]["seed"] == 101
    assert len({f["fixture_id"] for f in fx}) == len(fx)


@pytest.mark.parametrize(
    ("challengers", "pool"),
    [(["a"], ["a", "b"]), (["a__x"], ["b"]), (["a"], ["b__y"]), (["a", "a"], ["b"])],
)
def test_cross_fixtures_reject_bad_ids(challengers: list[str], pool: list[str]) -> None:
    with pytest.raises(ValueError):
        cross_fixtures(challengers, pool, seed_pool_start=0, n_blocks=1)


def test_round_robin_order_unchanged() -> None:
    ids = [f["fixture_id"] for f in round_robin_fixtures(["b", "a"], 0, 1)]
    assert ids == ["a__vs__b__b0000__l0", "b__vs__a__b0000__l1"]


def test_load_manifest_accepts_v1_and_v2_only(tmp_path: Path) -> None:
    for version, ok in (("v1", True), ("v2", True), ("v3", False)):
        path = tmp_path / f"{version}.json"
        path.write_text(json.dumps({"campaign_id": "c", "rules_version": version}))
        if ok:
            assert load_manifest(path)["rules_version"] == version
        else:
            with pytest.raises(AssertionError):
                load_manifest(path)
