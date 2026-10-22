"""Official AWS AgentCore wire-format command builders.

Every builder returns a `Command` whose `commandType` belongs to the 12 official
`VALID_COMMANDS` in `aws-samples/sample-ai-possibilities/agentic-football-sample-agents/lib/parsing.py`:
    MOVE_TO, PASS, SHOOT, SLIDE_TACKLE, PRESS_BALL, INTERCEPT,
    MARK, FOLLOW_PLAYER, GK_DISTRIBUTE, SET_STANCE, CLEAR_OVERRIDE, RESET

Crucial wire compatibility notes:
1. `MOVE_TO` requires `target_x`, `target_y`, `sprint`. We also include `x` and `y`
   as harmless mirror keys so both the official parser (`target_x`/`target_y`) and
   any legacy test harness (`x`/`y`) work seamlessly.
2. There is NO `"DRIBBLE"` command in `VALID_COMMANDS`. Carrying the ball while in
   possession is performed via `MOVE_TO`.
3. `SHOOT` `aim_location` must be one of `{"TL", "TR", "BL", "BR", "CENTER"}`.
4. `PASS` requires `{"target_player_id": int, "type": "GROUND" | "AERIAL" | "THROUGH"}`.
"""

from __future__ import annotations

from .model import Command, PITCH_HALF_L, PITCH_HALF_W

VALID_COMMANDS: frozenset[str] = frozenset({
    "MOVE_TO",
    "PASS",
    "SHOOT",
    "SLIDE_TACKLE",
    "PRESS_BALL",
    "INTERCEPT",
    "MARK",
    "FOLLOW_PLAYER",
    "GK_DISTRIBUTE",
    "SET_STANCE",
    "CLEAR_OVERRIDE",
    "RESET",
})

VALID_AIM_LOCATIONS: frozenset[str] = frozenset({"TL", "TR", "BL", "BR", "CENTER"})
VALID_PASS_TYPES: frozenset[str] = frozenset({"GROUND", "AERIAL", "THROUGH"})
VALID_GK_METHODS: frozenset[str] = frozenset({"THROW", "KICK"})
VALID_TIGHTNESS: frozenset[str] = frozenset({"LOOSE", "TIGHT"})

_LEGACY_AIM_MAP: dict[str, str] = {
    "LEFT": "TL",
    "RIGHT": "TR",
    "TOP_LEFT": "TL",
    "TOP_RIGHT": "TR",
    "BOTTOM_LEFT": "BL",
    "BOTTOM_RIGHT": "BR",
}


def _clamp_x(x: float) -> float:
    return round(max(-PITCH_HALF_L, min(PITCH_HALF_L, float(x))), 2)


def _clamp_y(y: float) -> float:
    return round(max(-PITCH_HALF_W, min(PITCH_HALF_W, float(y))), 2)


def _unmirror(x: float, y: float, attack_sign: int) -> tuple[float, float]:
    s = -1 if attack_sign < 0 else 1
    return _clamp_x(x * s), _clamp_y(y * s)


def move_to(
    pid: int,
    x: float,
    y: float,
    sprint: bool = False,
    attack_sign: int = 1,
    team_id: int = 0,
    duration: int = 0,
) -> Command:
    """Move player (off-ball or while dribbling the ball) to normalized `(x, y)`."""
    wx, wy = _unmirror(x, y, attack_sign)
    return Command(
        commandType="MOVE_TO",
        playerId=pid,
        teamId=team_id,
        parameters={
            "target_x": wx,
            "target_y": wy,
            "sprint": bool(sprint),
            # Legacy compatibility aliases (ignored by official server, read by old tests)
            "x": wx,
            "y": wy,
        },
        duration=duration,
    )


def dribble_to(
    pid: int,
    x: float,
    y: float,
    sprint: bool = True,
    attack_sign: int = 1,
    team_id: int = 0,
) -> Command:
    """Carry the ball toward normalized `(x, y)` using official `MOVE_TO`."""
    return move_to(pid, x, y, sprint=sprint, attack_sign=attack_sign, team_id=team_id, duration=0)


def pass_to(
    pid: int,
    target_id: int,
    pass_type: str = "GROUND",
    team_id: int = 0,
) -> Command:
    """Pass the ball to `target_id` (`0..4`) with type `GROUND`, `THROUGH`, or `AERIAL`."""
    ptype = pass_type.upper() if isinstance(pass_type, str) else "GROUND"
    if ptype not in VALID_PASS_TYPES:
        ptype = "GROUND"
    return Command(
        commandType="PASS",
        playerId=pid,
        teamId=team_id,
        parameters={
            "target_player_id": max(0, min(4, int(target_id))),
            "type": ptype,
        },
        duration=0,
    )


def shoot(
    pid: int,
    aim: str = "TR",
    power: float = 0.85,
    team_id: int = 0,
) -> Command:
    """Shoot at the opponent goal aiming at `TL`, `TR`, `BL`, `BR`, or `CENTER`."""
    raw_aim = aim.upper() if isinstance(aim, str) else "TR"
    norm_aim = _LEGACY_AIM_MAP.get(raw_aim, raw_aim)
    if norm_aim not in VALID_AIM_LOCATIONS:
        norm_aim = "TR"
    clamped_power = round(max(0.1, min(1.0, float(power))), 2)
    return Command(
        commandType="SHOOT",
        playerId=pid,
        teamId=team_id,
        parameters={
            "aim_location": norm_aim,
            "power": clamped_power,
        },
        duration=0,
    )


def gk_distribute(
    pid: int,
    target_id: int,
    method: str = "THROW",
    team_id: int = 0,
) -> Command:
    """Goalkeeper distribution via `THROW` (fast, accurate short) or `KICK` (long)."""
    m = method.upper() if isinstance(method, str) else "THROW"
    if m not in VALID_GK_METHODS:
        m = "THROW"
    return Command(
        commandType="GK_DISTRIBUTE",
        playerId=pid,
        teamId=team_id,
        parameters={
            "target_player_id": max(0, min(4, int(target_id))),
            "method": m,
        },
        duration=0,
    )


def slide_tackle(
    pid: int,
    target_id: int,
    sprint: bool = True,
    distance: float = 2.0,
    team_id: int = 0,
) -> Command:
    """High-commitment slide tackle against opponent `target_id`."""
    return Command(
        commandType="SLIDE_TACKLE",
        playerId=pid,
        teamId=team_id,
        parameters={
            "target_player_id": max(0, min(4, int(target_id))),
            "sprint": bool(sprint),
            "distance": round(max(0.5, min(5.0, float(distance))), 2),
        },
        duration=0,
    )


def press_ball(
    pid: int,
    intensity: float = 0.8,
    duration: int = 2,
    team_id: int = 0,
) -> Command:
    """Close down the ball carrier at specified `intensity` (0.0..1.0)."""
    return Command(
        commandType="PRESS_BALL",
        playerId=pid,
        teamId=team_id,
        parameters={
            "intensity": round(max(0.1, min(1.0, float(intensity))), 2),
        },
        duration=max(0, min(5, int(duration))),
    )


def intercept(
    pid: int,
    aggressive: bool = True,
    duration: int = 2,
    team_id: int = 0,
) -> Command:
    """Move to intercept a pass or claim a loose ball."""
    return Command(
        commandType="INTERCEPT",
        playerId=pid,
        teamId=team_id,
        parameters={"aggressive": bool(aggressive)},
        duration=max(0, min(5, int(duration))),
    )


def mark(
    pid: int,
    target_id: int,
    tightness: str = "TIGHT",
    duration: int = 2,
    team_id: int = 0,
) -> Command:
    """Mark opponent `target_id` (`0..4`) with `LOOSE` or `TIGHT` tightness."""
    t = tightness.upper() if isinstance(tightness, str) else "TIGHT"
    if t not in VALID_TIGHTNESS:
        t = "TIGHT"
    return Command(
        commandType="MARK",
        playerId=pid,
        teamId=team_id,
        parameters={
            "target_player_id": max(0, min(4, int(target_id))),
            "tightness": t,
        },
        duration=max(0, min(5, int(duration))),
    )


def follow_player(
    pid: int,
    target_id: int,
    target_team: str = "AWAY",
    distance: float = 2.5,
    duration: int = 2,
    team_id: int = 0,
) -> Command:
    """Shadow a specific player at a fixed distance."""
    team_str = target_team.upper() if isinstance(target_team, str) else "AWAY"
    if team_str not in ("HOME", "AWAY"):
        team_str = "AWAY"
    return Command(
        commandType="FOLLOW_PLAYER",
        playerId=pid,
        teamId=team_id,
        parameters={
            "target_player_id": max(0, min(4, int(target_id))),
            "target_team": team_str,
            "distance": round(max(0.5, min(15.0, float(distance))), 2),
        },
        duration=max(0, min(5, int(duration))),
    )


def set_stance(
    pid: int,
    stance: int = 0,
    team_id: int = 0,
) -> Command:
    """Set player stance: 0=Balanced, 1=Attack, 2=Defend."""
    s = int(stance) if stance in (0, 1, 2) else 0
    return Command(
        commandType="SET_STANCE",
        playerId=pid,
        teamId=team_id,
        parameters={"stance": s},
        duration=0,
    )


def clear_override(pid: int, team_id: int = 0) -> Command:
    """Clear sticky duration command override."""
    return Command(
        commandType="CLEAR_OVERRIDE",
        playerId=pid,
        teamId=team_id,
        parameters={},
        duration=0,
    )
