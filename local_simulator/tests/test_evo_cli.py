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
    assert len(m["frozen_hashes"]) == 1  # one self-play opponent: football_agent_v1
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
