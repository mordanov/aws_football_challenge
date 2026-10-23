from __future__ import annotations

from typing import Any

import pytest

from football_lab.coaches import COACHES, scripted_coach
from football_lab.obs_encoder import encode_observation


def _state(tick: int, score: list[int]) -> Any:
    return {"tick": tick, "play_mode": "OPEN_PLAY", "mode_team": None, "players": [],
            "ball": {"x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "vz": 0.0,
                     "owner_team": None, "owner_player": None, "in_flight": False},
            "score": score}


@pytest.mark.parametrize(
    ("tick", "score", "team", "expected"),
    [
        (0, [0, 1], 0, "press_high"),
        (1600, [0, 1], 0, "go_all_out_attack"),   # r = 0.333
        (1440, [1, 0], 0, "slow_the_tempo"),      # r = 0.40
        (1400, [1, 0], 0, ""),                    # r = 0.417
        (1920, [0, 0], 0, "shoot_on_sight"),      # r = 0.20
        (1900, [0, 0], 0, ""),
        (1600, [0, 1], 1, "slow_the_tempo"),      # team 1 is leading
    ],
)
def test_scripted_coach_table(tick: int, score: list[int], team: int, expected: str) -> None:
    assert scripted_coach(_state(tick, score), team) == expected


def test_registry_and_encoder() -> None:
    assert COACHES["scripted"] is scripted_coach
    state = _state(0, [0, 0])
    assert encode_observation(state, 0, [0], 1)["gameState"]["coachInstruction"] == ""
    obs = encode_observation(state, 0, [0], 1, coach="press_high")
    assert obs["gameState"]["coachInstruction"] == "press_high"
