"""Normalize wire commands (camelCase) to engine format (snake_case)."""
from __future__ import annotations

from typing import Any

_REMAP: dict[str, str] = {
    "DRIBBLE": "MOVE_TO",
    "TACKLE": "SLIDE_TACKLE",
    "FOLLOW_PLAYER": "MARK",
    "CLEAR": "SET_STANCE",
    "CLEAR_OVERRIDE": "SET_STANCE",
    "RESET": "SET_STANCE",
    "IDLE": "SET_STANCE",
}
_SET_STANCE_CMDS = frozenset({"CLEAR", "CLEAR_OVERRIDE", "RESET", "IDLE"})
_DROP_FOLLOW_PARAMS = frozenset({"distance", "target_team"})


def decode_command(wire: dict[str, Any], team_id: int) -> dict[str, Any]:
    """Normalize one wire command to engine format.

    - camelCase keys → snake_case
    - command type remapped per _REMAP table
    - team_id injected from caller (overrides wire teamId)
    """
    ct = wire.get("commandType", "SET_STANCE")
    pid = int(wire.get("playerId", 0))
    params = dict(wire.get("parameters") or {})
    target_ct = _REMAP.get(ct, ct)

    if ct in _SET_STANCE_CMDS:
        params = {"stance": 0}
    elif ct == "DRIBBLE":
        params = {"target_x": params.get("x", 0.0), "target_y": params.get("y", 0.0)}
    elif ct == "FOLLOW_PLAYER":
        for k in _DROP_FOLLOW_PARAMS:
            params.pop(k, None)

    # MOVE_TO legacy x/y → target_x/target_y (initial_idea emits x/y)
    if target_ct == "MOVE_TO" and "x" in params and "target_x" not in params:
        params["target_x"] = params.pop("x")
        params["target_y"] = params.pop("y", 0.0)

    return {
        "team_id": team_id,
        "player_id": pid,
        "command_type": target_ct,
        "parameters": params,
    }
