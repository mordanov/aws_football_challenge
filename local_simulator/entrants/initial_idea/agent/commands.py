"""Command constructors - the ONLY place that knows the wire format.

ASSUMPTION (verify against BUILD_AND_DEPLOY.md in the AWS sample repo):
the envelope below comes from a published example,
  [{"commandType":"SHOOT","playerId":3,
    "parameters":{"aim_location":"CENTER","power":1.0},"duration":0}]
Command *names* come from the AWS/Strands write-ups. Parameter names other
than aim_location/power are educated guesses.
"""
from __future__ import annotations

from typing import Any

COMMAND_TYPES = {
    "MOVE_TO", "PASS", "SHOOT", "DRIBBLE", "PRESS_BALL", "MARK", "INTERCEPT",
    "TACKLE", "SLIDE_TACKLE", "CLEAR", "IDLE", "GK_DISTRIBUTE", "SET_STANCE",
}


def _c(pid: Any, kind: str, **params: Any) -> dict:
    return {"commandType": kind, "playerId": pid, "parameters": params, "duration": 0}


def move_to(pid, x: float, y: float) -> dict:
    return _c(pid, "MOVE_TO", x=round(x, 2), y=round(y, 2))


def pass_to(pid, target_id) -> dict:
    return _c(pid, "PASS", target_player_id=target_id)


def shoot(pid, aim: str = "CENTER", power: float = 1.0) -> dict:
    return _c(pid, "SHOOT", aim_location=aim, power=power)


def dribble(pid, x: float, y: float) -> dict:
    return _c(pid, "DRIBBLE", x=round(x, 2), y=round(y, 2))


def press_ball(pid) -> dict:
    return _c(pid, "PRESS_BALL")


def mark(pid, target_id) -> dict:
    return _c(pid, "MARK", target_player_id=target_id)


def intercept(pid) -> dict:
    return _c(pid, "INTERCEPT")


def tackle(pid) -> dict:
    return _c(pid, "TACKLE")


def clear(pid, x: float | None = None, y: float | None = None) -> dict:
    return _c(pid, "CLEAR") if x is None else _c(pid, "CLEAR", x=round(x, 2), y=round(y or 0, 2))


def gk_distribute(pid, target_id) -> dict:
    return _c(pid, "GK_DISTRIBUTE", target_player_id=target_id)


def set_stance(pid) -> dict:
    return _c(pid, "SET_STANCE")


def idle(pid) -> dict:
    return _c(pid, "IDLE")


def to_wire(cmd: dict, offset: tuple[float, float]) -> dict:
    """Convert internal centered coordinates back to the server's frame."""
    out = {**cmd, "parameters": dict(cmd["parameters"])}
    p = out["parameters"]
    if "x" in p and "y" in p:
        p["x"] = round(p["x"] + offset[0], 2)
        p["y"] = round(p["y"] + offset[1], 2)
    return out


def is_valid(cmd: Any, pid: Any) -> bool:
    return (
        isinstance(cmd, dict)
        and cmd.get("commandType") in COMMAND_TYPES
        and cmd.get("playerId") == pid
        and isinstance(cmd.get("parameters"), dict)
    )
