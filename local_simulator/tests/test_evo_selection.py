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
