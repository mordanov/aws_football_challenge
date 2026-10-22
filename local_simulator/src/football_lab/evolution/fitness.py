"""Fitness computation, ranking and the exact-input evaluation cache."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .contracts import BlockResult, Evaluator, Fitness, PoolSpec, Settings
from .mutation import profile_hash


def fitness_from_blocks(phash: str, blocks: Sequence[BlockResult]) -> Fitness:
    n = sum(b["legs"] for b in blocks)
    return {
        "profile_hash": phash,
        "mean_points": sum(b["points"] for b in blocks) / n,
        "mean_goal_diff": sum(b["goal_diff"] for b in blocks) / n,
        "n_matches": n,
    }


def rank_key(f: Fitness) -> tuple[float, float, str]:
    """Best first: mean points, then goal difference, then stable hash."""
    return (-f["mean_points"], -f["mean_goal_diff"], f["profile_hash"])


class EvaluationCache:
    """Reuse evidence only when every evaluation input is identical."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._store: dict[str, list[BlockResult]] = {}
        self.unique_evaluations = 0
        self.cache_hits = 0
        if path is not None and path.exists():
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                self._store[rec["key"]] = rec["blocks"]

    @staticmethod
    def key(evaluator_id: str, settings: Settings, pool: PoolSpec, opponents: Sequence[str]) -> str:
        return json.dumps(
            [evaluator_id, profile_hash(settings), pool, list(opponents)], sort_keys=True
        )

    def get_or_run(
        self,
        evaluator: Evaluator,
        settings: Settings,
        pool: PoolSpec,
        opponents: Sequence[str],
    ) -> list[BlockResult]:
        k = self.key(evaluator.evaluator_id, settings, pool, opponents)
        if k in self._store:
            self.cache_hits += 1
            return self._store[k]
        blocks = evaluator.evaluate(settings, pool, opponents)
        self._store[k] = blocks
        self.unique_evaluations += 1
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(json.dumps({"key": k, "blocks": blocks}) + "\n")
                f.flush()
        return blocks
