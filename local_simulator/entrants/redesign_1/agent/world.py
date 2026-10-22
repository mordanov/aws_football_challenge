"""Internal world model.

Coordinates are rotated so that WE ALWAYS ATTACK TOWARD +x: for the away team
(attacking -x on the wire) both x and y are negated. Rotating (not just
flipping x) keeps "attacker's left" the same side for both teams.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ROLE_BY_ID = {0: "GK", 1: "DEF", 2: "MID", 3: "FWD", 4: "FWD"}
OUTFIELD = (1, 2, 3, 4)
LENGTH = 110.0
WIDTH = 70.0
HALF_L = LENGTH / 2
HALF_W = WIDTH / 2
GOAL_HALF_WIDTH = 3.66
BOX_DEPTH = 16.5
BOX_HALF_WIDTH = 20.0


@dataclass(frozen=True)
class Pl:
    id: int
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    stamina: float = 1.0
    sprinting: bool = False
    last_action: str = ""

    @property
    def pos(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass
class World:
    me: Pl
    mates: list[Pl]
    opps: list[Pl]
    ball: tuple[float, float]
    ball_vel: tuple[float, float] = (0.0, 0.0)
    owner: Optional[tuple[str, int]] = None  # ("us"|"them", idx); None = free/unknown
    score_us: int = 0
    score_them: int = 0
    game_time: float = 0.0
    tick: int = 0
    play_mode: str = ""
    mode_team_us: Optional[bool] = None
    team_id: int = 0
    s: int = 1  # wire_x = s * internal_x, wire_y = s * internal_y
    coach: str = ""
    match_len: float = 300.0

    @property
    def role(self) -> str:
        return ROLE_BY_ID.get(self.me.id, "MID")

    @property
    def all_ours(self) -> list[Pl]:
        return [self.me, *self.mates]

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
    def opp_goal(self) -> tuple[float, float]:
        return (HALF_L, 0.0)

    @property
    def own_goal(self) -> tuple[float, float]:
        return (-HALF_L, 0.0)

    def ours(self, pid: int) -> Optional[Pl]:
        return next((p for p in self.all_ours if p.id == pid), None)

    def opp(self, pid: int) -> Optional[Pl]:
        return next((p for p in self.opps if p.id == pid), None)

    @property
    def open_play(self) -> bool:
        m = (self.play_mode or "").upper().replace("_", "")
        return m in ("", "OPENPLAY", "PLAYON", "0")


def in_own_box(x: float, y: float) -> bool:
    return x < -HALF_L + BOX_DEPTH and abs(y) < BOX_HALF_WIDTH
