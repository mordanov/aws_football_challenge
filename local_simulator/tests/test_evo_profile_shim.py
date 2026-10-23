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


_play_counter = 0


def _play(shim, env, tmp_path, seed=3):
    global _play_counter
    _play_counter += 1
    with TeamRunner(_spec(shim), 0, BUDGET, tmp_path, env=env) as r0, \
         TeamRunner(_spec("entrants/redesign_4/shim.py"), 1, BUDGET, tmp_path) as r1:
        engine = MatchEngine(seed, _Agent(r0), _Agent(r1), JsonMatchReplayStore())
        return engine.run(tmp_path / f"r{_play_counter}.json")["frames"]


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
