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

_VALID_MODES = {"normal", "attack", "defend"}

_SYSTEM_PROMPT = (
    "You are a football tactical advisor for a 5-a-side match (120 s duration).\n"
    "Respond with valid JSON only. Schema:\n"
    '{"mode": "normal"|"attack"|"defend",\n'
    ' "def_x_fraction": float -1.0..1.0,\n'
    ' "gk_stays": bool,\n'
    ' "rationale": string}\n'
    'mode "attack": DEF pushes forward (def_x_fraction > 0), all outfield players advance.\n'
    'mode "defend": DEF and MID pull back, hold shape.\n'
    'mode "normal": use default profile positions.\n'
    "def_x_fraction: 0.0 = centre, 0.3 = our attacking third, -0.3 = our defensive third.\n"
    "gk_stays: true = GK holds goal line regardless of mode.\n"
    "Stay conservative unless score or time clearly demands action."
)


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


class InMatchAdvisor:
    def __init__(self, llm: Any, team_id: str) -> None:
        self._llm = llm
        self._team_id = team_id
        self.reset("__init__")

    def reset(self, match_id: str) -> None:
        self._match_id = match_id
        self._cache = TacticalAdvice(mode="normal", def_x_fraction=0.0, gk_stays=False, rationale="default")
        self._last_call_clock: float = -math.inf
        self._last_score: tuple = (-1, -1)
        self._snapshot_history: deque = deque(maxlen=15)
        self._advice_log: list[AdviceEvent] = []

    def current_mode(self) -> TacticalAdvice:
        return self._cache

    def maybe_advise(self, s: Snapshot) -> None:
        features = _extract_features(s)
        self._snapshot_history.append(features)
        current_score = (s.score[s.team], s.score[1 - s.team])
        score_changed = current_score != self._last_score and self._last_score != (-1, -1)
        elapsed = s.clock - self._last_call_clock
        if not (score_changed or elapsed >= 20):
            self._last_score = current_score
            return
        # Fill outcome for any open events
        for event in self._advice_log:
            if event.outcome is None:
                event.outcome = {
                    "score_delta": features["score_diff"] - event.features["score_diff"],
                    "possession": features["possession"],
                    "clock_evaluated": s.clock,
                }
        self._last_call_clock = s.clock
        self._last_score = current_score
        new_advice = self._call_llm(list(self._snapshot_history)[-3:])
        self._cache = new_advice
        self._advice_log.append(AdviceEvent(clock=s.clock, features=features, advice=new_advice))

    def _call_llm(self, recent_features: list[dict]) -> TacticalAdvice:
        if self._llm is None:
            return self._cache
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            history_summary = [
                {"clock": e.clock, "advice": e.advice.mode}
                for e in self._advice_log[-5:]
            ]
            payload = {
                "recent_snapshots": recent_features,
                "advice_history": history_summary,
            }
            messages = [SystemMessage(_SYSTEM_PROMPT), HumanMessage(json.dumps(payload))]
            response = self._llm.invoke(messages)
            raw = json.loads(response.content)
            mode = raw.get("mode", "")
            if mode not in _VALID_MODES:
                return self._cache
            return TacticalAdvice(
                mode=mode,
                def_x_fraction=float(raw.get("def_x_fraction", 0.0)),
                gk_stays=bool(raw.get("gk_stays", False)),
                rationale=str(raw.get("rationale", "")),
            )
        except Exception:
            return self._cache

    def flush_advice_log(self, flush_fn: Callable | None) -> None:
        if flush_fn is not None and self._advice_log:
            flush_fn(self._advice_log)
        self._advice_log = []
