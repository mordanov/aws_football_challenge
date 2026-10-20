"""Canonical internal domain model for Redesign 3.

All spatial coordinates inside `World` are normalized so our team ALWAYS
defends `x = -55.0` and attacks `x = +55.0` (`y in [-35.0, +35.0]`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import math
from typing import Any


PITCH_HALF_L: float = 55.0
PITCH_HALF_W: float = 35.0
GOAL_HALF_W: float = 4.5
PENALTY_BOX_X: float = 38.5   # penalty area extends 16.5m from goal line (55 - 16.5 = 38.5)
PENALTY_BOX_HALF_W: float = 16.5
GOAL_AREA_X: float = 49.5     # 6-yard box extends 5.5m from goal line
PICKUP_RADIUS: float = 2.1
TACKLE_RADIUS: float = 2.4
BASE_MAX_SPEED: float = 5.2   # units per tick at full stamina without sprint


class Role(IntEnum):
    GK = 0
    DEF = 1
    MID = 2
    FWD1 = 3
    FWD2 = 4


ROLE_NAMES: dict[int, str] = {
    0: "GK",
    1: "DEF",
    2: "MID",
    3: "FWD1",
    4: "FWD2",
}


class Phase(str, Enum):
    IN_POSSESSION = "IN_POSSESSION"
    OUT_OF_POSSESSION = "OUT_OF_POSSESSION"
    TRANSITION_ATTACK = "TRANSITION_ATTACK"
    TRANSITION_DEFENSE = "TRANSITION_DEFENSE"
    LOOSE_BALL = "LOOSE_BALL"
    SET_PIECE = "SET_PIECE"


class OpponentArchetype(str, Enum):
    UNKNOWN = "UNKNOWN"
    BALANCED = "BALANCED"
    MOB_PRESS = "MOB_PRESS"
    ULTRA_HIGH_LINE = "ULTRA_HIGH_LINE"
    LOW_BLOCK = "LOW_BLOCK"
    FROZEN_LLM = "FROZEN_LLM"


@dataclass(frozen=True)
class Vec2:
    x: float
    y: float

    def dist(self, other: "Vec2") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def add(self, dx: float, dy: float) -> "Vec2":
        return Vec2(self.x + dx, self.y + dy)

    def scale(self, factor: float) -> "Vec2":
        return Vec2(self.x * factor, self.y * factor)

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def toward(self, target: "Vec2", distance: float) -> "Vec2":
        d = self.dist(target)
        if d < 1e-6:
            return self
        ratio = distance / d
        return Vec2(
            self.x + (target.x - self.x) * ratio,
            self.y + (target.y - self.y) * ratio,
        )

    def lerp(self, target: "Vec2", t: float) -> "Vec2":
        t_clamped = max(0.0, min(1.0, t))
        return Vec2(
            self.x + (target.x - self.x) * t_clamped,
            self.y + (target.y - self.y) * t_clamped,
        )

    def clamp_to_pitch(self, margin_x: float = 1.0, margin_y: float = 1.0) -> "Vec2":
        lim_x = PITCH_HALF_L - margin_x
        lim_y = PITCH_HALF_W - margin_y
        return Vec2(
            max(-lim_x, min(lim_x, self.x)),
            max(-lim_y, min(lim_y, self.y)),
        )


@dataclass(frozen=True)
class Player:
    id: int
    team: str               # "me" | "opp"
    pos: Vec2
    vel: Vec2 = field(default_factory=lambda: Vec2(0.0, 0.0))
    stamina: float = 1.0    # normalized to [0.0, 1.0]
    has_ball: bool = False
    orientation: float = 0.0
    speed: float = 0.0
    is_sprinting: bool = False
    last_action: str = ""
    yellow_cards: int = 0
    red_card: bool = False

    @property
    def role(self) -> Role:
        idx = max(0, min(4, int(self.id) % 5))
        return Role(idx)

    @property
    def speed_factor(self) -> float:
        """Stamina speed multiplier matching simulator physics (0.6 + 0.4 * stamina)."""
        return 0.6 + 0.4 * max(0.0, min(1.0, self.stamina))


@dataclass(frozen=True)
class Ball:
    pos: Vec2
    vel: Vec2
    z: float = 0.0
    vz: float = 0.0
    is_free: bool = True
    owner_team: str | None = None   # "me" | "opp" | None
    owner_id: int | None = None
    possession_raw: str | None = None


@dataclass(frozen=True)
class Score:
    me: int = 0
    opp: int = 0

    @property
    def diff(self) -> int:
        return self.me - self.opp


@dataclass(frozen=True)
class World:
    tick: int
    clock: float                    # seconds elapsed (0..300)
    time_remaining: float           # estimated seconds remaining
    play_mode: str                  # e.g., "OPEN_PLAY", "KICK_OFF", "CORNER", etc.
    mode_team: str | None           # "me" | "opp" | None
    my_team_id: int                 # 0 (HOME) or 1 (AWAY)
    attack_sign: int                # +1 if my_team_id == 0 else -1
    controlled_ids: tuple[int, ...] # e.g. (0, 1, 2, 3, 4) or (2,)
    ball: Ball
    me: tuple[Player, ...]
    opp: tuple[Player, ...]
    score: Score
    coach_instruction: str | None = None
    team_chat: tuple[str, ...] = ()

    def my_player(self, pid: int) -> Player | None:
        for p in self.me:
            if p.id == pid:
                return p
        return None

    def opp_player(self, pid: int) -> Player | None:
        for p in self.opp:
            if p.id == pid:
                return p
        return None

    def my_gk(self) -> Player | None:
        return self.my_player(int(Role.GK))

    def opp_gk(self) -> Player | None:
        return self.opp_player(int(Role.GK))

    def ball_carrier(self) -> Player | None:
        if self.ball.owner_team == "me" and self.ball.owner_id is not None:
            return self.my_player(self.ball.owner_id)
        if self.ball.owner_team == "opp" and self.ball.owner_id is not None:
            return self.opp_player(self.ball.owner_id)
        for p in (*self.me, *self.opp):
            if p.has_ball:
                return p
        return None


@dataclass
class Command:
    commandType: str
    playerId: int
    teamId: int = 0
    parameters: dict[str, Any] = field(default_factory=dict)
    duration: int = 0

    def to_wire(self) -> dict[str, Any]:
        return {
            "commandType": self.commandType,
            "playerId": int(self.playerId),
            "teamId": int(self.teamId),
            "parameters": dict(self.parameters),
            "duration": int(self.duration),
        }
