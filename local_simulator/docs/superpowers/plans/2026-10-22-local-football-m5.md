# Local Football M5: Profile Evolution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a reproducible, auditable bounded parameter search over redesign_4's four tactical
settings against the five frozen primary-track opponents, pick exactly one finalist before
final-test access, and publish a promotion or rejection decision.

**Architecture:** New package `football_lab/evolution/`. Search, selection and promotion logic
depend only on an `Evaluator` protocol (contract first), so they are unit-tested with a fake
evaluator. A real `MatchEvaluator` runs matches through `TeamRunner` + `MatchEngine`. A new
profile-capable shim for redesign_4 injects a candidate profile via `AFC_PROFILE`; the frozen
`redesign_4_v1` shim and all reference directories are not modified.

**Tech Stack:** Python 3.11+, existing `football_lab` modules, `pytest`, `ruff`, `mypy`
(`.venv/bin/...` inside `local_simulator/`).

**Spec:** `docs/superpowers/specs/2026-10-21-local-football-design.md` section 10 and the
Evolution gate in section 12. Roadmap: M5 in `2026-10-21-local-football-roadmap.md`.

## Global Constraints

- All new work in `local_simulator/`. `initial_idea/`, `redesign_1..4/` stay byte-identical
  (checked in Task 14).
- Run all commands from `local_simulator/` with the venv: `.venv/bin/pytest`, `.venv/bin/ruff`,
  `.venv/bin/mypy`.
- Only `pass_margin`, `shot_range`, `press_radius`, `support_width` vary. Bounds come from the
  frozen validator's `BOUNDS` (currently 2.5–4.5, 20–34, 12–30, 10–22); never hard-code copies.
  Every candidate passes the validator (`parse_profile`) before running. `opponents` is always
  `None` (no scouting profile).
- Opponents (`initial_idea_v1`, `redesign_1_v1`, `redesign_2_v1`, `redesign_3_v1`) and the
  incumbent (`redesign_4_v1` default profile) are frozen; their directory hashes are recorded at
  init and re-verified before final-test access and at report time.
- Seed pools (disjoint, checked in code): development `0–9999` (never scored), Stage 1 scored
  `10000–10199` (read from the benchmark manifest), training start `100000`, selection start
  `200000`, final start `300000`. Seed layout inside a pool:
  `fixture_seed(pool.start + opp_index*blocks*2, block, leg)`.
- Candidate and incumbent are always evaluated on identical fixtures (same seeds, same opponent,
  same leg/side assignment: leg 0 profile = team0, leg 1 profile = team1).
- Never tune or select against an opened final pool. A new attempt = a new experiment dir and a
  new final pool start.
- Operational failures (`RunnerError`, crashes, invalid commands counted as failure) abort the
  experiment; no partial evidence is accepted.

## Assumptions (call out to reviewer; resolve before Task 6 if wrong)

1. "Top three profiles" for mutation parents = the three best **distinct** profile hashes of the
   **previous generation's population**, ranked by training fitness.
2. Training fixtures are the same 8 blocks/opponent in every generation (spec: "same fixtures for
   every candidate in that experiment"), so cache hits across generations are exact.
3. Aggregate "mean points" is the unweighted mean of per-opponent mean points (equal block counts
   make this identical to pooling matches).
4. Percentile bootstrap intervals use the same index convention as `bootstrap_ci` in
   `campaign_report.py`.
5. When the previous generation's best equals the incumbent hash, both slots are kept (population
   stays 12; duplicates reuse cached evidence and are counted in the disclosure).
6. The experiment seed is a CLI argument recorded in the manifest; all draws derive from it.

## File Structure

| File | Responsibility |
|---|---|
| `src/football_lab/evolution/__init__.py` | package marker |
| `src/football_lab/evolution/contracts.py` | TypedDicts, `Evaluator` protocol, constants (no behavior) |
| `src/football_lab/evolution/bounds.py` | load frozen `BOUNDS` + validator from the entrant's `agent` package |
| `src/football_lab/evolution/pools.py` | pool specs, seed allocation, disjointness checks |
| `src/football_lab/evolution/mutation.py` | profile hash, seed derivation, mutation, initial variants |
| `src/football_lab/evolution/fitness.py` | fitness from block results, ranking key, evaluation cache |
| `src/football_lab/evolution/search.py` | generation loop, lineage |
| `src/football_lab/evolution/selection.py` | finalist selection + `FinalTestLock` |
| `src/football_lab/evolution/promotion.py` | paired bootstrap + promotion decision |
| `src/football_lab/evolution/integrity.py` | directory hashing for frozen-opponent checks |
| `src/football_lab/evolution/evaluator.py` | real `MatchEvaluator` (subprocess matches) |
| `src/football_lab/evolution/cli.py` | `evolve init|run|report` |
| `entrants/redesign_4/profile_shim.py` | profile-capable shim (new file; `shim.py` untouched) |
| `src/football_lab/team_runner.py` | modify: optional `env` mapping for the shim process |
| `src/football_lab/__main__.py` | modify: dispatch `evolve` |
| `tests/test_evo_*.py` | one test file per module |

---

### Task 1: Define contracts (no behavior)

**Files:**
- Create: `src/football_lab/evolution/__init__.py` (empty)
- Create: `src/football_lab/evolution/contracts.py`

- [ ] **Step 1: Write the contracts**

```python
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
```

- [ ] **Step 2: Type-check**

Run: `.venv/bin/mypy`
Expected: `Success: no issues found`

- [ ] **Step 3: Commit**

```bash
git add src/football_lab/evolution
git commit -m "feat(M5): define evolution contracts"
```

---

### Task 2: Bounds from the frozen validator

**Files:**
- Create: `src/football_lab/evolution/bounds.py`
- Test: `tests/test_evo_bounds.py`

The entrant's `agent` package uses relative imports, so load it under a private package name.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from football_lab.evolution.bounds import load_bounds, validate_settings
from football_lab.evolution.contracts import PARAM_NAMES


def test_bounds_cover_exactly_the_four_parameters():
    bounds = load_bounds()
    assert set(bounds) == set(PARAM_NAMES)
    for lo, hi in bounds.values():
        assert lo < hi


def test_validate_accepts_defaults_and_rejects_out_of_range():
    ok = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}
    validate_settings(ok)
    with pytest.raises(ValueError):
        validate_settings({**ok, "shot_range": 99.0})
    with pytest.raises(ValueError):
        validate_settings({**ok, "extra": 1.0})
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest tests/test_evo_bounds.py -v`
Expected: FAIL (`ModuleNotFoundError: football_lab.evolution.bounds`)

- [ ] **Step 3: Implement**

```python
"""Bounds and validation taken from redesign_4's frozen profile validator."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from .contracts import Bounds, Settings

AGENT_DIR = Path(__file__).resolve().parents[3] / "entrants" / "redesign_4" / "agent"
_PKG = "_redesign4_agent"


def _load() -> ModuleType:
    if f"{_PKG}.profile" in sys.modules:
        return sys.modules[f"{_PKG}.profile"]
    spec = importlib.util.spec_from_file_location(
        _PKG, AGENT_DIR / "__init__.py", submodule_search_locations=[str(AGENT_DIR)]
    )
    assert spec is not None and spec.loader is not None
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[_PKG] = pkg
    spec.loader.exec_module(pkg)
    return importlib.import_module(f"{_PKG}.profile")


def load_bounds() -> Bounds:
    return {k: (float(lo), float(hi)) for k, (lo, hi) in _load().BOUNDS.items()}


def validate_settings(settings: Settings) -> None:
    """Raise ValueError unless the frozen validator accepts a profile with these settings."""
    _load().parse_profile({"version": "candidate", "settings": dict(settings), "opponents": None})
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/pytest tests/test_evo_bounds.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/bounds.py tests/test_evo_bounds.py
git commit -m "feat(M5): load bounds and validator from frozen redesign_4"
```

---

### Task 3: Seed pools and disjointness

**Files:**
- Create: `src/football_lab/evolution/pools.py`
- Test: `tests/test_evo_pools.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from football_lab.campaign import fixture_seed
from football_lab.evolution.pools import (
    PoolOverlap,
    assert_disjoint,
    make_pool,
    pool_seed,
    stage1_range,
)


def test_pool_size_and_seed_layout():
    p = make_pool("training", start=100_000, blocks=8, n_opponents=5)
    assert p["size"] == 80
    assert pool_seed(p, opp_index=0, block=0, leg=0) == 100_000
    assert pool_seed(p, opp_index=1, block=0, leg=0) == 100_000 + 16
    assert pool_seed(p, opp_index=4, block=7, leg=1) == 100_000 + 79
    assert pool_seed(p, 2, 3, 1) == fixture_seed(100_000 + 2 * 16, 3, 1)


def test_all_seeds_unique_within_pool():
    p = make_pool("selection", 200_000, 20, 5)
    seeds = {pool_seed(p, o, b, l) for o in range(5) for b in range(20) for l in range(2)}
    assert len(seeds) == p["size"]


def test_overlap_detected_between_pools_and_reserved_ranges():
    a = make_pool("training", 100_000, 8, 5)
    b = make_pool("selection", 100_050, 20, 5)
    with pytest.raises(PoolOverlap):
        assert_disjoint([a, b], reserved=[])
    c = make_pool("final", 10_100, 100, 5)
    with pytest.raises(PoolOverlap):
        assert_disjoint([c], reserved=[range(10_000, 10_200)])
    assert_disjoint([a], reserved=[range(10_000, 10_200)])


def test_stage1_range_from_manifest_fixtures():
    manifest = {"fixtures": [{"seed": 10_000}, {"seed": 10_199}]}
    assert stage1_range(manifest) == range(10_000, 10_200)
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest tests/test_evo_pools.py -v` → FAIL (module missing)

- [ ] **Step 3: Implement**

```python
"""Seed pools for Stage 2: allocation and disjointness checks."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..campaign import fixture_seed
from .contracts import PoolName, PoolSpec


class PoolOverlap(Exception):
    pass


def make_pool(name: PoolName, start: int, blocks: int, n_opponents: int) -> PoolSpec:
    return {
        "name": name,
        "start": start,
        "blocks": blocks,
        "n_opponents": n_opponents,
        "size": n_opponents * blocks * 2,
    }


def pool_range(pool: PoolSpec) -> range:
    return range(pool["start"], pool["start"] + pool["size"])


def pool_seed(pool: PoolSpec, opp_index: int, block: int, leg: int) -> int:
    assert 0 <= opp_index < pool["n_opponents"] and 0 <= block < pool["blocks"]
    return fixture_seed(pool["start"] + opp_index * pool["blocks"] * 2, block, leg)


def stage1_range(manifest: Mapping[str, Any]) -> range:
    seeds = [f["seed"] for f in manifest["fixtures"]]
    return range(min(seeds), max(seeds) + 1)


def _overlaps(a: range, b: range) -> bool:
    return a.start < b.stop and b.start < a.stop


def assert_disjoint(pools: Sequence[PoolSpec], reserved: Sequence[range]) -> None:
    ranges = [(p["name"], pool_range(p)) for p in pools]
    for i, (na, ra) in enumerate(ranges):
        for nb, rb in ranges[i + 1:]:
            if _overlaps(ra, rb):
                raise PoolOverlap(f"pools {na} and {nb} overlap")
        for rr in reserved:
            if _overlaps(ra, rr):
                raise PoolOverlap(f"pool {na} overlaps reserved range {rr}")
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/pytest tests/test_evo_pools.py -v` → 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/pools.py tests/test_evo_pools.py
git commit -m "feat(M5): add seed pools with disjointness checks"
```

---

### Task 4: Profile hash and mutation

**Files:**
- Create: `src/football_lab/evolution/mutation.py`
- Test: `tests/test_evo_mutation.py`

- [ ] **Step 1: Write the failing test**

```python
import random

from football_lab.evolution.bounds import load_bounds, validate_settings
from football_lab.evolution.mutation import derive_seed, mutate, profile_hash

INC = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}


def test_hash_is_stable_and_order_independent():
    shuffled = dict(reversed(list(INC.items())))
    assert profile_hash(INC) == profile_hash(shuffled)
    assert profile_hash(INC) != profile_hash({**INC, "pass_margin": 3.1})


def test_derive_seed_deterministic_and_distinct():
    assert derive_seed(7, 0, 1) == derive_seed(7, 0, 1)
    assert derive_seed(7, 0, 1) != derive_seed(7, 0, 2)
    assert derive_seed(7, 0, 1) != derive_seed(8, 0, 1)


def test_mutation_valid_reproducible_and_changes_at_least_one_param():
    bounds = load_bounds()
    for s in range(200):
        child, mutated = mutate(INC, random.Random(s), bounds)
        validate_settings(child)
        assert len(mutated) >= 1
        for k in bounds:
            assert (child[k] != INC[k]) <= (k in mutated)
        again, again_mut = mutate(INC, random.Random(s), bounds)
        assert (child, mutated) == (again, again_mut)


def test_mutation_clamps_at_bounds():
    bounds = load_bounds()
    edge = {k: hi for k, (_, hi) in bounds.items()}
    for s in range(100):
        child, _ = mutate(edge, random.Random(s), bounds)
        assert all(child[k] <= bounds[k][1] for k in bounds)
```

- [ ] **Step 2: Run, expect failure** — `.venv/bin/pytest tests/test_evo_mutation.py -v` → FAIL

- [ ] **Step 3: Implement**

```python
"""Profile identity and seeded Gaussian mutation (spec section 10)."""
from __future__ import annotations

import hashlib
import json
import random

from .contracts import PARAM_NAMES, Bounds, Settings

_DECIMALS = 6
_SIGMA_FRACTION = 0.10
_SELECT_PROB = 0.5


def profile_hash(settings: Settings) -> str:
    canonical = json.dumps(
        {k: settings[k] for k in PARAM_NAMES}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def derive_seed(experiment_seed: int, generation: int, slot: int) -> int:
    digest = hashlib.sha256(f"{experiment_seed}:{generation}:{slot}".encode()).hexdigest()
    return int(digest[:16], 16)


def mutate(parent: Settings, rng: random.Random, bounds: Bounds) -> tuple[Settings, list[str]]:
    """Select each parameter w.p. 0.5 (at least one), perturb by N(0, 10% of range), clamp."""
    chosen = [n for n in PARAM_NAMES if rng.random() < _SELECT_PROB]
    if not chosen:
        chosen = [rng.choice(PARAM_NAMES)]
    child = {k: parent[k] for k in PARAM_NAMES}
    for name in chosen:
        lo, hi = bounds[name]
        value = parent[name] + rng.gauss(0.0, _SIGMA_FRACTION * (hi - lo))
        child[name] = round(min(hi, max(lo, value)), _DECIMALS)
    return child, chosen
```

Note: a selected parameter can end equal to the parent (e.g. clamped at the bound); the test
asserts only "changed ⇒ selected".

- [ ] **Step 4: Run, expect pass** — 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/mutation.py tests/test_evo_mutation.py
git commit -m "feat(M5): add profile hashing and seeded mutation"
```

---

### Task 5: Fitness, ranking and exact-input cache

**Files:**
- Create: `src/football_lab/evolution/fitness.py`
- Test: `tests/test_evo_fitness.py`

- [ ] **Step 1: Write the failing test**

```python
from football_lab.evolution.fitness import EvaluationCache, fitness_from_blocks, rank_key
from football_lab.evolution.pools import make_pool

POOL = make_pool("training", 100_000, 2, 2)
OPPS = ["a", "b"]


def _blocks(points_gd):
    return [
        {"opponent": o, "block": b, "points": p, "goal_diff": g, "legs": 2}
        for (o, b), (p, g) in zip([(o, b) for o in OPPS for b in range(2)], points_gd)
    ]


def test_fitness_per_match():
    f = fitness_from_blocks("h", _blocks([(6, 2), (0, -2), (2, 0), (2, 0)]))
    assert f["n_matches"] == 8
    assert f["mean_points"] == 10 / 8
    assert f["mean_goal_diff"] == 0.0


def test_rank_key_points_then_gd_then_hash():
    hi = {"profile_hash": "b", "mean_points": 1.5, "mean_goal_diff": 0.0, "n_matches": 8}
    gd = {"profile_hash": "c", "mean_points": 1.5, "mean_goal_diff": 0.5, "n_matches": 8}
    tie = {"profile_hash": "a", "mean_points": 1.5, "mean_goal_diff": 0.5, "n_matches": 8}
    assert sorted([hi, gd, tie], key=rank_key) == [tie, gd, hi]


class CountingEvaluator:
    evaluator_id = "fake-v1"

    def __init__(self):
        self.calls = 0

    def evaluate(self, settings, pool, opponents):
        self.calls += 1
        return _blocks([(3, 1)] * 4)


def test_cache_reuses_identical_inputs_only(tmp_path):
    ev, cache = CountingEvaluator(), EvaluationCache(tmp_path / "evals.jsonl")
    s = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}
    cache.get_or_run(ev, s, POOL, OPPS)
    cache.get_or_run(ev, s, POOL, OPPS)
    assert (ev.calls, cache.unique_evaluations, cache.cache_hits) == (1, 1, 1)
    cache.get_or_run(ev, s, make_pool("training", 100_001, 2, 2), OPPS)
    assert ev.calls == 2  # different pool start = different input
    resumed = EvaluationCache(tmp_path / "evals.jsonl")
    resumed.get_or_run(ev, s, POOL, OPPS)
    assert ev.calls == 2  # persisted entry reused after "restart"
```

- [ ] **Step 2: Run, expect failure** — FAIL (module missing)

- [ ] **Step 3: Implement**

```python
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
    """Best first: mean points, then goal difference, then stable hash (a search rule)."""
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
```

- [ ] **Step 4: Run, expect pass** — 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/fitness.py tests/test_evo_fitness.py
git commit -m "feat(M5): add fitness ranking and exact-input evaluation cache"
```

---

### Task 6: Generation loop with lineage

**Files:**
- Create: `src/football_lab/evolution/search.py`
- Test: `tests/test_evo_search.py`

- [ ] **Step 1: Write the failing test** (fake evaluator: fitness = closeness to a hidden optimum,
  so the loop can make measurable progress without any matches)

```python
from football_lab.evolution.bounds import load_bounds
from football_lab.evolution.fitness import EvaluationCache
from football_lab.evolution.mutation import profile_hash
from football_lab.evolution.pools import make_pool
from football_lab.evolution.search import SearchConfig, run_search

INC = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}
OPPS = ["a", "b"]
POOL = make_pool("training", 100_000, 2, 2)


class QuadraticEvaluator:
    evaluator_id = "quad-v1"

    def evaluate(self, settings, pool, opponents):
        score = -sum((settings[k] - INC[k] - 1.0) ** 2 for k in settings)
        return [
            {"opponent": o, "block": b, "points": score, "goal_diff": 0, "legs": 2}
            for o in opponents
            for b in range(pool["blocks"])
        ]


def _run(seed=1):
    cfg = SearchConfig(population=12, generations=10, experiment_seed=seed, pool=POOL, opponents=OPPS)
    cache = EvaluationCache()
    return run_search(cfg, INC, QuadraticEvaluator(), cache, load_bounds()), cache


def test_population_shape_and_incumbent_retained_every_generation():
    result, _ = _run()
    inc = profile_hash(INC)
    assert len(result["generations"]) == 10
    assert all(len(g) == 12 and g[0] == inc for g in result["generations"])


def test_lineage_records_parents_seed_and_origin():
    result, _ = _run()
    by_origin = {e["origin"] for e in result["lineage"]}
    assert {"incumbent", "initial_variant", "retained_best", "mutation"} <= by_origin
    gen0 = [e for e in result["lineage"] if e["generation"] == 0]
    assert sum(e["origin"] == "initial_variant" for e in gen0) == 11
    for e in result["lineage"]:
        if e["origin"] in ("initial_variant", "mutation"):
            assert e["parents"] and e["mutation_seed"] is not None and e["mutated_params"]


def test_reproducible_and_all_candidates_in_bounds():
    a, _ = _run(seed=5)
    b, _ = _run(seed=5)
    assert a == b
    bounds = load_bounds()
    for e in a["lineage"]:
        assert all(bounds[k][0] <= v <= bounds[k][1] for k, v in e["settings"].items())


def test_cache_disclosure_counts_unique_evaluations():
    result, cache = _run()
    assert result["unique_evaluations"] == cache.unique_evaluations
    assert result["unique_evaluations"] + result["cache_hits"] == 10 * 12
    assert len(result["training_fitness"]) == result["unique_evaluations"]
```

- [ ] **Step 2: Run, expect failure** — FAIL (module missing)

- [ ] **Step 3: Implement**

```python
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
        return sorted(scored.values(), key=rank_key)  # distinct hashes, best first

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
```

Note: the generation-0 entry is origin `"incumbent"`; later generations use
`"retained_incumbent"` (test asserts both origin sets).

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/pytest tests/test_evo_search.py -v` → 4 passed.
If `test_cache_disclosure_counts_unique_evaluations` fails because `training_fitness` has fewer
entries than unique evaluations, the cause is cache-key vs hash mismatch; fix the code, not the
test.

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/search.py tests/test_evo_search.py
git commit -m "feat(M5): add generation loop with lineage and cache disclosure"
```

---

### Task 7: Finalist selection and final-test lock

**Files:**
- Create: `src/football_lab/evolution/selection.py`
- Test: `tests/test_evo_selection.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from football_lab.evolution.fitness import EvaluationCache
from football_lab.evolution.pools import make_pool
from football_lab.evolution.selection import (
    FinalTestLock,
    FinalTestLockError,
    select_finalist,
)

POOL = make_pool("selection", 200_000, 2, 1)
OPPS = ["a"]
S = {n: {"pass_margin": 3.0 + i / 100, "shot_range": 29.0, "press_radius": 22.0,
         "support_width": 15.0} for i, n in enumerate("xyzwv")}


def _fit(h, p, gd=0.0):
    return {"profile_hash": h, "mean_points": p, "mean_goal_diff": gd, "n_matches": 4}


class TableEvaluator:
    evaluator_id = "table-v1"

    def __init__(self, points_by_pm):
        self.points_by_pm = points_by_pm

    def evaluate(self, settings, pool, opponents):
        pts = self.points_by_pm[settings["pass_margin"]]
        return [{"opponent": "a", "block": b, "points": pts, "goal_diff": 0, "legs": 2}
                for b in range(pool["blocks"])]


def _setup(points):
    from football_lab.evolution.mutation import profile_hash
    hashes = {n: profile_hash(s) for n, s in S.items()}
    training = {hashes[n]: _fit(hashes[n], 2.0 - i * 0.1) for i, n in enumerate("xyzwv")}
    return hashes, training, TableEvaluator({S[n]["pass_margin"]: p for n, p in points.items()})


def test_takes_top_three_non_incumbent_and_picks_best_on_selection(tmp_path):
    hashes, training, ev = _setup({"x": 3.0, "y": 4.0, "z": 3.5, "w": 6.0, "v": 6.0})
    inc = hashes["x"]
    result = select_finalist(training, {h: S[n] for n, h in hashes.items()}, inc, ev,
                             EvaluationCache(), POOL, OPPS)
    assert result["candidates"] == [hashes["y"], hashes["z"], hashes["w"]]
    assert result["finalist"] == hashes["y"]  # w, v never evaluated


def test_incumbent_wins_exact_tie_and_no_candidate_manufactured():
    hashes, training, ev = _setup({"x": 4.0, "y": 4.0, "z": 3.0, "w": 3.0, "v": 3.0})
    result = select_finalist(training, {h: S[n] for n, h in hashes.items()}, hashes["x"], ev,
                             EvaluationCache(), POOL, OPPS)
    assert result["finalist"] is None


def test_lock_freezes_once_and_final_opens_once(tmp_path):
    lock = FinalTestLock(tmp_path / "final.lock.json")
    with pytest.raises(FinalTestLockError):
        lock.begin_final("abc")  # not frozen yet
    lock.freeze("abc", "inc")
    with pytest.raises(FinalTestLockError):
        lock.freeze("other", "inc")  # cannot change finalist
    with pytest.raises(FinalTestLockError):
        lock.begin_final("other")
    lock.begin_final("abc")
    with pytest.raises(FinalTestLockError):
        lock.begin_final("abc")  # final pool opened at most once
    assert FinalTestLock(tmp_path / "final.lock.json").is_opened()
```

- [ ] **Step 2: Run, expect failure** — FAIL

- [ ] **Step 3: Implement**

```python
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
    for h in [incumbent_hash, *challengers]:
        blocks = cache.get_or_run(evaluator, settings_by_hash[h], pool, opponents)
        fitness[h] = fitness_from_blocks(h, blocks)

    def key(f: Fitness) -> tuple[float, float, int, str]:
        # exact ties favor the incumbent, then stable hash
        return (-f["mean_points"], -f["mean_goal_diff"],
                0 if f["profile_hash"] == incumbent_hash else 1, f["profile_hash"])

    best = min(fitness.values(), key=key)["profile_hash"]
    return {
        "candidates": challengers,
        "fitness": fitness,
        "finalist": None if best == incumbent_hash else best,
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
```

- [ ] **Step 4: Run, expect pass** — 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/selection.py tests/test_evo_selection.py
git commit -m "feat(M5): add finalist selection and one-shot final-test lock"
```

---

### Task 8: Paired bootstrap and promotion decision

**Files:**
- Create: `src/football_lab/evolution/promotion.py`
- Test: `tests/test_evo_promotion.py`

Statistic: per (opponent, block) difference `(finalist_points - incumbent_points) / 2` points per
match, both legs inside the unit. Resample blocks within each opponent (stratified); aggregate =
mean of the per-opponent resampled means. 95% for aggregate; 99% for each opponent (Bonferroni:
0.05/5).

- [ ] **Step 1: Write the failing test** (known datasets, including a guard that legs are not
  independent samples)

```python
from football_lab.evolution.promotion import decide_promotion, paired_block_diffs

OPPS = ["a", "b", "c", "d", "e"]


def _blocks(points_by_opp, n=20):
    return [{"opponent": o, "block": b, "points": points_by_opp[o], "goal_diff": 0, "legs": 2}
            for o in OPPS for b in range(n)]


def test_paired_diffs_per_match_and_grouped_by_block():
    diffs = paired_block_diffs(_blocks({o: 4 for o in OPPS}, 3), _blocks({o: 2 for o in OPPS}, 3))
    assert set(diffs) == set(OPPS)
    assert diffs["a"] == [1.0, 1.0, 1.0]  # (4-2)/2 legs; one entry per block, not per leg


def test_clear_improvement_promotes():
    d = decide_promotion("fin", _blocks({o: 4 for o in OPPS}), _blocks({o: 2 for o in OPPS}),
                         bootstrap_seed=1, n_resamples=500)
    assert d["promoted"] and d["reasons"] == []
    assert d["aggregate"]["lo"] == 1.0 and d["aggregate"]["confidence"] == 0.95
    assert all(v["confidence"] == 0.99 for v in d["per_opponent"].values())


def test_no_change_rejected_for_aggregate_not_positive():
    same = _blocks({o: 2 for o in OPPS})
    d = decide_promotion("fin", same, same, bootstrap_seed=1, n_resamples=500)
    assert not d["promoted"]
    assert any("aggregate" in r for r in d["reasons"])


def test_one_opponent_regression_beyond_tolerance_rejects_despite_higher_mean():
    cand = _blocks({"a": 0, "b": 6, "c": 6, "d": 6, "e": 6})
    ctrl = _blocks({o: 2 for o in OPPS})
    d = decide_promotion("fin", cand, ctrl, bootstrap_seed=1, n_resamples=500)
    assert d["aggregate"]["lo"] > 0
    assert not d["promoted"]
    assert any("opponent a" in r for r in d["reasons"])


def test_incomplete_evidence_rejected():
    cand, ctrl = _blocks({o: 4 for o in OPPS}), _blocks({o: 2 for o in OPPS})
    d = decide_promotion("fin", cand[:-1], ctrl[:-1], bootstrap_seed=1, n_resamples=500,
                         expected_blocks=20)
    assert not d["promoted"] and any("incomplete" in r for r in d["reasons"])
```

- [ ] **Step 2: Run, expect failure** — FAIL

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/pytest tests/test_evo_promotion.py -v` → 5 passed.
(In the regression test, opponent `a` diffs are all −1.0 so its lower bound is −1.0 < −0.10, and
the aggregate mean is (−1 + 4·2)/5 = 1.4 > 0.)

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/promotion.py tests/test_evo_promotion.py
git commit -m "feat(M5): add paired bootstrap and promotion criteria"
```

---

### Task 9: Profile-capable shim and `TeamRunner` env

**Files:**
- Create: `entrants/redesign_4/profile_shim.py`
- Modify: `src/football_lab/team_runner.py` (constructor + `Popen` call)
- Modify: `entrants/redesign_4/FIX_LEDGER.md` (append patch entry)
- Test: `tests/test_evo_profile_shim.py`

`shim.py` and `redesign_4_v1` stay untouched. `Runtime(profile=...)` already accepts a profile
(`redesign_4/agent/runtime.py:21`).

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path

from football_lab.engine import MatchEngine
from football_lab.entrant_contracts import EntrantSpec
from football_lab.match_replay import JsonMatchReplayStore
from football_lab.team_runner import TeamRunner

ROOT = Path(__file__).resolve().parent.parent
BUDGET = {"startup_timeout_s": 15.0, "request_timeout_s": 10.0, "output_size_limit": 65536}


def _spec(shim: str) -> EntrantSpec:
    m = json.loads((ROOT / "entrants/redesign_4/entrant.json").read_text())
    return {"id": m["id"], "source_name": "redesign_4", "topology": "per_player", "shim": shim,
            "entrant_hash": m["entrant_hash"], "rules_version": "v1", "track": "patched"}


class _Agent:
    def __init__(self, runner):
        self.runner = runner

    def decide(self, obs):  # same adaptor as cli_campaign._TeamRunnerEngineAgent
        from football_lab.cli_campaign import _TeamRunnerEngineAgent
        return _TeamRunnerEngineAgent(self.runner).decide(obs)


def _play(shim, env, tmp_path, seed=3):
    with TeamRunner(_spec(shim), 0, BUDGET, tmp_path, env=env) as r0, \
         TeamRunner(_spec("entrants/redesign_4/shim.py"), 1, BUDGET, tmp_path) as r1:
        engine = MatchEngine(seed, _Agent(r0), _Agent(r1), JsonMatchReplayStore())
        return engine.run(tmp_path / "r.json")["frames"]


def test_default_profile_equals_frozen_shim(tmp_path):
    frozen = _play("entrants/redesign_4/shim.py", None, tmp_path)
    profiled = _play("entrants/redesign_4/profile_shim.py", None, tmp_path)
    assert frozen == profiled


def test_profile_file_changes_behavior_and_bad_profile_fails(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"version": "t", "opponents": None, "settings": {
        "pass_margin": 4.5, "shot_range": 20.0, "press_radius": 12.0, "support_width": 22.0}}))
    default = _play("entrants/redesign_4/profile_shim.py", None, tmp_path)
    changed = _play("entrants/redesign_4/profile_shim.py", {"AFC_PROFILE": str(p)}, tmp_path)
    assert default != changed
    p.write_text("{}")
    import pytest
    from football_lab.team_runner import RunnerError
    with pytest.raises(RunnerError):
        _play("entrants/redesign_4/profile_shim.py", {"AFC_PROFILE": str(p)}, tmp_path)
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/pytest tests/test_evo_profile_shim.py -v`
Expected: FAIL (`TeamRunner.__init__() got an unexpected keyword argument 'env'`)

- [ ] **Step 3: Modify `TeamRunner`**

In `__init__` add `env: Mapping[str, str] | None = None` (import `Mapping` from `typing`), store
`self._env = dict(env) if env else None`. In `start()` change the `Popen` call to pass
`env={**os.environ, **self._env} if self._env else None` (add `import os`).

- [ ] **Step 4: Create `entrants/redesign_4/profile_shim.py`**

Copy `shim.py` exactly and change only the Runtime construction:

```python
#!/usr/bin/env python3
"""Per-player JSONL shim for redesign_4 with optional profile injection (M5).

Identical to shim.py except the Profile is loaded from $AFC_PROFILE (validated by the frozen
redesign_4 validator). Without AFC_PROFILE behavior equals shim.py (the default Profile()).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from agent.model import Profile  # noqa: E402
from agent.profile import load_profile  # noqa: E402
from agent.runtime import Runtime  # noqa: E402

_path = os.getenv("AFC_PROFILE")
_profile = load_profile(_path) if _path else Profile()


def _new_runtimes():
    return {pid: Runtime(_profile, emit=lambda _: None) for pid in range(5)}


_runtimes = _new_runtimes()

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    msg = json.loads(raw)
    if msg.get("_command") == "stop":
        for rt in _runtimes.values():
            rt.close(timeout=1.0)
        break
    if msg.get("_command") == "reset":
        for rt in _runtimes.values():
            rt.close(timeout=1.0)
        _runtimes = _new_runtimes()
        print(json.dumps({"ok": True}), flush=True)
        continue
    obs = msg["obs"]
    all_cmds = []
    for pid in range(5):
        player_obs = dict(obs)
        player_obs["myPlayers"] = [pid]
        result = _runtimes[pid].handle(player_obs)
        cmds = json.loads(result) if isinstance(result, str) else result
        all_cmds.extend(cmds)
    print(json.dumps(all_cmds), flush=True)
```

Before relying on this, view `redesign_4/agent/runtime.py:15-30` to confirm the positional
parameter order of `Runtime.__init__` (profile must be accepted as the first positional or switch
to `profile=_profile`). A bad `AFC_PROFILE` makes `load_profile` raise at import, so the shim exits
and `TeamRunner.start()` raises `RunnerError` (startup reset not acknowledged) — this is the
tested failure path.

- [ ] **Step 5: Run, expect pass**

Run: `.venv/bin/pytest tests/test_evo_profile_shim.py tests/test_team_runner.py -v` → all pass.
If `test_default_profile_equals_frozen_shim` fails, the profile shim is not behavior-identical;
fix the shim (do not weaken the test). If `default != changed` fails, the settings do not affect
play on this seed; pick another seed or more extreme settings and record the choice.

- [ ] **Step 6: Append to `entrants/redesign_4/FIX_LEDGER.md`**

```markdown
## Addition profile_shim — profile injection for M5 (not a behavior patch)

**Classification:** Harness addition (new file; `shim.py` and `redesign_4_v1` unchanged)
**Changed behavior:** none by default; `$AFC_PROFILE` loads a profile validated by the frozen
`agent/profile.py`. Used only by Stage 2 evaluation.
**Regression evidence:** `tests/test_evo_profile_shim.py::test_default_profile_equals_frozen_shim`
**Revision:** v1-2026-10-22
```

- [ ] **Step 7: Commit**

```bash
git add entrants/redesign_4/profile_shim.py entrants/redesign_4/FIX_LEDGER.md \
        src/football_lab/team_runner.py tests/test_evo_profile_shim.py
git commit -m "feat(M5): add profile-capable redesign_4 shim and TeamRunner env"
```

---

### Task 10: Frozen-opponent integrity hashes

**Files:**
- Create: `src/football_lab/evolution/integrity.py`
- Test: `tests/test_evo_integrity.py`

- [ ] **Step 1: Write the failing test**

```python
from football_lab.evolution.integrity import hash_tree, verify_unchanged


def test_hash_changes_on_content_or_name_and_ignores_pycache(tmp_path):
    (tmp_path / "a.py").write_text("x")
    base = hash_tree(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"junk")
    assert hash_tree(tmp_path) == base
    (tmp_path / "a.py").write_text("y")
    assert hash_tree(tmp_path) != base


def test_verify_unchanged_reports_every_difference(tmp_path):
    d = tmp_path / "opp"
    d.mkdir()
    (d / "a.py").write_text("x")
    recorded = {"opp": hash_tree(d)}
    assert verify_unchanged({"opp": d}, recorded) == []
    (d / "a.py").write_text("tampered")
    assert verify_unchanged({"opp": d}, recorded) == ["opp changed since experiment init"]
```

- [ ] **Step 2: Run, expect failure** — FAIL

- [ ] **Step 3: Implement**

```python
"""Directory hashing to prove frozen opponents/incumbent are unchanged."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping


def hash_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            h.update(str(p.relative_to(root)).encode())
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def verify_unchanged(dirs: Mapping[str, Path], recorded: Mapping[str, str]) -> list[str]:
    return [
        f"{name} changed since experiment init"
        for name, d in dirs.items()
        if hash_tree(d) != recorded[name]
    ]
```

- [ ] **Step 4: Run, expect pass** — 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/integrity.py tests/test_evo_integrity.py
git commit -m "feat(M5): add frozen-directory integrity hashing"
```

---

### Task 11: Real `MatchEvaluator`

**Files:**
- Create: `src/football_lab/evolution/evaluator.py`
- Test: `tests/test_evo_evaluator.py`

Reuse `cli_campaign._load_entrant_spec`, `_DEV_BUDGET`, `_TeamRunnerEngineAgent`. Profile team uses
`profile_shim.py` with `AFC_PROFILE`; opponents use their normal patched shim.

- [ ] **Step 1: Write the failing test** (real subprocesses, tiny pool: 1 opponent, 1 block)

```python
import pytest

from football_lab.evolution.contracts import EvaluationError
from football_lab.evolution.evaluator import MatchEvaluator
from football_lab.evolution.pools import make_pool

INC = {"pass_margin": 3.0, "shot_range": 29.0, "press_radius": 22.0, "support_width": 15.0}


def test_evaluates_one_block_deterministically(tmp_path):
    ev = MatchEvaluator(work_dir=tmp_path, evaluator_id="t")
    pool = make_pool("training", 100_000, blocks=1, n_opponents=1)
    a = ev.evaluate(INC, pool, ["initial_idea_v1"])
    b = ev.evaluate(INC, pool, ["initial_idea_v1"])
    assert a == b and len(a) == 1
    assert a[0]["legs"] == 2 and a[0]["points"] in {0, 1, 2, 3, 4, 6}


def test_unknown_opponent_raises_evaluation_error(tmp_path):
    ev = MatchEvaluator(work_dir=tmp_path, evaluator_id="t")
    with pytest.raises(EvaluationError):
        ev.evaluate(INC, make_pool("training", 100_000, 1, 1), ["nope_v1"])
```

- [ ] **Step 2: Run, expect failure** — FAIL (module missing)

- [ ] **Step 3: Implement**

```python
"""Real evaluator: plays profile-vs-opponent blocks through subprocess shims."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from ..cli_campaign import _DEV_BUDGET, _load_entrant_spec, _TeamRunnerEngineAgent
from ..engine import MatchEngine
from ..match_replay import JsonMatchReplayStore
from ..team_runner import RunnerError, TeamRunner
from .contracts import BlockResult, EvaluationError, PoolSpec, Settings
from .mutation import profile_hash
from .pools import pool_seed

PROFILE_SHIM = "entrants/redesign_4/profile_shim.py"
INCUMBENT_ID = "redesign_4_v1"


def _points(own: int, other: int) -> int:
    return 3 if own > other else (1 if own == other else 0)


class MatchEvaluator:
    def __init__(self, work_dir: Path, evaluator_id: str, keep_replays: bool = False) -> None:
        self._work = Path(work_dir)
        self.evaluator_id = evaluator_id
        self._keep = keep_replays
        self._store = JsonMatchReplayStore()

    def _profile_file(self, settings: Settings) -> Path:
        path = self._work / "profiles" / f"{profile_hash(settings)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"version": profile_hash(settings)[:12], "settings": settings, "opponents": None}
        ))
        return path

    def _play(self, seed: int, profile_team: int, settings: Settings,
              opp_spec, replay: Path) -> tuple[int, int]:
        own = {**_load_entrant_spec(INCUMBENT_ID), "shim": PROFILE_SHIM}
        env = {"AFC_PROFILE": str(self._profile_file(settings))}
        specs = (own, opp_spec) if profile_team == 0 else (opp_spec, own)
        envs = (env, None) if profile_team == 0 else (None, env)
        with (
            TeamRunner(specs[0], 0, _DEV_BUDGET, self._work, env=envs[0]) as r0,
            TeamRunner(specs[1], 1, _DEV_BUDGET, self._work, env=envs[1]) as r1,
        ):
            engine = MatchEngine(seed, _TeamRunnerEngineAgent(r0), _TeamRunnerEngineAgent(r1),
                                 self._store)
            frames = engine.run(replay)["frames"]
        g0, g1 = frames[-1]["score"]
        return (g0, g1) if profile_team == 0 else (g1, g0)

    def evaluate(self, settings: Settings, pool: PoolSpec,
                 opponents: Sequence[str]) -> list[BlockResult]:
        out: list[BlockResult] = []
        try:
            for oi, opp in enumerate(opponents):
                spec = _load_entrant_spec(opp)
                for block in range(pool["blocks"]):
                    pts, gd = 0, 0
                    for leg in range(2):
                        seed = pool_seed(pool, oi, block, leg)
                        replay = (self._work / "replays" / pool["name"]
                                  / f"{profile_hash(settings)[:12]}__{opp}__b{block:04d}__l{leg}.json"
                                  if self._keep else self._work / "scratch.json")
                        replay.parent.mkdir(parents=True, exist_ok=True)
                        own, other = self._play(seed, leg, settings, spec, replay)
                        pts += _points(own, other)
                        gd += own - other
                    out.append({"opponent": opp, "block": block, "points": float(pts),
                                "goal_diff": gd, "legs": 2})
        except (RunnerError, ValueError, OSError) as exc:
            raise EvaluationError(str(exc)) from exc
        return out
```

(`profile_team == leg`: leg 0 → profile is team0, leg 1 → profile is team1, matching the global
constraint.)

- [ ] **Step 4: Run, expect pass**

Run: `.venv/bin/pytest tests/test_evo_evaluator.py -v` → 2 passed.
If `_load_entrant_spec` raises `ValueError` for the unknown id, it maps to `EvaluationError` as
tested. Note the measured wall time per block here for Task 14.

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/evaluator.py tests/test_evo_evaluator.py
git commit -m "feat(M5): add subprocess-backed MatchEvaluator"
```

---

### Task 12: Experiment CLI (`init`, `run`, `report`)

**Files:**
- Create: `src/football_lab/evolution/cli.py`
- Modify: `src/football_lab/__main__.py` (add `evolve` dispatch next to `campaign`)
- Test: `tests/test_evo_cli.py`

**Experiment directory layout** (`--dir`): `manifest.json` (immutable after init),
`evaluations.jsonl` (cache), `search.json`, `selection.json`, `final.lock.json`,
`final.json` (decision), `report.md`.

**`init` writes `manifest.json`:** experiment id, `experiment_seed`, rules_version `v1`,
incumbent settings + hash, bounds copied from the validator, opponent ids + `hash_tree` of each
`entrants/<source_name>/` dir and of `entrants/redesign_4/`, pools (training 8 blocks,
selection 20, final 100, development, each `make_pool`), Stage 1 reserved range from
`--stage1-manifest`, bootstrap seed + 10,000 resamples, `population=12`, `generations=10`.
It calls `assert_disjoint([development, training, selection, final], reserved=[0..9999 is the
development pool itself, stage1_range])`; development pool = `make_pool("development", 0, 1000, 5)`
(seeds 0–9999) and must not overlap the others.

**`run` flow:** load manifest → `verify_unchanged` (abort if any) → `run_search` → save
`search.json` → `select_finalist` on the selection pool → save `selection.json` → if
`finalist is None`: write `final.json` = `{"promoted": false, "reasons": ["incumbent retained at selection"]}`
and stop (final pool never opened) → else `lock.freeze` → `verify_unchanged` again →
`lock.begin_final` → evaluate finalist and incumbent on the final pool (`keep_replays=True`) →
`decide_promotion(..., expected_blocks=100, extra_failures=verify_unchanged(...))` → save
`final.json`. `run` is resumable: the evaluation cache replays finished evaluations; a frozen but
unopened lock resumes at the final step; an opened lock refuses to re-run (new experiment
required).

**`report`:** renders `report.md` from the saved JSON files: bounds, pools and seed ranges,
opponent hashes, population/generation table, unique evaluations vs cache hits, lineage count,
top training fitness, selection table, finalist, final intervals, decision and reasons, and an
explicit statement "bounded profile search; no model weights were trained or modified."

- [ ] **Step 1: Write the failing test** (fast: monkeypatch the evaluator with a fake)

```python
import json

from football_lab.evolution import cli

STAGE1 = {"fixtures": [{"seed": 10_000}, {"seed": 10_199}]}


def _init(tmp_path, monkeypatch):
    s1 = tmp_path / "stage1.json"
    s1.write_text(json.dumps(STAGE1))
    d = tmp_path / "exp"
    assert cli.main(["init", "--dir", str(d), "--stage1-manifest", str(s1),
                     "--experiment-seed", "7"]) == 0
    return d


def test_init_records_bounds_pools_hashes_and_disjointness(tmp_path, monkeypatch):
    m = json.loads((_init(tmp_path, monkeypatch) / "manifest.json").read_text())
    assert set(m["bounds"]) == {"pass_margin", "shot_range", "press_radius", "support_width"}
    assert [m["pools"][k]["blocks"] for k in ("training", "selection", "final")] == [8, 20, 100]
    assert len(m["frozen_hashes"]) == 5
    assert m["population"] == 12 and m["generations"] == 10


def test_init_refuses_overlapping_stage1_range(tmp_path):
    s1 = tmp_path / "s1.json"
    s1.write_text(json.dumps({"fixtures": [{"seed": 100_000}, {"seed": 100_050}]}))
    assert cli.main(["init", "--dir", str(tmp_path / "e"), "--stage1-manifest", str(s1),
                     "--experiment-seed", "1"]) != 0


def test_run_rejects_without_promotion_when_incumbent_best(tmp_path, monkeypatch):
    d = _init(tmp_path, monkeypatch)

    class Flat:
        evaluator_id = "flat"

        def evaluate(self, settings, pool, opponents):
            return [{"opponent": o, "block": b, "points": 2.0, "goal_diff": 0, "legs": 2}
                    for o in opponents for b in range(pool["blocks"])]

    monkeypatch.setattr(cli, "make_evaluator", lambda *a, **k: Flat())
    assert cli.main(["run", "--dir", str(d)]) == 0
    final = json.loads((d / "final.json").read_text())
    assert final["promoted"] is False
    assert not (d / "final.lock.json").exists()  # final pool never opened
    assert cli.main(["report", "--dir", str(d)]) == 0
    assert "no model weights" in (d / "report.md").read_text()
```

- [ ] **Step 2: Run, expect failure** — FAIL (module missing)

- [ ] **Step 3: Implement `cli.py`** following the flow above. Required pieces, all using functions
  already defined in Tasks 2–11 (`load_bounds`, `make_pool`, `assert_disjoint`, `stage1_range`,
  `hash_tree`, `verify_unchanged`, `run_search`, `select_finalist`, `FinalTestLock`,
  `decide_promotion`, `EvaluationCache`, `MatchEvaluator`):
  - `make_evaluator(work_dir, evaluator_id, keep_replays=False)` thin factory (the test patches
    it); `evaluator_id` = `"rules-v1|" + manifest["frozen_hashes"]` joined, so a changed opponent
    or shim invalidates the cache.
  - Constants: `OPPONENTS = ["initial_idea_v1","redesign_1_v1","redesign_2_v1","redesign_3_v1"]`
    plus the incumbent is **not** an opponent. Spec says "all five frozen opponents": the five
    primary entrants include redesign_4 itself, so the five opponents of a *candidate redesign_4*
    are… only four others. **Resolve before coding (see Open Question 1).**
  - Incumbent settings: `Settings()` defaults read from the validator module
    (`pass_margin 3.0, shot_range 29.0, press_radius 22.0, support_width 15.0`) via
    `_load().DEFAULT_SETTINGS` fields, not literals.
  - `init` exits non-zero with a message on `PoolOverlap`; writes the manifest atomically.
  - `main(argv)` returns int; add `evolve` branch in `__main__.py`:

```python
    if len(sys.argv) > 1 and sys.argv[1] == "evolve":
        from .evolution.cli import main as evolve_main
        return evolve_main(sys.argv[2:])
```

- [ ] **Step 4: Run, expect pass** — `.venv/bin/pytest tests/test_evo_cli.py -v` → 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/football_lab/evolution/cli.py src/football_lab/__main__.py tests/test_evo_cli.py
git commit -m "feat(M5): add evolve CLI (init/run/report)"
```

---

### Task 13: Lint, types, full suite

- [ ] **Step 1:** `.venv/bin/ruff check src tests` → fix findings in M5 files only.
- [ ] **Step 2:** `.venv/bin/mypy` → `Success`.
- [ ] **Step 3:** `.venv/bin/pytest -q` → all pass (existing tests unchanged).
- [ ] **Step 4: Commit** `git commit -am "chore(M5): lint and type fixes"` (only if changes).

---

### Task 14: Documentation, preservation and measured smoke run

**Files:**
- Modify: `CONTEXT.md` (add terms: **Profile**, **Lineage**, **Finalist**, **Final-test lock**;
  avoid "model", "training weights")
- Modify: `README.md` (evolve usage)
- Create: `docs/m5-evolution.md` (procedure, assumptions list above as decisions, how to read
  `final.json`)

- [ ] **Step 1: Preservation check**

Run from repo root:
`git --no-pager diff --stat main -- initial_idea redesign_1 redesign_2 redesign_3 redesign_4`
Expected: no output.

- [ ] **Step 2: Smoke run at tiny budget** (not an experiment; throwaway dir under `local/`)

`.venv/bin/python -m football_lab evolve init --dir local/evolve-smoke --stage1-manifest <M4 benchmark manifest path> --experiment-seed 1`
then record wall time of one `MatchEvaluator.evaluate` block from Task 11 and extrapolate the
full budget (≤ 10×12 training profiles × 40 blocks + 4×100 selection blocks + 2×500 final blocks,
less cache hits). Report the measured rate in `docs/m5-evolution.md`; do not promise one.
Locate the M4 benchmark manifest with `ls local/benchmark/`.

- [ ] **Step 3: Delete the smoke dir**, commit docs.

```bash
git add CONTEXT.md README.md docs/m5-evolution.md
git commit -m "docs(M5): document profile evolution procedure"
```

- [ ] **Step 4: The real experiment is a separate, explicitly authorized run** (hours of
  compute). Command: `football-lab evolve init ... && football-lab evolve run --dir <dir> && football-lab evolve report --dir <dir>`.
  Exit gate: `final.json` + `report.md` exist, lineage reproducible by re-running `run` against the
  same dir (cache hits only), preservation check (Step 1) clean.

---

## Open Questions (need user answers before Task 12)

1. **Opponent set:** the spec says candidates face "all five frozen opponents", but the primary
   track has five entrants *including* redesign_4 (the incumbent). Options: (a) the four other
   entrants plus an unmodified frozen `redesign_4_v1` (default profile) as the fifth opponent
   (candidate profile vs frozen default — matches "five" and gives a self-play reference), or
   (b) only the four others (changes pool sizes: 4×… matches and the 5-way Bonferroni becomes 4).
   This plan assumes **(a)** in the pool math (`n_opponents=5`, 99%). Task 12's `OPPONENTS` list
   must include `redesign_4_v1` under (a) — and then the `AFC_PROFILE`-less `shim.py` is the
   opponent, the profile shim the candidate.
2. **Stage 1 benchmark manifest path** (needed for the reserved scored-seed range).
3. **Approve assumptions 1–6** above.

## Self-Review

- **Spec coverage:** bounds/validator reuse (T2), four disjoint pools + Stage 1 exclusion (T3,
  T12), population 12 / ≤10 generations incl. initial / incumbent + best retained / top-3 parents /
  seeded draws (T6), mutation p=0.5 + ≥1 + 10% Gaussian + clamp + validator (T4, T6), stable-hash
  ties (T5, T7), duplicate evidence reuse with disclosure (T5, T6), ≤3 challengers on 20 selection
  blocks, incumbent ties, one finalist before final (T7), 100 paired final blocks, 95%/99%
  intervals, −0.10 margin, complete-evidence and unchanged-opponent criteria (T8, T10, T12),
  rejection recorded without a manufactured candidate (T7, T12), no source modification (T9 new
  file only, T14 preservation), no weight-update claims (T12 report).
- **Placeholders:** Task 12 `cli.py` is specified by flow and required pieces rather than full
  code, and it contains an unresolved decision (Open Question 1). Resolve before executing; then
  expand to full code.
- **Type consistency:** `BlockResult`, `Fitness`, `PoolSpec`, `Evaluator.evaluate(settings, pool,
  opponents)`, `EvaluationCache.get_or_run`, `profile_hash`, `rank_key`, `decide_promotion` names
  and signatures match across Tasks 1–12.
