"""Physics step for the M2 local simulator.

One "decision window" = 40 physics steps = 2 simulated seconds.
All coordinates in pitch units (1 unit ≈ 1 m, origin = centre).
"""
from __future__ import annotations

import math
from typing import Any

from football_lab.match_contracts import (
    EngineBallState,
    EnginePlayerState,
    EngineState,
    Intent,
    MatchEvent,
)

# ── Decision window ───────────────────────────────────────────────────────────
STEPS_PER_DECISION: int = 40

# ── Pitch ─────────────────────────────────────────────────────────────────────
PITCH_HALF_L: float = 55.0
PITCH_HALF_W: float = 35.0
GOAL_HALF_W: float = 4.5

# ── Ball ──────────────────────────────────────────────────────────────────────
PICKUP_RADIUS: float = 2.1
TACKLE_RADIUS: float = 2.4

# 0.82 per 40-step decision window → per-step factor
FREE_BALL_DAMPING_PER_STEP: float = 0.82 ** (1.0 / 40.0)

# Ball speeds (units/step, constant during flight)
_BALL_SPEED: dict[str, float] = {
    "PASS_GROUND": 1.5,
    "PASS_THROUGH": 1.5,
    "PASS_AERIAL": 1.2,
    "SHOOT": 2.5,
    "GK_THROW": 1.5,
    "GK_KICK": 2.0,
}

# ── Player ────────────────────────────────────────────────────────────────────
# speed = (4.0 + 2.0 * stamina) / 40.0  per step  (from redesign_2 arrival())
def _max_speed(stamina: float) -> float:
    return (4.0 + 2.0 * stamina) / 40.0

# Stamina deltas per step
_STAMINA_DRAIN = {
    "PRESS_BALL": -0.005,
    "SLIDE_TACKLE": -0.005,
    "MOVE_TO": -0.002,
    "INTERCEPT": -0.002,
    "PASS": -0.002,     # burst; only on one-shot step
    "SHOOT": -0.002,
    "GK_DISTRIBUTE": -0.002,
}
_STAMINA_RECOVERY = 0.002


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _intent_for(
    team_id: int, player_id: int, intents: list[Intent]
) -> Intent | None:
    for intent in intents:
        if intent["team_id"] == team_id and intent["player_id"] == player_id:
            return intent
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def step(
    state: EngineState,
    intents: list[Intent],
) -> tuple[EngineState, list[MatchEvent]]:
    """Advance state by one physics step. Returns (new_state, events)."""
    events: list[MatchEvent] = []
    tick = state["tick"] + 1

    # Deep-copy mutable parts
    ball: EngineBallState = dict(state["ball"])  # type: ignore[assignment]
    players: list[EnginePlayerState] = [dict(p) for p in state["players"]]  # type: ignore[assignment]
    score: list[int] = list(state["score"])

    # ── 1. Move players ───────────────────────────────────────────────────────
    for p in players:
        intent = _intent_for(p["team_id"], p["player_id"], intents)
        cmd = intent["command_type"] if intent else "SET_STANCE"
        params: dict[str, Any] = intent["parameters"] if intent else {}

        if cmd == "MOVE_TO":
            tx = float(params.get("target_x", p["x"]))
            ty = float(params.get("target_y", p["y"]))
            dx, dy = tx - p["x"], ty - p["y"]
            dist = math.hypot(dx, dy)
            speed = _max_speed(p["stamina"])
            if dist > 0:
                move = min(dist, speed)
                p["x"] = _clamp(p["x"] + dx / dist * move, -PITCH_HALF_L, PITCH_HALF_L)
                p["y"] = _clamp(p["y"] + dy / dist * move, -PITCH_HALF_W, PITCH_HALF_W)
            p["stamina"] = _clamp(p["stamina"] + _STAMINA_DRAIN["MOVE_TO"], 0.0, 1.0)

        elif cmd == "PRESS_BALL":
            # move toward ball
            dx, dy = ball["x"] - p["x"], ball["y"] - p["y"]
            dist = math.hypot(dx, dy)
            speed = _max_speed(p["stamina"])
            if dist > 0:
                move = min(dist, speed)
                p["x"] = _clamp(p["x"] + dx / dist * move, -PITCH_HALF_L, PITCH_HALF_L)
                p["y"] = _clamp(p["y"] + dy / dist * move, -PITCH_HALF_W, PITCH_HALF_W)
            p["stamina"] = _clamp(p["stamina"] + _STAMINA_DRAIN["PRESS_BALL"], 0.0, 1.0)

        elif cmd == "INTERCEPT":
            # move toward ball (simplified; no prediction)
            dx, dy = ball["x"] - p["x"], ball["y"] - p["y"]
            dist = math.hypot(dx, dy)
            speed = _max_speed(p["stamina"])
            if dist > 0:
                move = min(dist, speed)
                p["x"] = _clamp(p["x"] + dx / dist * move, -PITCH_HALF_L, PITCH_HALF_L)
                p["y"] = _clamp(p["y"] + dy / dist * move, -PITCH_HALF_W, PITCH_HALF_W)
            p["stamina"] = _clamp(p["stamina"] + _STAMINA_DRAIN["INTERCEPT"], 0.0, 1.0)

        elif cmd == "SLIDE_TACKLE":
            # move toward target player
            tid = int(params.get("target_player_id", 0))
            target = next(
                (q for q in players if q["team_id"] != p["team_id"] and q["player_id"] == tid),
                None,
            )
            if target:
                dx, dy = target["x"] - p["x"], target["y"] - p["y"]
                dist = math.hypot(dx, dy)
                speed = _max_speed(p["stamina"])
                if dist > 0:
                    move = min(dist, speed)
                    p["x"] = _clamp(p["x"] + dx / dist * move, -PITCH_HALF_L, PITCH_HALF_L)
                    p["y"] = _clamp(p["y"] + dy / dist * move, -PITCH_HALF_W, PITCH_HALF_W)
            p["stamina"] = _clamp(p["stamina"] + _STAMINA_DRAIN["SLIDE_TACKLE"], 0.0, 1.0)

        else:
            # SET_STANCE, MARK, PASS (after fire), SHOOT (after fire) — recovery
            p["stamina"] = _clamp(p["stamina"] + _STAMINA_RECOVERY, 0.0, 1.0)

    # ── 2. Move ball ──────────────────────────────────────────────────────────
    if ball["owner_team"] is None:
        # Free ball: apply velocity
        ball["x"] += ball["vx"]
        ball["y"] += ball["vy"]
        # Decay only when rolling (not in_flight)
        if not ball["in_flight"]:
            ball["vx"] *= FREE_BALL_DAMPING_PER_STEP
            ball["vy"] *= FREE_BALL_DAMPING_PER_STEP
    else:
        # Owned ball travels with owner
        owner = next(
            (p for p in players
             if p["team_id"] == ball["owner_team"]
             and p["player_id"] == ball["owner_player"]),
            None,
        )
        if owner:
            ball["x"] = owner["x"]
            ball["y"] = owner["y"]

    # ── 3. One-shot commands (PASS, SHOOT, GK_DISTRIBUTE) ─────────────────────
    for intent in intents:
        if intent.get("executed"):
            continue
        cmd = intent["command_type"]
        params = intent["parameters"]
        team_id = intent["team_id"]
        player_id = intent["player_id"]
        player = next(
            (p for p in players if p["team_id"] == team_id and p["player_id"] == player_id),
            None,
        )
        if player is None:
            continue

        if cmd == "PASS" and ball["owner_team"] == team_id and ball["owner_player"] == player_id:
            ptype = str(params.get("type", "GROUND")).upper()
            key = f"PASS_{ptype}" if ptype in ("GROUND", "AERIAL", "THROUGH") else "PASS_GROUND"
            target_id = int(params.get("target_player_id", 0))
            target = next(
                (p for p in players if p["team_id"] == team_id and p["player_id"] == target_id),
                None,
            )
            if target:
                dx, dy = target["x"] - ball["x"], target["y"] - ball["y"]
                dist = math.hypot(dx, dy) or 1.0
                speed = _BALL_SPEED.get(key, 1.5)
                ball["vx"] = dx / dist * speed
                ball["vy"] = dy / dist * speed
                ball["owner_team"] = None
                ball["owner_player"] = None
                ball["in_flight"] = True
                intent["executed"] = True
                player["stamina"] = _clamp(player["stamina"] + _STAMINA_DRAIN["PASS"], 0.0, 1.0)

        elif cmd == "SHOOT" and ball["owner_team"] == team_id and ball["owner_player"] == player_id:
            # aim toward opponent goal on +x or -x depending on attack direction
            s = 1 if team_id == 0 else -1
            gx = s * PITCH_HALF_L
            gy = 0.0
            dx, dy = gx - ball["x"], gy - ball["y"]
            dist = math.hypot(dx, dy) or 1.0
            speed = _BALL_SPEED["SHOOT"]
            ball["vx"] = dx / dist * speed
            ball["vy"] = dy / dist * speed
            ball["owner_team"] = None
            ball["owner_player"] = None
            ball["in_flight"] = True
            intent["executed"] = True
            player["stamina"] = _clamp(player["stamina"] + _STAMINA_DRAIN["SHOOT"], 0.0, 1.0)

        elif cmd == "GK_DISTRIBUTE" and ball["owner_team"] == team_id and ball["owner_player"] == player_id:
            dtype = str(params.get("type", "THROW")).upper()
            key = "GK_THROW" if dtype == "THROW" else "GK_KICK"
            tx = float(params.get("target_x", 0.0))
            ty = float(params.get("target_y", 0.0))
            dx, dy = tx - ball["x"], ty - ball["y"]
            dist = math.hypot(dx, dy) or 1.0
            speed = _BALL_SPEED[key]
            ball["vx"] = dx / dist * speed
            ball["vy"] = dy / dist * speed
            ball["owner_team"] = None
            ball["owner_player"] = None
            ball["in_flight"] = True
            intent["executed"] = True
            player["stamina"] = _clamp(player["stamina"] + _STAMINA_DRAIN["GK_DISTRIBUTE"], 0.0, 1.0)

    # ── 4. Possession pickup ──────────────────────────────────────────────────
    if ball["owner_team"] is None:
        closest: EnginePlayerState | None = None
        closest_dist = PICKUP_RADIUS
        for p in players:
            d = _dist(p["x"], p["y"], ball["x"], ball["y"])
            if d <= closest_dist:
                closest_dist = d
                closest = p
        if closest is not None:
            ball["owner_team"] = closest["team_id"]
            ball["owner_player"] = closest["player_id"]
            ball["in_flight"] = False
            ball["vx"] = 0.0
            ball["vy"] = 0.0

    # ── 5. Boundary / goal detection ──────────────────────────────────────────
    bx, by = ball["x"], ball["y"]

    if abs(bx) > PITCH_HALF_L:
        # Ball crossed the goal line
        if abs(by) <= GOAL_HALF_W:
            # GOAL: team whose goal line was crossed is the defending team
            scoring_team = 0 if bx > 0 else 1
            score[scoring_team] += 1
            events.append({
                "tick": tick,
                "kind": "GOAL",
                "team_id": scoring_team,
                "player_id": None,
                "x": bx,
                "y": by,
            })
        else:
            events.append({
                "tick": tick,
                "kind": "GOAL_KICK",
                "team_id": None,
                "player_id": None,
                "x": bx,
                "y": by,
            })
        # Reset ball to centre (simplified restart)
        ball["x"] = 0.0
        ball["y"] = 0.0
        ball["vx"] = 0.0
        ball["vy"] = 0.0
        ball["owner_team"] = None
        ball["owner_player"] = None
        ball["in_flight"] = False

    elif abs(by) > PITCH_HALF_W:
        events.append({
            "tick": tick,
            "kind": "THROW_IN",
            "team_id": None,
            "player_id": None,
            "x": bx,
            "y": by,
        })
        ball["y"] = math.copysign(PITCH_HALF_W, by)
        ball["vy"] = 0.0
        ball["vx"] = 0.0
        ball["owner_team"] = None
        ball["owner_player"] = None
        ball["in_flight"] = False

    new_state: EngineState = {
        "tick": tick,
        "play_mode": state["play_mode"],
        "mode_team": state["mode_team"],
        "players": players,
        "ball": ball,
        "score": score,
    }
    return new_state, events
