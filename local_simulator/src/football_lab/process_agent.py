import json
import subprocess

from .contracts import Observation, Reply
from .wire import parse_reply


class ProcessAgent:
    def __init__(self, argv: tuple[str, ...], timeout: float = 5.0) -> None:
        self.argv = argv
        self.timeout = timeout

    def decide(self, observation: Observation) -> Reply:
        result = subprocess.run(
            self.argv,
            input=json.dumps(observation, allow_nan=False) + "\n",
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"agent exited {result.returncode}: {result.stderr[:200]}")
        if len(result.stdout.encode("utf-8")) > 65536:
            raise ValueError("skeleton reply exceeds 65536 bytes")
        value: object = json.loads(result.stdout)
        return parse_reply(value, observation["team_id"])
