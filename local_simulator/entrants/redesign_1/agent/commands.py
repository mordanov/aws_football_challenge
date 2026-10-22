"""Command constructors and validation for the real AFC wire format.

Source: aws-samples/sample-ai-possibilities agentic-football-sample-agents
(lib/parsing.py VALID_COMMANDS, position prompts, game-mechanics.md).
Coordinates here are INTERNAL (we attack +x); wire.to_wire rotates them.
"""
from __future__ import annotations

from typing import Any

from .world import HALF_L, HALF_W

AIMS = ("TL", "TR", "BL", "BR", "CENTER")
PASS_TYPES = ("GROUND", "AERIAL", "THROUGH")

# commandType -> {param: validator}
_NUM = (int, float)
SPEC: dict[str, dict[str, Any]] = {
    "MOVE_TO": {"target_x": _NUM, "target_y": _NUM, "sprint": bool},
    "PASS": {"target_player_id": int, "type": PASS_TYPES},
    "SHOOT": {"aim_location": AIMS, "power": _NUM},
    "GK_DISTRIBUTE": {"target_player_id": int, "method": ("THROW", "KICK")},
    "PRESS_BALL": {"intensity": _NUM},
    "MARK": {"target_player_id": int, "tightness": ("TIGHT", "LOOSE", "NORMAL")},
    "INTERCEPT": {"aggressive": bool},
    "FOLLOW_PLAYER": {"target_player_id": int, "target_team": ("HOME", "AWAY"), "distance": _NUM},
    "SLIDE_TACKLE": {"target_player_id": int, "sprint": bool, "distance": _NUM},
    "SET_STANCE": {"stance": int},
    "CLEAR_OVERRIDE": {},
    "RESET": {},
}


def _c(pid: int, kind: str, duration: int = 0, **params: Any) -> dict:
    return {"commandType": kind, "playerId": pid, "parameters": params, "duration": duration}


def _cx(x: float) -> float:
    return round(max(-HALF_L, min(HALF_L, x)), 2)


def _cy(y: float) -> float:
    return round(max(-HALF_W, min(HALF_W, y)), 2)


def move_to(pid: int, x: float, y: float, sprint: bool = False) -> dict:
    return _c(pid, "MOVE_TO", target_x=_cx(x), target_y=_cy(y), sprint=bool(sprint))


def pass_to(pid: int, target: int, kind: str = "GROUND") -> dict:
    return _c(pid, "PASS", target_player_id=int(target), type=kind)


def shoot(pid: int, aim: str, power: float) -> dict:
    return _c(pid, "SHOOT", aim_location=aim, power=round(max(0.0, min(1.0, power)), 2))


def gk_distribute(pid: int, target: int, method: str) -> dict:
    return _c(pid, "GK_DISTRIBUTE", target_player_id=int(target), method=method)


def press_ball(pid: int, intensity: float, duration: int = 1) -> dict:
    return _c(pid, "PRESS_BALL", duration, intensity=round(max(0.0, min(1.0, intensity)), 2))


def mark(pid: int, target: int, tight: bool = True, duration: int = 1) -> dict:
    return _c(pid, "MARK", duration, target_player_id=int(target), tightness="TIGHT" if tight else "LOOSE")


def intercept(pid: int, aggressive: bool = True, duration: int = 1) -> dict:
    return _c(pid, "INTERCEPT", duration, aggressive=bool(aggressive))


def slide_tackle(pid: int, target: int, distance: float) -> dict:
    return _c(pid, "SLIDE_TACKLE", target_player_id=int(target), sprint=True, distance=round(distance, 2))


def set_stance(pid: int, stance: int = 0) -> dict:
    return _c(pid, "SET_STANCE", stance=int(stance))


def validate(cmd: Any) -> bool:
    """Type + required params + ranges. Anything else is rejected."""
    if not isinstance(cmd, dict):
        return False
    spec = SPEC.get(cmd.get("commandType"))
    params = cmd.get("parameters", {})
    if spec is None or not isinstance(cmd.get("playerId"), int) or not isinstance(params, dict):
        return False
    for k, rule in spec.items():
        if k not in params:
            return False
        v = params[k]
        if isinstance(rule, tuple) and rule and isinstance(rule[0], str):
            if v not in rule:
                return False
        elif rule is bool:
            if not isinstance(v, bool):
                return False
        elif rule is int:
            if isinstance(v, bool) or not isinstance(v, int):
                return False
        elif isinstance(v, bool) or not isinstance(v, rule):
            return False
    if cmd["commandType"] in ("PRESS_BALL",) and not 0 <= params["intensity"] <= 1:
        return False
    if cmd["commandType"] == "SHOOT" and not 0 <= params["power"] <= 1:
        return False
    if cmd["commandType"] == "MOVE_TO" and (abs(params["target_x"]) > HALF_L or abs(params["target_y"]) > HALF_W):
        return False
    return True
