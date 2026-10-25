from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from agent.model import Snapshot


@dataclass
class TacticalAdvice:
    mode: str            # "normal" | "attack" | "defend"
    def_x_fraction: float
    gk_stays: bool
    rationale: str


@dataclass
class AdviceEvent:
    clock: float
    features: dict
    advice: TacticalAdvice
    outcome: dict | None = None


_DEFAULT_ADVICE = TacticalAdvice(mode="normal", def_x_fraction=0.0, gk_stays=False, rationale="default")


def _extract_features(s: Snapshot) -> dict:
    d = s.direction  # 1 for team 0, -1 for team 1
    ours = [p for p in s.players if p.team == s.team]
    theirs = [p for p in s.players if p.team != s.team]
    score_diff = s.score[s.team] - s.score[1 - s.team]
    if s.owner is None:
        possession = "free"
    elif s.owner[0] == s.team:
        possession = "us"
    else:
        possession = "them"
    our_avg_x = d * sum(p.x for p in ours) / len(ours) if ours else 0.0
    their_avg_x = d * sum(p.x for p in theirs) / len(theirs) if theirs else 0.0
    # opponent players in our half: their x < 0 from our perspective
    opp_in_our_half = sum(1 for p in theirs if d * p.x < 0)
    avg_stamina = sum(p.stamina for p in ours) / len(ours) if ours else 1.0
    return {
        "clock": s.clock,
        "remaining": s.duration - s.clock,
        "score_diff": score_diff,
        "possession": possession,
        "our_avg_x": round(our_avg_x, 2),
        "their_avg_x": round(their_avg_x, 2),
        "opp_in_our_half": opp_in_our_half,
        "avg_stamina": round(avg_stamina, 3),
    }
