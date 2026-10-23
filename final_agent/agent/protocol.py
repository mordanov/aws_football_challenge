from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any

from .model import Player, Snapshot


def number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def integer(value: Any, name: str, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def team_index(value: Any) -> int:
    if isinstance(value, str) and value.lower() in ("home", "away"):
        return 0 if value.lower() == "home" else 1
    return integer(value, "team", 0, 1)


def player_index(value: Any) -> int:
    if isinstance(value, str) and value.startswith("agentId_"):
        suffix = value.removeprefix("agentId_")
        if suffix.isdigit():
            value = int(suffix)
    return integer(value, "player", 0, 4)


def envelope(payload: dict) -> tuple[dict, int, int]:
    if not isinstance(payload, dict):
        raise TypeError("payload must be an object")
    raw = payload.get("prompt", payload)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError("prompt is not valid JSON") from exc
    if not isinstance(data, dict):
        raise TypeError("prompt must contain an object")
    team = team_index(data.get("teamId"))
    controlled = data.get("myPlayers")
    if not isinstance(controlled, list) or len(controlled) != 1:
        raise ValueError("exactly one myPlayers entry is required")
    return data, team, player_index(controlled[0])


def _xy(value: object, name: str, *, scale: float = 1.0, lateral: str = "y") -> tuple[float, float]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must contain x/y")
    return (
        number(value.get("x"), name + ".x") * scale,
        number(value.get(lateral), name + "." + lateral) * scale,
    )


HOLD_RADIUS = 3.0
HOLD_MARGIN = 1.0
MATCH_COORDINATE_SCALE = 8.0
MATCH_X_LIMIT = 6.4
MATCH_Y_LIMIT = 3.5


def match_command(command: dict) -> dict:
    """Convert canonical movement to the compact pitch observed in AWS matches."""
    if command["commandType"] != "MOVE_TO":
        return command
    params = command["parameters"]
    return {
        **command,
        "parameters": {
            **params,
            "target_x": round(
                max(
                    -MATCH_X_LIMIT, min(MATCH_X_LIMIT, params["target_x"] / MATCH_COORDINATE_SCALE)
                ),
                3,
            ),
            "target_y": round(
                max(
                    -MATCH_Y_LIMIT, min(MATCH_Y_LIMIT, params["target_y"] / MATCH_COORDINATE_SCALE)
                ),
                3,
            ),
        },
    }


def _nearest_holder(
    players: list[Player], holder_id: int, ball: tuple[float, float]
) -> tuple[int, int] | None:
    """Resolve a team-less agentId_N (present on both teams) by ball proximity.

    The carrier sits on the ball; the same-index opponent rarely does. Close or
    distant ties stay unknown rather than guessed.
    """
    near, far = sorted((math.dist((p.x, p.y), ball), p.team) for p in players if p.id == holder_id)
    if near[0] <= HOLD_RADIUS and far[0] - near[0] >= HOLD_MARGIN:
        return near[1], holder_id
    return None


def decode(
    payload: dict,
    *,
    stamina_scale: float = 1.0,
    duration: float = 120.0,
    compact_pitch: bool = False,
) -> tuple[Snapshot, int]:
    data, team, pid = envelope(payload)
    if stamina_scale not in (1.0, 100.0):
        raise ValueError("stamina_scale must be 1 or 100")
    state = data.get("gameState")
    if not isinstance(state, dict):
        raise TypeError("gameState must be an object")
    raw_players = state.get("players")
    if not isinstance(raw_players, list):
        raise TypeError("players must be a list")
    players = []
    scale = MATCH_COORDINATE_SCALE if compact_pitch else 1.0
    for raw in raw_players:
        if not isinstance(raw, dict):
            raise TypeError("player must be an object")
        side = team_index(raw.get("teamCode", raw.get("teamId")))
        idx = player_index(raw.get("agentId", raw.get("playerId")))
        x, y = _xy(raw.get("position"), "position", scale=scale)
        if abs(x) > 55 or abs(y) > 35:
            raise ValueError("player outside the sample pitch")
        vx, vy = _xy(raw.get("velocity", {"x": 0, "y": 0}), "velocity", scale=scale)
        stamina = number(raw.get("stamina", stamina_scale), "stamina") / stamina_scale
        if not 0 <= stamina <= 1:
            raise ValueError("stamina outside configured scale")
        players.append(Player(side, idx, x, y, vx, vy, stamina))
    identities = {(p.team, p.id) for p in players}
    if len(identities) != len(players) or identities != {
        (side, idx) for side in range(2) for idx in range(5)
    }:
        raise ValueError("expected exactly five distinct players per team")
    ball = state.get("ball")
    if not isinstance(ball, dict):
        raise TypeError("ball must be an object")
    position = ball.get("position")
    raw_velocity = ball.get("velocity", {"x": 0, "y": 0})
    xy = _xy(
        position,
        "ball",
        scale=scale,
        lateral="z" if compact_pitch and isinstance(position, dict) and "z" in position else "y",
    )
    velocity = _xy(
        raw_velocity,
        "ball velocity",
        scale=scale,
        lateral="z"
        if compact_pitch and isinstance(raw_velocity, dict) and "z" in raw_velocity
        else "y",
    )
    free = ball.get("isFree") is True
    if "isFree" in ball and not isinstance(ball["isFree"], bool):
        raise ValueError("isFree must be boolean")
    owner = None
    holder = ball.get("possessionAgentId", ball.get("possessionPlayerId"))
    if not free and holder is not None:
        holder_id = player_index(holder)
        holder_team = ball.get("possessionTeamId", ball.get("possessionTeamCode"))
        if holder_team is not None:
            owner = (team_index(holder_team), holder_id)
        else:
            owner = _nearest_holder(players, holder_id, xy)
    score = state.get("score")
    if not isinstance(score, dict):
        raise TypeError("score must have home and away entries")
    scores = (
        integer(score.get("home"), "score", 0, 10000),
        integer(score.get("away"), "score", 0, 10000),
    )
    clock = number(state.get("gameTime"), "gameTime")
    length = number(state.get("matchDuration", duration), "matchDuration")
    if clock < 0 or length <= 0:
        raise ValueError("invalid game time or duration")
    coach = state.get("coachInstruction", "")
    if not isinstance(coach, str):
        raise ValueError("coachInstruction must be text")
    coach = coach.strip().lower()
    if coach not in ("go_all_out_attack", "slow_the_tempo", "press_high", "shoot_on_sight"):
        coach = ""
    return Snapshot(
        team,
        integer(state.get("tick"), "tick", 0, 10**12),
        clock,
        length,
        tuple(sorted(players, key=lambda p: (p.team, p.id))),
        xy,
        velocity,
        owner,
        free,
        scores,
        coach,
    ), pid


def validate_command(command: object) -> None:
    if not isinstance(command, dict):
        raise TypeError("command must be an object")
    integer(command.get("teamId"), "teamId", 0, 1)
    pid = integer(command.get("playerId"), "playerId", 0, 4)
    integer(command.get("duration"), "duration", 0, 0)
    params = command.get("parameters")
    if not isinstance(params, dict):
        raise TypeError("parameters must be an object")
    schemas = {
        "MOVE_TO": {"target_x", "target_y", "sprint"},
        "PASS": {"target_player_id", "type"},
        "SHOOT": {"aim_location", "power"},
        "PRESS_BALL": {"intensity"},
        "MARK": {"target_player_id", "tightness"},
        "INTERCEPT": {"aggressive"},
        "GK_DISTRIBUTE": {"target_player_id", "method"},
        "SET_STANCE": {"stance"},
        "SLIDE_TACKLE": {"target_player_id"},
    }
    kind = command.get("commandType")
    if kind not in schemas or set(params) != schemas[kind]:
        raise ValueError(f"unsupported command or invalid parameters: {kind}")
    for key in ("target_x", "target_y", "power", "intensity"):
        if key in params and type(params[key]) not in (int, float):
            raise ValueError(f"{key} must be a JSON number")
    if kind == "MOVE_TO":
        if abs(number(params["target_x"], "target_x")) > 55:
            raise ValueError("target_x outside pitch")
        if abs(number(params["target_y"], "target_y")) > 35:
            raise ValueError("target_y outside pitch")
        if not isinstance(params["sprint"], bool):
            raise ValueError("sprint must be boolean")
    if "target_player_id" in params:
        target = integer(params["target_player_id"], "target_player_id", 0, 4)
        if kind in ("PASS", "GK_DISTRIBUTE") and target == pid:
            raise ValueError("cannot pass to self")
    for key in ("power", "intensity"):
        if key in params and not 0 <= number(params[key], key) <= 1:
            raise ValueError(f"{key} outside 0..1")
    if kind == "PASS" and params["type"] not in ("GROUND", "AERIAL", "THROUGH"):
        raise ValueError("invalid pass type")
    if kind == "SHOOT" and params["aim_location"] not in ("TL", "TR", "BL", "BR", "CENTER"):
        raise ValueError("invalid shot aim")
    if kind == "MARK" and params["tightness"] not in ("LOOSE", "TIGHT"):
        raise ValueError("invalid tightness")
    if kind == "INTERCEPT" and not isinstance(params["aggressive"], bool):
        raise ValueError("aggressive must be boolean")
    if kind == "GK_DISTRIBUTE" and (pid != 0 or params["method"] not in ("THROW", "KICK")):
        raise ValueError("invalid goalkeeper distribution")
    if kind == "SET_STANCE" and (type(params["stance"]) is not int or params["stance"] != 0):
        raise ValueError("only unambiguous balanced stance is supported")


def snapshot_dict(snapshot: Snapshot) -> dict:
    return json.loads(json.dumps(asdict(snapshot), allow_nan=False))


def restore_snapshot(data: object) -> Snapshot:
    if not isinstance(data, dict):
        raise TypeError("snapshot must be an object")
    try:
        players = data["players"]
        owner = data["owner"]
        state = {
            "tick": data["tick"],
            "gameTime": data["clock"],
            "matchDuration": data["duration"],
            "coachInstruction": data.get("coach", ""),
            "score": dict(zip(("home", "away"), data["score"], strict=True)),
            "players": [
                {
                    "playerId": p["id"],
                    "teamId": p["team"],
                    "position": {"x": p["x"], "y": p["y"]},
                    "velocity": {"x": p["vx"], "y": p["vy"]},
                    "stamina": p["stamina"],
                }
                for p in players
            ],
            "ball": {
                "position": dict(zip(("x", "y"), data["ball"], strict=True)),
                "velocity": dict(zip(("x", "y"), data["velocity"], strict=True)),
                "isFree": data["free"],
            },
        }
        if owner is not None:
            state["ball"].update(possessionTeamId=owner[0], possessionPlayerId=owner[1])
        snapshot, _ = decode({"gameState": state, "teamId": data["team"], "myPlayers": [0]})
        return snapshot
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("invalid normalized snapshot") from exc
