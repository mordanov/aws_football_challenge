"""Campaign scoring: standings and block-bootstrap confidence intervals."""
from __future__ import annotations

import random
from collections import defaultdict
from typing import NamedTuple

from .entrant_contracts import EntrantStanding, FixtureResult


def _parse_fixture_id(fixture_id: str) -> tuple[str, str, int, int]:
    """Return (team0, team1, block, leg) from fixture_id."""
    # Format: "{team0}__vs__{team1}__b{block:04d}__l{leg}"
    parts = fixture_id.split("__")
    # find "vs" separator, block and leg at the end
    leg = int(parts[-1][1:])        # "l0" → 0
    block = int(parts[-2][1:])      # "b0001" → 1
    vs_idx = parts.index("vs")
    team0 = "__".join(parts[:vs_idx])
    team1 = "__".join(parts[vs_idx + 1 : -2])
    return team0, team1, block, leg


def compute_standings(results: list[FixtureResult]) -> list[EntrantStanding]:
    """Points: win=3, draw=1, loss=0. Decodes entrant names from fixture_id."""
    acc: dict[str, EntrantStanding] = {}

    def _entry(eid: str) -> EntrantStanding:
        if eid not in acc:
            acc[eid] = {
                "entrant_id": eid, "games": 0, "wins": 0, "draws": 0,
                "losses": 0, "points": 0, "goals_for": 0,
                "goals_against": 0, "goal_diff": 0,
            }
        return acc[eid]

    for r in results:
        if r["status"] != "complete":
            continue
        t0, t1, _, _ = _parse_fixture_id(r["fixture_id"])
        g0, g1 = r["score"]
        e0, e1 = _entry(t0), _entry(t1)
        e0["games"] += 1
        e1["games"] += 1
        e0["goals_for"] += g0
        e0["goals_against"] += g1
        e1["goals_for"] += g1
        e1["goals_against"] += g0
        e0["goal_diff"] = e0["goals_for"] - e0["goals_against"]
        e1["goal_diff"] = e1["goals_for"] - e1["goals_against"]
        if g0 > g1:
            e0["wins"] += 1
            e0["points"] += 3
            e1["losses"] += 1
        elif g1 > g0:
            e1["wins"] += 1
            e1["points"] += 3
            e0["losses"] += 1
        else:
            e0["draws"] += 1
            e0["points"] += 1
            e1["draws"] += 1
            e1["points"] += 1

    return sorted(acc.values(), key=lambda s: (-s["points"], -s["goal_diff"]))


class _Block(NamedTuple):
    pts0: float  # points for team0 across both legs of this block
    pts1: float  # points for team1


def bootstrap_ci(
    results: list[FixtureResult],
    rng_seed: int,
    n_resamples: int = 10_000,
) -> dict[str, dict[str, tuple[float, float]]]:
    """95% CI on per-match points for each ordered entrant pair.

    Resamples complete two-leg seed blocks (block integrity).
    Returns {entrant_a: {vs_entrant_b: (lo95, hi95)}}.
    """
    # Group by canonical ordered pair (a, b) where a < b lexicographically,
    # then by seed block.  Each block collects points from both legs.
    # We keep an ordered pair (team0_lex, team1_lex) and track pts for each.
    BlockKey = tuple[str, str, int]  # (lex0, lex1, block)
    block_pts: dict[BlockKey, list[float]] = defaultdict(lambda: [0.0, 0.0])

    for r in results:
        if r["status"] != "complete":
            continue
        t0, t1, block, _ = _parse_fixture_id(r["fixture_id"])
        g0, g1 = r["score"]
        # canonical order
        if t0 <= t1:
            key: BlockKey = (t0, t1, block)
            p0 = 3.0 if g0 > g1 else (1.0 if g0 == g1 else 0.0)
            p1 = 3.0 if g1 > g0 else (1.0 if g0 == g1 else 0.0)
        else:
            key = (t1, t0, block)
            p0 = 3.0 if g1 > g0 else (1.0 if g0 == g1 else 0.0)
            p1 = 3.0 if g0 > g1 else (1.0 if g0 == g1 else 0.0)
        block_pts[key][0] += p0
        block_pts[key][1] += p1

    # Build per-pair block lists
    pair_blocks: dict[tuple[str, str], list[_Block]] = defaultdict(list)
    for (a, b, _block), (pa, pb) in block_pts.items():
        pair_blocks[(a, b)].append(_Block(pa, pb))

    rng = random.Random(rng_seed)
    ci: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)

    for (a, b), blocks in pair_blocks.items():
        n = len(blocks)
        # CI is per-match point differential: (a_pts - b_pts) / matches_per_block
        diffs: list[float] = []
        neg_diffs: list[float] = []
        for _ in range(n_resamples):
            sample = [blocks[rng.randrange(n)] for _ in range(n)]
            diff = sum(bl.pts0 - bl.pts1 for bl in sample) / (n * 2)
            diffs.append(diff)
            neg_diffs.append(-diff)

        diffs.sort()
        neg_diffs.sort()
        lo_a = diffs[int(0.025 * n_resamples)]
        hi_a = diffs[int(0.975 * n_resamples) - 1]
        lo_b = neg_diffs[int(0.025 * n_resamples)]
        hi_b = neg_diffs[int(0.975 * n_resamples) - 1]
        ci[a][f"vs_{b}"] = (lo_a, hi_a)
        ci[b][f"vs_{a}"] = (lo_b, hi_b)

    return dict(ci)
