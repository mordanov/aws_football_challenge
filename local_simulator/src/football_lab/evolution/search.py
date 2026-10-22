"""Population search over the four redesign_4 settings (spec section 10)."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from .bounds import validate_settings
from .contracts import (
    Bounds,
    Evaluator,
    Fitness,
    LineageEntry,
    Origin,
    PoolSpec,
    SearchResult,
    Settings,
)
from .fitness import EvaluationCache, fitness_from_blocks, rank_key
from .mutation import derive_seed, mutate, profile_hash

TOP_PARENTS = 3


@dataclass(frozen=True)
class SearchConfig:
    population: int
    generations: int
    experiment_seed: int
    pool: PoolSpec
    opponents: Sequence[str]


def run_search(
    cfg: SearchConfig,
    incumbent: Settings,
    evaluator: Evaluator,
    cache: EvaluationCache,
    bounds: Bounds,
) -> SearchResult:
    inc_hash = profile_hash(incumbent)
    settings_by_hash: dict[str, Settings] = {inc_hash: dict(incumbent)}
    lineage: list[LineageEntry] = []
    generations: list[list[str]] = []
    training: dict[str, Fitness] = {}

    def add_lineage(
        gen: int, settings: Settings, origin: Origin, parents: list[str],
        seed: int | None, mutated: list[str],
    ) -> str:
        h = profile_hash(settings)
        settings_by_hash.setdefault(h, dict(settings))
        lineage.append({
            "profile_hash": h, "settings": dict(settings), "generation": gen,
            "origin": origin, "parents": parents, "mutation_seed": seed,
            "mutated_params": mutated,
        })
        return h

    def make_child(gen: int, slot: int, parents: list[str]) -> tuple[Settings, int, list[str], str]:
        seed = derive_seed(cfg.experiment_seed, gen, slot)
        rng = random.Random(seed)
        parent = rng.choice(parents)
        child, mutated = mutate(settings_by_hash[parent], rng, bounds)
        validate_settings(child)
        return child, seed, mutated, parent

    def evaluate_population(population: list[str]) -> list[Fitness]:
        scored: dict[str, Fitness] = {}
        for h in population:
            blocks = cache.get_or_run(evaluator, settings_by_hash[h], cfg.pool, cfg.opponents)
            f = fitness_from_blocks(h, blocks)
            scored[h] = f
            training[h] = f
        return sorted(scored.values(), key=rank_key)

    # Generation 0: incumbent + (population - 1) variants of the incumbent.
    population = [add_lineage(0, incumbent, "incumbent", [], None, [])]
    for slot in range(1, cfg.population):
        child, seed, mutated, parent = make_child(0, slot, [inc_hash])
        population.append(add_lineage(0, child, "initial_variant", [parent], seed, mutated))
    generations.append(population)
    ranked = evaluate_population(population)

    for gen in range(1, cfg.generations):
        top = [f["profile_hash"] for f in ranked[:TOP_PARENTS]]
        best = top[0]
        population = [add_lineage(gen, incumbent, "retained_incumbent", [inc_hash], None, [])]
        population.append(add_lineage(gen, settings_by_hash[best], "retained_best", [best], None, []))
        for slot in range(2, cfg.population):
            child, seed, mutated, parent = make_child(gen, slot, top)
            population.append(add_lineage(gen, child, "mutation", [parent], seed, mutated))
        generations.append(population)
        ranked = evaluate_population(population)

    return {
        "lineage": lineage,
        "generations": generations,
        "training_fitness": training,
        "unique_evaluations": cache.unique_evaluations,
        "cache_hits": cache.cache_hits,
    }
