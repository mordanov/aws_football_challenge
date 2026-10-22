"""Internal, schema-independent world model.

Everything in policy.py/tactics.py works on these classes only. The raw game
JSON is translated into them by adapter.py, so if the real protocol differs
from our guesses, only adapter.py and commands.py need to change.

Coordinates here are CENTERED on the pitch middle, in metres. `s` is the
attack direction (+1 = we attack toward +x, -1 = toward -x).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

ROLE_BY_ID = {0: "GK", 1: "DEF", 2: "MID", 3: "FWD", 4: "FWD"}


@dataclass(frozen=True)
class Pl:
    id: Any
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    stamina: float = 1.0  # normalised 0..1


@dataclass
class World:
    me: Pl
    mates: list[Pl]  # teammates, excluding me
    opps: list[Pl]
    ball: tuple[float, float]
    ball_vel: tuple[float, float] = (0.0, 0.0)
    owner: Optional[tuple[str, Any]] = None  # ("us"|"them", player id)
    score_us: int = 0
    score_them: int = 0
    clock: float = 0.0  # elapsed seconds
    match_len: float = 120.0
    length: float = 110.0
    width: float = 70.0
    s: int = 1
    coach: str = ""
    offset: tuple[float, float] = (0.0, 0.0)  # raw = internal + offset

    @property
    def role(self) -> str:
        try:
            return ROLE_BY_ID[int(self.me.id)]
        except (KeyError, ValueError, TypeError):
            return "MID"

    @property
    def i_have_ball(self) -> bool:
        return self.owner == ("us", self.me.id)

    @property
    def we_have_ball(self) -> bool:
        return self.owner is not None and self.owner[0] == "us"

    @property
    def they_have_ball(self) -> bool:
        return self.owner is not None and self.owner[0] == "them"

    @property
    def all_ours(self) -> list[Pl]:
        return [self.me, *self.mates]

    def prog(self, x: float) -> float:
        """Progress toward the opponent goal: -L/2 (own goal line) .. +L/2."""
        return self.s * x

    @property
    def opp_goal(self) -> tuple[float, float]:
        return (self.s * self.length / 2, 0.0)

    @property
    def own_goal(self) -> tuple[float, float]:
        return (-self.s * self.length / 2, 0.0)


@dataclass
class Candidate:
    cmd: dict
    score: float
    why: str
    forced: bool = False  # True -> never worth asking the LLM


@dataclass
class Decision:
    cmd: dict
    why: str
    source: str = "rules"  # rules | llm | fallback
    extras: dict = field(default_factory=dict)
