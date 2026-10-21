from collections.abc import Sequence
from pathlib import Path

from .contracts import Agent, Frame, Observation, Player, Replay, ReplayStore, Reply, Stepper


def initial_frame() -> Frame:
    players: list[Player] = []
    for team in (0, 1):
        for player in range(5):
            players.append(
                {
                    "team_id": team,
                    "player_id": player,
                    "position": {
                        "x": float((-30 + player * 5) * (1 if team == 0 else -1)),
                        "y": float(-20 + player * 10),
                    },
                }
            )
    return {"tick": 0, "players": players, "ball": {"x": 0.0, "y": 0.0}}


class TrivialStepper:
    def advance(self, frame: Frame, replies: Sequence[Reply]) -> Frame:
        return {"tick": 1, "players": frame["players"], "ball": frame["ball"]}


def run(
    seed: int,
    agents: tuple[Agent, Agent],
    stepper: Stepper,
    store: ReplayStore,
    output: Path,
) -> Replay:
    if not 0 <= seed < 2**32:
        raise ValueError("seed must be a nonnegative 32-bit integer")
    frame = initial_frame()
    observations: list[Observation] = [
        {"schema_version": 1, "seed": seed, "team_id": team, "frame": frame} for team in (0, 1)
    ]
    replies = [agent.decide(obs) for agent, obs in zip(agents, observations)]
    next_frame = stepper.advance(frame, replies)
    replay: Replay = {
        "schema_version": 1,
        "kind": "wiring-check",
        "status": "complete",
        "seed": seed,
        "observations": observations,
        "replies": replies,
        "frames": [frame, next_frame],
    }
    store.save(replay, output)
    return replay
