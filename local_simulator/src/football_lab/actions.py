"""Command validation and intent resolution for the M2 engine."""
from __future__ import annotations

from typing import Any

from football_lab.match_contracts import ActionResult, EngineState, Intent
from football_lab.physics import PITCH_HALF_L, PITCH_HALF_W

ONE_SHOT_COMMANDS: frozenset[str] = frozenset({"PASS", "SHOOT", "GK_DISTRIBUTE", "SLIDE_TACKLE"})

_VALID_COMMANDS: frozenset[str] = frozenset({
    "MOVE_TO",
    "PASS",
    "SHOOT",
    "PRESS_BALL",
    "MARK",
    "INTERCEPT",
    "GK_DISTRIBUTE",
    "SET_STANCE",
    "SLIDE_TACKLE",
})

_PASS_TYPES = {"GROUND", "AERIAL", "THROUGH"}
_AIM_LOCATIONS = {"TL", "TR", "BL", "BR", "CENTER"}
_MARK_TIGHTNESS = {"TIGHT", "LOOSE"}

_FALLBACK_INTENT: dict[str, Any] = {"command_type": "SET_STANCE", "parameters": {"stance": 0}}


def _validate(
    command_type: str,
    params: dict[str, Any],
    team_id: int,
    player_id: int,
) -> str | None:
    """Return rejection reason string or None if accepted."""
    if command_type not in _VALID_COMMANDS:
        return "unknown_command"

    if command_type == "MOVE_TO":
        tx = params.get("target_x", 0.0)
        ty = params.get("target_y", 0.0)
        if abs(float(tx)) > PITCH_HALF_L or abs(float(ty)) > PITCH_HALF_W:
            return "target_out_of_bounds"

    elif command_type == "PASS":
        tid = params.get("target_player_id")
        if tid is not None and int(tid) == player_id:
            return "pass_to_self"
        ptype = str(params.get("type", "")).upper()
        if ptype not in _PASS_TYPES:
            return "unknown_pass_type"

    elif command_type == "SHOOT":
        aim = str(params.get("aim_location", "")).upper()
        if aim not in _AIM_LOCATIONS:
            return "bad_aim_location"

    elif command_type == "MARK":
        tightness = str(params.get("tightness", "")).upper()
        if tightness not in _MARK_TIGHTNESS:
            return "bad_tightness"

    elif command_type == "SET_STANCE":
        stance = params.get("stance", 0)
        if int(stance) < 0:
            return "negative_stance"

    return None


def resolve_command(
    cmd: dict[str, Any],
    state: EngineState,
) -> tuple[Intent, ActionResult]:
    """Validate command and return (Intent, ActionResult).

    Rejected commands produce a SET_STANCE fallback intent.
    """
    team_id: int = int(cmd.get("team_id", 0))
    player_id: int = int(cmd.get("player_id", 0))
    command_type: str = str(cmd.get("command_type", ""))
    params: dict[str, Any] = dict(cmd.get("parameters", {}))
    tick: int = state["tick"]

    reason = _validate(command_type, params, team_id, player_id)

    if reason is None:
        intent: Intent = {
            "team_id": team_id,
            "player_id": player_id,
            "command_type": command_type,
            "parameters": params,
            "is_one_shot": command_type in ONE_SHOT_COMMANDS,
            "executed": False,
        }
        result: ActionResult = {
            "tick": tick,
            "team_id": team_id,
            "player_id": player_id,
            "attempted_type": command_type,
            "attempted_params": params,
            "accepted": True,
            "rejection_reason": None,
        }
    else:
        intent = {
            "team_id": team_id,
            "player_id": player_id,
            "command_type": "SET_STANCE",
            "parameters": {"stance": 0},
            "is_one_shot": False,
            "executed": False,
        }
        result = {
            "tick": tick,
            "team_id": team_id,
            "player_id": player_id,
            "attempted_type": command_type,
            "attempted_params": params,
            "accepted": False,
            "rejection_reason": reason,
        }

    return intent, result
