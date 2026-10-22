"""Raw game-state JSON -> World.

The real schema is not publicly documented beyond: ball position, every
player's position/velocity/stamina, possession, score, clock, current actions,
coach instructions. This adapter therefore accepts several plausible shapes
and infers what it can (attack direction from where our own goalkeeper
stands, ball owner from proximity). When you see a real state payload, make
the key lookups below exact - that is the single most valuable first edit.
"""
from __future__ import annotations

import math
import os
from typing import Any, Optional

from .model import Pl, World

_ID_KEYS = ("id", "playerId", "player_id", "unum", "number")
_TEAM_KEYS = ("team", "side", "teamId", "team_id")


def _first(d: dict, keys: tuple[str, ...], default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return default


def _xy(v: Any) -> Optional[tuple[float, float]]:
    if v is None:
        return None
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return float(v[0]), float(v[1])
    if isinstance(v, dict):
        for k in ("position", "pos", "location"):
            if k in v:
                return _xy(v[k])
        if "x" in v and "y" in v:
            return float(v["x"]), float(v["y"])
    return None


def _stamina(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1.0
    return f / 100.0 if f > 1.5 else max(0.0, f)


def _player(d: dict, off: tuple[float, float]) -> Pl:
    x, y = _xy(d) or (0.0, 0.0)
    vx, vy = _xy(_first(d, ("velocity", "vel", "v"))) or (0.0, 0.0)
    pid = _first(d, _ID_KEYS)
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        pass
    return Pl(pid, x - off[0], y - off[1], vx, vy, _stamina(_first(d, ("stamina", "energy"), 1.0)))


def _split_players(state: dict, my_team: Any) -> tuple[list[dict], list[dict]]:
    if "teammates" in state or "opponents" in state:
        return list(state.get("teammates", [])), list(state.get("opponents", []))
    for ours_k, theirs_k in (("myTeam", "opponentTeam"), ("home", "away"), ("us", "them")):
        if ours_k in state and theirs_k in state:
            return list(state[ours_k]), list(state[theirs_k])
    flat = state.get("players", [])
    ours = [p for p in flat if _first(p, _TEAM_KEYS) == my_team]
    theirs = [p for p in flat if _first(p, _TEAM_KEYS) != my_team]
    return ours, theirs


def parse_world(state: dict, my_id: Any, *, length: float | None = None,
                width: float | None = None, origin: str | None = None) -> World:
    state = state.get("gameState", state)
    length = float(length or _first(state, ("pitchLength", "fieldLength"), None) or
                   os.getenv("PITCH_LENGTH", 110))
    width = float(width or _first(state, ("pitchWidth", "fieldWidth"), None) or
                  os.getenv("PITCH_WIDTH", 70))
    origin = origin or os.getenv("PITCH_ORIGIN", "center")  # center | corner
    off = (length / 2, width / 2) if origin == "corner" else (0.0, 0.0)

    my_team = _first(state, ("myTeam", "team", "teamId", "side"))
    ours_raw, theirs_raw = _split_players(state, my_team)
    ours = [_player(p, off) for p in ours_raw]
    opps = [_player(p, off) for p in theirs_raw]
    try:
        my_id = int(my_id)
    except (TypeError, ValueError):
        pass
    me = next((p for p in ours if p.id == my_id), None)
    if me is None:
        raise ValueError(f"player {my_id!r} not found in state")
    mates = [p for p in ours if p.id != my_id]

    bx, by = _xy(_first(state, ("ball",))) or (0.0, 0.0)
    ball = (bx - off[0], by - off[1])
    bvx, bvy = _xy(_first(state.get("ball", {}), ("velocity", "vel"))) or (0.0, 0.0) \
        if isinstance(state.get("ball"), dict) else (0.0, 0.0)

    # Attack direction: our goalkeeper (id 0) stands near our own goal.
    gk = me if me.id == 0 else next((p for p in mates if p.id == 0), None)
    s = 1 if (gk is None and me.x < 0) or (gk is not None and gk.x < 0) else -1

    owner = _owner(state, ours, opps, ball, my_team)

    score = state.get("score", {}) or {}
    su, st = _scores(score, state)
    clock = float(_first(state, ("clock", "time", "t", "elapsed"), 0.0) or 0.0)
    match_len = float(_first(state, ("matchLength", "duration", "matchDuration"), 120.0) or 120.0)
    coach = str(_first(state, ("coachInstruction", "coach_order", "coach", "coachOrder"), "") or "")

    return World(me, mates, opps, ball, (bvx, bvy), owner, su, st, clock, match_len,
                 length, width, s, coach, off)


def _scores(score: Any, state: dict) -> tuple[int, int]:
    if isinstance(score, dict):
        if "us" in score:
            return int(score["us"]), int(score.get("them", 0))
        if "home" in score:  # ambiguous; assume we are home unless told otherwise
            return int(score["home"]), int(score.get("away", 0))
    if isinstance(score, (list, tuple)) and len(score) == 2:
        return int(score[0]), int(score[1])
    return int(state.get("scoreUs", 0) or 0), int(state.get("scoreThem", 0) or 0)


def _owner(state: dict, ours: list[Pl], opps: list[Pl], ball: tuple[float, float],
           my_team: Any) -> Optional[tuple[str, Any]]:
    b = state.get("ball")
    raw = None
    if isinstance(b, dict):
        raw = _first(b, ("owner", "possessedBy", "holder", "playerId"))
    raw = raw if raw is not None else state.get("possession")
    if isinstance(raw, dict):
        team, pid = _first(raw, _TEAM_KEYS), _first(raw, _ID_KEYS)
        if pid is not None:
            return ("us" if team == my_team else "them", int(pid) if str(pid).isdigit() else pid)
    # Fallback: whoever is within 2 m of the ball and closest.
    best, best_d = None, 2.0
    for side, group in (("us", ours), ("them", opps)):
        for p in group:
            d = math.hypot(p.x - ball[0], p.y - ball[1])
            if d < best_d:
                best, best_d = (side, p.id), d
    return best
