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
