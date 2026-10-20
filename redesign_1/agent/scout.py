"""In-match scout: watches the opponent tick by tick and returns bounded nudges.

Runs inside each player runtime on the compact snapshots from events.snapshot().
Every observation uses hysteresis so one odd tick never flips the tactics.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Optional

from . import events as E

FLANK_MIN_SAMPLES = 10
FP_MIN_SAMPLES = 8


class Flag:
    """Switch on after `on_n` consecutive confirmations, off after `off_n` contrary ones."""

    def __init__(self, on_n: int = 3, off_n: int = 3) -> None:
        self.on_n, self.off_n, self.on, self._run = on_n, off_n, False, 0

    def update(self, cond: bool) -> bool:
        if cond != self.on:
            self._run += 1
            if self._run >= (self.on_n if cond else self.off_n):
                self.on, self._run = cond, 0
        else:
            self._run = 0
        return self.on


class Scout:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.prev: Optional[dict] = None
        self.ticks = 0
        self.events: list[dict] = []
        self.gk_out = Flag(5, 3)
        self.line_high = Flag(3, 3)
        self.deep_block = Flag(3, 3)
        self.flank_y: list[float] = []
        self.press_samples = 0
        self.press_hits = 0
        self.line_sum = 0.0
        self.gk_sum = 0.0
        self.sprint_sum = 0.0
        self.carrier_x: list[float] = []
        self.fam = Counter()
        self.pass_lost = 0
        self.counter_goals = 0
        self.extra: dict[str, float] = {}  # dossier pre-nudges

    # ------------------------------------------------------------ observing
    def observe(self, snap: dict) -> list[dict]:
        ev: list[dict] = []
        if self.prev is not None and snap.get("k") != self.prev.get("k"):
            ev = E.detect(self.prev, snap)
        elif self.prev is not None:
            return []  # duplicate tick (several players in one runtime)
        self.prev = snap
        self.ticks += 1
        self.events.extend(ev)
        for e in ev:
            if e["type"] == "PASS_LOST":
                self.pass_lost += 1
            elif e["type"] == "GOAL_AGAINST" and e.get("counter"):
                self.counter_goals += 1

        f = E.opp_features(snap)
        self.gk_out.update(f["gk_off"] > 8.0)
        self.line_high.update(f["line"] < -5.0)
        self.deep_block.update(f["line"] > 15.0)
        self.line_sum += f["line"]
        self.gk_sum += f["gk_off"]
        self.sprint_sum += f["sprint"]
        self.fam.update(f["fam"])
        if f["carrier_y"] is not None:
            self.flank_y.append(f["carrier_y"])
            own = snap.get("own")
            c = snap.get("them", {}).get(str(own[1])) if own else None
            if c:
                self.carrier_x.append(c[0])
        if f["pressers"] is not None:
            self.press_samples += 1
            self.press_hits += 1 if f["pressers"] > 0 else 0
        return ev

    def set_extra(self, nudges: dict[str, float]) -> None:
        self.extra = dict(nudges or {})

    # --------------------------------------------------------------- output
    def active(self) -> list[str]:
        """Names of the nudges currently on (logged and stored for dossiers)."""
        on = []
        if self.gk_out.on:
            on.append("gk_off_line")
        if self.pass_lost >= 2:
            on.append("passes_lost")
        side = self._flank()
        if side:
            on.append(f"flank_{side}")
        if self.line_high.on:
            on.append("line_high")
        if self.deep_block.on:
            on.append("deep_block")
        if self.counter_goals >= 2:
            on.append("counter_goals")
        return on

    def nudges(self) -> dict[str, float]:
        n: dict[str, float] = dict(self.extra)

        def add(k: str, v: float) -> None:
            n[k] = n.get(k, 0.0) + v

        for name in self.active():
            for k, v in NUDGES[name].items():
                add(k, v)
        return n

    def _flank(self) -> Optional[str]:
        if len(self.flank_y) < FLANK_MIN_SAMPLES:
            return None
        left = sum(1 for y in self.flank_y if y > 0) / len(self.flank_y)
        return "L" if left >= 0.65 else "R" if left <= 0.35 else None

    def fingerprint(self) -> Optional[list[float]]:
        """Fixed-order opponent descriptor, each feature roughly in 0..1."""
        if self.ticks < FP_MIN_SAMPLES:
            return None
        n = self.ticks
        fam_total = sum(self.fam.values()) or 1
        left = (sum(1 for y in self.flank_y if y > 0) / len(self.flank_y)) if self.flank_y else 0.5
        depth = (sum(self.carrier_x) / len(self.carrier_x)) if self.carrier_x else 0.0
        fp = [
            _sq((self.line_sum / n + 55) / 110),            # their mean line, 0 = deep in our half
            self.press_hits / self.press_samples if self.press_samples else 0.0,
            _sq(self.gk_sum / n / 20),                      # keeper off line
            left,                                           # carrier left-flank share
            _sq((55 - depth) / 110),                        # how deep their carriers get (1 = at our goal)
            _sq(self.sprint_sum / n),
        ]
        fp += [self.fam.get(k, 0) / fam_total for k in E.ACTION_FAMILIES]
        return [round(v, 3) for v in fp]


def _sq(v: float) -> float:
    return max(0.0, min(1.0, v))


def fp_distance(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return math.inf
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


NUDGES: dict[str, dict[str, float]] = {
    "gk_off_line": {"shoot_range": 6, "shot_power": 0.1},
    "passes_lost": {"pass_lane_min": 0.6},
    "flank_L": {"lateral_bias": 0.15},
    "flank_R": {"lateral_bias": -0.15},
    "line_high": {"through_bonus": 15, "aerial_bonus": 5},
    "deep_block": {"shot_clear_min": 0.5, "shape_shift": 0.05},
    "counter_goals": {"def_line": -0.1, "shape_shift": -0.05},
}
