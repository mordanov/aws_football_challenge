import pytest

from football_lab.evolution.contracts import EvaluationError
from football_lab.evolution.evaluator import MatchEvaluator
from football_lab.evolution.pools import make_pool


def test_unknown_opponent_raises_evaluation_error(tmp_path):
    ev = MatchEvaluator(work_dir=tmp_path, evaluator_id="t")
    with pytest.raises(EvaluationError):
        ev.evaluate(INC, make_pool("training", 100_000, 1, 1), ["nope_v1"])
