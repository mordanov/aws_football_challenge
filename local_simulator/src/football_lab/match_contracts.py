"""Engine-level typed contracts for M2 (match replay, not wiring-check).

Separate from the M0 contracts in contracts.py; the two schemas never overlap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, NotRequired, TypedDict

__all__ = [
    "EngineCommand",
    "EnginePlayerState",
    "EngineBallState",
    "EngineState",
    "Intent",
    "MatchEvent",
    "ActionResult",
    "FrameRecord",
    "MatchReplay",
]

RulesVersion = Literal["v1", "v2"]


class EngineCommand(TypedDict):
    """Command in the engine's canonical form (team/player identity + parameter dict)."""

    team_id: int
    player_id: int
    command_type: str
    parameters: dict[str, Any]


class EnginePlayerState(TypedDict):
    team_id: int  # 0 or 1
    player_id: int  # 0-4
    x: float  # ± PITCH_HALF_L (55)
    y: float  # ± PITCH_HALF_W (35)
    vx: float  # velocity units/step
    vy: float
    stamina: float  # [0.0, 1.0]
    fouls: NotRequired[int]  # rules v2 only
    sent_off: NotRequired[bool]  # rules v2 only
    stunned_until: NotRequired[int]  # rules v2 only; inactive while tick <= this


class EngineBallState(TypedDict):
    x: float
    y: float
    z: float  # height (0 for ground)
    vx: float  # velocity units/step
    vy: float
    vz: float
    owner_team: int | None  # 0, 1, or None (free)
    owner_player: int | None  # 0-4 or None
    in_flight: bool  # True while a pass/shot is actively traveling


class EngineState(TypedDict):
    tick: int  # 0 .. 2400  (2400 = match end)
    play_mode: str  # OPEN_PLAY | KICK_OFF | THROW_IN | CORNER | GOAL_KICK
    mode_team: int | None  # team that takes the set piece (None in OPEN_PLAY)
    players: list[EnginePlayerState]
    ball: EngineBallState
    score: list[int]  # [team_0_goals, team_1_goals]


class Intent(TypedDict):
    """Resolved engine intent for one player over the current decision window."""

    team_id: int
    player_id: int
    command_type: str
    parameters: dict[str, Any]
    is_one_shot: bool  # PASS, SHOOT, GK_DISTRIBUTE, SLIDE_TACKLE
    executed: bool  # True after a one-shot has fired (becomes idle for remaining steps)


class MatchEvent(TypedDict):
    tick: int
    kind: str  # GOAL | KICK_OFF | THROW_IN | CORNER | GOAL_KICK
    # | PASS_LAUNCHED | PASS_COMPLETE | PASS_INTERCEPTED | PASS_OUT
    # | SHOT_LAUNCHED | SHOT_GOAL | SHOT_SAVE | SHOT_OUT
    # | TACKLE_SUCCESS | MATCH_END | FOUL | PENALTY | SEND_OFF
    team_id: int | None
    player_id: int | None
    x: float | None
    y: float | None


class ActionResult(TypedDict):
    tick: int
    team_id: int
    player_id: int
    attempted_type: str
    attempted_params: dict[str, Any]
    accepted: bool
    rejection_reason: str | None


class FrameRecord(TypedDict):
    tick: int
    players: list[EnginePlayerState]
    ball: EngineBallState
    score: list[int]  # [team_0_goals, team_1_goals]


class MatchReplay(TypedDict):
    schema_version: Literal[2]
    kind: Literal["match-replay"]
    status: Literal["complete", "partial", "failed"]
    rules_version: RulesVersion
    rules_params: NotRequired[dict[str, Any]]  # rules v2 only
    seed: int
    duration_ticks: int  # 2400 for a complete 120 s match
    action_results: list[ActionResult]
    events: list[MatchEvent]
    frames: list[FrameRecord]  # one per tick; 2401 entries for complete match


ContestHook = Callable[
    [list[EnginePlayerState], EngineBallState, list[int], list[Intent], int],
    list[MatchEvent],
]
"""Called once per physics step after players move: (players, ball, score, intents, tick).

Mutates its arguments in place and returns the events it produced.
"""

CoachFn = Callable[[EngineState, int], str]
"""Scripted coach: (state, team_id) -> preset text written to coachInstruction."""
