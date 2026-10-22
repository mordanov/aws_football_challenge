"""Finalist selection and the one-shot final-test lock."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import Evaluator, Fitness, PoolSpec, SelectionResult, Settings
from .fitness import EvaluationCache, fitness_from_blocks, rank_key

MAX_CHALLENGERS = 3


class FinalTestLockError(Exception):
    pass


def select_finalist(
    training_fitness: Mapping[str, Fitness],
    settings_by_hash: Mapping[str, Settings],
    incumbent_hash: str,
    evaluator: Evaluator,
    cache: EvaluationCache,
    pool: PoolSpec,
    opponents: Sequence[str],
) -> SelectionResult:
    challengers = [
        f["profile_hash"]
        for f in sorted(training_fitness.values(), key=rank_key)
        if f["profile_hash"] != incumbent_hash
    ][:MAX_CHALLENGERS]
    fitness: dict[str, Fitness] = {}
    if not challengers:
        return {"candidates": challengers, "fitness": fitness, "finalist": None}
    # Only evaluate incumbent vs best challenger; ties favour incumbent
    top = challengers[0]
    for h in [incumbent_hash, top]:
        blocks = cache.get_or_run(evaluator, settings_by_hash[h], pool, opponents)
        fitness[h] = fitness_from_blocks(h, blocks)
    inc_f = fitness[incumbent_hash]
    top_f = fitness[top]
    challenger_wins = (top_f["mean_points"], top_f["mean_goal_diff"]) > (
        inc_f["mean_points"], inc_f["mean_goal_diff"]
    )
    return {
        "candidates": challengers,
        "fitness": fitness,
        "finalist": top if challenger_wins else None,
    }


class FinalTestLock:
    """Persisted state machine: unfrozen -> frozen(finalist) -> opened. No way back."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict | None:
        return json.loads(self._path.read_text()) if self._path.exists() else None

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    def freeze(self, finalist: str, incumbent: str) -> None:
        if self._read() is not None:
            raise FinalTestLockError("finalist already frozen")
        self._write({"finalist": finalist, "incumbent": incumbent, "opened": False})

    def begin_final(self, finalist: str) -> None:
        state = self._read()
        if state is None:
            raise FinalTestLockError("finalist not frozen; final-test access denied")
        if state["finalist"] != finalist:
            raise FinalTestLockError("profile is not the frozen finalist")
        if state["opened"]:
            raise FinalTestLockError("final-test pool already opened")
        state["opened"] = True
        self._write(state)

    def is_opened(self) -> bool:
        state = self._read()
        return bool(state and state["opened"])
