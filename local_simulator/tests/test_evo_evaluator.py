import pytest

from football_lab.evolution.bounds import load_bounds
from football_lab.evolution.contracts import EvaluationError
from football_lab.evolution.evaluator import MatchEvaluator
from football_lab.evolution.pools import make_pool

# Build a valid incumbent settings dict from the midpoint of each bound
_bounds = load_bounds()
INC = {k: (lo + hi) / 2 for k, (lo, hi) in _bounds.items()}


def test_unknown_opponent_raises_evaluation_error(tmp_path):
    ev = MatchEvaluator(work_dir=tmp_path, evaluator_id="t")
    with pytest.raises(EvaluationError):
        ev.evaluate(INC, make_pool("training", 100_000, 1, 1), ["nope_v1"])
