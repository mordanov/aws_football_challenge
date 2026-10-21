from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol, TypedDict


class Point(TypedDict):
    x: float
    y: float


class Player(TypedDict):
    team_id: int
    player_id: int
    position: Point


class Frame(TypedDict):
    tick: int
    players: list[Player]
    ball: Point


class Observation(TypedDict):
    schema_version: Literal[1]
    seed: int
    team_id: int
    frame: Frame


class Command(TypedDict):
    team_id: int
    player_id: int
    command_type: Literal["MOVE_TO"]
    target_x: float
    target_y: float


class Reply(TypedDict):
    schema_version: Literal[1]
    commands: list[Command]


class Replay(TypedDict):
    schema_version: Literal[1]
    kind: Literal["wiring-check"]
    status: Literal["complete"]
    seed: int
    observations: list[Observation]
    replies: list[Reply]
    frames: list[Frame]


class Agent(Protocol):
    def decide(self, observation: Observation) -> Reply: ...


class Stepper(Protocol):
    def advance(self, frame: Frame, replies: Sequence[Reply]) -> Frame: ...


class ReplayStore(Protocol):
    def save(self, replay: Replay, path: Path) -> None: ...
