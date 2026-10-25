"""Rules coach: picks a preset from shared snapshot fields only, so all players agree."""
from __future__ import annotations

from .model import Snapshot


def choose_preset(s: Snapshot) -> str:
    if s.coach:
        return s.coach
    diff = s.score[s.team] - s.score[1 - s.team]
    remaining = max(0.0, 1 - s.clock / s.duration) if s.duration > 0 else 0.0
    if diff < 0:
        return "go_all_out_attack" if remaining <= 0.35 else "press_high"
    if diff > 0 and remaining <= 0.40:
        return "slow_the_tempo"
    if diff == 0 and remaining <= 0.20:
        return "shoot_on_sight"
    return ""
