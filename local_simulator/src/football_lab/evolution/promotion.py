"""Paired, block-resampled bootstrap and the promotion criteria (spec section 10)."""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Sequence

from .contracts import BlockResult, IntervalCheck, PromotionDecision

AGG_CONFIDENCE = 0.95
OPP_CONFIDENCE = 0.99          # Bonferroni: 0.05 / 5 opponents, two-sided
NON_REGRESSION_MARGIN = -0.10  # explicit tolerance, not a claim of zero regression


def paired_block_diffs(
    finalist: Sequence[BlockResult], control: Sequence[BlockResult]
) -> dict[str, list[float]]:
    """Per (opponent, block): (finalist - control) points per match. One unit per block."""
    ctrl = {(b["opponent"], b["block"]): b for b in control}
    out: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for b in finalist:
        c = ctrl[(b["opponent"], b["block"])]
        out[b["opponent"]].append((b["block"], (b["points"] - c["points"]) / b["legs"]))
    return {o: [d for _, d in sorted(v)] for o, v in out.items()}


def _interval(samples: list[float], confidence: float) -> tuple[float, float]:
    s = sorted(samples)
    n = len(s)
    alpha = (1 - confidence) / 2
    return s[int(alpha * n)], s[int((1 - alpha) * n) - 1]


def decide_promotion(
    finalist_hash: str,
    finalist_blocks: Sequence[BlockResult],
    control_blocks: Sequence[BlockResult],
    bootstrap_seed: int,
    n_resamples: int = 10_000,
    expected_blocks: int | None = None,
    extra_failures: Sequence[str] = (),
) -> PromotionDecision:
    reasons: list[str] = list(extra_failures)
    diffs = paired_block_diffs(finalist_blocks, control_blocks)
    opps = sorted(diffs)
    if expected_blocks is not None and any(len(diffs[o]) != expected_blocks for o in opps):
        reasons.append(f"incomplete evidence: every opponent needs {expected_blocks} blocks")

    rng = random.Random(bootstrap_seed)
    per_opp: dict[str, list[float]] = {o: [] for o in opps}
    agg: list[float] = []
    for _ in range(n_resamples):
        means = []
        for o in opps:
            d = diffs[o]
            m = sum(d[rng.randrange(len(d))] for _ in range(len(d))) / len(d)
            per_opp[o].append(m)
            means.append(m)
        agg.append(sum(means) / len(means))

    lo, hi = _interval(agg, AGG_CONFIDENCE)
    aggregate: IntervalCheck = {"lo": lo, "hi": hi, "confidence": AGG_CONFIDENCE}
    if not lo > 0:
        reasons.append(f"aggregate lower bound {lo:.4f} is not positive")
    per_opponent: dict[str, IntervalCheck] = {}
    for o in opps:
        olo, ohi = _interval(per_opp[o], OPP_CONFIDENCE)
        per_opponent[o] = {"lo": olo, "hi": ohi, "confidence": OPP_CONFIDENCE}
        if olo < NON_REGRESSION_MARGIN:
            reasons.append(
                f"opponent {o} lower bound {olo:.4f} below {NON_REGRESSION_MARGIN}"
            )
    return {
        "promoted": not reasons,
        "finalist": finalist_hash,
        "aggregate": aggregate,
        "per_opponent": per_opponent,
        "reasons": reasons,
    }
