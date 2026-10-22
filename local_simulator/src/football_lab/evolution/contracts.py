"""M5 contracts for profile evolution. Shapes only; no behavior."""
from __future__ import annotations

from typing import Literal, Protocol, Sequence, TypedDict

PARAM_NAMES: tuple[str, ...] = ("pass_margin", "shot_range", "press_radius", "support_width")

Settings = dict[str, float]
Bounds = dict[str, tuple[float, float]]

PoolName = Literal["development", "training", "selection", "final"]
Origin = Literal[
    "incumbent", "initial_variant", "retained_incumbent", "retained_best", "mutation"
]


class PoolSpec(TypedDict):
    name: PoolName
    start: int
    blocks: int          # paired seed blocks per opponent
    n_opponents: int
    size: int            # n_opponents * blocks * 2 seeds, range [start, start + size)


class BlockResult(TypedDict):
    opponent: str
    block: int
    points: float        # profile's league points summed over both legs of the block
    goal_diff: int       # profile's goal difference summed over both legs
    legs: int            # always 2


class Fitness(TypedDict):
    profile_hash: str
    mean_points: float   # per match
    mean_goal_diff: float
    n_matches: int


class LineageEntry(TypedDict):
    profile_hash: str
    settings: Settings
    generation: int
    origin: Origin
    parents: list[str]
    mutation_seed: int | None
    mutated_params: list[str]


class SearchResult(TypedDict):
    lineage: list[LineageEntry]
    generations: list[list[str]]       # population profile hashes per generation (len 12 each)
    training_fitness: dict[str, Fitness]
    unique_evaluations: int
    cache_hits: int


class SelectionResult(TypedDict):
    candidates: list[str]              # <=3 distinct non-incumbent hashes, training order
    fitness: dict[str, Fitness]        # incl. incumbent
    finalist: str | None               # None => incumbent kept, no promotion


class IntervalCheck(TypedDict):
    lo: float
    hi: float
    confidence: float


class PromotionDecision(TypedDict):
    promoted: bool
    finalist: str
    aggregate: IntervalCheck
    per_opponent: dict[str, IntervalCheck]
    reasons: list[str]                 # empty when promoted; else every failed criterion


class Evaluator(Protocol):
    evaluator_id: str  # part of the cache key: identifies rules, shim, opponent hashes

    def evaluate(
        self, settings: Settings, pool: PoolSpec, opponents: Sequence[str]
    ) -> list[BlockResult]:
        """Play every (opponent, block) fixture of the pool for one profile.

        Raises EvaluationError on any operational failure.
        """
        ...


class EvaluationError(Exception):
    pass
