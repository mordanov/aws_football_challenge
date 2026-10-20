"""Shared builders for tests: real AFC gameState dicts (AWS sample schema)."""
from __future__ import annotations

import copy
import json
import os

HERE = os.path.dirname(__file__)
SAMPLE = json.load(open(os.path.join(HERE, "aws_sample_state.json")))


def sample() -> dict:
    return copy.deepcopy(SAMPLE)


def state(home: dict, away: dict, ball=(0.0, 0.0), owner=None, t=60.0, tick=100,
          score=(0, 0), stamina=0.9, play_mode="OPEN_PLAY") -> dict:
    """home/away: {id: (x, y)} in WORLD coordinates. owner: ("home"|"away", id) or None."""
    players = []
    for code, team in (("home", home), ("away", away)):
        for pid, (x, y) in team.items():
            players.append({"agentId": f"agentId_{pid}", "teamCode": code, "position": {"x": x, "y": y},
                            "velocity": {"x": 0, "y": 0}, "stamina": stamina, "lastAction": "MoveToTarget",
                            "isSprinting": False})
    ball_d = {"position": {"x": ball[0], "y": ball[1], "z": 0}, "velocity": {"x": 0, "y": 0, "z": 0},
              "isFree": owner is None}
    if owner:
        ball_d["possessionAgentId"] = f"agentId_{owner[1]}"
        ball_d["possessionTeamCode"] = owner[0]
    return {"tick": tick, "gameTime": t, "playMode": play_mode, "modeTeamId": None,
            "score": {"home": score[0], "away": score[1]}, "ball": ball_d, "players": players, "teamChat": []}


def payload(gs: dict, team: int, pids) -> dict:
    return {"prompt": json.dumps({"gameState": gs, "teamId": team, "myPlayers": list(pids)})}


HOME = {0: (-52, 0), 1: (-30, 0), 2: (-10, 5), 3: (10, -10), 4: (10, 10)}
AWAY = {0: (52, 0), 1: (30, 0), 2: (10, -5), 3: (-10, 10), 4: (-10, -10)}
