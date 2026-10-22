"""Rules v2: local foul / tackle model. Every constant is a local assumption.

See local_simulator/docs/rules-v2.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from football_lab.match_contracts import EngineBallState, EnginePlayerState, Intent, MatchEvent


class RandomSource(Protocol):
    def random(self) -> float: ...


@dataclass(frozen=True)
class RulesV2Config:
    foul_scale: float = 1.0
    p_foul: float = 0.25
    behind_multiplier: float = 2.0
    behind_angle_deg: float = 60.0
    p_win_slide: float = 0.55
    p_win_press_step: float = 0.01
    stun_steps: int = 40
    send_off_fouls: int = 3
    box_depth: float = 16.5
    box_half_width: float = 20.0
    p_penalty_goal: float = 0.75

    def __post_init__(self) -> None:
        if self.foul_scale < 0:
            raise ValueError("foul_scale must be >= 0")


def contest(
    players: list[EnginePlayerState],
    ball: EngineBallState,
    score: list[int],
    intents: list[Intent],
    tick: int,
    *,
    rng: RandomSource,
    cfg: RulesV2Config,
) -> list[MatchEvent]:
    raise NotImplementedError
