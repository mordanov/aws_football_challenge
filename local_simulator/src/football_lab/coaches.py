"""Scripted coaches: model a human coach shouting presets (spec §6)."""
from __future__ import annotations

from football_lab.engine import TOTAL_TICKS
from football_lab.match_contracts import CoachFn, EngineState


def scripted_coach(state: EngineState, team_id: int) -> str:
    diff = state["score"][team_id] - state["score"][1 - team_id]
    remaining = 1 - state["tick"] / TOTAL_TICKS
    if diff < 0 and remaining <= 0.35:
        return "go_all_out_attack"
    if diff < 0:
        return "press_high"
    if diff > 0 and remaining <= 0.40:
        return "slow_the_tempo"
    if diff == 0 and remaining <= 0.20:
        return "shoot_on_sight"
    return ""


COACHES: dict[str, CoachFn] = {"scripted": scripted_coach}
