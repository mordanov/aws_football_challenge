"""v1 must stay byte-identical while rules v2 is added."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from football_lab.engine import MatchEngine

GOLDEN_V1_SHA256 = "f3e32b86e04657c38a420c6af9dbb56e6df499e7c1fc3421204d9f00ac3acbc6"


class _ChaserAgent:
    """Carrier shoots, keeper holds the goal line, everyone else presses."""

    def __init__(self, team_id: int) -> None:
        self.team_id = team_id

    def decide(self, obs: dict[str, Any]) -> list[dict[str, Any]]:
        ball = obs["ball"]
        own_goal_x = -50.0 if self.team_id == 0 else 50.0
        out = []
        for pid in range(5):
            if ball["owner_team"] == self.team_id and ball["owner_player"] == pid:
                cmd = ("SHOOT", {"aim_location": "CENTER", "power": 1.0})
            elif pid == 0:
                cmd = ("MOVE_TO", {"target_x": own_goal_x, "target_y": 0.0})
            else:
                cmd = ("PRESS_BALL", {"intensity": 1.0})
            out.append(
                {"team_id": self.team_id, "player_id": pid,
                 "command_type": cmd[0], "parameters": cmd[1]}
            )
        return out


class _NullStore:
    def save(self, replay: Any, path: Path) -> None:
        return None


def replay_digest(**engine_kwargs: Any) -> str:
    engine = MatchEngine(7, _ChaserAgent(0), _ChaserAgent(1), _NullStore(), **engine_kwargs)
    replay = engine.run(Path("unused.json"))
    return hashlib.sha256(json.dumps(replay, sort_keys=True).encode()).hexdigest()


def test_v1_replay_is_byte_identical() -> None:
    assert replay_digest() == GOLDEN_V1_SHA256


if __name__ == "__main__":
    print(replay_digest())
