"""Tests for campaign harness: fixture scheduling and manifest management."""
import json
from pathlib import Path

import pytest

from football_lab.campaign import (
    add_result,
    fixture_seed,
    load_manifest,
    pending_fixtures,
    round_robin_fixtures,
    save_manifest,
)
from football_lab.entrant_contracts import CampaignManifest, FixtureResult


def _make_manifest(entrant_ids: list[str], n_blocks: int) -> CampaignManifest:
    fixtures = round_robin_fixtures(entrant_ids, seed_pool_start=0, n_blocks=n_blocks)
    return {
        "campaign_id": "test-campaign",
        "track": "development",
        "entrants": [],
        "seed_pool_start": 0,
        "budget": {"startup_timeout_s": 5.0, "request_timeout_s": 5.0, "output_size_limit": 65536},
        "rules_version": "v1",
        "fixtures": fixtures,
        "results": [],
        "locked": False,
    }


def _make_result(fixture_spec) -> FixtureResult:
    return {
        "fixture_id": fixture_spec["fixture_id"],
        "status": "complete",
        "score": [1, 0],
        "failure_reason": None,
        "replay_path": None,
        "duration_ms": 100.0,
        "team0_rejections": 0,
        "team1_rejections": 0,
    }


def test_round_robin_count():
    specs = round_robin_fixtures(["a", "b", "c"], seed_pool_start=1000, n_blocks=3)
    # 3 pairs × 3 blocks × 2 legs = 18
    assert len(specs) == 18


def test_no_duplicate_ids():
    specs = round_robin_fixtures(["a", "b", "c"], seed_pool_start=0, n_blocks=5)
    ids = [s["fixture_id"] for s in specs]
    assert len(ids) == len(set(ids))


def test_seed_differs_by_leg():
    s0 = fixture_seed(pool_start=0, block=7, leg=0)
    s1 = fixture_seed(pool_start=0, block=7, leg=1)
    assert s0 != s1


def test_seed_deterministic():
    assert fixture_seed(100, 3, 0) == fixture_seed(100, 3, 0)


def test_seed_formula():
    assert fixture_seed(1000, 5, 0) == 1010
    assert fixture_seed(1000, 5, 1) == 1011


def test_manifest_round_trip(tmp_path):
    manifest = _make_manifest(["a", "b"], n_blocks=2)
    path = tmp_path / "manifest.json"
    save_manifest(manifest, path)
    loaded = load_manifest(path)
    assert loaded["campaign_id"] == manifest["campaign_id"]
    assert len(loaded["fixtures"]) == len(manifest["fixtures"])


def test_resume_skips_completed(tmp_path):
    manifest = _make_manifest(["a", "b"], n_blocks=2)
    manifest["results"].append({
        "fixture_id": manifest["fixtures"][0]["fixture_id"],
        "status": "complete",
        "score": [1, 0], "failure_reason": None, "replay_path": None,
        "duration_ms": 10.0, "team0_rejections": 0, "team1_rejections": 0,
    })
    pending = pending_fixtures(manifest)
    assert manifest["fixtures"][0]["fixture_id"] not in [f["fixture_id"] for f in pending]


def test_no_duplicate_results(tmp_path):
    manifest = _make_manifest(["a", "b"], n_blocks=1)
    result = _make_result(manifest["fixtures"][0])
    add_result(manifest, result)
    with pytest.raises(ValueError, match="duplicate"):
        add_result(manifest, result)
