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


def round_robin_fixtures(
    entrant_ids: list[str],
    seed_pool_start: int,
    n_blocks: int,
) -> list[FixtureSpec]:
    """All C(n,2) ordered pairs × n_blocks blocks × 2 legs, sorted deterministically."""
    fixtures: list[FixtureSpec] = []
    for t0, t1 in itertools.combinations(sorted(entrant_ids), 2):
        for block in range(n_blocks):
            for leg in range(2):
                # leg 1 swaps home/away
                home, away = (t0, t1) if leg == 0 else (t1, t0)
                seed = fixture_seed(seed_pool_start, block, leg)
                fixture_id = f"{home}__vs__{away}__b{block:04d}__l{leg}"
                fixtures.append(
                    {
                        "fixture_id": fixture_id,
                        "team0_entrant": home,
                        "team1_entrant": away,
                        "seed_block": block,
                        "leg": leg,
                        "seed": seed,
                    }
                )
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
    assert data.get("rules_version") == "v1", (
        f"unsupported rules_version: {data.get('rules_version')}"
    )
    return data
