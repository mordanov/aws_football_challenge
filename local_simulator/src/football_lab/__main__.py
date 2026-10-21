import argparse
import subprocess
import sys
from pathlib import Path

from .process_agent import ProcessAgent
from .replay import JsonReplayStore
from .skeleton import TrivialStepper, run


def main() -> int:
    parser = argparse.ArgumentParser(description="Local football wiring check; not a match")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    agent = ProcessAgent((sys.executable, "-m", "football_lab.stub_agent"))
    try:
        run(args.seed, (agent, agent), TrivialStepper(), JsonReplayStore(), args.output)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"wiring check failed: {error}", file=sys.stderr)
        return 1
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
