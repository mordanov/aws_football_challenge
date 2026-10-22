"""Translate engine EngineState to the standard AWS gameState payload."""
from __future__ import annotations

from typing import Any

from football_lab.match_contracts import EngineState


def encode_observation(
    state: EngineState, team_id: int, player_ids: list[int], seed: int
) -> dict[str, Any]:
    """Return an AWS-format observation payload for the given team."""
    tick = state["tick"]
    b = state["ball"]
    players = [
        {
            "playerId": p["player_id"],
            "teamId": p["team_id"],
            "position": {"x": p["x"], "y": p["y"]},
            "velocity": {"x": p["vx"], "y": p["vy"]},
            "stamina": p["stamina"],
        }
        for p in state["players"]
    ]
    ball = {
        "position": {"x": b["x"], "y": b["y"]},
        "velocity": {"x": b["vx"], "y": b["vy"]},
        "isFree": b["owner_team"] is None,
        "possessionPlayerId": b["owner_player"],
        "possessionTeamId": b["owner_team"],
    }
    score = state["score"]
    return {
        "gameState": {
            "tick": tick,
            "gameTime": tick * 0.05,
            "matchDuration": 120.0,
            "playMode": state["play_mode"],
            "score": {"home": score[0], "away": score[1]},
            "players": players,
            "ball": ball,
            "coachInstruction": "",
        },
        "teamId": team_id,
        "myPlayers": player_ids,
    }
