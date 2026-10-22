"""Campaign scoring: standings and block-bootstrap confidence intervals."""
from __future__ import annotations

import itertools
import random
from collections import defaultdict
from typing import Any, NamedTuple, TypedDict

from .entrant_contracts import CampaignManifest, EntrantStanding, FixtureResult


class HeadToHeadRecord(TypedDict):
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int


class OperationalSummary(TypedDict):
    total: int
    complete: int
    failed: int


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


class WinnerClaimResult(TypedDict):
    winner_id: str | None
    is_conclusive: bool
    reason: str  # "clear_winner" | "inconclusive" | "incomplete_data"
    intervals_99_5: dict[str, dict[str, tuple[float, float]]]


def bootstrap_ci(
    results: list[FixtureResult],
    rng_seed: int,
    n_resamples: int = 10_000,
    confidence: float = 0.95,
) -> dict[str, dict[str, tuple[float, float]]]:
    """Bootstrap CI on per-match points for each ordered entrant pair.

    Resamples complete two-leg seed blocks (block integrity).
    Returns {entrant_a: {vs_entrant_b: (lo, hi)}}.
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

        alpha = (1 - confidence) / 2
        diffs.sort()
        neg_diffs.sort()
        lo_a = diffs[int(alpha * n_resamples)]
        hi_a = diffs[int((1 - alpha) * n_resamples) - 1]
        lo_b = neg_diffs[int(alpha * n_resamples)]
        hi_b = neg_diffs[int((1 - alpha) * n_resamples) - 1]
        ci[a][f"vs_{b}"] = (lo_a, hi_a)
        ci[b][f"vs_{a}"] = (lo_b, hi_b)

    return dict(ci)


def head_to_head_matrix(
    results: list[FixtureResult],
) -> dict[str, dict[str, HeadToHeadRecord]]:
    """Return {entrant_a: {entrant_b: record}} from a's perspective (complete fixtures only)."""
    acc: dict[str, dict[str, HeadToHeadRecord]] = defaultdict(dict)

    def _entry(a: str, b: str) -> HeadToHeadRecord:
        if b not in acc[a]:
            acc[a][b] = {"wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0}
        return acc[a][b]

    for r in results:
        if r["status"] != "complete":
            continue
        t0, t1, _, _ = _parse_fixture_id(r["fixture_id"])
        g0, g1 = r["score"]
        e01, e10 = _entry(t0, t1), _entry(t1, t0)
        e01["goals_for"] += g0
        e01["goals_against"] += g1
        e10["goals_for"] += g1
        e10["goals_against"] += g0
        if g0 > g1:
            e01["wins"] += 1
            e10["losses"] += 1
        elif g1 > g0:
            e01["losses"] += 1
            e10["wins"] += 1
        else:
            e01["draws"] += 1
            e10["draws"] += 1

    return dict(acc)


def operational_summary(
    manifest: CampaignManifest,
) -> tuple[OperationalSummary, dict[str, int]]:
    """Return (summary, rejections_by_entrant_id).

    Rejections are attributed to the entrant that played as team0/team1 in each fixture.
    """
    results_by_id = {r["fixture_id"]: r for r in manifest["results"]}
    rejections: dict[str, int] = defaultdict(int)
    complete = 0
    failed = 0

    for fixture in manifest["fixtures"]:
        fid = fixture["fixture_id"]
        r = results_by_id.get(fid)
        if r is None:
            continue
        if r["status"] == "complete":
            complete += 1
        else:
            failed += 1
        rejections[fixture["team0_entrant"]] += r["team0_rejections"]
        rejections[fixture["team1_entrant"]] += r["team1_rejections"]

    summary: OperationalSummary = {
        "total": len(manifest["fixtures"]),
        "complete": complete,
        "failed": failed,
    }
    return summary, dict(rejections)


def winner_claim(
    results: list[FixtureResult],
    entrant_ids: list[str],
    bootstrap_seed: int,
    n_resamples: int = 10_000,
) -> WinnerClaimResult:
    """Bonferroni-adjusted winner claim at 99.5% CI.

    Returns incomplete_data if any pair has no complete results or any failure exists.
    """
    _empty: WinnerClaimResult = {
        "winner_id": None,
        "is_conclusive": False,
        "reason": "incomplete_data",
        "intervals_99_5": {},
    }

    # Require all fixtures complete and all pairs covered
    if any(r["status"] != "complete" for r in results):
        return _empty

    complete_pairs: set[tuple[str, str]] = set()
    for r in results:
        t0, t1, _, _ = _parse_fixture_id(r["fixture_id"])
        pair = (min(t0, t1), max(t0, t1))
        complete_pairs.add(pair)

    required = {
        (min(a, b), max(a, b))
        for a, b in itertools.combinations(sorted(entrant_ids), 2)
    }
    if not required.issubset(complete_pairs):
        return _empty

    ci = bootstrap_ci(results, bootstrap_seed, n_resamples, confidence=0.995)

    candidates = [
        e for e in entrant_ids
        if all(
            ci.get(e, {}).get(f"vs_{rival}", (float("-inf"), 0.0))[0] > 0
            for rival in entrant_ids
            if rival != e
        )
    ]

    if len(candidates) == 1:
        return {
            "winner_id": candidates[0],
            "is_conclusive": True,
            "reason": "clear_winner",
            "intervals_99_5": ci,
        }
    return {
        "winner_id": None,
        "is_conclusive": False,
        "reason": "inconclusive",
        "intervals_99_5": ci,
    }


class PooledGoalDiff(TypedDict):
    entrant_id: str
    matches: int
    units: int
    mean: float
    lo: float
    hi: float


def _units(results: list[FixtureResult], entrant_id: str) -> dict[tuple[str, int], list[int]]:
    """Goal differences from `entrant_id`'s view, grouped by (opponent, block)."""
    units: dict[tuple[str, int], list[int]] = defaultdict(list)
    for r in results:
        if r["status"] != "complete":
            continue
        t0, t1, block, _ = _parse_fixture_id(r["fixture_id"])
        g0, g1 = r["score"]
        if t0 == entrant_id:
            units[(t1, block)].append(g0 - g1)
        elif t1 == entrant_id:
            units[(t0, block)].append(g1 - g0)
    return units


def _percentiles(stats: list[float], confidence: float) -> tuple[float, float]:
    stats.sort()
    n = len(stats)
    alpha = (1 - confidence) / 2
    return stats[int(alpha * n)], stats[int((1 - alpha) * n) - 1]


def pooled_goal_diff_ci(
    results: list[FixtureResult],
    entrant_id: str,
    rng_seed: int,
    n_resamples: int = 10_000,
    confidence: float = 0.95,
) -> PooledGoalDiff:
    """Mean goal difference per match vs all opponents; block bootstrap over units."""
    units = list(_units(results, entrant_id).values())
    if not units:
        raise ValueError(f"no complete results for {entrant_id!r}")
    matches = sum(len(u) for u in units)
    rng = random.Random(rng_seed)
    stats = []
    for _ in range(n_resamples):
        sample = [units[rng.randrange(len(units))] for _ in units]
        stats.append(sum(sum(u) for u in sample) / sum(len(u) for u in sample))
    lo, hi = _percentiles(stats, confidence)
    return {"entrant_id": entrant_id, "matches": matches, "units": len(units),
            "mean": sum(sum(u) for u in units) / matches, "lo": lo, "hi": hi}


def goal_diff_delta_ci(
    results: list[FixtureResult],
    a: str,
    b: str,
    rng_seed: int,
    n_resamples: int = 10_000,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """(mean, lo, hi) of a's minus b's per-match goal difference, paired on shared units."""
    ua, ub = _units(results, a), _units(results, b)
    shared = sorted(set(ua) & set(ub))
    if not shared:
        raise ValueError(f"{a!r} and {b!r} share no (opponent, block) units")
    deltas = [sum(ua[k]) / len(ua[k]) - sum(ub[k]) / len(ub[k]) for k in shared]
    rng = random.Random(rng_seed)
    stats = [
        sum(deltas[rng.randrange(len(deltas))] for _ in deltas) / len(deltas)
        for _ in range(n_resamples)
    ]
    lo, hi = _percentiles(stats, confidence)
    return sum(deltas) / len(deltas), lo, hi


_DISCIPLINE = {
    "FOUL": "fouls",
    "SEND_OFF": "send_offs",
    "TACKLE_SUCCESS": "tackles_won",
    "PENALTY": "penalties_awarded",
}


def discipline_counts(events: list[Any]) -> dict[str, list[int]]:
    """Per-team [team0, team1] counts of discipline events in one replay."""
    out = {name: [0, 0] for name in _DISCIPLINE.values()}
    for e in events:
        name = _DISCIPLINE.get(e["kind"])
        if name is not None and e["team_id"] in (0, 1):
            out[name][e["team_id"]] += 1
    return out


def discipline_by_entrant(results: list[FixtureResult]) -> dict[str, dict[str, int]]:
    """Totals per entrant over results that recorded discipline."""
    totals: dict[str, dict[str, int]] = {}
    for r in results:
        d = r.get("discipline")
        if r["status"] != "complete" or d is None:
            continue
        t0, t1, _, _ = _parse_fixture_id(r["fixture_id"])
        for side, eid in enumerate((t0, t1)):
            row = totals.setdefault(eid, {"matches": 0, **{n: 0 for n in _DISCIPLINE.values()}})
            row["matches"] += 1
            for name in _DISCIPLINE.values():
                row[name] += d.get(name, [0, 0])[side]
    return totals
