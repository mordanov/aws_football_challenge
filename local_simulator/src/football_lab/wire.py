import math

from .contracts import Command, Reply


def record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("expected an object with string keys")
    return {k: v for k, v in value.items() if isinstance(k, str)}


def integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected a native integer")
    return value


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a native number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError("number exceeds finite range") from error
    if not math.isfinite(result):
        raise ValueError("expected a finite number")
    return result


def parse_reply(value: object, team: int) -> Reply:
    root = record(value)
    if set(root) != {"schema_version", "commands"}:
        raise ValueError("unexpected reply fields")
    if integer(root["schema_version"]) != 1:
        raise ValueError("unsupported reply version")
    raw = root["commands"]
    if not isinstance(raw, list) or len(raw) != 5:
        raise ValueError("expected five commands")
    commands: list[Command] = []
    seen: set[int] = set()
    for item in raw:
        command = record(item)
        if set(command) != {"team_id", "player_id", "command_type", "target_x", "target_y"}:
            raise ValueError("unexpected command fields")
        player = integer(command["player_id"])
        if integer(command["team_id"]) != team or player not in range(5):
            raise ValueError("wrong command owner")
        if player in seen:
            raise ValueError("duplicate player command")
        if command["command_type"] != "MOVE_TO":
            raise ValueError("unsupported skeleton command")
        seen.add(player)
        commands.append(
            {
                "team_id": team,
                "player_id": player,
                "command_type": "MOVE_TO",
                "target_x": number(command["target_x"]),
                "target_y": number(command["target_y"]),
            }
        )
    return {"schema_version": 1, "commands": commands}
