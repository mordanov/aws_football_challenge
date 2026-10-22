"""Real AFC payload -> World, and internal command -> wire command.

Formats handled (from aws-samples lib/state.py):
  new: players[].agentId "agentId_N", teamCode "home"/"away",
       ball.isFree, ball.possessionAgentId
  old: players[].playerId, teamId, ball.possessionPlayerId (+possessionTeamId)
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Optional

from .world import Pl, World

# ASSUMPTION: aim labels are relative to the shooter facing the goal ("attacker").
# If the server uses fixed world sides, set AIM_FRAME=world and away-team aims swap L/R.
AIM_FRAME = os.getenv("AIM_FRAME", "attacker")


def parse_payload(payload: Any, default_pid: Optional[int]) -> tuple[dict, int, list[int]]:
    """Unwrap the AgentCore payload. Returns (gameState, team_id, my player ids)."""
    data = payload
    if isinstance(data, dict) and "prompt" in data:
        data = data["prompt"]
    if isinstance(data, (str, bytes)):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError("payload is not an object")
    gs = data.get("gameState", data)
    team_id = int(data.get("teamId", gs.get("teamId", 0)) or 0)
    my = data.get("myPlayers")
    pids = [int(p) for p in my] if isinstance(my, list) and my else []
    if not pids:
        pid = default_pid if default_pid is not None else data.get("playerId")
        if pid is None:
            raise ValueError("no player id (myPlayers / MY_PLAYER_ID)")
        pids = [int(pid)]
    return gs, team_id, pids


def _idx(p: dict) -> int:
    if "agentId" in p:
        try:
            return int(str(p["agentId"]).rsplit("_", 1)[-1])
        except ValueError:
            return -1
    try:
        return int(p.get("playerId", -1))
    except (TypeError, ValueError):
        return -1


def _team_of(p: dict) -> Optional[int]:
    code = p.get("teamCode")
    if isinstance(code, str):
        c = code.strip().lower()
        return 0 if c == "home" else 1 if c == "away" else None
    t = p.get("teamId")
    return int(t) if isinstance(t, (int, float)) else None


def _xy(v: Any) -> tuple[float, float]:
    if isinstance(v, dict):
        return float(v.get("x", 0) or 0), float(v.get("y", 0) or 0)
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return float(v[0]), float(v[1])
    return 0.0, 0.0


def _stamina(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, f / 100.0 if f > 1.5 else f))


def _pl(p: dict, s: int) -> Pl:
    x, y = _xy(p.get("position"))
    vx, vy = _xy(p.get("velocity"))
    return Pl(_idx(p), s * x, s * y, s * vx, s * vy, _stamina(p.get("stamina", 1.0)),
              bool(p.get("isSprinting", False)), str(p.get("lastAction", "") or ""))


def _holder(ball: dict, players: list[dict], bx: float, by: float) -> Optional[dict]:
    if ball.get("isFree") is True:
        return None
    flagged = [p for p in players if p.get("hasBall") is True]
    if len(flagged) == 1:
        return flagged[0]
    aid = ball.get("possessionAgentId")
    if isinstance(aid, str):
        cands = [p for p in players if p.get("agentId") == aid]
        code = ball.get("possessionTeamCode")
        if isinstance(code, str):
            cands = [p for p in cands if str(p.get("teamCode", "")).lower() == code.lower()]
    else:
        pid = ball.get("possessionPlayerId")
        if pid is None:
            return None
        cands = [p for p in players if p.get("playerId") == pid]
        tid = ball.get("possessionTeamId")
        if tid is not None:
            cands = [p for p in cands if p.get("teamId") == tid]
    if not cands:
        return None
    # agentId is not unique across teams: take the candidate closest to the ball.
    return min(cands, key=lambda p: math.dist(_xy(p.get("position")), (bx, by)))


def _coach(gs: dict) -> str:
    parts = []
    for k in ("coachInstruction", "coachInstructions", "coach"):
        v = gs.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts += [m if isinstance(m, str) else str(m.get("text", m.get("message", ""))) for m in v[-3:]]
    return " ".join(parts)


def parse_world(gs: dict, team_id: int, pid: int) -> World:
    players = gs.get("players")
    if not isinstance(players, list) or not players:
        raise ValueError("no players in gameState")
    s = 1 if team_id == 0 else -1
    ours_raw = [p for p in players if _team_of(p) == team_id]
    opps_raw = [p for p in players if _team_of(p) not in (team_id, None)]
    ours = [_pl(p, s) for p in ours_raw]
    me = next((p for p in ours if p.id == pid), None)
    if me is None:
        raise ValueError(f"player {pid} of team {team_id} not in state")
    ball = gs.get("ball", {}) or {}
    bx, by = _xy(ball.get("position"))
    bvx, bvy = _xy(ball.get("velocity"))
    h = _holder(ball, players, bx, by)
    owner = None
    if h is not None and _team_of(h) is not None:
        owner = ("us" if _team_of(h) == team_id else "them", _idx(h))
    score = gs.get("score", {}) or {}
    home, away = int(score.get("home", 0) or 0), int(score.get("away", 0) or 0)
    su, st = (home, away) if team_id == 0 else (away, home)
    mt = gs.get("modeTeamId")
    return World(
        me=me, mates=[p for p in ours if p.id != pid], opps=[_pl(p, s) for p in opps_raw],
        ball=(s * bx, s * by), ball_vel=(s * bvx, s * bvy), owner=owner,
        score_us=su, score_them=st, game_time=float(gs.get("gameTime", 0) or 0),
        tick=int(gs.get("tick", 0) or 0), play_mode=str(gs.get("playMode", "") or ""),
        mode_team_us=None if mt is None else (mt == team_id), team_id=team_id, s=s,
        coach=_coach(gs), match_len=float(gs.get("matchDuration", 300) or 300),
    )


_SWAP_LR = {"TL": "TR", "TR": "TL", "BL": "BR", "BR": "BL", "CENTER": "CENTER"}


def to_wire(cmd: dict, w: World) -> dict:
    """Rotate internal coordinates back to the wire frame and add teamId."""
    out = {**cmd, "parameters": dict(cmd.get("parameters", {})), "teamId": w.team_id}
    p = out["parameters"]
    if "target_x" in p:
        p["target_x"] = round(w.s * p["target_x"], 2) + 0.0
        p["target_y"] = round(w.s * p["target_y"], 2) + 0.0
    if out["commandType"] == "SHOOT" and AIM_FRAME == "world" and w.s < 0:
        p["aim_location"] = _SWAP_LR[p["aim_location"]]
    if out["commandType"] == "FOLLOW_PLAYER":
        p.setdefault("target_team", "AWAY" if w.team_id == 0 else "HOME")
    return out
