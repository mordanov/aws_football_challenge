"""Rules v2: local foul / tackle model. Every constant is a local assumption.

See local_simulator/docs/rules-v2.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from football_lab.match_contracts import EngineBallState, EnginePlayerState, Intent, MatchEvent
from football_lab.physics import PITCH_HALF_L, PITCH_HALF_W, TACKLE_RADIUS, is_inactive


class RandomSource(Protocol):
    def random(self) -> float: ...


@dataclass(frozen=True)
class RulesV2Config:
    foul_scale: float = 1.0
    p_foul: float = 0.25
    behind_multiplier: float = 2.0
    behind_angle_deg: float = 60.0
    p_win_slide: float = 0.55
    p_win_press_step: float = 0.01
    stun_steps: int = 40
    send_off_fouls: int = 3
    box_depth: float = 16.5
    box_half_width: float = 20.0
    p_penalty_goal: float = 0.75

    def __post_init__(self) -> None:
        if self.foul_scale < 0:
            raise ValueError("foul_scale must be >= 0")


def _event(tick: int, kind: str, team: int | None, pid: int | None,
           x: float | None = None, y: float | None = None) -> MatchEvent:
    return {"tick": tick, "kind": kind, "team_id": team, "player_id": pid, "x": x, "y": y}


def _from_behind(tackler: EnginePlayerState, carrier: EnginePlayerState,
                 cfg: RulesV2Config) -> bool:
    dx, dy = tackler["x"] - carrier["x"], tackler["y"] - carrier["y"]
    norm = math.hypot(dx, dy)
    if norm == 0:
        return False
    backward = -1.0 if carrier["team_id"] == 0 else 1.0
    return dx * backward / norm > math.cos(math.radians(cfg.behind_angle_deg))


def _win(tackler: EnginePlayerState, ball: EngineBallState, tick: int) -> MatchEvent:
    ball["owner_team"], ball["owner_player"] = tackler["team_id"], tackler["player_id"]
    ball["in_flight"], ball["vx"], ball["vy"] = False, 0.0, 0.0
    return _event(tick, "TACKLE_SUCCESS", tackler["team_id"], tackler["player_id"],
                  tackler["x"], tackler["y"])


def _foul(tackler: EnginePlayerState, carrier: EnginePlayerState, ball: EngineBallState,
          score: list[int], tick: int, rng: RandomSource,
          cfg: RulesV2Config) -> list[MatchEvent]:
    events = [_event(tick, "FOUL", tackler["team_id"], tackler["player_id"],
                     tackler["x"], tackler["y"])]
    tackler["fouls"] = tackler.get("fouls", 0) + 1
    if tackler["fouls"] >= cfg.send_off_fouls:
        tackler["sent_off"] = True
        tackler["y"] = math.copysign(PITCH_HALF_W, tackler["y"] or 1.0)
        events.append(_event(tick, "SEND_OFF", tackler["team_id"], tackler["player_id"],
                             tackler["x"], tackler["y"]))
    else:
        tackler["stunned_until"] = tick + cfg.stun_steps
    attack = 1.0 if carrier["team_id"] == 0 else -1.0
    in_box = (attack * carrier["x"] >= PITCH_HALF_L - cfg.box_depth
              and abs(carrier["y"]) <= cfg.box_half_width)
    if in_box:
        team = carrier["team_id"]
        events.append(_event(tick, "PENALTY", team, carrier["player_id"],
                             carrier["x"], carrier["y"]))
        if rng.random() < cfg.p_penalty_goal:
            score[team] += 1
            events.append(_event(tick, "GOAL", team, None, attack * PITCH_HALF_L, 0.0))
        ball.update({"x": 0.0, "y": 0.0, "vx": 0.0, "vy": 0.0,
                     "owner_team": None, "owner_player": None, "in_flight": False})
    return events


def contest(
    players: list[EnginePlayerState],
    ball: EngineBallState,
    score: list[int],
    intents: list[Intent],
    tick: int,
    *,
    rng: RandomSource,
    cfg: RulesV2Config,
) -> list[MatchEvent]:
    """Resolve at most one ball contest against the carrier this step."""
    if ball["owner_team"] is None:
        return []
    carrier = next(
        (p for p in players
         if p["team_id"] == ball["owner_team"] and p["player_id"] == ball["owner_player"]),
        None,
    )
    if carrier is None:
        return []
    first: dict[tuple[int, int], Intent] = {}
    for it in intents:
        first.setdefault((it["team_id"], it["player_id"]), it)
    for p in players:
        if p["team_id"] == carrier["team_id"] or is_inactive(p, tick):
            continue
        if math.hypot(p["x"] - carrier["x"], p["y"] - carrier["y"]) > TACKLE_RADIUS:
            continue
        intent = first.get((p["team_id"], p["player_id"]))
        if intent is None:
            continue
        kind = intent["command_type"]
        if (kind == "SLIDE_TACKLE" and not intent["executed"]
                and int(intent["parameters"].get("target_player_id", -1))
                == carrier["player_id"]):
            intent["executed"] = True
            behind = cfg.behind_multiplier if _from_behind(p, carrier, cfg) else 1.0
            if rng.random() < min(1.0, cfg.p_foul * cfg.foul_scale * behind):
                return _foul(p, carrier, ball, score, tick, rng, cfg)
            if rng.random() < cfg.p_win_slide:
                return [_win(p, ball, tick)]
        elif kind == "PRESS_BALL" and rng.random() < cfg.p_win_press_step:
            return [_win(p, ball, tick)]
    return []
