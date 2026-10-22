"""Campaign harness: fixture scheduling, manifest persistence, resume support."""

from __future__ import annotations

import itertools
import json
import os
import tempfile
from pathlib import Path

from .entrant_contracts import CampaignManifest, FixtureResult, FixtureSpec


def fixture_seed(pool_start: int, block: int, leg: int) -> int:
    return pool_start + block * 2 + leg


def _pair_fixtures(
    first: str, second: str, seed_pool_start: int, n_blocks: int
) -> list[FixtureSpec]:
    """n_blocks × 2 legs; `first` is home in leg 0, leg 1 swaps home/away."""
    fixtures: list[FixtureSpec] = []
    for block in range(n_blocks):
        for leg in range(2):
            home, away = (first, second) if leg == 0 else (second, first)
            fixtures.append(
                {
                    "fixture_id": f"{home}__vs__{away}__b{block:04d}__l{leg}",
                    "team0_entrant": home,
                    "team1_entrant": away,
                    "seed_block": block,
                    "leg": leg,
                    "seed": fixture_seed(seed_pool_start, block, leg),
                }
            )
    return fixtures


def round_robin_fixtures(
    entrant_ids: list[str],
    seed_pool_start: int,
    n_blocks: int,
) -> list[FixtureSpec]:
    """All C(n,2) ordered pairs × n_blocks blocks × 2 legs, sorted deterministically."""
    fixtures: list[FixtureSpec] = []
    for t0, t1 in itertools.combinations(sorted(entrant_ids), 2):
        fixtures.extend(_pair_fixtures(t0, t1, seed_pool_start, n_blocks))
    return fixtures


def cross_fixtures(
    challengers: list[str],
    pool: list[str],
    seed_pool_start: int,
    n_blocks: int,
) -> list[FixtureSpec]:
    """Every challenger vs every pool member (challenger home in leg 0); no intra-group games."""
    if len(set(challengers)) != len(challengers) or len(set(pool)) != len(pool):
        raise ValueError("duplicate entrant id")
    overlap = set(challengers) & set(pool)
    if overlap:
        raise ValueError(f"entrants in both groups: {sorted(overlap)}")
    bad = [e for e in (*challengers, *pool) if "__" in e]
    if bad:
        raise ValueError(f"entrant ids must not contain '__': {bad}")
    fixtures: list[FixtureSpec] = []
    for c in sorted(challengers):
        for p in sorted(pool):
            fixtures.extend(_pair_fixtures(c, p, seed_pool_start, n_blocks))
    return fixtures


def pending_fixtures(manifest: CampaignManifest) -> list[FixtureSpec]:
    done = {r["fixture_id"] for r in manifest["results"]}
    return [f for f in manifest["fixtures"] if f["fixture_id"] not in done]


def add_result(manifest: CampaignManifest, result: FixtureResult) -> None:
    done = {r["fixture_id"] for r in manifest["results"]}
    if result["fixture_id"] in done:
        raise ValueError(f"duplicate fixture_id: {result['fixture_id']}")
    manifest["results"].append(result)


def save_manifest(manifest: CampaignManifest, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_manifest(path: Path) -> CampaignManifest:
    data = json.loads(Path(path).read_text())
    assert "campaign_id" in data, "missing campaign_id"
    assert data.get("rules_version") in ("v1", "v2"), (
        f"unsupported rules_version: {data.get('rules_version')}"
    )
    return data
