from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Player:
    team: int
    id: int
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    stamina: float = 1.0


@dataclass(frozen=True)
class Snapshot:
    team: int
    tick: int
    clock: float
    duration: float
    players: tuple[Player, ...]
    ball: tuple[float, float]
    velocity: tuple[float, float]
    owner: tuple[int, int] | None
    free: bool
    score: tuple[int, int]
    coach: str = ""

    @property
    def direction(self) -> int:
        return 1 if self.team == 0 else -1

    @property
    def ours(self) -> tuple[Player, ...]:
        return tuple(p for p in self.players if p.team == self.team)

    @property
    def opponents(self) -> tuple[Player, ...]:
        return tuple(p for p in self.players if p.team != self.team)

    def player(self, pid: int) -> Player:
        return next(p for p in self.ours if p.id == pid)


@dataclass(frozen=True)
class Settings:
    pass_margin: float = 3.0
    shot_range: float = 29.0
    press_radius: float = 22.0
    support_width: float = 15.0


DEFAULT_SETTINGS = Settings()


@dataclass(frozen=True)
class Style:
    """Strategy archetype knobs. Defaults are identity values (pre-change behaviour)."""

    block_depth: float = 1.0
    attackers_committed: int = 3
    tackle_aggression: float = 0.0
    tempo: float = 0.5
    formation: str = "1-1-2"


DEFAULT_STYLE = Style()


@dataclass(frozen=True)
class Profile:
    version: str = "baseline"
    settings: Settings = DEFAULT_SETTINGS
    opponents: dict[str, dict[str, Any]] | None = None
    style: Style = DEFAULT_STYLE
    coach_policy: str = "none"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    command: dict[str, Any]
    reason: str
    alternatives: tuple[dict[str, Any], ...] = ()
    assignments: dict[str, Any] | None = None
