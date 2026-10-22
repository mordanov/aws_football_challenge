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
