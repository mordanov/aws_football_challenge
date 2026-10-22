"""Playbook: every tunable behaviour as a bounded knob.

The learners (in-match scout, cross-match lessons, LLM analyst) may only
change these values, and every change passes through clamp(), so no learner
can push the team into a nonsensical configuration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Knob:
    default: float
    lo: float
    hi: float
    step: float
    doc: str


KNOBS: dict[str, Knob] = {
    "shoot_range": Knob(24, 14, 34, 2, "max distance to goal to shoot (m)"),
    "shot_clear_min": Knob(2.0, 1.0, 4.0, 0.3, "min shot-lane clearance (m)"),
    "shot_power": Knob(0.9, 0.6, 1.0, 0.05, "shot power"),
    "pass_lane_min": Knob(3.0, 1.5, 5.0, 0.3, "min pass-lane clearance (m)"),
    "pass_max_len": Knob(35, 15, 55, 3, "longest pass considered (m)"),
    "through_bonus": Knob(20, 0, 40, 5, "score bonus: THROUGH to an open runner"),
    "aerial_bonus": Knob(0, -10, 20, 5, "score bonus: AERIAL over a blocked lane"),
    "press_height": Knob(0.15, -0.4, 0.6, 0.1, "ball progress (frac of half) above which DEF stops pressing"),
    "press_intensity": Knob(0.8, 0.4, 1.0, 0.1, "PRESS_BALL intensity"),
    "def_line": Knob(-0.55, -0.8, -0.2, 0.05, "DEF anchor depth (frac of half-length)"),
    "shape_shift": Knob(0.0, -0.25, 0.25, 0.05, "whole team forward(+)/back(-)"),
    "lateral_bias": Knob(0.0, -0.3, 0.3, 0.05, "DEF/MID shift toward a flank (frac of half-width)"),
    "mark_tight": Knob(1, 0, 1, 1, "1 = TIGHT marking, 0 = LOOSE"),
    "tackle_range": Knob(2.0, 0, 3.5, 0.5, "SLIDE_TACKLE trigger distance (0 = never)"),
    "gk_kick_min": Knob(25, 10, 45, 5, "GK kicks to targets beyond this distance, throws below"),
    "sprint_stamina_min": Knob(0.35, 0.1, 0.7, 0.05, "no sprinting below this stamina unless critical"),
    "counter_trigger": Knob(3, 2, 4, 1, "opp outfielders in our half that trigger counter mode"),
}


def defaults() -> dict[str, float]:
    return {k: v.default for k, v in KNOBS.items()}


def clamp_value(name: str, v: float) -> float:
    k = KNOBS[name]
    v = max(k.lo, min(k.hi, float(v)))
    return round(v, 4)


@dataclass
class Playbook:
    values: dict[str, float] = field(default_factory=defaults)
    version: int = 0
    name: str = "champion"
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.clamp()

    def get(self, k: str) -> float:
        return self.values[k]

    def clamp(self) -> "Playbook":
        merged = defaults()
        merged.update({k: v for k, v in self.values.items() if k in KNOBS and _num(v)})
        self.values = {k: clamp_value(k, v) for k, v in merged.items()}
        return self

    def with_nudges(self, nudges: dict[str, float]) -> "Playbook":
        """Additive deltas (unknown knobs ignored); result is clamped."""
        vals = dict(self.values)
        for k, d in nudges.items():
            if k in vals and _num(d):
                vals[k] = vals[k] + d
        return Playbook(vals, self.version, self.name, list(self.notes))

    def diff(self, other: "Playbook") -> dict[str, tuple[float, float]]:
        return {k: (other.values[k], v) for k, v in self.values.items() if abs(v - other.values[k]) > 1e-9}

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "values": self.values, "notes": self.notes}

    @classmethod
    def from_json(cls, d: Any) -> "Playbook":
        if not isinstance(d, dict):
            return cls()
        return cls(dict(d.get("values", {})), int(d.get("version", 0) or 0),
                   str(d.get("name", "champion")), list(d.get("notes", [])))


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)
