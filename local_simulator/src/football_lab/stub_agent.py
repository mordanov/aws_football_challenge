import json
import sys

from .contracts import Command, Reply
from .wire import integer, record


def main() -> None:
    request = record(json.load(sys.stdin))
    if integer(request.get("schema_version")) != 1:
        raise ValueError("unsupported observation version")
    team = integer(request.get("team_id"))
    if team not in (0, 1):
        raise ValueError("unsupported team")
    commands: list[Command] = [
        {
            "team_id": team,
            "player_id": player,
            "command_type": "MOVE_TO",
            "target_x": 0.0,
            "target_y": 0.0,
        }
        for player in range(5)
    ]
    reply: Reply = {"schema_version": 1, "commands": commands}
    print(json.dumps(reply, allow_nan=False, sort_keys=True))


if __name__ == "__main__":
    main()
