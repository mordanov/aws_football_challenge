"""CLI subcommand: python -m football_lab match --seed N --output FILE."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from football_lab.engine import MatchEngine, StubAgent
from football_lab.match_replay import JsonMatchReplayStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a simulated 120-second match")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    store = JsonMatchReplayStore()
    engine = MatchEngine(
        seed=args.seed,
        team0_agent=StubAgent(0),
        team1_agent=StubAgent(1),
        store=store,
    )
    try:
        engine.run(args.output)
    except OSError as err:
        print(f"match failed: {err}", file=sys.stderr)
        return 1

    print(args.output.resolve())
    return 0
